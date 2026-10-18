import io
import zipfile
from xml.etree import ElementTree as ET

import pytest
from django.test import Client

from catalog.models import Assignment, Pack, Reading


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


@pytest.mark.parametrize("flavor", ["canvas", "standard"])
def test_imscc_package_is_well_formed(catalog, flavor):
    resp = Client().post(f"/api/export/imscc?flavor={flavor}", {"state": STATE}, content_type="application/json")
    assert resp.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    names = z.namelist()
    manifest = ET.fromstring(z.read("imsmanifest.xml"))
    ns = {"cc": "http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1"}
    resources = {r.get("identifier") for r in manifest.iterfind(".//cc:resource", ns)}
    refs = [i.get("identifierref") for i in manifest.iterfind(".//cc:item", ns) if i.get("identifierref")]
    assert refs and all(r in resources for r in refs), "every module item must point at a resource"
    for r in manifest.iterfind(".//cc:resource", ns):
        for f in r.iterfind("cc:file", ns):
            assert f.get("href") in names
    if flavor == "canvas":
        assert "course_settings/canvas_export.txt" in names
        ET.fromstring(z.read("course_settings/module_meta.xml"))
        assert any(n.endswith("assignment_settings.xml") for n in names)
    else:
        assert not any(n.startswith("course_settings/") for n in names)
