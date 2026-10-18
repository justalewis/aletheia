"""Fit a course arc to a term.

Pure functions over plain dicts so the rules are easy to test and reuse. The inputs are an
arc's units and a term (length in weeks, fixed weeks at either end, a pages-per-week budget);
the output is a week-by-week plan with each unit's readings and assignments marked as
selected (pre-checked) or suggested (unchecked).

Rules, in order:
1. Reserve the term's fixed weeks (orientation, final portfolio/exam).
2. While the units' minimum weeks don't fit, drop optional units (last first), then fold
   mergeable units into their neighbor.
3. Split the remaining weeks in proportion to each unit's ideal weeks (written against a
   16-week semester), never below a unit's minimum.
4. Core readings are always selected; optional readings are selected in order while they
   fit the unit's page budget (weeks x pages per week).
5. Terms of 8 weeks or fewer swap in a unit's short-term assignment variant when it has one.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass

DEFAULT_PAGES = {"chapter": 15, "article": 12, "primary-text": 8, "website": 10, "book": 120}
SHORT_TERM_WEEKS = 8


@dataclass(frozen=True)
class Term:
    weeks: int
    fixed_start: int = 1
    fixed_end: int = 1
    pages_per_week: int = 40

    @property
    def available(self) -> int:
        return max(self.weeks - self.fixed_start - self.fixed_end, 1)


def estimate_pages(reading: dict) -> int:
    return reading.get("pages") or DEFAULT_PAGES.get(reading.get("level", ""), 15)


def _merge(a: dict, b: dict) -> dict:
    """Fold unit b into unit a (a comes first in the course)."""
    m = copy.deepcopy(a)
    m["key"] = f"{a['key']}+{b['key']}"
    m["title"] = f"{a['title']} + {b['title']}"
    m["min_weeks"] = max(a["min_weeks"], b["min_weeks"])
    m["ideal_weeks"] = a["ideal_weeks"] + b["ideal_weeks"]
    m["priority"] = "core" if "core" in (a["priority"], b["priority"]) else "optional"
    m["merge_with"] = b.get("merge_with", "")
    m["merged_from"] = a.get("merged_from", [a["key"]]) + b.get("merged_from", [b["key"]])
    m["goals"] = a.get("goals", []) + b.get("goals", [])
    m["outcomes"] = sorted(set(a.get("outcomes", [])) | set(b.get("outcomes", [])))
    m["readings"] = {
        "core": a["readings"].get("core", []) + b["readings"].get("core", []),
        "optional": a["readings"].get("optional", []) + b["readings"].get("optional", []),
    }
    # The later unit's project usually caps the sequence; keep both units' activities.
    m["assignments"] = {
        "major": b["assignments"].get("major") or a["assignments"].get("major") or [],
        "short_term": b["assignments"].get("short_term") or a["assignments"].get("short_term") or [],
        "activity": a["assignments"].get("activity", []) + b["assignments"].get("activity", []),
    }
    return m


def fit_units(units: list[dict], available: int) -> tuple[list[dict], list[str], list[list[str]]]:
    """Drop and merge units until their minimum weeks fit. Returns (units, dropped, merged)."""
    units = copy.deepcopy(units)
    dropped: list[str] = []
    merged: list[list[str]] = []
    while sum(u["min_weeks"] for u in units) > available:
        optional = [i for i, u in enumerate(units) if u["priority"] == "optional"]
        if optional and len(units) > 1:
            dropped.append(units.pop(optional[-1])["key"])
            continue
        pair = None
        for i, u in enumerate(units):
            if u.get("merge_with") == "next" and i + 1 < len(units):
                pair = (i, i + 1)
                break
            if u.get("merge_with") == "previous" and i > 0:
                pair = (i - 1, i)
                break
        if pair is None:
            # Nothing left to drop or merge: squeeze every unit to one week.
            for u in units:
                u["min_weeks"] = 1
            if len(units) > available:
                dropped.extend(u["key"] for u in units[available:])
                units = units[:available]
            break
        a, b = pair
        merged.append(units[a].get("merged_from", [units[a]["key"]]) + units[b].get("merged_from", [units[b]["key"]]))
        units[a:b + 1] = [_merge(units[a], units[b])]
    return units, dropped, merged


def allocate_weeks(units: list[dict], available: int) -> list[int]:
    """Largest-remainder split of `available` weeks by ideal weeks, respecting minimums."""
    total_ideal = sum(u["ideal_weeks"] for u in units) or 1
    raw = [u["ideal_weeks"] * available / total_ideal for u in units]
    alloc = [max(u["min_weeks"], math.floor(r)) for u, r in zip(units, raw)]
    frac = [r - math.floor(r) for r in raw]
    while sum(alloc) < available:
        i = max(range(len(units)), key=lambda k: (frac[k], -alloc[k]))
        alloc[i] += 1
        frac[i] = -1  # each unit gets at most one remainder week per pass
        if all(f < 0 for f in frac):
            frac = [r - math.floor(r) for r in raw]
    while sum(alloc) > available:
        shrinkable = [k for k in range(len(units)) if alloc[k] > units[k]["min_weeks"]]
        if not shrinkable:
            break
        i = max(shrinkable, key=lambda k: alloc[k] - raw[k])
        alloc[i] -= 1
    return alloc


def select_readings(unit: dict, weeks: int, pages_per_week: int, readings: dict[str, dict]) -> tuple[list[dict], int, int]:
    budget = weeks * pages_per_week
    out, used = [], 0
    for rid in unit["readings"].get("core", []):
        pages = estimate_pages(readings.get(rid, {}))
        used += pages
        out.append({"id": rid, "role": "core", "selected": True, "pages": pages})
    for rid in unit["readings"].get("optional", []):
        pages = estimate_pages(readings.get(rid, {}))
        fits = used + pages <= budget
        if fits:
            used += pages
        out.append({"id": rid, "role": "optional", "selected": fits, "pages": pages})
    return out, used, budget


def select_assignments(unit: dict, weeks: int, term: Term) -> list[dict]:
    a = unit["assignments"]
    short = term.weeks <= SHORT_TERM_WEEKS and a.get("short_term")
    out = []
    for aid in a.get("major", []):
        out.append({"id": aid, "role": "major", "selected": not short})
    for aid in a.get("short_term", []):
        out.append({"id": aid, "role": "short_term", "selected": bool(short)})
    for i, aid in enumerate(a.get("activity", [])):
        out.append({"id": aid, "role": "activity", "selected": i < max(weeks, 1)})
    return out


def plan_course(units: list[dict], term: Term, readings: dict[str, dict] | None = None) -> dict:
    """Lay an arc's units over a term. `readings` maps id -> {level, pages} for page estimates."""
    readings = readings or {}
    fitted, dropped, merged = fit_units(units, term.available)
    alloc = allocate_weeks(fitted, term.available)

    weeks = [{"n": n, "kind": "fixed", "label": "Orientation"} for n in range(1, term.fixed_start + 1)]
    plan_units = []
    week_no = term.fixed_start + 1
    for unit, n_weeks in zip(fitted, alloc):
        sel_readings, used, budget = select_readings(unit, n_weeks, term.pages_per_week, readings)
        plan_units.append({
            "key": unit["key"],
            "title": unit["title"],
            "merged_from": unit.get("merged_from", [unit["key"]]),
            "start_week": week_no,
            "end_week": week_no + n_weeks - 1,
            "weeks": n_weeks,
            "goals": unit.get("goals", []),
            "outcomes": unit.get("outcomes", []),
            "readings": sel_readings,
            "assignments": select_assignments(unit, n_weeks, term),
            "pages_selected": used,
            "page_budget": budget,
            "heavy": used > budget,
        })
        for _ in range(n_weeks):
            weeks.append({"n": week_no, "kind": "unit", "unit": unit["key"]})
            week_no += 1
    for _ in range(term.fixed_end):
        weeks.append({"n": week_no, "kind": "fixed", "label": "Final portfolio and reflection"})
        week_no += 1
    return {
        "term": {"weeks": term.weeks, "fixed_start": term.fixed_start, "fixed_end": term.fixed_end,
                 "pages_per_week": term.pages_per_week},
        "weeks": weeks,
        "units": plan_units,
        "dropped": dropped,
        "merged": merged,
    }
