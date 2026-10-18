"""Fetch reading content from publishers, politely.

The client names itself in its User-Agent, follows robots.txt (including Crawl-delay), waits
between requests to the same host, and gives up at the first sign of a bot check: a Cloudflare
challenge, a CAPTCHA page, or a WAF block. It never retries a gated request another way, and
once a host gates one request the rest of the run skips that host. WAC Clearinghouse /docs/
files sit behind a bot check, so they aren't requested at all; supply those PDFs in a folder.

HTML fetchers return the publisher's own body markup through an API, never a scraped guess:
  - Pressbooks: /wp-json/pressbooks/v2/{chapters,front-matter,back-matter,parts}?slug=...
  - WordPress: the page's rel="alternate" application/json link (/wp-json/wp/v2/...)
  - LibreTexts: its API is closed to robots, so the page's own content section, with a chapter's
    sections fetched in order when the page is a chapter listing.
"""
from __future__ import annotations

import io
import re
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; Aletheia/0.1; open-access course reader builder)"
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

    def _robots_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp = RobotFileParser(origin + "/robots.txt")
            try:
                self._wait(parts.netloc, 0)
                resp = self.session.get(origin + "/robots.txt", headers={"User-Agent": self.user_agent},
                                        timeout=TIMEOUT, allow_redirects=True)
                if resp.status_code in (401, 403):
                    rp.disallow_all = True
                elif resp.status_code >= 400:
                    rp.allow_all = True
                else:
                    rp.parse(resp.text.splitlines())
            except requests.RequestException:
                rp.allow_all = True  # unreachable robots.txt: the page fetch will fail on its own
            self._robots[origin] = rp
        return self._robots[origin]

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
        rp = self._robots_for(url)
        if not rp.can_fetch(self.user_agent, url):
            raise FetchError("robots", f"robots.txt disallows {urlsplit(url).path}")
        crawl_delay = rp.crawl_delay(self.user_agent) or 0
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


def fetch_pressbooks(client: Client, page_url: str) -> Fetched:
    api = pressbooks_api_url(page_url)
    if not api:
        raise FetchError("unsupported", "not a Pressbooks chapter URL")
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
    return Fetched(content.get("rendered", ""), api, item.get("link") or page_url, "pressbooks",
                   _plain((item.get("title") or {}).get("rendered", "")))


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


def _rebase(el, url: str) -> str:
    """Make a section's relative links absolute before it joins the chapter page."""
    for tag in el.select("[href], [src]"):
        for attr in ("href", "src"):
            if tag.get(attr) and not tag[attr].startswith(("#", "data:")):
                tag[attr] = urljoin(url, tag[attr])
    return el.decode_contents()


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
