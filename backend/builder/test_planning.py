from builder.planning import Term, allocate_weeks, fit_units, plan_course


def unit(key, mn, ideal, priority="core", merge="", core=("r1",), optional=(), major=(), short=(), acts=()):
    return {
        "key": key, "title": key.title(), "min_weeks": mn, "ideal_weeks": ideal, "priority": priority,
        "merge_with": merge, "goals": [], "outcomes": [],
        "readings": {"core": list(core), "optional": list(optional)},
        "assignments": {"major": list(major), "short_term": list(short), "activity": list(acts)},
    }


INQUIRY = [
    unit("questions", 2, 3, merge="next"),
    unit("sources", 2, 3, merge="previous"),
    unit("annotated-bib", 2, 3, major=("ab",), short=("ab-short",)),
    unit("primary", 2, 3, priority="optional"),
    unit("research-essay", 2, 2, major=("essay",), short=("essay-short",)),
]


def test_semester_keeps_every_unit():
    plan = plan_course(INQUIRY, Term(16, 1, 1, 40))
    assert [u["key"] for u in plan["units"]] == [u["key"] for u in INQUIRY]
    assert sum(u["weeks"] for u in plan["units"]) == 14
    assert len(plan["weeks"]) == 16
    assert plan["dropped"] == [] and plan["merged"] == []


def test_weeks_are_contiguous_and_numbered():
    plan = plan_course(INQUIRY, Term(10, 0, 1, 50))
    assert [w["n"] for w in plan["weeks"]] == list(range(1, 11))
    starts = [u["start_week"] for u in plan["units"]]
    assert starts == sorted(starts) and starts[0] == 1


def test_quarter_drops_optional_unit_first():
    plan = plan_course(INQUIRY, Term(10, 0, 1, 50))
    assert plan["dropped"] == ["primary"]
    assert sum(u["weeks"] for u in plan["units"]) == 9
    assert all(u["weeks"] >= 2 for u in plan["units"])


def test_five_weeks_merges_and_uses_short_variants():
    plan = plan_course(INQUIRY, Term(5, 0, 0, 75))
    assert "primary" in plan["dropped"]
    assert plan["merged"], "expected mergeable units to be folded together"
    majors = [a for u in plan["units"] for a in u["assignments"] if a["selected"] and a["role"] != "activity"]
    assert {a["id"] for a in majors} <= {"ab-short", "essay-short"}
    assert sum(u["weeks"] for u in plan["units"]) == 5


def test_allocation_respects_minimums_and_total():
    units = [unit("a", 3, 2), unit("b", 1, 6), unit("c", 1, 6)]
    alloc = allocate_weeks(units, 10)
    assert sum(alloc) == 10 and alloc[0] >= 3


def test_optional_readings_fill_page_budget():
    u = [unit("only", 1, 2, core=("c",), optional=("o1", "o2", "o3"))]
    readings = {"c": {"pages": 20}, "o1": {"pages": 20}, "o2": {"pages": 20}, "o3": {"pages": 20}}
    plan = plan_course(u, Term(3, 1, 0, 30), readings)  # 2 unit weeks x 30 pages = 60
    chosen = [r["id"] for r in plan["units"][0]["readings"] if r["selected"]]
    assert chosen == ["c", "o1", "o2"]
    assert plan["units"][0]["heavy"] is False


def test_core_readings_always_selected_and_flag_heavy_weeks():
    u = [unit("dense", 1, 1, core=("big",))]
    plan = plan_course(u, Term(1, 0, 0, 10), {"big": {"pages": 80}})
    assert plan["units"][0]["readings"][0]["selected"] is True
    assert plan["units"][0]["heavy"] is True


def test_impossible_fit_squeezes_instead_of_failing():
    units, dropped, merged = fit_units([unit("a", 3, 3), unit("b", 3, 3)], 2)
    assert sum(u["min_weeks"] for u in units) <= 2
