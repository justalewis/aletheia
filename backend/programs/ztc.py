"""Zero-textbook-cost (ZTC) reporting for a program's sections.

The zero-cost rule lives in `zero_cost()`, and what one item costs a student lives in
`student_cost()`. Today every catalog reading and assignment is free to read, open or link-only,
so a course is zero-cost when every selected item is a catalog record. When the catalog gains
paid or citation-only items (a commercial textbook, a library-database article), teach
`student_cost()` about them; the report and CSV pick it up from there.
"""
from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from catalog.models import Assignment, Reading

FREE = "free"
PAID = "paid"
UNKNOWN = "unknown"

REASONS = {
    PAID: "Students pay for this item.",
    UNKNOWN: "Not in the catalog, so we can't confirm it's free.",
}


def student_cost(record) -> str:
    """What a student pays to read one selected item: FREE, PAID, or UNKNOWN."""
    if record is None:
        return UNKNOWN
    # Every catalog record is free to read: openly licensed items are copied into the reader, and
    # link-only items are free to read at the publisher's site. Future records that carry an access
    # or price field should be checked here.
    access = getattr(record, "access", "") or ""
    if access in (PAID, UNKNOWN):
        return access
    return FREE


@dataclass
class Selected:
    kind: str  # "reading" | "assignment"
    id: str
    record: object | None


@dataclass
class CostCheck:
    zero_cost: bool
    readings: int = 0
    assignments: int = 0
    open: int = 0
    link_only: int = 0
    not_free: list = field(default_factory=list)  # [{"kind", "id", "cost", "reason"}]
    note: str = ""


def selected_ids(state: dict) -> tuple[list[str], list[str]]:
    units = (state or {}).get("units") or []
    r = [i.get("id") for u in units for i in (u.get("readings") or []) if isinstance(i, dict) and i.get("selected")]
    a = [i.get("id") for u in units for i in (u.get("assignments") or []) if isinstance(i, dict) and i.get("selected")]
    return [x for x in r if x], [x for x in a if x]


def zero_cost(items: list[Selected]) -> CostCheck:
    """The zero-cost rule: a course is zero-cost when it selects at least one item and every
    selected item is free to students. Unknown ids count against it and are listed."""
    check = CostCheck(zero_cost=False)
    for it in items:
        if it.kind == "reading":
            check.readings += 1
        else:
            check.assignments += 1
        if it.record is not None:
            if it.record.effective_tier == "link-only":
                check.link_only += 1
            else:
                check.open += 1
        cost = student_cost(it.record)
        if cost != FREE:
            check.not_free.append({"kind": it.kind, "id": it.id, "cost": cost, "reason": REASONS[cost]})
    if not items:
        check.note = "No readings or assignments are selected yet."
    check.zero_cost = bool(items) and not check.not_free
    return check


def check_states(states: list[dict]) -> list[CostCheck]:
    """Run zero_cost() over many build states with two catalog queries in all."""
    picked = [selected_ids(s) for s in states]
    r_ids = {i for r, _ in picked for i in r}
    a_ids = {i for _, a in picked for i in a}
    readings = {r.id: r for r in Reading.objects.filter(id__in=r_ids)}
    assignments = {a.id: a for a in Assignment.objects.filter(id__in=a_ids)}
    out = []
    for r, a in picked:
        items = [Selected("reading", i, readings.get(i)) for i in r]
        items += [Selected("assignment", i, assignments.get(i)) for i in a]
        out.append(zero_cost(items))
    return out


# ---------------------------------------------------------------- the report

def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _term_matches(section: dict, term: str) -> bool:
    return not term or (section.get("term") or "").strip().lower() == term.strip().lower()


def build_report(program, builds, term: str = "") -> dict:
    """One row per linked build (section), then totals. `builds` is an iterable of Build."""
    cost = money(program.setting("textbook_cost_per_student"))
    currency = program.setting("currency") or "USD"
    builds = list(builds)
    all_terms = sorted({(b.section or {}).get("term", "").strip() for b in builds} - {""})
    builds = [b for b in builds if _term_matches(b.section or {}, term)]
    checks = check_states([b.state for b in builds])
    rows = []
    for b, check in zip(builds, checks):
        sec = b.section or {}
        enrollment = int(sec.get("enrollment") or 0)
        savings = money(enrollment * cost) if check.zero_cost else money(0)
        rows.append({
            "build_id": str(b.id),
            "title": b.title,
            "course": sec.get("course_code", ""),
            "section": sec.get("section", ""),
            "term": sec.get("term", ""),
            "instructor": sec.get("instructor") or (b.state or {}).get("instructor", ""),
            "enrollment": enrollment,
            "readings": check.readings,
            "assignments": check.assignments,
            "zero_cost": check.zero_cost,
            "open": check.open,
            "link_only": check.link_only,
            "not_free": check.not_free,
            "note": check.note,
            "savings": float(savings),
            "updated": b.updated.isoformat(),
        })
    rows.sort(key=lambda r: (r["term"].lower(), r["course"].lower(), r["section"].lower(), r["title"].lower()))
    keys = Counter((r["course"].lower(), r["section"].lower(), r["term"].lower()) for r in rows
                   if r["course"] and r["section"])
    warnings = []
    for (course, section, t), n in keys.items():
        if n > 1:
            r = next(x for x in rows if (x["course"].lower(), x["section"].lower(), x["term"].lower()) == (course, section, t))
            label = f"{r['course']} section {r['section']}" + (f" ({r['term']})" if r["term"] else "")
            warnings.append(f"{label} appears in {n} saved courses. Check that it isn't counted twice.")
    total_savings = sum((money(r["savings"]) for r in rows), Decimal("0.00"))
    return {
        "program": {"id": str(program.id), "name": program.name, "institution": program.institution},
        "term": term,
        "terms": sorted(set(all_terms) | set(program.setting("terms") or []), key=str.lower),
        "textbook_cost_per_student": float(cost),
        "currency": currency,
        "rows": rows,
        "totals": {
            "sections": len(rows),
            "students": sum(r["enrollment"] for r in rows),
            "zero_cost_sections": sum(1 for r in rows if r["zero_cost"]),
            "zero_cost_students": sum(r["enrollment"] for r in rows if r["zero_cost"]),
            "savings": float(total_savings),
        },
        "warnings": warnings,
    }


def _cell(value) -> str:
    """Keep spreadsheet apps from reading user-typed text as a formula."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def report_csv(report: dict, link_for=lambda build_id: "") -> str:
    cur = report["currency"]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Course", "Section", "Term", "Instructor", "Enrollment", "Readings selected",
                "Assignments selected", "Zero cost", "Open items", "Link-only items", "Not free or unknown",
                f"Estimated student savings ({cur})", "Course title", "Course link"])
    for r in report["rows"]:
        w.writerow([_cell(r["course"]), _cell(r["section"]), _cell(r["term"]), _cell(r["instructor"]),
                    r["enrollment"], r["readings"], r["assignments"], "yes" if r["zero_cost"] else "no",
                    r["open"], r["link_only"], _cell("; ".join(i["id"] for i in r["not_free"])),
                    f"{money(r['savings']):.2f}", _cell(r["title"]), link_for(r["build_id"])])
    t = report["totals"]
    w.writerow([])
    w.writerow(["Total", f"{t['sections']} section{'' if t['sections'] == 1 else 's'}", _cell(report["term"]), "",
                t["students"], "", "",
                f"{t['zero_cost_sections']} of {t['sections']} at zero cost", "", "", "",
                f"{money(t['savings']):.2f}", "", ""])
    w.writerow(["Textbook cost per student used for savings", f"{money(report['textbook_cost_per_student']):.2f}", cur])
    return out.getvalue()
