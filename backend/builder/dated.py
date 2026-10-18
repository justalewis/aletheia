"""Put a build's schedule on the export context (see schedule.py for the date rules).

exports.resolve() calls annotate() once. Without a schedule every added field is empty, so the
existing exports render exactly as before. With one:

- each unit gets ``dates_label`` ("Jan 11–29");
- each selected major or short-term assignment gets ``due_date``, ``due_label``, and ``due_at``
  (ISO 8601 with the course time zone's offset, for Canvas);
- ``ctx["schedule"]`` holds schedule.compute()'s result;
- ``ctx["schedule_rows"]`` is a week-by-week table (with break rows) for the Common Cartridge
  schedule page and the syllabus;
- ``ctx["meetings"]`` lists each class meeting with the readings due that day, for the calendar.

Readings are spread over a unit's meetings in order, as evenly as possible (or over its weeks
when no meeting days are set), so each meeting lists what to read before class.
"""
from __future__ import annotations

from collections import defaultdict

from builder import schedule as sched


def annotate(ctx: dict, state: dict) -> dict:
    result = sched.for_state(state)
    result = result if result and result.get("ok") else None
    ctx["schedule"] = result
    for u in ctx["units"]:
        info = result["units"].get(u.key) if result else None
        u.dates_label = info["label"] if info else ""
        for a in u.assignments:
            day = info["due"].get(a.id) if info else None
            a.due_date = day or ""
            a.due_label = sched.day_label(sched.parse_date(day)) if day else ""
            a.due_at = sched.due_datetime(day, result["timezone"]).isoformat() if day else ""
    ctx["schedule_rows"] = schedule_rows(ctx, result) if result else []
    ctx["meetings"] = meetings(ctx, result) if result else []
    return ctx


def _units_for_rules(ctx: dict) -> list[dict]:
    return [{"key": u.key, "start_week": u.start_week, "end_week": u.end_week} for u in ctx["units"]]


def _readings_by_slot(ctx: dict, result: dict | None) -> tuple[dict, dict]:
    """(week n -> readings due that week, meeting n -> readings due at that meeting)."""
    by_week: dict[int, list] = defaultdict(list)
    by_meeting: dict[int, list] = defaultdict(list)
    for u in ctx["units"]:
        if not u.start_week:
            continue
        end = u.end_week or u.start_week
        slots = [m for m in (result or {}).get("meetings", []) if u.start_week <= m["week"] <= end]
        if slots:
            for m, items in zip(slots, sched.spread(u.readings, len(slots))):
                by_meeting[m["n"]].extend(items)
                by_week[m["week"]].extend(items)
        else:
            weeks = list(range(u.start_week, end + 1))
            for w, items in zip(weeks, sched.spread(u.readings, len(weeks))):
                by_week[w].extend(items)
    return by_week, by_meeting


def _due_by_week(ctx: dict, result: dict | None) -> dict[int, list]:
    """Week n -> assignments due that week. A date in a break counts toward the week before it."""
    out: dict[int, list] = defaultdict(list)
    weeks = (result or {}).get("weeks", [])
    for u in ctx["units"]:
        for a in u.assignments:
            if not getattr(a, "due_date", ""):
                continue
            starts = [w for w in weeks if w["start"] <= a.due_date]
            n = starts[-1]["n"] if starts else (weeks[0]["n"] if weeks else u.end_week)
            out[n].append(a)
    return out


def schedule_rows(ctx: dict, result: dict | None) -> list[dict]:
    """One row per term week, plus a row for each break. Works without a schedule (no dates)."""
    units = _units_for_rules(ctx)
    owners = sched.week_owners(ctx.get("term") or {}, units)
    n_weeks = sched.total_weeks(ctx.get("term") or {}, units)
    by_key = {u.key: u for u in ctx["units"]}
    weeks = {w["n"]: w for w in (result or {}).get("weeks", [])}
    breaks: dict[int, list] = defaultdict(list)
    for b in (result or {}).get("breaks", []):
        breaks[b["after_week"]].append(b)
    readings, _ = _readings_by_slot(ctx, result)
    due = _due_by_week(ctx, result)

    def break_rows(n: int) -> list[dict]:
        return [{"kind": "break", "week": None, "dates": b["dates"], "unit": b["label"], "unit_obj": None,
                 "first": False, "readings": [], "due": [], "notes": []} for b in breaks.get(n, [])]

    rows = break_rows(0)
    for n in range(1, n_weeks + 1):
        kind, owner = owners.get(n, ("fixed", ""))
        unit = by_key.get(owner) if kind == "unit" else None
        cancelled = weeks[n]["cancelled"] if n in weeks else []
        rows.append({
            "kind": "week", "week": n, "dates": weeks[n]["label"] if n in weeks else "",
            "unit": unit.title if unit else owner, "unit_obj": unit,
            "first": bool(unit and unit.start_week == n),
            "readings": readings.get(n, []), "due": due.get(n, []),
            "notes": [f"No class {c['day']}: {c['label']}" for c in cancelled],
        })
        rows.extend(break_rows(n))
    return rows


def meetings(ctx: dict, result: dict) -> list[dict]:
    """Each class meeting with its unit (or fixed-week label) and the readings due."""
    _, by_meeting = _readings_by_slot(ctx, result)
    by_key = {u.key: u for u in ctx["units"]}
    per_week: dict[int, int] = defaultdict(int)
    for m in result["meetings"]:
        per_week[m["week"]] += 1
    out, seen = [], defaultdict(int)
    for m in result["meetings"]:
        seen[m["week"]] += 1
        unit = next((u for u in ctx["units"] if u.start_week and u.start_week <= m["week"] <= (u.end_week or u.start_week)),
                    None) or by_key.get(m["unit"])
        out.append({**m, "unit_obj": unit, "title": unit.title if unit else (m["fixed"] or "Class"),
                    "readings": by_meeting.get(m["n"], []), "of_week": (seen[m["week"]], per_week[m["week"]])})
    return out
