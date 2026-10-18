"""Fetch reading content from publishers, politely.

The client names itself in its User-Agent, follows robots.txt (including Crawl-delay), waits
between requests to the same host, and gives up at the first sign of a bot check: a Cloudflare
challenge, a CAPTCHA page, or a WAF block. It never retries a gated request another way, and
once a host gates one request the rest of the run skips that host. WAC Clearinghouse /docs/
files sit behind a bot check, so they aren't requested at all; supply those PDFs in a folder.

HTML fetchers return the publisher's own body markup, through an API where robots.txt allows
one and otherwise from the page's own content element, never a scraped guess:
  - Pressbooks: /wp-json/pressbooks/v2/{chapters,front-matter,back-matter}?slug=..., or the
    page's section[data-type] where robots.txt closes /wp-json/ (most pressbooks.pub hosts do).
    A part is its introduction plus every chapter the book's contents list under it.
  - WordPress: the page's rel="alternate" application/json link (/wp-json/wp/v2/...)
  - LibreTexts: its API is closed to robots, so the page's own content section, with a chapter's
    sections fetched in order when the page is a chapter listing.
  - Manifold: /api/v1/texts/<text> for the contents, then each section's body, a chapter's
    subsections in contents order.
  - OpenStax: robots.txt closes /apps/archive, so the book page's [data-type=page] element; a
    chapter's introduction page brings the whole chapter, in the order of the book's own tree.
"""
from __future__ import annotations

import io
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Doctype

USER_AGENT = "Mozilla/5.0 (compatible; Aletheia/0.1; open-access course reader builder)"
ROBOTS_TOKEN = "aletheia"  # the product token a robots.txt group would name this crawler by
HOST_DELAY = 2.0
TIMEOUT = 60
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_IMAGE_BYTES = 15 * 1024 * 1024
CHALLENGE_MARKERS = ("just a moment", "challenge-platform", "cf-chl", "captcha", "are you a robot",
                     "verify you are human", "attention required", "ddos protection")
BLOCK_MARKERS = ("request blocked", "access denied", "the request could not be satisfied", "forbidden")


class FetchError(Exception):
    """A fetch that didn't produce content. `status` is a short code for the warm report."""

    def __init__(self, status: str, detail: str = ""):
        super().__init__(f"{status}: {detail}" if detail else status)
        self.status = status
        self.detail = detail


def bot_gated_by_rule(url: str) -> bool:
    parts = urlsplit(url)
    return parts.netloc.lower().endswith("wacclearinghouse.org") and parts.path.startswith("/docs/")


def gate_status(resp: requests.Response, head: bytes = b"") -> str | None:
    """'bot-gated' for a challenge page, 'blocked' for a WAF refusal, else None."""
    if resp.headers.get("cf-mitigated", "").lower() == "challenge":
        return "bot-gated"
    if resp.status_code in (401, 403, 429, 503):
        text = head.decode("utf-8", "replace").lower()
        if any(m in text for m in CHALLENGE_MARKERS):
            return "bot-gated"
        if resp.status_code == 403 or any(m in text for m in BLOCK_MARKERS):
            return "blocked"
    return None


class Robots:
    """One site's robots.txt, read as RFC 9309 says.

    The rules come from the groups naming this crawler's product token, or else the * groups.
    The longest matching rule wins and Allow wins a tie; * matches any run of characters and a
    final $ anchors the end. urllib.robotparser does none of the wildcard matching, so a rule
    like `Disallow: /*/wp-json/` never applied under it.
    """

    def __init__(self, text: str = "", token: str = ROBOTS_TOKEN, allow_all: bool = False,
                 disallow_all: bool = False):
        self.allow_all, self.disallow_all = allow_all, disallow_all
        self.rules: list[tuple[bool, str, re.Pattern]] = []
        self.delay: float | None = None
        groups: list[tuple[set, list]] = []
        for raw in text.splitlines():
            key, _, value = raw.split("#", 1)[0].partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if not groups or groups[-1][1]:  # a rule line closed the previous group
                    groups.append((set(), []))
                groups[-1][0].add(value.split("/")[0].strip().lower())
            elif groups and key in ("allow", "disallow", "crawl-delay"):
                groups[-1][1].append((key, value))
        mine = [g for g in groups if token in g[0]] or [g for g in groups if "*" in g[0]]
        for _, lines in mine:
            for key, value in lines:
                if key == "crawl-delay":
                    try:
                        self.delay = float(value)
                    except ValueError:
                        pass
                elif value:
                    self.rules.append((key == "allow", value, _robots_pattern(value)))

    def allowed(self, url: str) -> bool:
        if self.disallow_all or self.allow_all:
            return not self.disallow_all
        parts = urlsplit(url)
        target = unquote(parts.path or "/") + (f"?{unquote(parts.query)}" if parts.query else "")
        best = None
        for allow, rule, pattern in self.rules:
            if pattern.match(target) and (best is None or len(rule) > len(best[1])
                                          or (len(rule) == len(best[1]) and allow)):
                best = (allow, rule)
        return best is None or best[0]


def _robots_pattern(rule: str) -> re.Pattern:
    rule = unquote(rule)
    anchored = rule.endswith("$")
    body = re.escape(rule[:-1] if anchored else rule).replace(r"\*", ".*")
    return re.compile(body + (r"\Z" if anchored else ""))


@dataclass
class Client:
    """requests.Session with manners. Pass `session` to stub the network in tests."""

    session: requests.Session = field(default_factory=requests.Session)
    delay: float = HOST_DELAY
    user_agent: str = USER_AGENT
    sleep: callable = time.sleep
    gated_hosts: dict = field(default_factory=dict)
    _robots: dict = field(default_factory=dict)
    _last: dict = field(default_factory=dict)

    def _robots_for(self, url: str) -> Robots:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            try:
                self._wait(parts.netloc, 0)
                resp = self.session.get(origin + "/robots.txt", headers={"User-Agent": self.user_agent},
                                        timeout=TIMEOUT, allow_redirects=True)
                if resp.status_code in (401, 403):
                    rules = Robots(disallow_all=True)
                elif resp.status_code >= 400:
                    rules = Robots(allow_all=True)
                else:
                    rules = Robots(resp.text)
            except requests.RequestException:
                rules = Robots(allow_all=True)  # unreachable robots.txt: the page fetch will fail on its own
            self._robots[origin] = rules
        return self._robots[origin]

    def can_fetch(self, url: str) -> bool:
        """What robots.txt says about `url` (fetching robots.txt once per site)."""
        return self._robots_for(url).allowed(url)

    def _wait(self, host: str, delay: float) -> None:
        wait = delay - (time.monotonic() - self._last.get(host, -1e9))
        if wait > 0:
            self.sleep(wait)
        self._last[host] = time.monotonic()

    def get(self, url: str, accept: str = "*/*", stream: bool = False) -> requests.Response:
        host = urlsplit(url).netloc.lower()
        if bot_gated_by_rule(url):
            raise FetchError("bot-gated", "WAC Clearinghouse /docs/ files sit behind a bot check; not requested")
        if host in self.gated_hosts:
            raise FetchError(self.gated_hosts[host], f"{host} refused an earlier request in this run")
        rules = self._robots_for(url)
        if not rules.allowed(url):
            raise FetchError("robots", f"robots.txt disallows {urlsplit(url).path}")
        crawl_delay = rules.delay or 0
        self._wait(host, max(self.delay, float(crawl_delay)))
        try:
            resp = self.session.get(url, headers={"User-Agent": self.user_agent, "Accept": accept},
                                    timeout=TIMEOUT, allow_redirects=True, stream=stream)
        except requests.RequestException as e:
            raise FetchError("error", f"{type(e).__name__}: {e}") from e
        if resp.status_code != 200:
            head = next(resp.iter_content(8192), b"") if stream else resp.content[:8192]
            resp.close()
            gate = gate_status(resp, head)
            if gate:
                self.gated_hosts[host] = gate
                raise FetchError(gate, f"HTTP {resp.status_code} from {host}; not retried")
            raise FetchError("not-found" if resp.status_code in (404, 410) else f"http-{resp.status_code}", url)
        if resp.headers.get("cf-mitigated", "").lower() == "challenge":
            self.gated_hosts[host] = "bot-gated"
            raise FetchError("bot-gated", f"challenge page from {host}")
        return resp

    def get_json(self, url: str):
        resp = self.get(url, accept="application/json")
        try:
            return resp.json()
        except ValueError as e:
            head = resp.content[:4000].lower()
            if any(m.encode() in head for m in CHALLENGE_MARKERS):
                self.gated_hosts[urlsplit(url).netloc.lower()] = "bot-gated"
                raise FetchError("bot-gated", "challenge page instead of JSON") from e
            raise FetchError("not-json", url) from e

    def get_bytes(self, url: str, limit: int, accept: str = "*/*") -> tuple[bytes, str]:
        resp = self.get(url, accept=accept, stream=True)
        buf = io.BytesIO()
        for chunk in resp.iter_content(1 << 16):
            buf.write(chunk)
            if buf.tell() > limit:
                resp.close()
                raise FetchError("too-large", f"over {limit // 2**20} MB")
        return buf.getvalue(), resp.headers.get("content-type", "")


@dataclass
class Fetched:
    """Raw publisher HTML for a reading, before sanitizing."""

    html: str
    source_url: str  # what was requested (the API URL, or the page)
    base_url: str    # what relative links resolve against (the human-facing page)
    fetcher: str
    title: str = ""


# ---------------------------------------------------------------- Pressbooks

PRESSBOOKS_PATH = re.compile(r"^/(?P<book>[^/]+)/(?P<kind>chapter|front-matter|back-matter|part)/(?P<slug>[^/]+)/?$")
PRESSBOOKS_ENDPOINT = {"chapter": "chapters", "front-matter": "front-matter", "back-matter": "back-matter",
                       "part": "parts"}


def pressbooks_api_url(page_url: str) -> str | None:
    parts = urlsplit(page_url)
    m = PRESSBOOKS_PATH.match(parts.path)
    if not m or parts.scheme not in ("http", "https"):
        return None
    endpoint = PRESSBOOKS_ENDPOINT[m["kind"]]
    return (f"{parts.scheme}://{parts.netloc}/{m['book']}/wp-json/pressbooks/v2/{endpoint}"
            f"?slug={m['slug']}&_fields=id,slug,link,title,content")


def fetch_pressbooks(client: Client, page_url: str, max_sections: int = 40) -> Fetched:
    api = pressbooks_api_url(page_url)
    if not api:
        raise FetchError("unsupported", "not a Pressbooks chapter URL")
    if PRESSBOOKS_PATH.match(urlsplit(page_url).path)["kind"] == "part":
        return _pressbooks_part(client, page_url, max_sections)
    html, title, source, link = _pressbooks_item(client, page_url)
    return Fetched(html, source, link, "pressbooks", title)


def _pressbooks_item(client: Client, page_url: str) -> tuple[str, str, str, str]:
    """(body, title, source URL, page URL) of one chapter, front- or back-matter page: from the
    REST API when robots.txt allows it, else from the page's own content section."""
    api = pressbooks_api_url(page_url)
    if not client.can_fetch(api):
        resp = client.get(page_url, accept="text/html")
        section = _soup(resp).select_one("section[data-type]")
        if section is None:
            raise FetchError("unsupported", "no Pressbooks content section on the page")
        header = section.find("header", recursive=False)
        title = header.get_text(" ", strip=True) if header else ""
        if header:
            header.decompose()
        return _rebase(section, resp.url), title, page_url, resp.url
    data = client.get_json(api)
    if not isinstance(data, list) or not data:
        raise FetchError("not-found", "Pressbooks returned no matching chapter")
    slug = unquote(PRESSBOOKS_PATH.match(urlsplit(page_url).path)["slug"])
    item = next((d for d in data if unquote(str(d.get("slug", ""))) == slug), data[0] if len(data) == 1 else None)
    if not item:
        raise FetchError("not-found", f"no chapter with slug {slug}")
    content = item.get("content") or {}
    if content.get("protected"):
        raise FetchError("unavailable", "password-protected chapter")
    return (content.get("rendered", ""), _plain((item.get("title") or {}).get("rendered", "")), api,
            item.get("link") or page_url)


def _pressbooks_part(client: Client, page_url: str, max_sections: int) -> Fetched:
    """A part's introduction, then each chapter the book's contents list under it, in order.

    The part page carries the whole book's contents (li.toc__part > li.toc__chapter), so the
    chapter list costs no extra request. Without it the part stays unfetched: its introduction
    alone is a page or two of a much longer reading.
    """
    resp = client.get(page_url, accept="text/html")
    soup = _soup(resp)
    section = soup.select_one("section[data-type]")
    if section is None:
        raise FetchError("unsupported", "no Pressbooks content section on the page")
    header = section.find("header", recursive=False)
    title = header.get_text(" ", strip=True) if header else ""
    if header:
        header.decompose()
    here = {_page_key(page_url), _page_key(resp.url)}
    part = next((li for li in soup.select("li.toc__part")
                 if (a := li.find("a", href=True)) and _page_key(urljoin(resp.url, a["href"])) in here), None)
    if part is None:
        raise FetchError("unsupported", "the part page has no contents listing its chapters")
    chapters = []
    for a in part.select("li.toc__chapter a[href]"):
        url = urldefrag(urljoin(resp.url, a["href"]))[0]
        m = PRESSBOOKS_PATH.match(urlsplit(url).path)
        if m and m["kind"] == "chapter" and url not in chapters:
            chapters.append(url)
    if len(chapters) > max_sections:
        raise FetchError("too-large", f"{len(chapters)} chapters in this part")
    parts = [_rebase(section, resp.url)]
    for url in chapters:
        body, heading, _, link = _pressbooks_item(client, url)
        soup = BeautifulSoup(body, "html.parser")
        first = soup.find(True)
        if first is not None and first.name in ("h1", "h2", "h3") and _same_text(first.get_text(""), heading):
            heading = ""  # the chapter already opens with its title
        heading = f"<h2>{_escape(heading)}</h2>" if heading else ""
        parts.append(f"<div>{heading}{_rebase(soup, link)}</div>")
    return Fetched("\n".join(parts), page_url, resp.url, "pressbooks", title)


def _same_text(a: str, b: str) -> bool:
    return re.sub(r"\W", "", a).lower() == re.sub(r"\W", "", b).lower()


def _page_key(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc.lower()}{unquote(parts.path).rstrip('/')}"


# ---------------------------------------------------------------- WordPress

def _wp_json_link(resp: requests.Response) -> str:
    for link in requests.utils.parse_header_links(resp.headers.get("link", "")):
        if link.get("rel") == "alternate" and link.get("type") == "application/json":
            return link["url"]
    soup = BeautifulSoup(resp.text, "html.parser")
    tag = soup.find("link", rel="alternate", type="application/json")
    return urljoin(resp.url, tag["href"]) if tag and tag.get("href") else ""


def fetch_wordpress(client: Client, page_url: str) -> Fetched:
    resp = client.get(page_url, accept="text/html")
    if "html" not in resp.headers.get("content-type", "html"):
        raise FetchError("unsupported", "the page isn't HTML")
    api = _wp_json_link(resp)
    if "/wp-json/" not in api and "rest_route=" not in api:
        raise FetchError("unsupported", "no content API on this site")
    data = client.get_json(api)
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, dict) or content.get("protected"):
        raise FetchError("unsupported", "the API gave no readable content")
    return Fetched(content.get("rendered", ""), api, data.get("link") or page_url, "wordpress",
                   _plain((data.get("title") or {}).get("rendered", "")))


# ---------------------------------------------------------------- LibreTexts

def is_libretexts(url: str) -> bool:
    return urlsplit(url).netloc.lower().endswith(".libretexts.org")


def fetch_libretexts(client: Client, page_url: str, max_sections: int = 40) -> Fetched:
    resp = client.get(page_url, accept="text/html")
    soup = BeautifulSoup(resp.text, "html.parser")
    body = soup.select_one("section.mt-content-container")
    if body is None:
        raise FetchError("unsupported", "no LibreTexts content section on the page")
    listing = body.select_one(".mt-guide-content, .mt-sortable-listings-container")
    section_urls = []
    if listing is not None:
        prefix = resp.url.rstrip("/") + "/"
        for a in listing.select("a[href]"):
            href = urljoin(resp.url, a["href"])
            if href.startswith(prefix) and href not in section_urls:
                section_urls.append(href)
        listing.decompose()
    for el in body.select(".noindex"):
        el.decompose()
    if len(section_urls) > max_sections:
        raise FetchError("too-large", f"{len(section_urls)} sections; this is a book, not a chapter")
    parts = [body.decode_contents()]
    for url in section_urls:
        page = BeautifulSoup(client.get(url, accept="text/html").text, "html.parser")
        sec = page.select_one("section.mt-content-container")
        if sec is None:
            continue
        for el in sec.select(".mt-guide-content, .mt-sortable-listings-container, .noindex"):
            el.decompose()
        title = page.select_one("#title") or page.find("h1")
        heading = f"<h2>{_escape(title.get_text(' ', strip=True))}</h2>" if title else ""
        parts.append(f"<div>{heading}{_rebase(sec, url)}</div>")
    return Fetched("\n".join(parts), page_url, page_url, "libretexts")


# ---------------------------------------------------------------- Manifold

MANIFOLD_PATH = re.compile(r"^/read/(?P<text>[^/]+)(?:/section/(?P<section>[^/]+))?/?$")


def is_manifold(url: str) -> bool:
    return bool(MANIFOLD_PATH.match(urlsplit(url).path))


def fetch_manifold(client: Client, page_url: str, max_sections: int = 40) -> Fetched:
    """A Manifold section and its subsections (or a whole text when the URL names no section).

    Manifold's API is public JSON:API. A chapter is often a short part page whose subsections
    are separate text sections, listed under it in the text's contents.
    """
    parts = urlsplit(page_url)
    m = MANIFOLD_PATH.match(parts.path)
    origin = f"{parts.scheme}://{parts.netloc}"
    api = f"{origin}/api/v1/texts/{m['text']}"
    data = client.get_json(api)
    text = data.get("data") if isinstance(data, dict) else None
    if not isinstance(text, dict) or text.get("type") != "texts":
        raise FetchError("unsupported", "no Manifold text behind this URL")
    attrs = text.get("attributes") or {}
    if m["section"]:
        entry = _toc_entry(attrs.get("toc") or [], m["section"])
        ids = [m["section"], *_toc_ids(entry.get("children") if entry else [])]
    else:
        ids = list(attrs.get("spine") or [])
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise FetchError("not-found", "the text lists no sections")
    if len(ids) > max_sections:
        raise FetchError("too-large", f"{len(ids)} sections; this is a book, not a chapter")
    bodies, title = [], ""
    for sid in ids:
        section = (client.get_json(f"{api}/relationships/text_sections/{sid}").get("data") or {})
        sattrs = section.get("attributes") or {}
        title = title or sattrs.get("name", "")
        body = BeautifulSoup(sattrs.get("body") or "", "html.parser")
        for node in body.find_all(string=lambda s: isinstance(s, Doctype)):
            node.extract()
        bodies.append(f"<div>{_rebase(body, origin + '/')}</div>")
    return Fetched("\n".join(bodies), api, page_url, "manifold", title)


def _toc_entry(toc: list, section_id: str) -> dict | None:
    for entry in toc or []:
        if entry.get("id") == section_id:
            return entry
        if found := _toc_entry(entry.get("children"), section_id):
            return found
    return None


def _toc_ids(toc: list) -> list[str]:
    """Section ids under a contents entry, depth first (anchors within a section repeat its id)."""
    ids = []
    for entry in toc or []:
        ids.append(entry.get("id"))
        ids.extend(_toc_ids(entry.get("children")))
    return [i for i in ids if i]


# ---------------------------------------------------------------- OpenStax

OPENSTAX_PATH = re.compile(r"^/books/(?P<book>[^/]+)/pages/(?P<page>[^/]+)/?$")


def is_openstax(url: str) -> bool:
    parts = urlsplit(url)
    return parts.netloc.lower() in ("openstax.org", "www.openstax.org") and bool(OPENSTAX_PATH.match(parts.path))


def fetch_openstax(client: Client, page_url: str, max_sections: int = 40) -> Fetched:
    """An OpenStax page; a chapter's first page brings every page of the chapter.

    Book pages are served prerendered, with the book's tree in window.__PRELOADED_STATE__, so
    the chapter's pages (sections, then further reading and works cited) come in the book's
    own order. A page that comes back without its content stops the fetch: a chapter missing a
    section isn't the chapter.
    """
    parts = urlsplit(page_url)
    m = OPENSTAX_PATH.match(parts.path)
    resp = client.get(page_url, accept="text/html")
    soup = _soup(resp)
    page = _openstax_content(soup)
    if page is None:
        raise FetchError("unsupported", "no OpenStax page content")
    slug = unquote(m["page"])
    chapter = _openstax_chapter(_openstax_book(soup).get("tree") or {}, slug)
    slugs = _openstax_pages(chapter) if chapter else []
    title = _plain(chapter.get("title", "")) if chapter else ""
    if not slugs or slugs[0] != slug:  # a section page on its own
        h1 = soup.find("h1")
        slugs, title = [slug], (h1.get_text(" ", strip=True) if h1 else "")
    if len(slugs) > max_sections:
        raise FetchError("too-large", f"{len(slugs)} pages in this chapter")
    bodies = [_rebase(page, resp.url)]
    for slug in slugs[1:]:
        url = f"{parts.scheme}://{parts.netloc}/books/{m['book']}/pages/{slug}"
        other_soup = _soup(client.get(url, accept="text/html"))
        other = _openstax_content(other_soup)
        if other is None:
            raise FetchError("unsupported", f"no page content at {url}")
        heading = ""
        if not other.select_one('[data-type="document-title"]') and (h1 := other_soup.find("h1")):
            heading = f"<h2>{_escape(h1.get_text(' ', strip=True))}</h2>"  # end-of-chapter pages carry no title
        bodies.append(f"<div>{heading}{_rebase(other, url)}</div>")
    return Fetched("\n".join(bodies), page_url, page_url, "openstax", title)


def _openstax_content(soup: BeautifulSoup):
    """The book content on a page: a [data-type=page] module, or the composite page (further
    reading, works cited, key terms) that ends a chapter."""
    return soup.select_one('[data-book-content="true"]') or soup.select_one('[data-type="page"], [data-type="composite-page"]')


def _openstax_book(soup: BeautifulSoup) -> dict:
    marker = "__PRELOADED_STATE__"
    for script in soup.find_all("script"):
        text = script.string or ""
        if marker in text:
            try:
                state, _ = json.JSONDecoder().raw_decode(text, text.index("{", text.index(marker)))
                return (state.get("content") or {}).get("book") or {}
            except (ValueError, AttributeError):
                break
    return {}


def _openstax_chapter(node: dict, slug: str) -> dict | None:
    """The chapter (a tree node holding only pages) that holds page `slug`. A unit holds
    chapters as well as its own introduction page, so it never counts as one."""
    contents = node.get("contents") or []
    if all(not c.get("contents") for c in contents) and any(c.get("slug") == slug for c in contents):
        return node
    for child in contents:
        if found := _openstax_chapter(child, slug):
            return found
    return None


def _openstax_pages(node: dict) -> list[str]:
    pages = []
    for child in node.get("contents") or []:
        pages.extend(_openstax_pages(child) if child.get("contents") else [child.get("slug", "")])
    return [p for p in pages if p]


def _rebase(el, url: str) -> str:
    """Make a section's relative links absolute before it joins the chapter page."""
    for tag in el.select("[href], [src]"):
        for attr in ("href", "src"):
            if tag.get(attr) and not tag[attr].startswith(("#", "data:")):
                tag[attr] = urljoin(url, tag[attr])
    return el.decode_contents()


def _soup(resp: requests.Response) -> BeautifulSoup:
    """Parse a page. When the server names no charset, the page's own <meta charset> decides;
    requests would assume ISO-8859-1 and garble every curly quote (openstax.org does this)."""
    declared = "charset=" in resp.headers.get("content-type", "").lower()
    return BeautifulSoup(resp.text if declared else resp.content, "html.parser")


def _plain(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- files

def fetch_pdf(client: Client, url: str) -> bytes:
    data, ctype = client.get_bytes(url, MAX_PDF_BYTES, accept="application/pdf")
    if not data.startswith(b"%PDF-"):
        head = data[:4000].lower()
        if any(m.encode() in head for m in CHALLENGE_MARKERS):
            client.gated_hosts[urlsplit(url).netloc.lower()] = "bot-gated"
            raise FetchError("bot-gated", "challenge page instead of the PDF")
        raise FetchError("not-a-pdf", ctype or "unknown type")
    return data


IMAGE_MAGIC = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"GIF8": ".gif", b"RIFF": ".webp"}


def fetch_image(client: Client, url: str) -> tuple[bytes, str]:
    data, ctype = client.get_bytes(url, MAX_IMAGE_BYTES, accept="image/*")
    for magic, ext in IMAGE_MAGIC.items():
        if data.startswith(magic):
            return data, ext
    if b"<svg" in data[:2000].lower() and "svg" in ctype:
        return data, ".svg"
    raise FetchError("not-an-image", ctype or "unknown type")
