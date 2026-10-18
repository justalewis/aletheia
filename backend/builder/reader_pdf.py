"""Assemble the PDF reader.

WeasyPrint lays out the pages Aletheia writes: cover, contents, unit pages, readings published
as web pages, and credits. Each reading published as a PDF gets a title page in that flow, and
pypdf inserts the publisher's PDF right after it, page for page. Nothing is drawn on, cropped
from, or added inside those pages, which is what NoDerivatives works need.

Page numbers run through the whole book (inserted pages count) and are stamped afterward on
Aletheia's own pages only; inserted pages keep the publisher's numbering. The contents are laid
out last, once the body's pagination is known, so they can give real page numbers.
"""
from __future__ import annotations

import io
import mimetypes
from dataclasses import dataclass

from django.template.loader import render_to_string
from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from content.embed import attach
from content.store import ContentStore

FOLIO_FONT_SIZE = 9
FOLIO_Y = 0.45 * 72  # points above the bottom edge, inside the 0.9in margin


@dataclass
class Rendered:
    pdf: bytes
    anchors: dict  # element id -> 0-based page index
    pages: int


class WeasyRenderer:
    """HTML -> Rendered. Images come only from the content cache; nothing is fetched at export time."""

    def __init__(self, images: dict | None = None):
        self.images = images or {}  # url -> (Entry, cached file name)

    def _fetch(self, url: str):
        from weasyprint import default_url_fetcher

        if url.startswith("data:"):
            return default_url_fetcher(url)
        if url in self.images:
            entry, name = self.images[url]
            return {"string": entry.image_bytes(name), "mime_type": mimetypes.guess_type(name)[0] or "image/png"}
        raise ValueError(f"not in the content cache: {url}")

    def render(self, html: str) -> Rendered:
        try:
            from weasyprint import HTML
        except (ImportError, OSError) as e:  # pragma: no cover - depends on system libraries
            raise RuntimeError("PDF export needs WeasyPrint and its system libraries (Pango).") from e
        doc = HTML(string=html, url_fetcher=self._fetch).render()
        anchors: dict[str, int] = {}
        for i, page in enumerate(doc.pages):
            for name in page.anchors:
                anchors.setdefault(name, i)
        # Untagged on purpose: pypdf can't merge structure trees, and a tag tree covering only
        # Aletheia's pages would hide the publishers' pages from screen readers.
        return Rendered(doc.write_pdf(), anchors, len(doc.pages))


def _open_pdf(entry) -> tuple[PdfReader, object]:
    fh = entry.open_pdf()
    reader = PdfReader(fh)
    if reader.is_encrypted:
        reader.decrypt("")
    return reader, fh


def print_units(ctx: dict, pdf_pages: dict) -> list[dict]:
    out = []
    for n, u in enumerate(ctx["units"], 1):
        items = []
        for k, r in enumerate(u.readings, 1):
            ft = getattr(r, "fulltext", None)
            items.append({"r": r, "anchor": f"u{n}-r{k}", "mode": ft.mode if ft else "",
                          "entry": ft.entry if ft else None, "pages": pdf_pages.get(r.id), "page": None})
        out.append({"unit": u, "anchor": f"unit-{n}", "readings": items})
    return out


def folio(page: PageObject, text: str) -> None:
    """Stamp a page number at the bottom center of one of Aletheia's own pages."""
    box = page.mediabox
    width, height = float(box.width), float(box.height)
    text_width = len(text) * 0.556 * FOLIO_FONT_SIZE  # Helvetica figures are 556/1000 em wide
    x = float(box.left) + (width - text_width) / 2
    y = float(box.bottom) + FOLIO_Y
    overlay = PageObject.create_blank_page(width=width, height=height)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })
    overlay[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/AletheiaFolio"): font})})
    stream = DecodedStreamObject()
    stream.set_data(f"q 0.357 0.357 0.4 rg BT /AletheiaFolio {FOLIO_FONT_SIZE} Tf {x:.2f} {y:.2f} Td ({text}) Tj ET Q"
                    .encode("ascii"))
    overlay[NameObject("/Contents")] = stream
    page.merge_page(overlay)


def build(ctx: dict, renderer=None, store: ContentStore | None = None) -> bytes:
    store = store or ContentStore()
    attach(ctx["readings"], "pdf", store)

    # Count each publisher PDF's pages now; one that won't open falls back to a link.
    pdf_pages: dict[str, int] = {}
    for r in ctx["readings"]:
        ft = getattr(r, "fulltext", None)
        if ft and ft.mode == "pdf" and r.id not in pdf_pages:
            try:
                reader, fh = _open_pdf(ft.entry)
                pdf_pages[r.id] = len(reader.pages)
                fh.close()
            except Exception:  # noqa: BLE001 - a damaged cache file shouldn't sink the export
                r.fulltext = None
    units = print_units(ctx, pdf_pages)

    if renderer is None:
        images = {url: (r.fulltext.entry, name) for r in ctx["readings"]
                  if getattr(r, "fulltext", None) and r.fulltext.mode == "html"
                  for url, name in r.fulltext.entry.images.items()}
        renderer = WeasyRenderer(images)

    body = renderer.render(render_to_string("builder/reader_print.html", {**ctx, "part": "body", "print_units": units}))
    slots = sorted(((body.anchors[it["anchor"]], it) for u in units for it in u["readings"] if it["mode"] == "pdf"),
                   key=lambda s: s[0])

    def final_index(body_index: int, front_pages: int) -> int:
        return front_pages + body_index + sum(it["pages"] for p, it in slots if p < body_index)

    # Lay out the front matter with real page numbers; redo it if the contents grow a page.
    front_pages, front = 0, None
    for _ in range(4):
        def num(anchor: str) -> int | None:
            return final_index(body.anchors[anchor], front_pages) + 1 if anchor in body.anchors else None
        toc = [{"title": u["unit"].title, "weeks": u["unit"].weeks_label, "page": num(u["anchor"]),
                "items": [{"title": it["r"].title, "page": num(it["anchor"])} for it in u["readings"] if it["mode"]]}
               for u in units]
        toc.append({"title": "Licenses and credits", "weeks": "", "page": num("licenses"), "items": []})
        front = renderer.render(render_to_string("builder/reader_print.html", {**ctx, "part": "front", "toc": toc}))
        if front.pages == front_pages:
            break
        front_pages = front.pages

    writer = PdfWriter(clone_from=io.BytesIO(body.pdf))
    handles = []
    try:
        for p, it in reversed(slots):
            reader, fh = _open_pdf(it["entry"])
            handles.append(fh)
            for k, page in enumerate(reader.pages):
                writer.insert_page(page, p + 1 + k)
        for k, page in enumerate(PdfReader(io.BytesIO(front.pdf)).pages):
            writer.insert_page(page, k)

        own = list(range(1, front.pages)) + [final_index(i, front.pages) for i in range(body.pages)]
        for idx in own:
            folio(writer.pages[idx], str(idx + 1))
        out = io.BytesIO()
        writer.write(out)
    finally:
        for fh in handles:
            fh.close()
    return out.getvalue()
