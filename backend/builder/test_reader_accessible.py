"""The accessible PDF reader and the variant switch on /api/export/pdf."""
import io

import pytest
from django.test import Client
from pypdf import PdfReader

from builder import exports, reader_accessible, reader_pdf
from builder.test_reader_fulltext import ALL, FakeRenderer, cached, catalog, state  # noqa: F401 - fixtures
from content.store import Entry


def post_pdf(st, query=""):
    return Client().post(f"/api/export/pdf{query}", {"state": st}, content_type="application/json")


def test_variant_picks_the_builder(catalog, monkeypatch):  # noqa: F811
    monkeypatch.setattr(reader_accessible, "build", lambda ctx: b"%PDF-accessible")
    monkeypatch.setattr(exports, "reader_pdf", lambda ctx: b"%PDF-print")
    st = state("adapt-html")

    resp = post_pdf(st)
    assert resp.status_code == 200 and resp.content == b"%PDF-accessible"
    assert resp["Content-Disposition"] == 'attachment; filename="reader.pdf"'

    resp = post_pdf(st, "?variant=accessible")
    assert resp.content == b"%PDF-accessible"

    resp = post_pdf(st, "?variant=print")
    assert resp.status_code == 200 and resp.content == b"%PDF-print"
    assert resp["Content-Disposition"] == 'attachment; filename="reader-print.pdf"'

    assert post_pdf(st, "?variant=poster").status_code == 400


def test_missing_weasyprint_is_a_501_for_either_variant(catalog, monkeypatch):  # noqa: F811
    def no_weasyprint(ctx):
        raise RuntimeError("PDF export needs WeasyPrint and its system libraries (Pango).")
    monkeypatch.setattr(reader_accessible, "build", no_weasyprint)
    monkeypatch.setattr(exports, "reader_pdf", no_weasyprint)
    assert post_pdf(state("adapt-html")).status_code == 501
    assert post_pdf(state("adapt-html"), "?variant=print").status_code == 501


def test_accessible_reader_never_opens_or_merges_publisher_pdfs(cached, monkeypatch):  # noqa: F811
    def forbidden(*a, **k):
        raise AssertionError("the accessible reader opened a publisher PDF")
    monkeypatch.setattr(Entry, "open_pdf", forbidden)
    monkeypatch.setattr(reader_pdf, "_open_pdf", forbidden)
    monkeypatch.setattr(reader_pdf, "PdfWriter", forbidden)

    renderer = FakeRenderer()
    out = reader_accessible.build(exports.resolve(state(*ALL, units=2)), renderer=renderer)

    # One render, returned as is: no pages from anywhere else.
    assert len(renderer.html) == 1
    pages = PdfReader(io.BytesIO(out)).pages
    assert {(round(float(p.mediabox.width)), round(float(p.mediabox.height))) for p in pages} == {(612, 792)}
    html = renderer.html[0]

    # Web text is in full; PDF-only readings get credit, link, and the note instead.
    assert "Adaptable text body." in html and "Adapted copy text." in html
    assert html.count(reader_accessible.PDF_ONLY_NOTE) == 2  # nd-pdf and adapt-pdf
    assert 'href="https://example.org/nd-pdf.pdf">PDF</a>' in html
    assert "SHOULD NEVER APPEAR" not in html
    # Every reading carries its credit line, on its item and again in the credits.
    assert html.count('class="credit"') == 2 * len(ALL)
    assert all(f"Reading {rid}" in html for rid in ALL)


def test_accessible_contents_list_only_text_in_the_document(cached):  # noqa: F811
    ctx = exports.resolve(state("adapt-html", "nd-pdf", "closed", "nd-html", units=2))
    renderer = FakeRenderer()
    reader_accessible.build(ctx, renderer=renderer)
    toc = reader_accessible.contents(ctx)
    assert [t["anchor"] for t in toc] == ["unit-1", "unit-2"]
    assert [it["anchor"] for it in toc[0]["items"]] == ["u1-r1"]           # adapt-html, not nd-pdf
    assert [it["anchor"] for it in toc[1]["items"]] == ["u2-r2"]           # nd-html, not closed
    assert '<a href="#u2-r2">Reading nd-html</a>' in renderer.html[0]


def test_exports_work_with_no_cache_folder(catalog, settings, tmp_path):  # noqa: F811
    settings.STORAGES = {**settings.STORAGES, "content": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": tmp_path / "not-mounted" / "content_cache", "allow_overwrite": True}}}
    st = state("adapt-html", "nd-pdf", "closed")
    html = Client().post("/api/export/html", {"state": st}, content_type="application/json")
    assert html.status_code == 200 and b'href="https://example.org/adapt-html"' in html.content

    renderer = FakeRenderer()
    reader_accessible.build(exports.resolve(st), renderer=renderer)
    assert reader_accessible.PDF_ONLY_NOTE not in renderer.html[0] and 'class="fulltext"' not in renderer.html[0]

    printed = PdfReader(io.BytesIO(exports.reader_pdf(exports.resolve(st), renderer=FakeRenderer())))
    assert len(printed.pages) == 4  # cover, contents, unit page, credits
    assert not (tmp_path / "not-mounted").exists()  # exporting never creates the cache


def test_accessible_pdf_with_weasyprint_is_tagged(cached):  # noqa: F811
    """The real renderer. Needs Pango, so it runs in the Docker image and skips elsewhere."""
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        pytest.skip("WeasyPrint and Pango aren't installed here; run in the Docker image")
    resp = post_pdf(state(*ALL, units=2))
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF-")
    pdf = PdfReader(io.BytesIO(resp.content))
    root = pdf.trailer["/Root"]
    assert "/StructTreeRoot" in root and root["/MarkInfo"]["/Marked"]
    text = "\n".join(p.extract_text() for p in pdf.pages)
    assert "Adaptable text body." in text and reader_accessible.PDF_ONLY_NOTE in text
    assert "ND1" not in text and "AP1" not in text  # publisher pages stay out
    assert "SHOULD NEVER APPEAR" not in text
