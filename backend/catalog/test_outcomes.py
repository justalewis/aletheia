import io
import json
from pathlib import Path

import pytest
import yaml
from django.conf import settings
from django.core.management import call_command
from django.test import Client

from catalog import outcomes
from catalog.models import (
    Arc, Assignment, Outcome, OutcomeSet, Pack, Reading, Unit, UnitAssignment, UnitReading,
)

CROSSWALK = """
weights: {core: 0.8, strong: 0.5, related: 0.25, arc_core: 1.5, arc_optional: 1.2, arc_focus: 3,
          arc_extra: 0.15, arc_unconfirmed: 0.5, map_threshold: 0.5}
codes:
  "wpa:processes":
    core: [revision, peer review]
    strong: [reflection]
    related: [genre]
  "wpa:rhetorical-knowledge":
    core: [audience]
    strong: [genre, revision]
  "acrl:authority":
    core: [source evaluation]
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def crosswalk(tmp_path) -> dict:
    cw, problems = outcomes.load_crosswalk(write(tmp_path / "crosswalk.yaml", CROSSWALK))
    assert problems == []
    return cw


# ---------------------------------------------------------------------------- crosswalk

def test_load_crosswalk_resolves_tiers(tmp_path):
    cw = crosswalk(tmp_path)
    assert cw["codes"]["wpa:processes"] == {"revision": 0.8, "peer review": 0.8, "reflection": 0.5, "genre": 0.25}
    assert cw["weights"]["arc_core"] == 1.5


def test_load_crosswalk_keeps_the_strongest_tier_and_warns(tmp_path):
    path = write(tmp_path / "cw.yaml", """
codes:
  "wpa:processes":
    core: [Revision]
    related: [revision, made up topic]
    sideways: [audience]
  "wpa:nonsense":
    core: [audience]
""")
    cw, problems = outcomes.load_crosswalk(path, known_codes={"wpa:processes"}, known_topics={"revision", "audience"})
    # Unknown topics are reported but kept, so a topic added to the catalog later starts working.
    assert cw["codes"]["wpa:processes"] == {"revision": 0.8, "made up topic": 0.25}
    assert any("wpa:nonsense" in p for p in problems)
    assert any("made up topic" in p for p in problems)
    assert any("sideways" in p for p in problems)


def test_missing_crosswalk_is_empty_not_an_error(tmp_path):
    cw, problems = outcomes.load_crosswalk(tmp_path / "nope.yaml")
    assert cw["codes"] == {} and cw["weights"]["map_threshold"] == 0.5
    assert problems


def test_real_crosswalk_covers_every_framework_code_with_catalog_topics():
    root = Path(settings.CORPUS_DIR) / "rhetcomp"
    fw = yaml.safe_load((root / "outcomes" / "frameworks.yaml").read_text(encoding="utf-8"))
    codes = {e["code"] for f in fw["frameworks"] for e in (f.get("categories") or f.get("frames") or [])}
    topics = set()
    for name in ("readings.jsonl", "assignments.jsonl"):
        for line in (root / name).read_text(encoding="utf-8").splitlines():
            if line.strip():
                topics |= {t.lower() for t in json.loads(line).get("topics") or []}
    cw, problems = outcomes.load_crosswalk(root / "outcomes" / "crosswalk.yaml", codes, topics)
    assert problems == []
    assert codes == set(cw["codes"]), "every wpa:, wpa4:, acrl:, and tc: code needs crosswalk entries"
    assert len(codes) == 4 + 8 + 6 + 8
    assert all(len(tmap) >= 10 for tmap in cw["codes"].values())


# ---------------------------------------------------------------------------- item scores

def test_item_scores_combine_topics_with_diminishing_returns(tmp_path):
    cw = crosswalk(tmp_path)
    w = outcomes.weights_of(cw)
    scores, evidence = outcomes.item_scores(["Revision", "peer review", "genre"], {}, outcomes.topic_index(cw), w)
    assert scores["wpa:processes"] == pytest.approx(1 - 0.2 * 0.2 * 0.75, abs=1e-3)
    assert evidence["wpa:processes"]["topics"] == ["peer review", "revision", "genre"]
    assert scores["wpa:rhetorical-knowledge"] == pytest.approx(0.75, abs=1e-3)  # genre + revision, both strong
    assert "acrl:authority" not in scores


def unit_ref(code_count: int, role: str = "core", w: float | None = None, arc="a1", unit=1) -> dict:
    return {"arc": arc, "arc_title": "Arc One", "unit": unit, "unit_title": "U", "role": role,
            "w": w if w is not None else 1.5 * min(1, 3 / code_count)}


def test_arc_evidence_outweighs_topics_and_needs_topic_agreement(tmp_path):
    cw = crosswalk(tmp_path)
    w, idx = outcomes.weights_of(cw), outcomes.topic_index(cw)
    topic_only, _ = outcomes.item_scores(["revision"], {}, idx, w)
    with_arc, ev = outcomes.item_scores(["revision"], {"wpa:processes": [unit_ref(2)]}, idx, w)
    arc_only, _ = outcomes.item_scores([], {"wpa:processes": [unit_ref(2)]}, idx, w)
    assert with_arc["wpa:processes"] == pytest.approx(0.8 + 1.5, abs=1e-3)
    assert arc_only["wpa:processes"] == pytest.approx(1.5 * 0.5, abs=1e-3)   # unconfirmed by topics: half
    assert with_arc["wpa:processes"] > topic_only["wpa:processes"]
    assert ev["wpa:processes"]["units"][0]["arc_title"] == "Arc One"
    # A second and third unit add a little; a fourth adds nothing more.
    units = [unit_ref(2, unit=i) for i in range(1, 5)]
    many, _ = outcomes.item_scores(["revision"], {"wpa:processes": units}, idx, w)
    assert many["wpa:processes"] == pytest.approx(0.8 + 1.5 + 2 * 0.15, abs=1e-3)


@pytest.fixture
def pack(db, tmp_path):
    p = Pack.objects.create(id="t", name="Test", version="0", config={}, crosswalk=crosswalk(tmp_path))
    fw = OutcomeSet.objects.create(id="wpa", pack=p, kind="framework", title="WPA", data={"categories": [
        {"code": "wpa:processes", "name": "Processes"}, {"code": "wpa:rhetorical-knowledge", "name": "Rhetorical Knowledge"}]})
    Outcome.objects.create(id="wpa:processes", outcome_set=fw, order=0, text="Processes: Writers use strategies.",
                           frameworks=["wpa:processes"])
    Outcome.objects.create(id="wpa:rhetorical-knowledge", outcome_set=fw, order=1, text="Rhetorical Knowledge: Audiences.",
                           frameworks=["wpa:rhetorical-knowledge"])
    acrl = OutcomeSet.objects.create(id="acrl", pack=p, kind="framework", title="ACRL", data={"frames": [
        {"code": "acrl:authority", "name": "Authority"}]})
    Outcome.objects.create(id="acrl:authority", outcome_set=acrl, order=0, text="Authority: context.",
                           frameworks=["acrl:authority"])
    course = OutcomeSet.objects.create(id="course", pack=p, kind="course", institution="U", course="ENG 1", title="Writing")
    Outcome.objects.create(id="course-1", outcome_set=course, order=0, text="Revise with peers.",
                           frameworks=["wpa:processes"], topics=["Peer Review", "collaboration"])
    OutcomeSet.objects.create(id="empty", pack=p, kind="course", institution="OC", course="ENGL 9", title="No outcomes")
    return p


def reading(p, rid, topics, **kw):
    defaults = dict(level="chapter", title=rid.replace("-", " ").title(), reuse_tier="adapt", license_verified=True,
                    audience="student", html_url=f"https://example.org/{rid}")
    defaults.update(kw)
    r = Reading(id=rid, pack=p, topics=topics, **defaults)
    r.fill_index_fields()
    r.save()
    return r


def assignment(p, aid, topics, **kw):
    defaults = dict(title=aid.replace("-", " ").title(), reuse_tier="adapt", license_verified=True,
                    kind="major assignment", url=f"https://example.org/{aid}")
    defaults.update(kw)
    a = Assignment(id=aid, pack=p, topics=topics, **defaults)
    a.fill_index_fields()
    a.save()
    return a


def rescore(p):
    outcomes.score_items(p, p.crosswalk)


def test_score_items_stores_scores_evidence_and_filter_text(pack):
    reading(pack, "rev", ["revision", "audience"])
    reading(pack, "none", ["citation"])
    arc = Arc.objects.create(id="arc", pack=pack, focus="FYC", title="Sample Arc")
    unit = Unit.objects.create(arc=arc, key="u1", order=1, title="Revising", outcomes=["wpa:processes"])
    UnitReading.objects.create(unit=unit, reading_id="none", role="optional")
    a = assignment(pack, "peer", ["peer review"])
    UnitAssignment.objects.create(unit=unit, assignment=a, role="major")
    rescore(pack)
    rev, none, a = Reading.objects.get(id="rev"), Reading.objects.get(id="none"), Assignment.objects.get(id="peer")
    assert set(rev.outcome_scores) == {"wpa:processes", "wpa:rhetorical-knowledge"}
    assert rev.outcomes_text == "|wpa:processes|wpa:rhetorical-knowledge|"
    assert none.outcome_scores == {"wpa:processes": pytest.approx(1.2 * 0.5)}
    assert none.outcome_evidence["wpa:processes"]["units"][0] == {
        "arc": "arc", "arc_title": "Sample Arc", "unit": 2, "unit_title": "Revising", "role": "optional"}
    assert a.outcome_scores["wpa:processes"] == pytest.approx(0.8 + 1.5)


def test_load_pack_scores_items_and_skips_the_crosswalk_file(db, tmp_path, settings):
    root = tmp_path / "corpus" / "mini"
    write(root / "pack.yaml", "id: mini\nname: Mini\nversion: 1\n")
    write(root / "readings.jsonl", "\n".join(json.dumps(r) for r in [
        {"id": "r1", "title": "Revising", "level": "chapter", "topics": ["revision"], "reuse_tier": "adapt", "license_verified": True},
        {"id": "r2", "title": "Audiences", "level": "chapter", "topics": ["audience"]},
    ]))
    write(root / "assignments.jsonl", json.dumps({"id": "a1", "title": "Peer review", "topics": ["peer review"]}))
    write(root / "outcomes" / "frameworks.yaml", yaml.safe_dump({"frameworks": [{"id": "wpa", "name": "WPA", "categories": [
        {"code": "wpa:processes", "name": "Processes", "definition": "Writers use strategies."},
        {"code": "wpa:rhetorical-knowledge", "name": "Rhetorical Knowledge", "definition": "Audiences."}]}]}))
    write(root / "outcomes" / "crosswalk.yaml", CROSSWALK)
    write(root / "arcs" / "arc.yaml", yaml.safe_dump({"id": "arc", "title": "Arc", "focus": "FYC", "units": [
        {"id": "u1", "title": "Audience", "outcomes": ["wpa:rhetorical-knowledge"], "readings": {"core": ["r2"]}}]}))
    settings.CORPUS_DIR = tmp_path / "corpus"
    out = io.StringIO()
    call_command("load_pack", "mini", stdout=out)
    assert "1 outcome sets" in out.getvalue()
    assert not OutcomeSet.objects.filter(id="crosswalk").exists()
    p = Pack.objects.get(id="mini")
    assert p.crosswalk["codes"]["wpa:processes"]["revision"] == 0.8
    assert "acrl:authority" in out.getvalue()  # crosswalk code missing from frameworks.yaml is reported
    r2 = Reading.objects.get(id="r2")
    assert r2.outcome_scores["wpa:rhetorical-knowledge"] == pytest.approx(0.8 + 1.5)
    assert Assignment.objects.get(id="a1").outcomes_text == "|wpa:processes|"


# ---------------------------------------------------------------------------- ranking

def test_quality_factors_and_deterministic_ties(pack):
    reading(pack, "b-open-student", ["revision"])
    reading(pack, "a-open-student", ["revision"])          # same score as b: title breaks the tie
    reading(pack, "closed", ["revision"], reuse_tier="link-only")
    reading(pack, "unverified", ["revision"], license_verified=False)
    reading(pack, "teacher", ["revision"], audience="instructor")
    reading(pack, "whole-book", ["revision"], level="book")
    reading(pack, "long", ["revision"], pages=60)
    rescore(pack)
    spec = outcomes.specs_for_ids(pack, ["wpa:processes"])[0][0]
    ranked = outcomes.rank(Reading.objects.filter(pack=pack), spec)
    order = [s.item.id for s in ranked]
    assert order[:2] == ["a-open-student", "b-open-student"]
    assert order.index("closed") > 1 and order.index("unverified") > 1
    assert order.index("long") < order.index("whole-book")
    assert order[-1] == "whole-book"
    assert ranked == outcomes.rank(list(Reading.objects.filter(pack=pack).order_by("-id")), spec)


def test_course_outcome_uses_its_own_topics(pack):
    reading(pack, "peers", ["peer review", "collaboration"])
    reading(pack, "reflect", ["reflection"])
    reading(pack, "collab-only", ["collaboration"])  # no crosswalk code, but named by the course outcome
    rescore(pack)
    spec = outcomes.specs_for_ids(pack, ["course-1"])[0][0]
    ranked = outcomes.rank(Reading.objects.filter(pack=pack), spec)
    # "reflect" matches only through the outcome's broad framework code, which counts for less once the
    # outcome names its own topics, so it falls below the threshold; naming a topic is enough.
    assert [s.item.id for s in ranked] == ["peers", "collab-only"]
    assert ranked[0].evidence.why().startswith("Topics: peer review, collaboration")


def test_diversity_cap_per_book_and_series(pack):
    for i in range(6):
        reading(pack, f"vol1-ch{i}", ["revision"], parent_title="Writing Spaces: Readings on Writing, Volume 1")
    reading(pack, "vol2-ch", ["revision"], parent_title="Writing Spaces: Readings on Writing, Volume 2")
    reading(pack, "other-1", ["revision", "reflection"], parent_title="Another Book", audience="both")
    reading(pack, "other-2", ["revision"], parent_title="A Third Book", reuse_tier="link-only")
    rescore(pack)
    spec = outcomes.specs_for_ids(pack, ["wpa:processes"])[0][0]
    ranked = outcomes.rank(Reading.objects.filter(pack=pack), spec)
    picked = outcomes.diversify(ranked, 4, cap=2)
    books = [outcomes.group_key(s.item) for s in picked]
    assert len(picked) == 4
    assert max(books.count(b) for b in set(books)) <= 2
    assert {"other-1", "other-2"} <= {s.item.id for s in picked}
    # With nothing else to choose from, the list fills up from the capped book.
    assert len(outcomes.diversify(ranked, 9, cap=2)) == 9
    assert outcomes.series_title("constellations: a cultural rhetorics publishing space, Issue 4") == \
        "constellations: a cultural rhetorics publishing space"
    assert outcomes.series_title("Bad Ideas About Writing") == "bad ideas about writing"


# ---------------------------------------------------------------------------- /api/recommend

def post(path, body):
    return Client().post(path, body, content_type="application/json")


def test_recommend_by_outcome_set(pack):
    reading(pack, "rev", ["revision", "peer review"])
    reading(pack, "aud", ["audience"])
    reading(pack, "src", ["source evaluation"])
    assignment(pack, "peer", ["peer review"])
    rescore(pack)
    data = post("/api/recommend", {"outcome_set_id": "wpa"}).json()
    assert data["outcome_set"]["id"] == "wpa" and data["outcome_set"]["count"] == 2
    first, second = data["outcomes"]
    assert (first["id"], first["label"], first["text"]) == ("wpa:processes", "Processes", "Writers use strategies.")
    assert [r["id"] for r in first["readings"]] == ["rev"]
    assert first["readings"][0]["why"] == "Topics: peer review, revision"
    assert first["readings"][0]["est_pages"] and first["readings"][0]["reuse_tier"] == "adapt"
    assert [a["id"] for a in first["assignments"]] == ["peer"]
    assert first["assignments"][0]["kind"] == "major assignment" and first["assignments"][0]["item_type"] == "assignment"
    assert first["total"] == {"readings": 1, "assignments": 1}
    assert [r["id"] for r in second["readings"]] == ["aud", "rev"]   # revision is a strong topic for RK


def test_recommend_filters_and_excludes(pack):
    reading(pack, "rev-ch", ["revision"], skins=["FYC"])
    reading(pack, "rev-book", ["revision"], level="book", skins=["FYC"])
    reading(pack, "rev-tc", ["revision"], skins=["TECHCOMM"])
    reading(pack, "rev-closed", ["revision"], skins=["FYC"], reuse_tier="link-only")
    rescore(pack)
    ids = lambda body: [r["id"] for r in post("/api/recommend", body).json()["outcomes"][0]["readings"]]  # noqa: E731
    base = {"outcome_ids": ["wpa:processes"]}
    assert set(ids(base)) == {"rev-ch", "rev-book", "rev-tc", "rev-closed"}
    assert "rev-tc" not in ids({**base, "focus": "FYC"})
    assert ids({**base, "levels": ["book"]}) == ["rev-book"]
    assert "rev-closed" not in ids({**base, "tiers": ["adapt"]})
    assert ids({**base, "tiers": ["link-only"]}) == ["rev-closed"]
    assert "rev-ch" not in ids({**base, "exclude_ids": ["rev-ch"]})
    assert len(ids({**base, "per_outcome": 2})) == 2
    assert len(ids({**base, "per_outcome": 0})) == 1


def test_recommend_unknown_and_empty(pack):
    assert post("/api/recommend", {"outcome_set_id": "nope"}).status_code == 404
    assert post("/api/recommend", {"outcome_ids": ["nope"]}).status_code == 404
    assert post("/api/recommend", {}).status_code == 400
    partial = post("/api/recommend", {"outcome_ids": ["nope", "wpa:processes"]}).json()
    assert partial["unknown_ids"] == ["nope"] and [o["id"] for o in partial["outcomes"]] == ["wpa:processes"]
    empty = post("/api/recommend", {"outcome_set_id": "empty"}).json()
    assert empty["outcome_set"]["count"] == 0 and empty["outcomes"] == []


# ---------------------------------------------------------------------------- /api/coverage

def coverage_state(**extra):
    state = {
        "title": "Test", "focus": "FYC",
        "units": [
            {"key": "u1", "title": "Audiences", "topics": ["audience"], "outcomes": [],
             "readings": [{"id": "aud", "selected": True}, {"id": "rev", "selected": False}],
             "assignments": [{"id": "peer", "role": "major", "selected": True}]},
            {"key": "u2", "title": "Revision", "topics": [], "outcomes": ["wpa:processes"],
             "readings": [{"id": "aud2", "selected": True}], "assignments": []},
            {"key": "u3", "title": "Sources", "topics": ["source evaluation"], "outcomes": [],
             "readings": [{"id": "missing-from-catalog", "selected": True}], "assignments": []},
        ],
    }
    state.update(extra)
    return state


@pytest.fixture
def course(pack):
    reading(pack, "aud", ["audience"])
    reading(pack, "aud2", ["audience", "genre"])
    reading(pack, "rev", ["revision", "peer review"])
    reading(pack, "rev2", ["revision"], parent_title="Book B")
    reading(pack, "src", ["source evaluation"])
    reading(pack, "src2", ["source evaluation"], parent_title="Book C")
    assignment(pack, "peer", ["peer review"])
    rescore(pack)
    return pack


def test_coverage_matrix_totals_and_gaps(course):
    data = post("/api/coverage", {"state": coverage_state(), "outcome_set_ids": ["wpa", "acrl"]}).json()
    assert [u["key"] for u in data["units"]] == ["u1", "u2", "u3"]
    assert [s["id"] for s in data["sets"]] == ["wpa", "acrl"]
    rows = {r["id"]: r for r in data["outcomes"]}
    proc, rk, auth = rows["wpa:processes"], rows["wpa:rhetorical-knowledge"], rows["acrl:authority"]
    assert [c["count"] for c in rk["cells"]] == [1, 1, 0]
    assert rk["cells"][0]["reading_ids"] == ["aud"] and rk["total"] == 2 and rk["status"] == "ok"
    assert [c["count"] for c in proc["cells"]] == [1, 0, 0]           # "rev" is unchecked; aud2's genre is too weak
    assert proc["cells"][0]["assignment_ids"] == ["peer"] and proc["status"] == "weak"
    assert auth["total"] == 0 and auth["status"] == "none"
    gaps = {g["outcome_id"]: g for g in data["gaps"]}
    assert set(gaps) == {"wpa:processes", "acrl:authority"}
    # The unit that lists the outcome's code is the place to fill the gap.
    assert gaps["wpa:processes"]["unit_key"] == "u2"
    assert [s["id"] for s in gaps["wpa:processes"]["suggestions"]][:2] == ["rev", "rev2"]
    assert gaps["acrl:authority"]["unit_key"] == "u3"
    sugg = gaps["acrl:authority"]["suggestions"]
    assert {s["id"] for s in sugg} == {"src", "src2"} and all(s["item_type"] == "reading" for s in sugg)
    assert "Fits the Sources unit: source evaluation" in sugg[0]["why"]
    selected = {"aud", "aud2", "peer"}
    assert not any(s["id"] in selected for g in data["gaps"] for s in g["suggestions"])


def test_coverage_empty_and_unknown_sets(course):
    data = post("/api/coverage", {"state": coverage_state(), "outcome_set_ids": ["empty", "nope"]}).json()
    assert data["sets"] == [{"id": "empty", "kind": "course", "title": "No outcomes", "institution": "OC",
                             "course": "ENGL 9", "count": 0, "source_url": ""}]
    assert data["unknown_set_ids"] == ["nope"]
    assert data["outcomes"] == [] and data["gaps"] == []
    none = post("/api/coverage", {"state": coverage_state()}).json()
    assert none["sets"] == [] and none["outcomes"] == []
    from_state = post("/api/coverage", {"state": coverage_state(outcome_set_ids=["course"])}).json()
    assert [r["id"] for r in from_state["outcomes"]] == ["course-1"]
    assert from_state["outcomes"][0]["cells"][0]["assignment_ids"] == ["peer"]


def test_coverage_handles_an_empty_course(course):
    data = post("/api/coverage", {"state": {"units": []}, "outcome_set_ids": ["wpa"]}).json()
    assert data["units"] == []
    assert all(r["status"] == "none" and r["cells"] == [] for r in data["outcomes"])
    assert all(g["unit_key"] is None and g["suggestions"] for g in data["gaps"])
