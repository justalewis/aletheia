import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

import pytest
from django.test import Client

from catalog.models import Assignment, Pack, Reading
from conftest import make_pdf


@pytest.fixture
def catalog(db):
    pack = Pack.objects.create(id="t", name="Test", version="0", config={})
    for rid, tier, verified in [("open", "adapt", True), ("nd", "verbatim", True), ("closed", "link-only", True),
                                ("unverified", "adapt", False)]:
        Reading.objects.create(id=rid, pack=pack, level="chapter", title=f"Reading {rid}", authors="Doe, Jane",
                               license="CC BY-NC-ND 4.0" if tier == "verbatim" else "CC BY 4.0",
                               license_family="CC BY-NC-ND" if tier == "verbatim" else "CC BY",
                               reuse_tier=tier, license_verified=verified, html_url=f"https://example.org/{rid}")
    Assignment.objects.create(id="essay", pack=pack, title="Research essay", kind="major assignment",
                              license="CC BY-NC 4.0", license_family="CC BY-NC", reuse_tier="adapt-nc",
                              license_verified=True, url="https://example.org/essay")
    return pack


STATE = {
    "title": "ENGL 101: Test & Course",
    "term": {"weeks": 10},
    "units": [
        {"key": "u1", "title": "Unit <One>", "start_week": 1, "end_week": 3, "goals": ["Read closely"],
         "readings": [{"id": "open", "selected": True}, {"id": "nd", "selected": True},
                      {"id": "closed", "selected": True}, {"id": "unverified", "selected": True},
                      {"id": "skipped", "selected": False}],
         "assignments": [{"id": "essay", "role": "major", "selected": True}]},
    ],
}


def test_reader_html_escapes_and_credits(catalog):
    resp = Client().post("/api/export/html", {"state": STATE}, content_type="application/json")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Unit &lt;One&gt;" in html
    assert "Reading open" in html and "Licensed under CC BY 4.0" in html
    assert "linked, not copied" in html
    assert "NoDerivatives" in html


def test_unverified_license_is_treated_as_link_only(catalog):
    report = Client().post("/api/license-report", {"state": STATE}, content_type="application/json").json()
    assert report["tiers"]["link-only"] == 2
    assert report["unverified"] == ["unverified"]


NS = {"cc": "http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1"}


def page_meta(**extra):
    return {"fetcher": "pressbooks", "source_url": "https://example.org/api", "page_url": "https://example.org/page",
            "fetched": "2026-10-17", "dropped": [], "images": {}, "missing_images": [], **extra}


@pytest.fixture
def cached(catalog, content_store):
    """Full text for every reading, including the two that must stay links."""
    s = content_store
    s.save_html("open", "<h1>Intro</h1><p>Open text body.</p><h2>Part</h2>", page_meta(dropped=["math"]))
    s.save_pdf("nd", make_pdf(2, (300, 400), "ND"), {"fetcher": "local-pdf", "source_url": "https://example.org/nd.pdf",
                                                     "page_url": "https://example.org/nd", "fetched": "2026-10-17",
                                                     "pages": 2})
    s.save_html("closed", "<p>SHOULD NEVER APPEAR closed</p>", page_meta())
    s.save_pdf("unverified", make_pdf(1, (350, 450), "UV"), {"fetcher": "download", "fetched": "2026-10-17",
                                                             "source_url": "https://example.org/uv.pdf", "pages": 1})
    return s


def export_imscc(flavor: str) -> zipfile.ZipFile:
    resp = Client().post(f"/api/export/imscc?flavor={flavor}", {"state": STATE}, content_type="application/json")
    assert resp.status_code == 200
    return zipfile.ZipFile(io.BytesIO(resp.content))


def local_target(page: str, href: str) -> str | None:
    """The package path a link in a wiki page points at, or None for a link off the package."""
    if href.startswith("$IMS-CC-FILEBASE$/"):
        return "web_resources/" + href.removeprefix("$IMS-CC-FILEBASE$/")
    if re.match(r"[a-z]+:|#", href):
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(page), href))


@pytest.mark.parametrize("flavor", ["canvas", "standard"])
def test_imscc_package_is_well_formed(cached, flavor):
    z = export_imscc(flavor)
    names = z.namelist()
    manifest = ET.fromstring(z.read("imsmanifest.xml"))
    resources = {r.get("identifier") for r in manifest.iterfind(".//cc:resource", NS)}
    refs = [i.get("identifierref") for i in manifest.iterfind(".//cc:item", NS) if i.get("identifierref")]
    assert refs and all(r in resources for r in refs), "every module item must point at a resource"
    for r in manifest.iterfind(".//cc:resource", NS):
        assert r.get("href") in names
        for f in r.iterfind("cc:file", NS):
            assert f.get("href") in names
        for d in r.iterfind("cc:dependency", NS):
            assert d.get("identifierref") in resources
    # Links from pages to files in the package land on files that are there.
    for page in (n for n in names if n.startswith("wiki_content/")):
        for href in re.findall(r'href="([^"]+)"', z.read(page).decode()):
            target = local_target(page, href)
            assert target is None or target in names, f"{page} links to missing {href}"
    if flavor == "canvas":
        assert "course_settings/canvas_export.txt" in names
        ET.fromstring(z.read("course_settings/module_meta.xml"))
        assert any(n.endswith("assignment_settings.xml") for n in names)
    else:
        assert not any(n.startswith("course_settings/") for n in names)


@pytest.mark.parametrize("flavor", ["canvas", "standard"])
def test_imscc_reading_page_carries_full_text(cached, flavor):
    page = export_imscc(flavor).read("wiki_content/reading-open.html").decode()
    assert "Open text body." in page
    # The text sits under the credit line, with the source and the omission note after it.
    assert page.index("Licensed under CC BY 4.0") < page.index("Open text body.")
    assert re.search(r'Text from <a href="https://example\.org/page"[^>]*>example\.org</a>, copied 2026-10-17\.', page)
    assert "This copy leaves out media that has no print form (math); see the original for them." in page
    # The LMS titles the page as h1, so the reading's own headings start at h2 and keep their order.
    assert "<h2>Intro</h2>" in page and "<h3>Part</h3>" in page and "<h1>" not in page


@pytest.mark.parametrize("flavor", ["canvas", "standard"])
def test_imscc_never_copies_link_only_text(cached, flavor):
    z = export_imscc(flavor)
    for name in z.namelist():
        assert b"SHOULD NEVER APPEAR" not in z.read(name), name
    # The unverified reading's cached PDF stays out; only the ND reading's PDF goes in.
    assert [n for n in z.namelist() if n.endswith(".pdf")] == ["web_resources/readings/reading-nd.pdf"]
    for rid in ("closed", "unverified"):
        page = z.read(f"wiki_content/reading-{rid}.html").decode()
        assert f'href="https://example.org/{rid}"' in page and "Text from" not in page


@pytest.mark.parametrize("flavor", ["canvas", "standard"])
def test_imscc_pdfs_are_the_cached_bytes(cached, flavor):
    z = export_imscc(flavor)
    path = "web_resources/readings/reading-nd.pdf"
    with cached.get("nd").open_pdf() as fh:
        assert z.read(path) == fh.read()
    page = z.read("wiki_content/reading-nd.html").decode()
    href = "$IMS-CC-FILEBASE$/readings/reading-nd.pdf" if flavor == "canvas" else "../web_resources/readings/reading-nd.pdf"
    assert f'href="{href}">PDF</a>' in page
    assert "The PDF (2 pages) is reproduced as published" in page
    # The PDF is a manifest resource of its own, and the reading page declares it depends on it.
    manifest = ET.fromstring(z.read("imsmanifest.xml"))
    by_href = {r.get("href"): r for r in manifest.iterfind(".//cc:resource", NS)}
    pdf_res = by_href[path]
    assert pdf_res.get("type") == "webcontent" and [f.get("href") for f in pdf_res.iterfind("cc:file", NS)] == [path]
    deps = [d.get("identifierref") for d in by_href["wiki_content/reading-nd.html"].iterfind("cc:dependency", NS)]
    assert deps == [pdf_res.get("identifier")]
