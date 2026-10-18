"""Start from your course: theme and outcomes in, a planned course out."""
import pytest
from django.test import Client

from builder import autobuild
from catalog.models import Arc, Pack, Reading, Unit, UnitReading


def test_tokens_stem_and_drop_filler():
    assert autobuild.tokens("Technologies of the Labor Movements") == ["technology", "labor", "movement"]
    assert autobuild.tokens("food justice and writing", drop_domain=True) == ["food", "justice"]
    assert autobuild.stem("ies") == "ies" and autobuild.stem("class") == "class"


def test_outcome_lines_lose_numbering_and_stems():
    text = "Students will:\n1. Analyze audience and purpose.\n- Design accessible documents.\n  \nok"
    assert autobuild.clean_outcome_lines(text) == ["Analyze audience and purpose.", "Design accessible documents."]


@pytest.fixture
def catalog(db):
    pack = Pack.objects.create(id="t", name="Test", version="0", config={"focuses": [{"code": "FYC", "label": "First-Year"}]})

    def reading(rid, title, topics, summary="", **kw):
        r = Reading(id=rid, pack=pack, level="chapter", title=title, topics=topics, summary=summary, audience="student",
                    course_level="100", license="CC BY 4.0", reuse_tier="adapt", license_verified=True, skins=["FYC"], **kw)
        r.fill_index_fields()
        r.save()
        return r

    for rid, title, topics in [
        ("core-a", "Reading the Rhetorical Situation", ["rhetorical situation"]),
        ("core-b", "Writing about Food Systems", ["food", "ethics"]),
        ("food-1", "Food Justice in Cities", ["food justice", "cities"]),
        ("food-2", "Who Gets to Eat? Food Access", ["food justice"]),
        ("other", "Citing Sources", ["citation"]),
    ]:
        reading(rid, title, topics)
    for key, title, topics, core in [("situation", "The Rhetorical Situation", ["rhetorical situation"], "core-a"),
                                     ("sources", "Sources and Citation", ["citation"], "other")]:
        arc = Arc.objects.filter(id="arc-" + key).first() or Arc.objects.create(
            id="arc-" + key, pack=pack, focus="FYC", title=title, tagline=f"A course on {title.lower()}.", course_level="100")
        unit = Unit.objects.create(arc=arc, key=key, order=0, title=title, summary=title, topics=topics, min_weeks=1,
                                   ideal_weeks=3, priority="core")
        UnitReading.objects.create(unit=unit, reading_id=core, role="core", order=0)
    return pack


def build(**body):
    return Client().post("/api/auto-build", {"weeks": 10, **body}, content_type="application/json")


def test_theme_readings_join_the_best_arc(catalog):
    r = build(theme="food justice", focus="FYC")
    assert r.status_code == 200
    d = r.json()
    # A two-word theme needs both words: "Writing about Food Systems" has only one, so it stays out.
    assert [t["id"] for t in d["theme_added"]] == ["food-1", "food-2"]
    added = [x for u in d["state"]["units"] for x in u["readings"] if x["role"] == "added"]
    assert {x["id"] for x in added} == {"food-1", "food-2"} and all(x["selected"] and x["added"] for x in added)
    assert d["state"]["arc_id"] == d["arc"]["id"] and d["state"]["term"]["weeks"] == 10


def test_outcomes_pick_the_arc_and_report_coverage(catalog):
    d = build(outcomes_text="1. Analyze the rhetorical situation of a text.").json()
    assert d["arc"]["id"] == "arc-situation"
    assert d["outcomes"][0]["matched_topics"] == ["rhetorical situation"]


def test_a_request_with_nothing_to_go_on_is_refused(catalog):
    r = build()
    assert r.status_code == 400 and "theme" in r.json()["detail"]
    assert build(theme="food", focus="NOPE").status_code == 400
    assert build(theme="food", weeks=0).status_code == 400
