"""Fill the content cache for a set of readings.

For each redistributable reading (adapt, adapt-nc, or verbatim with a verified license):
  1. a web page we can read (Pressbooks, WordPress, LibreTexts, Manifold, OpenStax) is fetched,
     sanitized, and stored with its images;
  2. otherwise its PDF is copied from a local folder (see library.py) or downloaded, unless the
     link is bot-gated. A chapter whose only PDF is the whole book gets its own pages, cut from
     the book unchanged (see bookpdf.py);
  3. otherwise it stays a link, and the report says why.
Link-only readings are never fetched. Whole books and collections aren't cached either: a
reader assigns their chapters, and a 300-page book doesn't belong inside one.
"""
from __future__ import annotations

import hashlib
import io
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urldefrag

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from content.bookpdf import BookPdf
from content.fetch import (
    Client, FetchError, Fetched, fetch_image, fetch_libretexts, fetch_manifold, fetch_openstax, fetch_pdf,
    fetch_pressbooks, fetch_wordpress, is_libretexts, is_manifold, is_openstax, pressbooks_api_url,
)
from content.library import LocalPdf, PdfLibrary, url_tail
from content.sanitize import sanitize
from content.store import ContentStore

WHOLE_WORKS = {"book", "collection", "journal", "website"}
PART_LEVELS = {"chapter", "article", "primary-text"}
MIN_TEXT_CHARS = 400
MAX_IMAGES = 60


@dataclass
class Outcome:
    id: str
    status: str        # cached | kept | link-only | an error code from FetchError
    kind: str = ""     # html | pdf
    via: str = ""      # pressbooks | wordpress | libretexts | local-pdf | download
    detail: str = ""
    title: str = ""
    pdf_url: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("cached", "kept")


def html_fetcher(url: str):
    if not url or url.lower().split("?")[0].endswith(".pdf"):
        return None
    if pressbooks_api_url(url):
        return fetch_pressbooks
    if is_openstax(url):
        return fetch_openstax
    if is_manifold(url):
        return fetch_manifold
    if is_libretexts(url):
        return fetch_libretexts
    return fetch_wordpress


def pdf_pages(data: bytes) -> int:
    """Page count of a PDF we can read and copy pages from, or FetchError."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise FetchError("encrypted", "the PDF needs a password")
        return len(reader.pages)
    except (PdfReadError, ValueError, KeyError) as e:
        raise FetchError("bad-pdf", f"{type(e).__name__}: {e}") from e


class Warmer:
    def __init__(self, store: ContentStore, client: Client | None = None, library: PdfLibrary | None = None,
                 offline: bool = False, images: bool = True, log=None):
        self.store = store
        self.client = client or Client()
        self.library = library or PdfLibrary([])
        self.offline = offline
        self.images = images
        self.log = log or (lambda msg: None)
        self.shared_pdf_urls: set[str] = set()
        self.ambiguous_names: frozenset = frozenset()
        self._books: dict[str, BookPdf] = {}            # sha256 -> parsed whole-book PDF
        self._downloads: dict[str, bytes | FetchError] = {}

    def prepare(self, readings: list, catalog_pdf_urls: list[str]) -> None:
        """`catalog_pdf_urls`: every pdf_url in the catalog, to spot whole-book PDFs shared by chapters
        (book.pdf#page=41 and book.pdf#page=82 are the same file)."""
        seen = Counter(urldefrag(u)[0] for u in catalog_pdf_urls if u)
        self.shared_pdf_urls = {u for u, n in seen.items() if n > 1}
        names = Counter(url_tail(r.pdf_url, 1) for r in readings if r.pdf_url)
        self.ambiguous_names = frozenset(n for n, c in names.items() if c > 1)

    def warm(self, reading, refresh: bool = False) -> Outcome:
        out = Outcome(reading.id, "", title=reading.title, pdf_url=reading.pdf_url)
        if not reading.redistributable:
            out.status, out.detail = "link-only", (
                "license not verified" if not reading.license_verified else "all rights reserved")
            return out
        if reading.level in WHOLE_WORKS:
            out.status, out.detail = "whole-work", f"a {reading.level}; readers link to whole works"
            return out
        if not refresh and (entry := self.store.get(reading.id)):
            out.status, out.kind, out.via = "kept", entry.kind, entry.meta.get("fetcher", "")
            return out

        errors: list[FetchError] = []
        fetch = html_fetcher(reading.html_url)
        if fetch and not self.offline:
            try:
                return self._save_html(reading, fetch(self.client, reading.html_url), out)
            except FetchError as e:
                errors.append(e)

        # A chapter whose PDF is the whole book (a pdf_url shared with other chapters, a file
        # listed for several readings, or a download far longer than a chapter) gets its pages cut out.
        whole_book = reading.level in PART_LEVELS and (
            urldefrag(reading.pdf_url)[0] in self.shared_pdf_urls or reading.id in self.library.shared)
        found = None  # (bytes, source url, LocalPdf) of a PDF that turned out to be the whole book
        if not whole_book:
            local = self.library.find(reading.id, reading.pdf_url, self.ambiguous_names)
            try:
                if local:
                    found = (local.path.read_bytes(), local.source_url or reading.pdf_url, local)
                    return self._save_pdf(reading, found[0], out, "local-pdf", found[1], local)
                if reading.pdf_url and not self.offline:
                    found = (fetch_pdf(self.client, reading.pdf_url), reading.pdf_url, None)
                    return self._save_pdf(reading, found[0], out, "download", found[1])
            except FetchError as e:
                errors.append(e)
                whole_book = e.status == "whole-book-pdf"
        if whole_book:
            try:
                return self._save_chapter_pages(reading, out, found)
            except FetchError as e:
                errors.append(e)

        if not errors:
            errors.append(FetchError("offline" if self.offline else "no-source",
                                     "no API for its web page and no PDF" if not self.offline else "network fetches off"))
        # The most telling error: a gate or a missing PDF says more than "no API on this site".
        first = sorted(errors, key=lambda e: e.status in ("unsupported", "not-found"))[0]
        out.status, out.detail = first.status, "; ".join(f"{e.status}: {e.detail}" for e in errors)
        return out

    def _save_html(self, reading, f: Fetched, out: Outcome) -> Outcome:
        prefix = "r" + hashlib.sha1(reading.id.encode()).hexdigest()[:6] + "-"
        s = sanitize(f.html, base_url=f.base_url, id_prefix=prefix)
        if s.text_chars < MIN_TEXT_CHARS:
            raise FetchError("empty", f"only {s.text_chars} characters of text")
        images, files, missing = {}, {}, []
        for url in s.images:
            if not self.images or len(images) >= MAX_IMAGES:
                missing.append(url)
                continue
            try:
                data, ext = fetch_image(self.client, url)
            except FetchError as e:
                self.log(f"    image {e.status}: {url}")
                missing.append(url)
                continue
            name = hashlib.sha1(url.encode()).hexdigest()[:20] + ext
            images[url], files[name] = name, data
        meta = {
            "fetcher": f.fetcher, "source_url": f.source_url, "page_url": f.base_url,
            "fetched": date.today().isoformat(), "title_at_source": f.title,
            "license_at_fetch": reading.license, "tier_at_fetch": reading.effective_tier,
            "sha256": hashlib.sha256(s.html.encode("utf-8")).hexdigest(), "text_chars": s.text_chars,
            "dropped": s.dropped, "images": images, "missing_images": missing,
        }
        self.store.save_html(reading.id, s.html, meta, files)
        out.status, out.kind, out.via = "cached", "html", f.fetcher
        out.detail = ", ".join(filter(None, [
            f"{len(images)} images" if images else "",
            f"{len(missing)} images missing" if missing else "",
            f"dropped {', '.join(s.dropped)}" if s.dropped else ""]))
        return out

    def _save_chapter_pages(self, reading, out: Outcome, found=None) -> Outcome:
        """Cache a chapter as its own pages of the whole-book PDF, copied unchanged."""
        data, source_url, local = found or self._book_source(reading)
        key = hashlib.sha256(data).hexdigest()
        if key not in self._books:
            self._books[key] = BookPdf(data)
        book = self._books[key]
        hint = re.search(r"\bpage=(\d+)", urldefrag(reading.pdf_url)[1])
        ch = book.chapter(reading.title, int(hint.group(1)) - 1 if hint else None, reading.pages)
        self._save_pdf(reading, book.extract(ch), out, "book-pages", urldefrag(source_url)[0], local, {
            "book_sha256": key, "book_pages": f"{ch.first + 1}-{ch.last + 1}",
            "printed_pages": f"{ch.printed_first}-{ch.printed_last}"})
        out.detail = f"{ch.pages} pages (printed {ch.printed_first}-{ch.printed_last})"
        if local:
            out.detail += f" from {local.label}"
        return out

    def _book_source(self, reading) -> tuple[bytes, str, LocalPdf | None]:
        """The whole book a chapter is in: on disk if possible, else downloaded once per run."""
        local = self.library.book_for(reading.id, reading.parent_id or "", reading.pdf_url)
        if local:
            return local.path.read_bytes(), local.source_url or reading.pdf_url, local
        url = urldefrag(reading.pdf_url)[0]
        if not url or self.offline:
            raise FetchError("whole-book-pdf", "only the whole book has this chapter, and it isn't on disk")
        if url not in self._downloads:
            try:
                self._downloads[url] = fetch_pdf(self.client, url)
            except FetchError as e:
                self._downloads[url] = e
        if isinstance(self._downloads[url], FetchError):
            raise self._downloads[url]
        return self._downloads[url], url, None

    def _save_pdf(self, reading, data: bytes, out: Outcome, via: str, source_url: str,
                  local: LocalPdf | None = None, extra: dict | None = None) -> Outcome:
        pages = pdf_pages(data)
        if reading.level in PART_LEVELS and pages > 90 and (not reading.pages or pages > 2 * reading.pages + 10):
            raise FetchError("whole-book-pdf", f"{pages} pages for a {reading.level}")
        meta = {
            "fetcher": via, "source_url": source_url, "page_url": reading.landing_url or reading.html_url,
            "fetched": date.today().isoformat(), "license_at_fetch": reading.license,
            "tier_at_fetch": reading.effective_tier, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "pages": pages, **(extra or {}),
        }
        if local:
            meta["local_file"] = local.label
            meta["matched_by"] = local.matched_by
        self.store.save_pdf(reading.id, data, meta)
        out.status, out.kind, out.via, out.detail = "cached", "pdf", via, f"{pages} pages"
        if local:
            out.detail += f" from {local.label}"
        return out


def summarize(outcomes: list[Outcome]) -> dict:
    return {
        "generated": date.today().isoformat(),
        "counts": dict(Counter(o.status for o in outcomes)),
        "by_kind": dict(Counter(f"{o.kind}:{o.via}" for o in outcomes if o.ok)),
        "readings": [asdict(o) for o in outcomes],
    }


def reading_ids_in_arcs(arc_files: list[Path]) -> list[str]:
    """Reading ids in arc YAML files, in course order, without repeats."""
    import yaml

    ids: list[str] = []
    for f in arc_files:
        doc = yaml.safe_load(Path(f).read_text(encoding="utf-8")) or {}
        for u in doc.get("units", []):
            for role in ("core", "optional"):
                ids.extend((u.get("readings") or {}).get(role) or [])
    return list(dict.fromkeys(ids))
