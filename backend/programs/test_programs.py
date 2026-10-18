import csv
import io
from types import SimpleNamespace

import pytest
from django.test import Client

from builder.models import Build
from catalog.models import Assignment, Pack, Reading
from programs import ztc
from programs.models import Program, ProgramArc


@pytest.fixture
def catalog(db):
    pack = Pack.objects.create(id="t", name="Test", version="0", config={})
    for rid, tier, verified in [("open", "adapt", True), ("closed", "link-only", True), ("unverified", "adapt", False)]:
        Reading.objects.create(id=rid, pack=pack, level="chapter", title=f"Reading {rid}", license="CC BY 4.0",
                               license_family="CC BY", reuse_tier=tier, license_verified=verified)
    Assignment.objects.create(id="essay", pack=pack, title="Research essay", license="CC BY-NC 4.0",
                              license_family="CC BY-NC", reuse_tier="adapt-nc", license_verified=True)
    return pack


def course(readings=("open", "closed"), assignments=("essay",), title="ENGL&101: Inquiry", **extra):
    return {
        "title": title, "instructor": "Lee Park", "focus": "FYC", "arc_id": None,
        "term": {"weeks": 10, "fixed_start": 0, "fixed_end": 1, "pages_per_week": 50},
        "units": [{"key": "u1", "title": "Unit 1", "weeks": 9, "start_week": 1, "end_week": 9,
                   "readings": [{"id": r, "selected": True} for r in readings] + [{"id": "skipped", "selected": False}],
                   "assignments": [{"id": a, "role": "major", "selected": True} for a in assignments]}],
        **extra,
    }


def post(client, url, body, token=None):
    headers = {"HTTP_X_PROGRAM_TOKEN": token} if token else {}
    return client.post(url, body, content_type="application/json", **headers)


def put(client, url, body, token=None, header="HTTP_X_PROGRAM_TOKEN"):
    headers = {header: token} if token else {}
    return client.put(url, body, content_type="application/json", **headers)


def get(client, url, token=None):
    return client.get(url, **({"HTTP_X_PROGRAM_TOKEN": token} if token else {}))


@pytest.fixture
def program(catalog):
    c = Client()
    r = post(c, "/api/programs", {"name": "Olympic College English: ENGL&101", "institution": "Olympic College",
                                  "description": "Shared courses for ENGL&101."})
    assert r.status_code == 200
    data = r.json()
    return SimpleNamespace(client=c, id=data["id"], token=data["admin_token"])


def save_section(client, program_id, state, section):
    r = client.post("/api/builds", {"state": state, "program_id": program_id, "section": section},
                    content_type="application/json")
    assert r.status_code == 200, r.content
    return r.json()


# ---------------------------------------------------------------- tokens

def test_create_program_needs_a_name(catalog):
    assert post(Client(), "/api/programs", {"name": "  "}).status_code == 400


def test_public_page_needs_no_token_and_hides_the_token(program):
    data = get(program.client, f"/api/programs/{program.id}").json()
    assert data["name"] == "Olympic College English: ENGL&101"
    assert data["arcs"] == []
    assert data["settings"]["textbook_cost_per_student"] == 100.0
    assert "admin_token" not in data and program.token not in str(data)


@pytest.mark.parametrize("method,path", [
    ("put", ""), ("get", "/arcs"), ("post", "/arcs"), ("get", "/sections"), ("get", "/report"),
    ("get", "/report?format=csv"),
])
def test_admin_endpoints_refuse_missing_or_wrong_token(program, method, path):
    url = f"/api/programs/{program.id}{path}"
    for token in (None, "wrong-token"):
        if method == "get":
            r = get(program.client, url, token)
        elif method == "put":
            r = put(program.client, url, {"name": "Taken over"}, token)
        else:
            r = post(program.client, url, {"state": course()}, token)
        assert r.status_code == 403, (method, path, token)
    assert Program.objects.get(id=program.id).name != "Taken over"


def test_arc_edits_need_the_token(program):
    arc = post(program.client, f"/api/programs/{program.id}/arcs", {"state": course()}, program.token).json()
    url = f"/api/programs/{program.id}/arcs/{arc['id']}"
    assert put(program.client, url, {"published": True}, "nope").status_code == 403
    assert program.client.delete(url).status_code == 403
    assert ProgramArc.objects.filter(id=arc["id"]).exists()
    # Another program's token doesn't work here either.
    other = post(program.client, "/api/programs", {"name": "Other"}).json()
    assert put(program.client, url, {"published": True}, other["admin_token"]).status_code == 403


def test_unknown_or_malformed_program_is_404(program):
    assert get(program.client, "/api/programs/not-a-uuid").status_code == 404
    assert get(program.client, "/api/programs/00000000-0000-0000-0000-000000000000").status_code == 404


def test_update_program_settings(program):
    url = f"/api/programs/{program.id}"
    r = put(program.client, url, {"name": "ENGL&101 at OC", "settings": {
        "textbook_cost_per_student": "120.5", "terms": ["Fall 2026", "fall 2026", " Winter 2027 "]}}, program.token)
    assert r.status_code == 200
    s = r.json()["settings"]
    assert s == {"textbook_cost_per_student": 120.5, "currency": "USD", "terms": ["Fall 2026", "Winter 2027"]}
    assert put(program.client, url, {"settings": {"textbook_cost_per_student": -5}}, program.token).status_code == 400
    assert put(program.client, url, {"settings": {"currency": "dollars"}}, program.token).status_code == 400


# ---------------------------------------------------------------- arcs and the fork flow

def test_fork_flow_from_saved_build_to_linked_section(program):
    c, pid, tok = program.client, program.id, program.token
    # A WPA saves a course in the builder, then adds it to the program by pasting its link.
    saved = c.post("/api/builds", {"state": course(section={"course_code": "stale"})},
                   content_type="application/json").json()
    r = post(c, f"/api/programs/{pid}/arcs", {"build_id": f"http://localhost/build/{saved['id']}#edit=x",
                                              "summary": "Inquiry-based first-year course."}, tok)
    assert r.status_code == 200, r.content
    arc = r.json()
    assert arc["title"] == "ENGL&101: Inquiry" and arc["published"] is False and arc["unit_count"] == 1
    assert arc["source_build"] == saved["id"]

    # Unpublished: not on the public page, and not fetchable without the token.
    assert get(c, f"/api/programs/{pid}").json()["arcs"] == []
    assert get(c, f"/api/programs/{pid}/arcs/{arc['id']}").status_code == 404
    assert get(c, f"/api/programs/{pid}/arcs/{arc['id']}", tok).status_code == 200

    assert put(c, f"/api/programs/{pid}/arcs/{arc['id']}", {"published": True}, tok).status_code == 200
    public = get(c, f"/api/programs/{pid}").json()
    assert [a["id"] for a in public["arcs"]] == [arc["id"]]
    assert public["arcs"][0]["unit_count"] == 1 and public["arcs"][0]["summary"].startswith("Inquiry")

    # An instructor uses the course: the arc's state (minus anyone's section info) seeds a new build.
    detail = get(c, f"/api/programs/{pid}/arcs/{arc['id']}").json()
    assert "section" not in detail["state"] and detail["program"]["id"] == pid
    state = {**detail["state"], "program": {"id": pid, "name": public["name"], "course": arc["id"]}}
    created = save_section(c, pid, state, {"course_code": " ENGL&101 ", "section": "03", "term": "Fall 2026",
                                           "enrollment": "25", "instructor": "Lee Park"})
    b = Build.objects.get(id=created["id"])
    assert str(b.program_id) == pid
    assert b.section == {"course_code": "ENGL&101", "section": "03", "term": "Fall 2026", "instructor": "Lee Park",
                         "enrollment": 25}
    got = c.get(f"/api/builds/{b.id}").json()
    assert got["program_id"] == pid and got["section"]["enrollment"] == 25

    sections = get(c, f"/api/programs/{pid}/sections", tok).json()
    assert [s["id"] for s in sections] == [str(b.id)]
    assert sections[0]["readings"] == 2 and sections[0]["assignments"] == 1

    # Updating keeps the link unless the request changes it; sending program_id null unlinks.
    r = put(c, f"/api/builds/{b.id}", {"state": state, "section": {**b.section, "enrollment": 27}},
            created["edit_token"], header="HTTP_X_EDIT_TOKEN")
    assert r.status_code == 200
    b.refresh_from_db()
    assert str(b.program_id) == pid and b.section["enrollment"] == 27
    r = put(c, f"/api/builds/{b.id}", {"state": state, "program_id": None}, created["edit_token"],
            header="HTTP_X_EDIT_TOKEN")
    b.refresh_from_db()
    assert b.program_id is None and b.section == {}


def test_bad_enrollment_is_refused(program):
    r = program.client.post("/api/builds", {"state": course(), "program_id": program.id,
                                            "section": {"enrollment": "lots"}}, content_type="application/json")
    assert r.status_code == 400
    assert not Build.objects.exists()


def test_arc_from_state_reorder_and_delete(program):
    c, pid, tok = program.client, program.id, program.token
    a1 = post(c, f"/api/programs/{pid}/arcs", {"state": course(title="One"), "published": True}, tok).json()
    a2 = post(c, f"/api/programs/{pid}/arcs", {"state": course(title="Two"), "title": "Second course",
                                               "published": True}, tok).json()
    assert a2["title"] == "Second course" and a2["order"] > a1["order"]
    assert post(c, f"/api/programs/{pid}/arcs", {"state": {"title": "No units"}}, tok).status_code == 400
    put(c, f"/api/programs/{pid}/arcs/{a1['id']}", {"order": 5}, tok)
    put(c, f"/api/programs/{pid}/arcs/{a2['id']}", {"order": 1, "summary": "Short"}, tok)
    assert [a["title"] for a in get(c, f"/api/programs/{pid}").json()["arcs"]] == ["Second course", "One"]
    assert c.delete(f"/api/programs/{pid}/arcs/{a1['id']}", HTTP_X_PROGRAM_TOKEN=tok).status_code == 200
    assert [a["id"] for a in get(c, f"/api/programs/{pid}/arcs", tok).json()] == [a2["id"]]


# ---------------------------------------------------------------- the report

def make_sections(program):
    c, pid = program.client, program.id
    save_section(c, pid, course(), {"course_code": "ENGL&101", "section": "01", "term": "Fall 2026",
                                    "enrollment": 25, "instructor": "Lee Park"})
    # Includes an id the catalog doesn't have, so it can't be counted as zero cost.
    save_section(c, pid, course(readings=("open", "mystery-reading"), title="Other"),
                 {"course_code": "ENGL&101", "section": "02", "term": "Fall 2026", "enrollment": 30})
    save_section(c, pid, course(readings=("unverified",), assignments=()),
                 {"course_code": "ENGL&101", "section": "01", "term": "Winter 2027", "enrollment": 22})
    # Saved without a program: never in the report.
    c.post("/api/builds", {"state": course()}, content_type="application/json")


def test_report_math(program):
    make_sections(program)
    put(program.client, f"/api/programs/{program.id}", {"settings": {"textbook_cost_per_student": 120.5}},
        program.token)
    rep = get(program.client, f"/api/programs/{program.id}/report", program.token).json()
    rows = {(r["section"], r["term"]): r for r in rep["rows"]}
    assert len(rows) == 3
    first = rows[("01", "Fall 2026")]
    assert first["zero_cost"] is True and first["readings"] == 2 and first["assignments"] == 1
    assert first["open"] == 2 and first["link_only"] == 1  # the essay is open; "closed" is link-only
    assert first["savings"] == 3012.5 and first["instructor"] == "Lee Park"
    second = rows[("02", "Fall 2026")]
    assert second["zero_cost"] is False and second["savings"] == 0
    assert [i["id"] for i in second["not_free"]] == ["mystery-reading"]
    assert second["instructor"] == "Lee Park"  # falls back to the course's instructor field
    third = rows[("01", "Winter 2027")]
    assert third["zero_cost"] is True and third["link_only"] == 1 and third["savings"] == 2651.0
    assert rep["totals"] == {"sections": 3, "students": 77, "zero_cost_sections": 2, "zero_cost_students": 47,
                             "savings": 5663.5}
    assert rep["terms"] == ["Fall 2026", "Winter 2027"]

    fall = get(program.client, f"/api/programs/{program.id}/report?term=fall%202026", program.token).json()
    assert fall["totals"]["sections"] == 2 and fall["totals"]["savings"] == 3012.5
    assert fall["terms"] == ["Fall 2026", "Winter 2027"]


def test_report_flags_empty_courses_and_duplicate_sections(program):
    c, pid = program.client, program.id
    save_section(c, pid, course(readings=(), assignments=()), {"course_code": "ENGL&101", "section": "05",
                                                              "term": "Fall 2026", "enrollment": 20})
    save_section(c, pid, course(), {"course_code": "engl&101", "section": "05", "term": "Fall 2026", "enrollment": 20})
    rep = get(c, f"/api/programs/{pid}/report", program.token).json()
    empty = next(r for r in rep["rows"] if r["readings"] == 0)
    assert empty["zero_cost"] is False and empty["note"]
    assert rep["totals"]["savings"] == 2000.0
    assert len(rep["warnings"]) == 1 and "section 05" in rep["warnings"][0]


def test_report_csv(program):
    make_sections(program)
    save_section(program.client, program.id, course(title="=HYPERLINK(\"http://x\")"),
                 {"course_code": "ENGL&102", "section": "01", "term": "Fall 2026", "enrollment": 10,
                  "instructor": "@evil"})
    r = get(program.client, f"/api/programs/{program.id}/report?format=csv&term=Fall%202026", program.token)
    assert r.status_code == 200
    assert r["Content-Type"].startswith("text/csv")
    assert r["Content-Disposition"] == 'attachment; filename="olympic-college-english-engl-101-ztc-report-fall-2026.csv"'
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][:5] == ["Course", "Section", "Term", "Instructor", "Enrollment"]
    assert rows[0][11] == "Estimated student savings (USD)"
    body = [row for row in rows[1:] if row and row[0] not in ("Total", "Textbook cost per student used for savings")]
    assert len(body) == 3
    by_section = {(row[0], row[1]): row for row in body}
    assert by_section[("ENGL&101", "01")][7] == "yes" and by_section[("ENGL&101", "01")][11] == "2500.00"
    assert by_section[("ENGL&101", "02")][7] == "no" and by_section[("ENGL&101", "02")][10] == "mystery-reading"
    evil = by_section[("ENGL&102", "01")]
    assert evil[3] == "'@evil" and evil[12].startswith("'=")
    assert evil[13].startswith("http://testserver/build/")
    total = next(row for row in rows if row and row[0] == "Total")
    assert total[4] == "65" and total[11] == "3500.00"


# ---------------------------------------------------------------- the rule itself

def test_zero_cost_rule_is_extensible():
    free = SimpleNamespace(effective_tier="adapt")
    paid = SimpleNamespace(effective_tier="link-only", access="paid")
    assert ztc.zero_cost([ztc.Selected("reading", "a", free)]).zero_cost is True
    check = ztc.zero_cost([ztc.Selected("reading", "a", free), ztc.Selected("reading", "b", paid),
                           ztc.Selected("assignment", "c", None)])
    assert check.zero_cost is False
    assert [(i["id"], i["cost"]) for i in check.not_free] == [("b", "paid"), ("c", "unknown")]
    assert ztc.zero_cost([]).zero_cost is False
