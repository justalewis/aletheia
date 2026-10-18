"""Decide which readings carry their full text in an export, and in what form.

The rules, applied to the catalog's current tier (never the tier recorded at fetch time):
  - Link-only readings (all rights reserved, or a license nobody has verified) are never
    embedded, whatever the cache holds.
  - Adapt and adapt-nc readings are embedded when cached. If the copy had to leave something
    out (media with no print form, or images that couldn't be cached for the PDF), the reader
    says so, as CC BY requires when a work is changed.
  - Verbatim (NoDerivatives) readings are embedded only when the copy is whole: nothing
    dropped, and for the PDF every image cached. Sanitizing and moving to print are format
    shifts the licenses allow; a copy missing part of the work isn't.
  - PDF-native readings go into the PDF reader as the publisher's pages, untouched. The HTML
    reader points to them, since a single web page can't carry them.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from content.sanitize import shift_headings
from content.store import ContentStore, Entry

READING_HEADING_LEVEL = 4  # reading titles are h4 in the reader, so their text starts at h5


@dataclass
class FullText:
    mode: str          # "html" or "pdf"
    entry: Entry
    html: str = ""     # sanitized text with headings renumbered (mode "html")
    note: str = ""     # what this copy leaves out, if anything

    @property
    def fetched(self) -> str:
        return self.entry.fetched

    @property
    def source_page(self) -> str:
        return self.entry.meta.get("page_url") or self.entry.source_url

    @property
    def source_host(self) -> str:
        return urlsplit(self.source_page).netloc

    @property
    def pages(self) -> int | None:
        return self.entry.pages


def full_text_for(reading, entry: Entry | None, medium: str) -> FullText | None:
    """`medium` is "html" or "pdf"."""
    if entry is None or not reading.redistributable:
        return None
    nd = reading.effective_tier == "verbatim"
    if entry.kind == "pdf":
        return FullText("pdf", entry)
    if entry.kind != "html":
        return None
    gaps = []
    if entry.dropped:
        gaps.append("media that has no print form (" + ", ".join(entry.dropped) + ")")
    if medium == "pdf" and entry.missing_images:
        gaps.append(f"{len(entry.missing_images)} image(s) that couldn't be copied")
    if nd and gaps:
        return None
    note = f"This copy leaves out {' and '.join(gaps)}; see the original for them." if gaps else ""
    return FullText("html", entry, shift_headings(entry.html, READING_HEADING_LEVEL + 1), note)


def attach(readings, medium: str, store: ContentStore | None = None) -> None:
    """Set `fulltext` (FullText or None) on each reading."""
    store = store or ContentStore()
    for r in readings:
        r.fulltext = full_text_for(r, store.get(r.id) if r.redistributable else None, medium)
