"""The accessible PDF reader: one tagged WeasyPrint document, nothing merged in.

The print reader (reader_pdf.py) inserts publisher PDFs page for page, and pypdf can't merge
structure trees, so it goes out untagged. This version renders everything from HTML with
pdf_tags on, so screen readers get headings, lists, tables, alt text, and reading order.
Readings with cached web text carry it in full, as in the HTML reader. Readings that exist
only as publisher PDFs get their credit line, a link, and a note pointing to the print
version; their PDFs are never opened here.
"""
from __future__ import annotations

from django.template.loader import render_to_string

from builder.reader_pdf import Rendered, WeasyRenderer
from content.embed import attach
from content.store import ContentStore

PDF_ONLY_NOTE = "The original PDF is in the print version and at its source."


class TaggedRenderer(WeasyRenderer):
    """WeasyRenderer with PDF tags on. Images still come only from the content cache."""

    def render(self, html: str):
        try:
            from weasyprint import HTML
        except (ImportError, OSError) as e:  # pragma: no cover - depends on system libraries
            raise RuntimeError("PDF export needs WeasyPrint and its system libraries (Pango).") from e
        doc = HTML(string=html, url_fetcher=self.fetcher()).render()
        return Rendered(doc.write_pdf(pdf_tags=True), {}, len(doc.pages))


def build(ctx: dict, renderer=None, store: ContentStore | None = None) -> bytes:
    store = store or ContentStore()
    attach(ctx["readings"], "pdf", store)
    if renderer is None:
        images = {url: (r.fulltext.entry, name) for r in ctx["readings"]
                  if getattr(r, "fulltext", None) and r.fulltext.mode == "html"
                  for url, name in r.fulltext.entry.images.items()}
        renderer = TaggedRenderer(images)
    html = render_to_string("builder/reader_accessible.html",
                            {**ctx, "toc": contents(ctx), "pdf_only_note": PDF_ONLY_NOTE})
    return renderer.render(html).pdf


def contents(ctx: dict) -> list[dict]:
    """Units, and under each the readings whose full text is in this document."""
    return [{"anchor": f"unit-{n}", "title": u.title, "weeks": u.weeks_label,
             "items": [{"anchor": f"u{n}-r{k}", "title": r.title} for k, r in enumerate(u.readings, 1)
                       if getattr(r, "fulltext", None) and r.fulltext.mode == "html"]}
            for n, u in enumerate(ctx["units"], 1)]
