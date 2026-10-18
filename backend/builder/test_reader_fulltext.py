"""Full text in the exported reader: tier rules, untouched NoDerivatives text, and PDF assembly."""
import io
import re

import pytest
from django.test import Client
from pypdf import PdfReader, PdfWriter

from builder import exports
from builder.reader_pdf import Rendered
from catalog.models import Pack, Reading
from conftest import make_pdf

ND_TEXT = ('<h2 id="r1-s">Section</h2><p>Every word of this essay stays exactly as written: '
           '“curly quotes,” em dashes — and <em>emphasis</em>.</p>')


@pytest.fixture
def catalog(db):
    pack = Pack.objects.create(id="t", name="Test", version="0", config={})
    rows = [("adapt-html", "adapt", True), ("nd-html", "verbatim", True), ("nd-pdf", "verbatim", True),
            ("adapt-pdf", "adapt-nc", True), ("closed", "link-only", True), ("unverified", "adapt", False),
            ("nd-dropped", "verbatim", True), ("adapt-dropped", "adapt", True), ("uncached", "adapt", True)]
    for rid, tier, verified in rows:
        Reading.objects.create(id=rid, pack=pack, level="chapter", title=f"Reading {rid}", authors="Doe, Jane",
                               license="CC BY-NC-ND 4.0" if tier == "verbatim" else "CC BY 4.0",
                               license_url="https://creativecommons.org/licenses/by/4.0/",
                               reuse_tier=tier, license_verified=verified, html_url=f"https://example.org/{rid}",
                               pdf_url=f"https://example.org/{rid}.pdf")
    return pack


def html_meta(**extra):
    return {"fetcher": "pressbooks", "source_url": "https://example.org/api", "page_url": "https://example.org/page",
            "fetched": "2026-10-17", "dropped": [], "images": {}, "missing_images": [], **extra}


@pytest.fixture
def cached(catalog, content_store):
    s = content_store
    s.save_html("adapt-html", "<h2>Intro</h2><p>Adaptable text body.</p>", html_meta())
    s.save_html("nd-html", ND_TEXT, html_meta())
    s.save_html("closed", "<p>SHOULD NEVER APPEAR closed</p>", html_meta())
    s.save_html("unverified", "<p>SHOULD NEVER APPEAR unverified</p>", html_meta())
    s.save_html("nd-dropped", "<p>SHOULD NEVER APPEAR nd-dropped</p>", html_meta(dropped=["video"]))
    s.save_html("adapt-dropped", "<p>Adapted copy text.</p>", html_meta(dropped=["math"]))
    s.save_pdf("nd-pdf", make_pdf(2, (300, 400), "ND"), {"fetcher": "local-pdf", "source_url": "https://example.org/nd.pdf",
                                                          "fetched": "2026-10-17", "pages": 2})
    s.save_pdf("adapt-pdf", make_pdf(1, (350, 450), "AP"), {"fetcher": "download", "source_url": "https://example.org/a.pdf",
                                                            "fetched": "2026-10-17", "pages": 1})
    return s


def state(*ids, units=1):
    per = [list(ids)] if units == 1 else [list(ids[: len(ids) // 2]), list(ids[len(ids) // 2:])]
    return {"title": "Reader", "units": [
        {"key": f"u{n}", "title": f"Unit {n}", "start_week": n, "end_week": n,
         "readings": [{"id": i, "selected": True} for i in group], "assignments": []}
        for n, group in enumerate(per, 1)]}


ALL = ["adapt-html", "nd-html", "nd-pdf", "adapt-pdf", "closed", "unverified", "nd-dropped", "adapt-dropped", "uncached"]


def export_html(st):
    resp = Client().post("/api/export/html", {"state": st}, content_type="application/json")
    assert resp.status_code == 200
    return resp.content.decode()


def test_html_reader_embeds_redistributable_text(cached):
    html = export_html(state(*ALL))
    assert "Adaptable text body." in html
    assert "Text from <a href=\"https://example.org/page\">example.org</a>, copied 2026-10-17." in html
    # Headings move under the reading's own title (h4) without losing their order.
    assert "<h5>Intro</h5>" in html and "<h2>Intro</h2>" not in html
    # The TASL credit line still sits with every reading.
    assert html.count("Licensed under") >= 6


def test_link_only_and_unverified_are_never_embedded(cached):
    html = export_html(state(*ALL))
    assert "SHOULD NEVER APPEAR closed" not in html
    assert "SHOULD NEVER APPEAR unverified" not in html
    assert 'href="https://example.org/closed"' in html and 'href="https://example.org/unverified"' in html


def test_nd_text_is_reproduced_unchanged(cached):
    html = export_html(state("nd-html"))
    body = re.search(r'<div class="fulltext">\s*(.*?)\s*</div>', html, re.S).group(1)
    # Only the heading level changes (h2 -> h5, a format shift); every character of text is identical.
    assert body == ND_TEXT.replace("<h2", "<h5").replace("</h2>", "</h5>")


def test_nd_copy_missing_media_is_linked_not_embedded(cached):
    html = export_html(state("nd-dropped", "adapt-dropped"))
    assert "SHOULD NEVER APPEAR nd-dropped" not in html
    assert "Adapted copy text." in html
    assert "This copy leaves out media that has no print form (math); see the original for them." in html


def test_pdf_readings_point_to_the_pdf_edition(cached):
    html = export_html(state("nd-pdf", "uncached"))
    assert "is in the PDF edition of this reader" in html
    assert html.count("is in the PDF edition") == 1  # the uncached reading is just a link
    assert 'href="https://example.org/nd-pdf.pdf">PDF</a>' in html


# ---------------------------------------------------------------- PDF

class FakeRenderer:
    """Lays out one letter page per page break, so the merge logic runs without WeasyPrint."""

    def __init__(self):
        self.html = []

    def render(self, html: str) -> Rendered:
        self.html.append(html)
        anchors, page, started = {}, 0, False
        for attrs in re.findall(r"<(?:section|article)\b([^>]*)>", html):
            if "data-break" in attrs and started:
                page += 1
            started = True
            if m := re.search(r'id="([^"]+)"', attrs):
                anchors[m.group(1)] = page
        w = PdfWriter()
        for _ in range(page + 1):
            w.add_blank_page(612, 792)
        buf = io.BytesIO()
        w.write(buf)
        return Rendered(buf.getvalue(), anchors, page + 1)


def test_pdf_reader_merges_publisher_pdfs_untouched(cached):
    renderer = FakeRenderer()
    st = state("adapt-html", "nd-pdf", "closed", "adapt-pdf", units=2)
    ctx = exports.resolve(st)
    pdf = PdfReader(io.BytesIO(exports.reader_pdf(ctx, renderer=renderer)))
    sizes = [(round(float(p.mediabox.width)), round(float(p.mediabox.height))) for p in pdf.pages]
    letter, nd, ap = (612, 792), (300, 400), (350, 450)
    # cover, contents | unit 1, adapt-html, nd-pdf title, ND p1, ND p2 | unit 2, adapt-pdf title, AP p1 | credits
    assert sizes == [letter, letter, letter, letter, letter, nd, nd, letter, letter, ap, letter]

    # Publisher pages are byte-for-byte the source pages: nothing stamped on them.
    with cached.get("nd-pdf").open_pdf() as fh:
        src = PdfReader(fh)
        assert [p.get_contents().get_data() for p in pdf.pages[5:7]] == [p.get_contents().get_data() for p in src.pages]
    assert [p.extract_text().strip() for p in pdf.pages[5:7]] == ["ND1", "ND2"]
    assert pdf.pages[9].extract_text().strip() == "AP1"

    # Aletheia's own pages are numbered through the whole book; the cover isn't numbered.
    numbers = {i: p.extract_text().strip() for i, p in enumerate(pdf.pages) if sizes[i] == letter}
    assert numbers[0] == ""
    assert all(numbers[i] == str(i + 1) for i in numbers if i)

    # The contents give final page numbers: unit 2 starts on page 8, credits on page 11.
    front = renderer.html[-1]
    assert re.search(r'<span class="pg">8</span>Unit 2', front)
    assert re.search(r'<span class="pg">11</span>Licenses and credits', front)
    # The link-only reading is listed on its unit page but gets no page of its own.
    body = renderer.html[0]
    assert "Reading closed" in body and 'id="u2-r1"' not in body
    assert body.count('<article class="reading') == 3


def test_pdf_reader_without_cache_lists_links_only(catalog, content_store):
    renderer = FakeRenderer()
    pdf = PdfReader(io.BytesIO(exports.reader_pdf(exports.resolve(state("closed", "uncached")), renderer=renderer)))
    assert len(pdf.pages) == 4  # cover, contents, unit page, credits
    assert '<article class="reading' not in renderer.html[0]


def test_pdf_export_with_weasyprint(cached):
    """The real renderer. Needs Pango, so it runs in the Docker image and skips elsewhere."""
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        pytest.skip("WeasyPrint and Pango aren't installed here; run in the Docker image")
    resp = Client().post("/api/export/pdf", {"state": state(*ALL, units=2)}, content_type="application/json")
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF-")
    pdf = PdfReader(io.BytesIO(resp.content))
    text = "\n".join(p.extract_text() for p in pdf.pages)
    assert "Adaptable text body." in text and "ND1" in text and "AP1" in text
    assert "SHOULD NEVER APPEAR" not in text
