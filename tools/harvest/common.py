"""Shared pieces for the Aletheia harvesters (pressbooks.py, otl.py, merge.py).

- PoliteSession: HTTP with an honest User-Agent. It reads robots.txt and honors crawl-delay,
  spaces requests to each host (2 s by default), retries transient failures with backoff, and
  stops on bot checks and 403s without retrying.
- The subject filter and the skin/topic tagger. Their keyword rules are plain tables in the
  middle of this file, written so a reviewer can read and edit them.
- Mapping from source metadata to the pack's reading schema (corpus/rhetcomp/readings.jsonl).

License-to-tier mapping and URL/title normalization are imported from tools/build_catalog.py,
so harvested records follow the same rules as the rest of the catalog.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from build_catalog import SKINS, license_family, norm_text, norm_url, reuse_tier  # noqa: E402

HARVEST_DIR = ROOT / "research" / "harvest"
RAW_DIR = HARVEST_DIR / "raw"
PACK_READINGS = ROOT / "corpus" / "rhetcomp" / "readings.jsonl"

USER_AGENT = "Mozilla/5.0 (compatible; Aletheia-harvester/0.1; +https://github.com/justalewis/aletheia)"
ROBOTS_AGENT = "Aletheia-harvester"  # the product token robots.txt rules are matched against
DEFAULT_DELAY = 2.0


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Polite HTTP
# ---------------------------------------------------------------------------

class Blocked(Exception):
    """The host refused us: a 401/403, a bot check, or robots.txt. Never retried."""


class Disallowed(Blocked):
    """robots.txt disallows this path (the rest of the host may still be fine)."""


class FetchError(Exception):
    """A request failed after its retries, or returned an unexpected status."""


class BudgetExhausted(Exception):
    """The run reached its --max-requests limit."""


def parent_domain(host: str) -> str:
    """'raider.pressbooks.pub' -> 'pressbooks.pub' (last two labels; good enough here)."""
    labels = host.lower().split(":")[0].split(".")
    return ".".join(labels[-2:])


BOT_MARKERS = re.compile(
    r"just a moment|cf-chl|challenge-platform|attention required|captcha|are you a robot|"
    r"request blocked|access denied|verify you are human|ddos protection|bot detection",
    re.I)


def looks_like_bot_check(resp: requests.Response, expect_json: bool = False) -> bool:
    """True for Cloudflare/CloudFront challenge or block pages, whatever the status code."""
    if resp.headers.get("cf-mitigated"):
        return True
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype:
        return False
    head = resp.text[:6000]
    if resp.status_code in (403, 429, 503) and BOT_MARKERS.search(head):
        return True
    return bool(expect_json and resp.status_code == 200 and BOT_MARKERS.search(head))


class PoliteSession:
    """requests.Session wrapper that behaves like a well-mannered crawler.

    `state` is a plain dict the caller may persist between runs (otl.py does), holding the last
    request time per host, cached robots.txt files, and hosts that blocked us. A restarted run
    then keeps honoring the crawl-delay instead of firing a request the moment it starts.
    """

    ROBOTS_TTL = 24 * 3600

    def __init__(self, delay: float = DEFAULT_DELAY, retries: int = 3, timeout: float = 45,
                 max_requests: int | None = None, state: dict | None = None):
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, */*;q=0.5"})
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.max_requests = max_requests
        self.requests_made = 0
        self.state = state if state is not None else {}
        for k in ("last", "robots", "blocked"):
            self.state.setdefault(k, {})
        self._parsers: dict[str, robotparser.RobotFileParser] = {}

    # -- timing ---------------------------------------------------------------
    def host_delay(self, host: str) -> float:
        rp = self._parsers.get(host)
        crawl = None
        if rp is not None:
            try:
                crawl = rp.crawl_delay(ROBOTS_AGENT)
            except Exception:  # malformed Crawl-delay value
                crawl = None
        return max(self.delay, float(crawl or 0))

    def _wait_turn(self, host: str) -> None:
        wait = self.state["last"].get(host, 0) + self.host_delay(host) - time.time()
        if wait > 0:
            if wait > 10:
                log(f"waiting {wait:.0f}s for {host} (crawl-delay)")
            time.sleep(wait)

    def _send(self, url: str) -> requests.Response:
        if self.max_requests is not None and self.requests_made >= self.max_requests:
            raise BudgetExhausted(f"reached --max-requests {self.max_requests}")
        host = urlsplit(url).netloc.lower()
        self._wait_turn(host)
        try:
            return self.http.get(url, timeout=self.timeout, allow_redirects=True)
        finally:
            self.requests_made += 1
            self.state["last"][host] = time.time()

    def _fetch(self, url: str, expect_json: bool) -> requests.Response | None:
        """GET with retries. None for 404/410. Raises Blocked, FetchError, BudgetExhausted."""
        host = urlsplit(url).netloc.lower()
        last_error = ""
        for attempt in range(self.retries + 1):
            backoff = max(5.0, self.delay) * (2 ** attempt)
            try:
                resp = self._send(url)
            except (requests.ConnectionError, requests.Timeout) as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt < self.retries:
                    log(f"{last_error} on {url}; retrying in {backoff:.0f}s")
                    time.sleep(backoff)
                continue
            if looks_like_bot_check(resp, expect_json) or resp.status_code in (401, 403):
                reason = f"HTTP {resp.status_code}" + (" bot check" if looks_like_bot_check(resp, expect_json) else "")
                server = resp.headers.get("server") or resp.headers.get("via") or ""
                self.state["blocked"][host] = f"{reason} ({server})".strip() if server else reason
                raise Blocked(f"{host} refused the harvester: {self.state['blocked'][host]}")
            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after", "")
                wait = float(retry_after) if retry_after.isdigit() else backoff
                if wait > 900:
                    raise FetchError(f"{url}: 429 with Retry-After {wait:.0f}s; stopping")
                last_error = "HTTP 429"
                if attempt < self.retries:
                    log(f"429 from {host}; waiting {wait:.0f}s")
                    time.sleep(wait)
                continue
            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                if attempt < self.retries:
                    log(f"{last_error} on {url}; retrying in {backoff:.0f}s")
                    time.sleep(backoff)
                continue
            if resp.status_code in (404, 410):
                return None
            if resp.status_code >= 400:
                raise FetchError(f"{url}: HTTP {resp.status_code}")
            return resp
        raise FetchError(f"{url}: {last_error} after {self.retries + 1} tries")

    # -- robots.txt -------------------------------------------------------------
    def robots(self, url: str) -> robotparser.RobotFileParser:
        parts = urlsplit(url)
        host = parts.netloc.lower()
        if host in self._parsers:
            return self._parsers[host]
        cached = self.state["robots"].get(host)
        if not cached or time.time() - cached.get("fetched", 0) > self.ROBOTS_TTL:
            robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
            try:
                resp = self._fetch(robots_url, expect_json=False)
            except FetchError as e:
                # RFC 9309: an unreachable robots.txt means assume everything is disallowed.
                self.state["blocked"][host] = f"robots.txt unreachable ({e})"
                raise Blocked(f"{host}: robots.txt unreachable; skipping host") from e
            if resp is None:
                cached = {"fetched": time.time(), "status": 404, "text": ""}
            else:
                if "html" in resp.headers.get("content-type", "") and BOT_MARKERS.search(resp.text[:6000]):
                    self.state["blocked"][host] = "bot check on robots.txt"
                    raise Blocked(f"{host}: bot check on robots.txt")
                cached = {"fetched": time.time(), "status": resp.status_code, "text": resp.text}
            self.state["robots"][host] = cached
        rp = robotparser.RobotFileParser()
        rp.parse(cached.get("text", "").splitlines() if cached.get("status") == 200 else [])
        self._parsers[host] = rp
        if self.host_delay(host) > self.delay:
            log(f"{host}: robots.txt crawl-delay {self.host_delay(host):.0f}s")
        return rp

    # Once this many hosts under one parent domain have refused us (say two *.pressbooks.pub
    # networks behind the same CDN rule), the rest of that domain is skipped without asking.
    DOMAIN_BLOCK_AFTER = 2

    def is_blocked(self, host: str) -> str:
        host = host.lower()
        if host in self.state["blocked"]:
            return self.state["blocked"][host]
        parent = parent_domain(host)
        siblings = [h for h in self.state["blocked"] if h != parent and parent_domain(h) == parent]
        if len(siblings) >= self.DOMAIN_BLOCK_AFTER:
            return f"{len(siblings)} other {parent} hosts refused the harvester"
        return ""

    def get(self, url: str, expect_json: bool = True) -> requests.Response | None:
        host = urlsplit(url).netloc.lower()
        if reason := self.is_blocked(host):
            raise Blocked(f"{host} skipped: {reason}")
        rp = self.robots(url)
        if not rp.can_fetch(ROBOTS_AGENT, url):
            raise Disallowed(f"robots.txt disallows {url}")
        return self._fetch(url, expect_json)

    def get_json(self, url: str):
        resp = self.get(url, expect_json=True)
        if resp is None:
            return None
        try:
            return resp.json()
        except ValueError as e:
            raise FetchError(f"{url}: response is not JSON ({resp.headers.get('content-type')})") from e


# ---------------------------------------------------------------------------
# Subject filter rules
#
# Every candidate book gets a score. Each rule that matches adds its points (negative rules
# subtract). A book is kept when its score reaches KEEP_SCORE. The reasons travel with the
# record (harvest.filter.reasons) so the review queue shows why each book was kept.
#
# Patterns are case-insensitive regular expressions. Edit freely; test_harvest.py has
# examples that should keep passing.
# ---------------------------------------------------------------------------

KEEP_SCORE = 3
PRESCREEN_SCORE = 2   # looser bar for the first pass, before full metadata is fetched
# Subject headings are chosen by each book's authors and are noisy (lab manuals and Pressbooks
# templates carry "Writing and editing guides"), so codes and heading names together add at
# most this much: a book also needs a title or description signal to be kept.
SUBJECT_CAP = 2

# Subject codes, matched by prefix: "thema:CBW" also matches "thema:CBW1".
SUBJECT_CODE_RULES: dict[str, tuple[int, str]] = {
    # Thema
    "thema:CBW": (3, "Thema CBW writing and editing guides"),
    "thema:CBP": (3, "Thema CBP public speaking guides"),
    "thema:KJP": (3, "Thema KJP business communication and presentation"),
    "thema:CFG": (1, "Thema CFG discourse analysis, stylistics"),
    "thema:GTC": (1, "Thema GTC communication studies"),
    "thema:GLM": (1, "Thema GLM library and information services"),
    # BISAC
    "bisac:LAN005": (3, "BISAC composition"),
    "bisac:LAN015": (3, "BISAC rhetoric"),
    "bisac:LAN026": (3, "BISAC public speaking"),
    "bisac:LAN022": (2, "BISAC editing and proofreading"),
    "bisac:LAN018": (2, "BISAC style manuals"),
    "bisac:LAN006": (1, "BISAC grammar and punctuation"),
    "bisac:LAN004": (1, "BISAC communication studies"),
    "bisac:LAN020": (1, "BISAC language arts study and teaching"),
    "bisac:LAN025": (1, "BISAC library and information science"),
    "bisac:REF026": (3, "BISAC writing skills"),
    "bisac:TEC044": (3, "BISAC technical writing"),
    "bisac:BUS007": (3, "BISAC business communication"),
    "bisac:BUS011": (3, "BISAC business writing"),
}

# Library of Congress classes: (class letters, first number, last number, points, label).
LC_RULES: list[tuple[str, float, float, int, str]] = [
    ("PE", 1101, 1497, 3, "LC PE1101-1497 English grammar, style, composition"),
    ("PN", 4001, 4355, 3, "LC PN4001-4355 oratory, public speaking"),
    ("PN", 145, 249, 2, "LC PN145-249 authorship"),
    ("P", 301, 301.99, 3, "LC P301 rhetoric, style"),
    ("P", 87, 96.99, 1, "LC P87-96 communication"),
    ("T", 10.5, 11.99, 3, "LC T10.5-11 technical writing"),
    ("HF", 5718, 5734.99, 3, "LC HF5718-5734 business communication"),
    ("ZA", 3075, 3075.99, 3, "LC ZA3075 information literacy"),
    ("Z", 710, 711.99, 2, "LC Z710-711 library research"),
    ("LB", 2369, 2369.99, 3, "LC LB2369 research papers and theses"),
    ("Q", 223, 224.99, 3, "LC Q223-224 science communication"),
]

# Subject heading names (Thema/BISAC/OTL headings as words, for sources that give names only).
SUBJECT_NAME_RULES: list[tuple[str, int, str]] = [
    (r"writing and editing guides|writing skills|technical writing|business writing", 3, "subject: writing"),
    (r"\bcomposition (and|&) (rhetoric|creative writing)|\brhetoric\b", 3, "subject: composition/rhetoric"),
    (r"public speaking|speech communication|oratory", 3, "subject: public speaking"),
    (r"business communication", 3, "subject: business communication"),
    (r"information literacy", 3, "subject: information literacy"),
    (r"editing (and|&) proofreading|style manuals", 2, "subject: editing"),
    (r"literacy \(theories of reading and writing\)|\bliteracy\b", 1, "subject: literacy"),
    (r"communication studies|^communications?$", 1, "subject: communication studies"),
    (r"information retrieval|library and information", 1, "subject: library and information"),
]

# Title rules carry the most weight.
TITLE_RULES: list[tuple[str, int, str]] = [
    (r"\bwrit(e|es|ing|ers?|ten)\b", 3, "title: writing"),
    (r"\brhetoric(s|al)?\b", 3, "title: rhetoric"),
    (r"\b(english|college|first[- ]year|freshman|academic|writing)\s+composition\b|"
     r"\bcomposition\s+(and|&)\s+rhetoric\b|\bcomposition\s+(i{1,2}|[12]|10[12])\b|"
     r"\bcomposition\s+(classroom|studies|course|pedagogy|program|theory)\b|\bcomposing\b", 3, "title: composition"),
    (r"\b(technical|professional|business|workplace|scientific|science|engineering|written)\s+"
     r"communications?\b|\bcommunicating\s+(science|research|at work|in the workplace)\b|"
     r"\bcommunication for (business|the workplace|professionals?)\b", 3, "title: tech/prof/business communication"),
    (r"\bpublic speaking\b|\bspeech(es)?\s+(communication|making)\b|\bspeechmaking\b|"
     r"\boral (communication|presentations?)\b|\bpresentation skills\b|\bpublic address\b", 3, "title: public speaking"),
    (r"\binformation literacy\b|\bresearch (skills|process|papers?)\b|\b(library|college|academic) research\b|"
     r"\bevaluating (information|sources)\b|\bcitation\b|\bplagiarism\b", 3, "title: research/information literacy"),
    (r"\bessays?\b", 2, "title: essays"),
    (r"\bargument(s|ation|ative)?\b", 2, "title: argument"),
    (r"\bgrammar\b|\bpunctuation\b|\bstyle (guide|manual)\b|\bplain language\b|\bediting\b", 2, "title: grammar/style/editing"),
    (r"\bliterac(y|ies)\b", 1, "title: literacy"),
]

# Description rules: each distinct match adds 2, up to DESCRIPTION_CAP in total.
DESCRIPTION_CAP = 4
DESCRIPTION_RULES: list[tuple[str, str]] = [
    (r"\b(first[- ]year|college|english|freshman) (writing|composition)\b", "description: first-year/college writing"),
    (r"\bcomposition (course|class|classroom|students|instructors|program)s?\b", "description: composition course"),
    (r"\bwriting (course|class|classroom|instruction|instructors|pedagogy|process|studies|program)s?\b", "description: writing course/process"),
    (r"\b(academic|research|technical|professional|business|scientific|workplace) writing\b", "description: academic/technical/professional writing"),
    (r"\b(grant|proposal|report|essay|thesis) writing\b", "description: genre writing"),
    (r"\brhetoric(al)?\b", "description: rhetoric"),
    (r"\binformation literacy\b", "description: information literacy"),
    (r"\bpublic speaking\b", "description: public speaking"),
    (r"\b(technical|professional|business) communication\b", "description: tech/prof/business communication"),
    (r"\bteaching (of )?writing\b|\bwriting (center|centre)s?\b|\bwriting across the curriculum\b", "description: writing pedagogy"),
]

# Negative title rules: things that look like writing but belong to another field.
# (pattern, points, label, exception): the rule doesn't apply when the title also matches the exception.
NEGATIVE_TITLE_RULES: list[tuple[str, int, str, str]] = [
    (r"\b(poetry|poems?|short stor(y|ies)|novels?|screenwrit\w*|playwrit\w*|songwrit\w*|scriptwrit\w*|"
     r"creative writing)\b|(?<![-\w])fiction\b", -4, "not in scope: creative writing (fiction, poetry, scripts)",
     r"\bnon-?fiction\b|\bessays?\b|\brhetoric"),
    (r"\b(american|british|world|english|canadian|african american) literature\b", -3,
     "not in scope: literature survey or anthology", r"\bcomposition\b|\brhetoric|writing (about|and|with) literature"),
    (r"\b(early childhood|elementary|kindergarten|preschool|primary school|k-12)\b", -3,
     "not in scope: K-12 literacy", ""),
    (r"^\W*archived\b", -3, "archived copy of a book", ""),
    (r"\b(didacticiel|manual de|gu[ií]a|introducci[oó]n|[ée]crire|[ée]criture|r[ée]daction|redacci[oó]n|"
     r"escritura)\b", -3, "not in scope: book in another language", ""),
    (r"\bproofs?\b|\bmathematical\b", -3, "not in scope: mathematical proof writing", ""),
    (r"\b(spanish|french|german|italian|chinese|mandarin|japanese|korean|arabic|portuguese|russian|"
     r"hebrew|hindi|urdu|punjabi|nepali|tagalog|vietnamese|turkish|persian|farsi|greek|latin|swahili|"
     r"ojibwe|cree|español|français)\b", -3, "not in scope: language other than English", r"\brhetoric|\benglish\b"),
    (r"\bpressbooks\b|\btemplate\b|publishing open textbooks|\boer publishing\b|\bself-publishing\b|"
     r"making open textbooks", -3, "not in scope: guides to producing OER", ""),
    (r"\blaboratory (manual|guide)\b|\blab manual\b|\bpracticum\b|\bclinical (\w+ )?(manual|laboratory)\b", -3,
     "not in scope: lab and clinical manuals", ""),
]


def subject_score(title: str, description: str = "", subject_names: list[str] | None = None,
                  subject_codes: list[str] | None = None, language: str = "") -> tuple[int, list[str]]:
    """Score a book against the rules above. Returns (score, reasons)."""
    reasons: list[str] = []
    score = 0
    if language and not re.match(r"^(en|eng)\b", language.strip().lower()):
        return -99, [f"not in English (language {language})"]
    title = title or ""
    desc = description or ""
    subject_points = 0
    for code in subject_codes or []:
        hit = _code_rule(code)
        if hit and hit[1] not in reasons:
            subject_points += hit[0]
            reasons.append(hit[1])
    for name in subject_names or []:
        for pat, pts, label in SUBJECT_NAME_RULES:
            if re.search(pat, name or "", re.I) and label not in reasons:
                subject_points += pts
                reasons.append(label)
    score += min(subject_points, SUBJECT_CAP)
    for pat, pts, label in TITLE_RULES:
        if re.search(pat, title, re.I):
            score += pts
            reasons.append(label)
    desc_points = 0
    for pat, label in DESCRIPTION_RULES:
        if desc_points < DESCRIPTION_CAP and re.search(pat, desc, re.I):
            desc_points += 2
            reasons.append(label)
    score += desc_points
    for pat, pts, label, exception in NEGATIVE_TITLE_RULES:
        if re.search(pat, title, re.I) and not (exception and re.search(exception, title, re.I)):
            score += pts
            reasons.append(label)
    return score, reasons


def _code_rule(code: str) -> tuple[int, str] | None:
    scheme, _, value = code.partition(":")
    if scheme == "lc":
        return lc_rule(value)
    best = None
    for prefix, rule in SUBJECT_CODE_RULES.items():
        if code.upper().startswith(prefix.upper()) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, rule)
    return best[1] if best else None


LC_PATTERN = re.compile(r"^\s*([A-Z]{1,3})\s*(\d+(?:\.\d+)?)")


def lc_rule(call_number: str) -> tuple[int, str] | None:
    m = LC_PATTERN.match((call_number or "").upper())
    if not m:
        return None
    letters, number = m.group(1), float(m.group(2))
    for cls, lo, hi, pts, label in LC_RULES:
        if letters == cls and lo <= number <= hi:
            return pts, label
    return None


def keep_book(score: int, prescreen: bool = False) -> bool:
    return score >= (PRESCREEN_SCORE if prescreen else KEEP_SCORE)


BISAC_CODE = re.compile(r"^([A-Z]{3}\d{6})\b")
THEMA_CODE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")


def classify_subject_code(identifier: str) -> str | None:
    """'CBW' -> 'thema:CBW'; 'REF026000  REFERENCE / Writing Skills' -> 'bisac:REF026000'."""
    ident = (identifier or "").strip()
    if m := BISAC_CODE.match(ident):
        return f"bisac:{m.group(1)}"
    if THEMA_CODE.match(ident):
        return f"thema:{ident}"
    return None


# ---------------------------------------------------------------------------
# Skin and topic tagging (keyword heuristics; every harvested record is needs_review)
# ---------------------------------------------------------------------------

SKIN_RULES: dict[str, str] = {
    "FYC": r"first[- ]year (writing|composition)|college (reading and )?(writing|composition)|english composition|"
           r"composition (i{1,2}|[12]|10[12])\b|freshman (writing|composition)|\bengl?\s?(10[12]|1301|1302)\b|"
           r"composition (and|&) rhetoric|rhetoric (and|&) composition|introduction to (college )?writing|"
           r"writing (for|in) (success|college)|\bcollege writers\b|^\W*composition\b|\breading and writing\b|"
           r"\bstudent writing\b|writing about (literature|texts)|successful writing",
    "RESEARCH": r"information literacy|research (writing|papers?|process|skills|questions?)|library research|"
                r"evaluat\w+ (sources|information)|citation|plagiarism|annotated bibliograph|literature reviews?|"
                r"(academic|practical|college) research|research and writing|\bsources\b",
    "ACADEMIC": r"academic (\w+ )?(writing|english|essays?|reading)|writing across the curriculum|writing in the disciplines|"
                r"\bwac\b|\be?gap\b|english for academic purposes|graduate (writing|students|researchers)|thesis|dissertation|"
                r"masters? level|scholarly writing|writing (in|for) (psychology|nursing|biology|history|the sciences|"
                r"engineering|social sciences|the humanities|business|law)|students of history",
    "WAW": r"writing about writing|writing studies|threshold concepts|discourse communit|literacy narratives?",
    "TECHCOMM": r"technical (writing|communication)|documentation|\binstructions\b|usability|user experience|"
                r"\bux\b|technical editing|engineering (writing|communication)|plain language|style guide|"
                r"\bhow-to\b|\btutorials?\b",
    "PROFWRIT": r"professional (\w+ )?(writing|communications?)|workplace (writing|communication)|grant writing|"
                r"\bgrants?\b|proposal writing|report writing|r[eé]sum[eé]s?|cover letters?|nonprofit|"
                r"public relations|strategic communication|legal writing",
    "BUSINESS": r"business (writing|communication|correspondence)|managerial communication|organizational communication",
    "SCIWRIT": r"scien\w+ (writing|communication)|communicating science|lab reports?|writing in the sciences|"
               r"science journalism|health communication",
    "RHETORIC": r"\brhetoric|\bargument(s|ation)?\b",
    "CULTURAL_RHET": r"cultural rhetorics?|indigenous|decolonial|african american rhetoric|black rhetoric|"
                     r"latinx|chicanx|feminist rhetoric|queer rhetoric|asian american",
    "MULTIMODAL": r"multimodal|digital (writing|composition|rhetoric|media)|visual rhetoric|podcast|"
                  r"writing for the web|web writing|social media",
    "CREATIVE_NF": r"creative non-?fiction|personal essays?|memoir|literary journalism",
    "L2": r"multilingual|second language|\besl\b|english language learners?|translingual|\bl2\b|world englishes",
    "SPEECH": r"public speaking|speech(es)? (communication|making)|speechmaking|oral (communication|presentations?)|"
              r"presentation skills|public address",
    "PEDAGOGY": r"teaching (of )?writing|writing pedagogy|for (writing )?instructors|writing (center|centre)s?|"
                r"\btutor(s|ing)\b|writing programs?\b|teachers of writing|course design|\bteach(ing)? [\w ]{0,30}writ|"
                r"writing instruction|essay graders|new faculty|writing assessment",
}

# Topic vocabulary taken from the existing pack (corpus/rhetcomp/readings.jsonl).
TOPIC_RULES: dict[str, str] = {
    "argument": r"\bargument(s|ation|ative)?\b|\bclaims?\b",
    "writing process": r"writing process|\bprewriting\b|\bdrafting\b",
    "information literacy": r"information literacy",
    "style": r"\bstyle\b|\bconcision\b|\bclarity\b",
    "audience": r"\baudiences?\b",
    "genre": r"\bgenres?\b",
    "citation": r"\bcitations?\b|\bciting\b|\bdocumentation styles?\b|\breferences?\b",
    "reading strategies": r"\breading (strategies|critically|actively)\b|\bcritical reading\b|\bannotat",
    "rhetorical situation": r"rhetorical situation|\bkairos\b|\bexigenc",
    "ai and writing": r"\b(generative )?ai\b|artificial intelligence|chatgpt|large language model",
    "grammar": r"\bgrammar\b|\bsentences?\b|\bverbs?\b|\bnouns?\b|\bpronouns?\b",
    "punctuation": r"\bpunctuation\b|\bcommas?\b|\bsemicolons?\b|\bapostrophes?\b",
    "ethics": r"\bethic(s|al)\b",
    "accessibility": r"\baccessib(le|ility)\b",
    "multimodal": r"\bmultimodal\b",
    "collaboration": r"\bcollaborat\w*|\bteamwork\b|\bteams?\b",
    "revision": r"\brevis(e|ing|ion)\b",
    "editing": r"\bedit(ing)?\b|\bproofread",
    "document design": r"document design|\blayout\b|\btypography\b|\bformatting\b",
    "visual rhetoric": r"visual rhetoric|\bvisuals?\b|\bimages?\b",
    "research question": r"research questions?",
    "source evaluation": r"evaluat\w+ (sources|information)|\bcredib\w+|\bcraap\b",
    "reflection": r"\breflect(ion|ive|ing)?\b",
    "public writing": r"public writing|\bop-?eds?\b",
    "rhetorical analysis": r"rhetorical analysis|analy[sz]\w+ (an? )?arguments?",
    "evidence": r"\bevidence\b|\bsupport(ing)? (your )?claims?\b",
    "invention": r"\binvention\b|\bbrainstorm",
    "paragraphs": r"\bparagraphs?\b|\btopic sentences?\b",
    "narrative": r"\bnarratives?\b|\bstorytelling\b",
    "plagiarism": r"\bplagiari",
    "synthesis": r"\bsynthes(is|izing|ize)\b",
    "rhetorical appeals": r"\bethos\b|\bpathos\b|\blogos\b|rhetorical appeals",
    "thesis": r"\bthesis( statements?)?\b",
    "peer review": r"peer review|peer response|workshop",
    "summary": r"\bsummar(y|ies|izing|ize)\b",
    "quoting and paraphrasing": r"\bquot(e|es|ing|ations?)\b|\bparaphras",
    "introductions and conclusions": r"\bintroductions?\b.*\bconclusions?\b|\bconclusions?\b",
    "primary research": r"primary research|\binterviews?\b|\bsurveys?\b|\bobservations?\b",
    "literature review": r"literature reviews?",
    "reports": r"\breports?\b",
    "proposals": r"\bproposals?\b",
    "grant writing": r"\bgrants?\b",
    "memos": r"\bmemos?\b|\bmemoranda\b",
    "email": r"\be-?mails?\b",
    "letters": r"\bletters?\b",
    "resumes": r"r[eé]sum[eé]s?",
    "cover letters": r"cover letters?",
    "job search": r"job search|\bemployment\b|\binterviewing\b",
    "instructions": r"\binstructions\b|\bprocedures?\b",
    "usability": r"\busability\b|user testing",
    "technical editing": r"technical editing",
    "plain language": r"plain language",
    "data visualization": r"data visuali[sz]ation|\bcharts?\b|\bgraphs?\b|\bfigures?\b|\btables?\b",
    "presentations": r"\bpresentations?\b|\bslides?\b",
    "speeches": r"\bspeech(es)?\b|\bdelivery\b",
    "public speaking": r"public speaking|speaking anxiety|\bstage fright\b",
    "mla": r"\bmla\b",
    "apa": r"\bapa\b",
    "scientific writing": r"scientific writing|\bimrad\b|lab reports?",
    "science communication": r"science communication|communicating science",
    "social media": r"social media",
    "digital writing": r"digital writing|writing for the web|\bweb writing\b|\bblogs?\b",
    "academic writing": r"academic writing",
    "writing in the disciplines": r"writing in the disciplines|writing across the curriculum",
    "multilingual writers": r"multilingual|second language|\besl\b",
    "language diversity": r"language diversity|\bdialects?\b|translingual|code-?meshing",
    "transfer": r"\btransfer\b",
    "genre analysis": r"genre analysis",
    "discourse community": r"discourse communit",
    "literacy narrative": r"literacy narratives?",
}

MAX_TOPICS = 6


def tag_skins(title: str, description: str = "", subjects=()) -> list[str]:
    """Skins whose pattern matches the title or a subject heading, or the description twice.

    One passing mention in a long description ("...and a chapter on social media") is not
    enough to tag a whole book; if nothing qualifies, single description matches are used.
    """
    strong = " ".join([title or "", *[s or "" for s in subjects]])
    desc = description or ""
    hits = [s for s in SKINS if s in SKIN_RULES and
            (re.search(SKIN_RULES[s], strong, re.I) or len(re.findall(SKIN_RULES[s], desc, re.I)) >= 2)]
    return hits or [s for s in SKINS if s in SKIN_RULES and re.search(SKIN_RULES[s], desc, re.I)]


def tag_topics(title: str, description: str = "", limit: int = MAX_TOPICS) -> list[str]:
    """Topics ranked by matches (a title match counts three times), at most `limit`."""
    scored = []
    for topic, pat in TOPIC_RULES.items():
        n = 3 * len(re.findall(pat, title or "", re.I)) + len(re.findall(pat, description or "", re.I))
        if n:
            scored.append((-n, topic))
    return sorted(t for _, t in sorted(scored)[:limit])


TOP_FOCUSES = ["FYC", "TECHCOMM", "PROFWRIT", "BUSINESS", "ACADEMIC", "SCIWRIT", "RHETORIC"]


def suggested_focus(skins: list[str]) -> str:
    for s in TOP_FOCUSES:
        if s in skins:
            return s
    return skins[0] if skins else "UNSORTED"


def guess_course_level(text: str, skins: list[str]) -> str:
    t = text or ""
    if re.search(r"\bgraduate (students|writing|school|level)\b|\bdissertation", t, re.I) and \
            not re.search(r"\bundergraduate", t, re.I):
        return "grad"
    if re.search(r"\badvanced\b|upper[- ](division|level)", t, re.I):
        return "300-400"
    if "FYC" in skins or re.search(r"first[- ]year|freshman|introduct\w+ to (college )?writing|developmental|basic writing", t, re.I):
        return "100"
    if re.search(r"\bsophomore|second[- ]year\b", t, re.I):
        return "200"
    return "mixed"


def guess_audience(text: str, skins: list[str]) -> str:
    if "PEDAGOGY" in skins:
        return "both" if re.search(r"\bstudents? (will|can|should)\b|for students", text or "", re.I) else "instructor"
    return "student"


# ---------------------------------------------------------------------------
# Mapping to the pack's reading schema
# ---------------------------------------------------------------------------

PACK_FIELDS = [
    "audience", "authors", "course_level", "epub_url", "html_url", "id", "landing_url", "level", "license",
    "license_family", "license_url", "license_verified", "notes", "pages", "parent_id", "parent_title",
    "pdf_url", "provenance", "publisher_platform", "reuse_tier", "skins", "summary", "title", "topics", "year",
]

TODAY = date.today().isoformat()


def clean_html(s) -> str:
    """Plain text from HTML or double-escaped HTML (OPDS descriptions arrive as '&lt;p&gt;')."""
    if not s:
        return ""
    s = str(s)
    for _ in range(2):
        if "&lt;" in s or "&amp;" in s or "&#" in s:
            s = html.unescape(s)
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"<[a-z/][^>]*$", " ", s, flags=re.I)  # a tag cut off by a truncated description
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def short_summary(text: str, limit: int = 420) -> str:
    text = clean_html(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[:end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "..."


def year_from(*values) -> int | str:
    for v in values:
        m = re.search(r"\b(19[5-9]\d|20[0-4]\d)\b", str(v or ""))
        if m:
            return int(m.group(1))
    return ""


ORG_WORDS = re.compile(
    r"\b(university|college|press|library|libraries|department|community|open|project|inc|foundation|team|"
    r"group|centre|center|institute|network|bccampus|oer|association|council|committee|faculty|school|"
    r"program|students|contributors|authors|editors|campus|office|services|board|society|consortium)\b", re.I)


HONORIFIC = re.compile(r"^(dr|prof|professor|mr|mrs|ms|mx)\.?\s+", re.I)


def person_name(first: str = "", last: str = "", full: str = "") -> str:
    """'Last, First' when the parts are known or the name looks personal; organizations as given."""
    first, last, full = (first or "").strip(), (last or "").strip(), clean_html(full).strip()
    full = HONORIFIC.sub("", full).strip()
    first = HONORIFIC.sub("", first).strip()
    if last:
        return f"{last}, {first}" if first else last
    if not full or "," in full or ORG_WORDS.search(full):
        return full
    words = full.split()
    if 2 <= len(words) <= 4 and all(w[:1].isupper() for w in words if w.lower() not in ("de", "van", "von", "la", "del")):
        suffix = words.pop() if words[-1].rstrip(".").lower() in ("jr", "sr", "ii", "iii", "phd") else ""
        last_name = words.pop()
        name = f"{last_name}, {' '.join(words)}"
        return f"{name}, {suffix}" if suffix else name
    return full


def join_people(names) -> str:
    seen, out = set(), []
    for n in names:
        n = (n or "").strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return "; ".join(out)


CC_URL = re.compile(r"creativecommons\.org/(licenses|publicdomain)/([a-z\-]+)/(\d\.\d)(?:/([a-z]{2}))?", re.I)
CC_NAME = re.compile(r"\bCC[\s-]*(BY(?:[\s-]*(?:NC|ND|SA))*)\b", re.I)
CC_WORDS = [  # (words in a license name, CC element) for spelled-out license names
    ("noncommercial", "NC"), ("non-commercial", "NC"), ("noderiv", "ND"), ("no deriv", "ND"),
    ("sharealike", "SA"), ("share alike", "SA"), ("share-alike", "SA"),
]


def license_string(url: str = "", name: str = "", code: str = "") -> tuple[str, str]:
    """Normalize a source's license to the pack's style: ('CC BY-NC-SA 4.0', deed URL)."""
    url, name, code = (url or "").strip(), clean_html(name), (code or "").strip()
    if m := CC_URL.search(url):
        kind, elems, version, juris = m.group(1).lower(), m.group(2).lower(), m.group(3), (m.group(4) or "").lower()
        if kind == "publicdomain":
            if elems == "zero":
                return "CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/"
            return "Public Domain Mark 1.0", "https://creativecommons.org/publicdomain/mark/1.0/"
        lic = "CC " + elems.upper() + f" {version}" + (f" {juris.upper()}" if juris else "")
        deed = f"https://creativecommons.org/licenses/{elems}/{version}/" + (f"{juris}/" if juris else "")
        return lic, deed
    text = " ".join(x for x in (code, name) if x)
    low = text.lower()
    if not low:
        return "", ""
    if "all rights reserved" in low or low in ("all-rights-reserved", "arr"):
        return "All rights reserved", ""
    if "cc0" in low or "no rights reserved" in low or "cc zero" in low:
        return "CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/"
    if "public domain" in low or low == "public-domain":
        return "Public Domain", ""
    version = (re.search(r"\b(\d\.\d)\b", text) or [None, ""])[1]
    if m := CC_NAME.search(text.replace("_", " ")):
        elems = re.sub(r"[\s-]+", "-", m.group(1).upper())
    elif low.startswith("cc-") or "attribution" in low or "creative commons" in low:
        parts = ["BY"] + [e for w, e in CC_WORDS if w in low or f"-{e.lower()}" in low]
        elems = "-".join(dict.fromkeys(parts))
    else:
        return text, ""
    order = [e for e in ("BY", "NC", "ND", "SA") if e in elems.split("-")]
    elems = "-".join(order)
    lic = f"CC {elems}" + (f" {version}" if version else "")
    deed = f"https://creativecommons.org/licenses/{elems.lower()}/{version}/" if version else ""
    return lic, deed


def make_reading(*, level: str, title: str, source: str, key: str, license: str = "", license_url: str = "",
                 license_verified: bool = False, parent_key: str = "", **fields) -> dict:
    """A record in pack shape (PACK_FIELDS) plus the harvest-only fields.

    `needs_review` is always true: tags and levels are keyword guesses. `harvest` holds what the
    review step needs (source, keys, filter reasons) and is dropped when a record is approved.
    """
    fam = license_family(license)
    rec = {k: "" for k in PACK_FIELDS}
    rec.update({
        "level": level,
        "title": clean_html(title),
        "license": license,
        "license_family": fam,
        "license_url": license_url,
        "license_verified": bool(license_verified and license),
        "reuse_tier": reuse_tier(fam),
        "provenance": f"harvest-{source}",
        "skins": [],
        "topics": [],
    })
    harvest = fields.pop("harvest", {})
    for k, v in fields.items():
        if k not in PACK_FIELDS:
            raise KeyError(f"unknown pack field {k!r}")
        rec[k] = v
    rec["needs_review"] = True
    rec["harvest"] = {"source": source, "key": key, "parent_key": parent_key, "fetched": TODAY, **harvest}
    return rec


def candidate_key(source: str, url: str) -> str:
    return f"{source}:{norm_url(url)}"


# ---------------------------------------------------------------------------
# Small file helpers
# ---------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records),
                   encoding="utf-8", newline="\n")
    tmp.replace(path)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def slug(text: str, maxlen: int = 60) -> str:
    s = re.sub(r"\s+", "-", norm_text(text)).strip("-")
    if len(s) > maxlen:
        s = s[:maxlen].rsplit("-", 1)[0] if "-" in s[:maxlen] else s[:maxlen]
    return s or "item"
