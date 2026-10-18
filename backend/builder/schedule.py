"""Put calendar dates on a course: term weeks, class meetings, unit ranges, and due dates.

Pure functions over plain dicts (no Django), like planning.py, so the rules are easy to test and
the frontend can ask for a preview through POST /api/schedule instead of copying them.

A build's optional ``schedule`` key:

    {"start_date": "2027-01-04",
     "meeting_days": ["Mon", "Wed", "Fri"],
     "no_class": ["2027-01-18",
                  {"date": "2027-02-15", "label": "Presidents' Day"},
                  {"start": "2027-03-22", "end": "2027-03-26", "label": "Spring break"}],
     "timezone": "America/Los_Angeles",
     "start_time": "10:00", "end_time": "10:50",      # optional, for timed calendar events
     "due_dates": {"<assignment id>": "2027-02-12"}}  # optional overrides

Rules:
1. Week 1 is the calendar week (Monday to Sunday) that holds the start date. Meetings before
   the start date don't happen. A start date on a weekend with no weekend classes rolls week 1
   to the next Monday.
2. A calendar week whose teaching days (Monday to Friday, plus any weekend meeting days) all fall
   inside no-class ranges is a break: it gets no term-week number, and later term weeks shift
   one calendar week later.
3. Single no-class dates, and ranges that don't cover a whole week, only cancel the meetings
   they fall on. The term week keeps its number.
4. A unit runs from the first day of its first week to the last day of its last week.
5. Each selected major (or short-term) assignment is due at its unit's last meeting, or on the
   last teaching day of the unit if the unit has no meetings, unless ``due_dates`` overrides it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DAY_ALIASES = {name.lower(): i for i, name in enumerate(DAYS)}
_DAY_ALIASES.update({name: i for i, name in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])})
_DAY_ALIASES.update({"tues": 1, "thur": 3, "thurs": 3})
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DUE_ROLES = ("major", "short_term")
ORIENTATION = "Orientation"
FINAL = "Final portfolio and reflection"
MAX_CALENDAR_WEEKS = 104  # stop runaway breaks from scanning forever
DEFAULT_TIMEZONE = "UTC"


@dataclass(frozen=True)
class NoClass:
    start: date
    end: date
    label: str
    single: bool  # a single date (cancels meetings only) rather than a range

    def covers(self, d: date) -> bool:
        return self.start <= d <= self.end


@dataclass(frozen=True)
class Parsed:
    start: date
    meeting_days: tuple[int, ...]
    no_class: tuple[NoClass, ...]
    timezone: str
    start_time: time | None
    end_time: time | None
    due_dates: dict


# ---------------------------------------------------------------- labels

def short(d: date) -> str:
    return f"{_MONTHS[d.month - 1]} {d.day}"


def day_label(d: date) -> str:
    return f"{DAYS[d.weekday()]}, {short(d)}"


def range_label(a: date, b: date) -> str:
    """Jan 4, Jan 4–8, Jan 25 – Feb 12, or Dec 28, 2026 – Jan 8, 2027."""
    if b < a:
        a, b = b, a
    if a == b:
        return short(a)
    if a.year != b.year:
        return f"{short(a)}, {a.year} – {short(b)}, {b.year}"
    if a.month == b.month:
        return f"{short(a)}–{b.day}"
    return f"{short(a)} – {short(b)}"


# ---------------------------------------------------------------- parsing

def parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def parse_time(value) -> time | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return time.fromisoformat(value.strip())
    except ValueError:
        return None


def valid_timezone(name) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name.strip()


def _parse_days(raw, warnings: list[str]) -> tuple[int, ...]:
    days: set[int] = set()
    bad = []
    for d in raw if isinstance(raw, (list, tuple)) else []:
        key = str(d).strip().lower()
        if key in _DAY_ALIASES:
            days.add(_DAY_ALIASES[key])
        elif key[:3] in _DAY_ALIASES:
            days.add(_DAY_ALIASES[key[:3]])
        else:
            bad.append(str(d))
    if bad:
        warnings.append(f"Skipped meeting days Aletheia didn't recognize: {', '.join(bad)}.")
    return tuple(sorted(days))


def _parse_no_class(raw, warnings: list[str]) -> list[NoClass]:
    out: list[NoClass] = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        if isinstance(item, str):
            d = parse_date(item)
            if d:
                out.append(NoClass(d, d, "", True))
            else:
                warnings.append(f"Skipped a no-class date that isn't a date: {item!r}.")
            continue
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        name = label or "a no-class entry"
        if item.get("start") or item.get("end"):
            a, b = parse_date(item.get("start")), parse_date(item.get("end"))
            if not a and not b:
                warnings.append(f"Skipped {name}: its dates aren't valid.")
                continue
            a, b = a or b, b or a
            if b < a:
                warnings.append(f"{label or 'A no-class range'} ends before it starts, so Aletheia swapped the dates.")
                a, b = b, a
            out.append(NoClass(a, b, label, False))
        elif item.get("date"):
            d = parse_date(item.get("date"))
            if d:
                out.append(NoClass(d, d, label, True))
            else:
                warnings.append(f"Skipped {name}: its date isn't valid.")
    return out


def _name(n: NoClass) -> str:
    return n.label or (short(n.start) if n.single else range_label(n.start, n.end))


def parse(raw: dict | None) -> tuple[Parsed | None, list[str]]:
    """Validate a schedule. Returns (None, warnings) when there's no usable start date."""
    warnings: list[str] = []
    if not isinstance(raw, dict) or not raw.get("start_date"):
        return None, warnings
    start = parse_date(raw.get("start_date"))
    if not start:
        return None, ["The start date isn't a valid date."]
    days = _parse_days(raw.get("meeting_days"), warnings)
    no_class = _parse_no_class(raw.get("no_class"), warnings)
    ranges = sorted((n for n in no_class if not n.single), key=lambda n: n.start)
    for i, a in enumerate(ranges):
        for b in ranges[i + 1:]:
            if b.start <= a.end:
                warnings.append(f"{_name(a)} and {_name(b)} overlap. Aletheia treats them as one stretch of days off.")
    tz = valid_timezone(raw.get("timezone"))
    if raw.get("timezone") and not tz:
        warnings.append(f"Aletheia doesn't know the time zone {raw.get('timezone')!r}, so it used {DEFAULT_TIMEZONE}.")
    st, et = parse_time(raw.get("start_time")), parse_time(raw.get("end_time"))
    if raw.get("start_time") and not st or raw.get("end_time") and not et:
        warnings.append("A class time isn't a valid time, so calendar events are all-day.")
        st = et = None
    if st and et and et <= st:
        warnings.append("Class ends before it starts, so calendar events are all-day.")
        st = et = None
    if (st is None) != (et is None):
        st = et = None  # need both for a timed event
    due = {}
    raw_due = raw.get("due_dates")
    for aid, value in (raw_due.items() if isinstance(raw_due, dict) else []):
        d = parse_date(value)
        if d:
            due[str(aid)] = d
    return Parsed(start, days, tuple(no_class), tz or DEFAULT_TIMEZONE, st, et, due), warnings


# ---------------------------------------------------------------- weeks

def week_owners(term: dict, units: list[dict]) -> dict[int, tuple[str, str]]:
    """Term week number -> ("fixed", label) or ("unit", unit key), laid out like the builder's
    week grid: orientation weeks, the units, then the final weeks after the last unit."""
    fixed_start = max(int(term.get("fixed_start", 1) or 0), 0)
    fixed_end = max(int(term.get("fixed_end", 1) or 0), 0)
    weeks = max(int(term.get("weeks", 16) or 16), 1)
    owners: dict[int, tuple[str, str]] = {n: ("fixed", ORIENTATION) for n in range(1, fixed_start + 1)}
    unit_end = weeks - fixed_end
    for u in units:
        s, e = u.get("start_week"), u.get("end_week")
        if not s:
            continue
        e = e or s
        unit_end = max(unit_end, e)
        for n in range(s, e + 1):
            owners.setdefault(n, ("unit", u.get("key", "")))
    for i in range(fixed_end):
        owners[unit_end + 1 + i] = ("fixed", FINAL)
    return owners


def total_weeks(term: dict, units: list[dict]) -> int:
    owners = week_owners(term, units)
    return max([max(int(term.get("weeks", 16) or 16), 1), *owners.keys()])


def _teaching_days(monday: date, meeting_days: tuple[int, ...], first_day: date) -> list[date]:
    days = set(range(5)) | {d for d in meeting_days if d >= 5}
    return [monday + timedelta(days=i) for i in sorted(days) if monday + timedelta(days=i) >= first_day]


_FULL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _first_monday(p: Parsed, notes: list[str]) -> date:
    weekday = p.start.weekday()
    monday = p.start - timedelta(days=weekday)
    if weekday >= 5 and not any(d >= weekday for d in p.meeting_days):
        nxt = monday + timedelta(days=7)
        notes.append(f"The start date is a {_FULL_DAYS[weekday]}, so week 1 starts {day_label(nxt)}.")
        return nxt
    if weekday != 0:
        skipped = [_FULL_DAYS[d] for d in p.meeting_days if d < weekday]
        extra = f" There's no {' or '.join(skipped)} class that week." if skipped else ""
        notes.append(f"The start date is a {_FULL_DAYS[weekday]}, so week 1 starts {day_label(p.start)}.{extra}")
    return monday


def compute(term: dict, units: list[dict], schedule: dict | None) -> dict | None:
    """Dates for every term week, class meeting, and unit. None when there's no schedule."""
    p, warnings = parse(schedule)
    if p is None:
        if warnings:
            return {"ok": False, "warnings": warnings, "notes": []}
        return None
    notes: list[str] = []
    if not p.meeting_days:
        warnings.append("Choose the days the class meets to date each class meeting.")
    ranges = [n for n in p.no_class if not n.single]
    n_weeks = total_weeks(term, units)
    owners = week_owners(term, units)
    first_day = p.start
    monday = _first_monday(p, notes)
    if monday > p.start:
        first_day = monday

    weeks: list[dict] = []
    breaks: list[dict] = []
    meetings: list[dict] = []
    used: set[NoClass] = set()
    scanned = 0
    while len(weeks) < n_weeks and scanned < MAX_CALENDAR_WEEKS:
        scanned += 1
        teaching = _teaching_days(monday, p.meeting_days, first_day)
        covering = [r for r in ranges if any(r.covers(d) for d in teaching)]
        if teaching and all(any(r.covers(d) for r in ranges) for d in teaching):
            used.update(covering)
            used.update(x for x in p.no_class if x.single and teaching[0] <= x.start <= teaching[-1])
            labels = list(dict.fromkeys(r.label for r in covering if r.label))
            breaks.append({"start": teaching[0].isoformat(), "end": teaching[-1].isoformat(),
                           "label": " / ".join(labels) or "No class",
                           "after_week": len(weeks), "dates": range_label(teaching[0], teaching[-1])})
            monday += timedelta(days=7)
            first_day = monday
            continue
        n = len(weeks) + 1
        week_meetings, cancelled = [], []
        for day in p.meeting_days:
            d = monday + timedelta(days=day)
            if d < first_day:
                continue
            off = [x for x in p.no_class if x.covers(d)]
            if off:
                used.update(off)
                cancelled.append({"date": d.isoformat(), "day": DAYS[day], "label": _name(off[0])})
                continue
            week_meetings.append({"date": d.isoformat(), "day": DAYS[day], "label": day_label(d)})
        last_day = teaching[-1] if teaching else monday + timedelta(days=4)
        kind, owner = owners.get(n, ("fixed", ""))
        weeks.append({
            "n": n, "start": first_day.isoformat(), "end": last_day.isoformat(),
            "label": range_label(first_day, last_day), "meetings": week_meetings, "cancelled": cancelled,
            "unit": owner if kind == "unit" else None, "fixed": owner if kind == "fixed" else None,
        })
        if p.meeting_days and not week_meetings:
            if cancelled:
                what = " and ".join(dict.fromkeys(c["label"] for c in cancelled))
                warnings.append(f"Week {n} has no class meetings ({what}). To skip the week instead, make the "
                                "no-class range cover Monday through Friday.")
            else:
                warnings.append(f"Week {n} has no class meetings: the term starts after that week's class days.")
        monday += timedelta(days=7)
        first_day = monday
    if len(weeks) < n_weeks:
        warnings.append(f"The no-class dates leave room for only {len(weeks)} of {n_weeks} weeks "
                        f"in the next {MAX_CALENDAR_WEEKS} calendar weeks.")

    count = 0
    for w in weeks:
        for m in w["meetings"]:
            count += 1
            meetings.append({**m, "n": count, "week": w["n"], "unit": w["unit"], "fixed": w["fixed"]})

    term_start = date.fromisoformat(weeks[0]["start"]) if weeks else p.start
    term_end = date.fromisoformat(weeks[-1]["end"]) if weeks else p.start
    for x in p.no_class:
        if x in used:
            continue
        if x.end < term_start or x.start > term_end:
            warnings.append(f"{_name(x)} is outside the term ({range_label(term_start, term_end)}), so it has no effect.")
        elif p.meeting_days:
            notes.append(f"{_name(x)} doesn't fall on a class day, so it has no effect.")

    by_week = {w["n"]: w for w in weeks}
    unit_out: dict[str, dict] = {}
    due: list[dict] = []
    for u in units:
        key = u.get("key", "")
        s, e = u.get("start_week"), u.get("end_week") or u.get("start_week")
        if not s or s not in by_week:
            continue
        e = e if e in by_week else max(by_week)
        first, last = by_week[s], by_week[e]
        u_meetings = [m for m in meetings if s <= m["week"] <= e]
        u_breaks = [b["label"] for b in breaks if s <= b["after_week"] < e]
        start_d, end_d = date.fromisoformat(first["start"]), date.fromisoformat(last["end"])
        info = {
            "start": first["start"], "end": last["end"], "label": range_label(start_d, end_d),
            "first_meeting": u_meetings[0]["date"] if u_meetings else None,
            "last_meeting": u_meetings[-1]["date"] if u_meetings else None,
            "meetings": [m["date"] for m in u_meetings], "breaks": u_breaks, "due": {},
        }
        suggested = date.fromisoformat(info["last_meeting"] or last["end"])
        for a in u.get("assignments", []):
            if not a.get("selected") or a.get("role") not in DUE_ROLES or not a.get("id"):
                continue
            chosen = p.due_dates.get(a["id"], suggested)
            info["due"][a["id"]] = chosen.isoformat()
            due.append({"unit": key, "id": a["id"], "role": a.get("role"), "date": chosen.isoformat(),
                        "suggested": suggested.isoformat(), "overridden": chosen != suggested,
                        "label": day_label(chosen)})
        unit_out[key] = info

    return {
        "ok": True,
        "start_date": p.start.isoformat(),
        "timezone": p.timezone,
        "meeting_days": [DAYS[d] for d in p.meeting_days],
        "start_time": p.start_time.strftime("%H:%M") if p.start_time else None,
        "end_time": p.end_time.strftime("%H:%M") if p.end_time else None,
        "term_start": term_start.isoformat(),
        "term_end": term_end.isoformat(),
        "label": range_label(term_start, term_end),
        "weeks": weeks,
        "breaks": breaks,
        "meetings": meetings,
        "units": unit_out,
        "due": due,
        "warnings": warnings,
        "notes": notes,
    }


def for_state(state: dict) -> dict | None:
    """compute() for a build state, reading every key with a default so older builds still work."""
    return compute(state.get("term") or {}, state.get("units") or [], state.get("schedule"))


# ---------------------------------------------------------------- helpers for exports

def spread(items: list, slots: int) -> list[list]:
    """Spread items over slots in order, as evenly as possible: [[...], [...], ...]."""
    out: list[list] = [[] for _ in range(max(slots, 0))]
    if not out:
        return out
    for i, item in enumerate(items):
        out[min(i * slots // max(len(items), 1), slots - 1)].append(item)
    return out


def due_datetime(day: str, tz: str, at: time = time(23, 59)) -> datetime:
    """A due date as an aware datetime in the course's time zone (Canvas wants an offset)."""
    return datetime.combine(date.fromisoformat(day), at, tzinfo=ZoneInfo(tz))


def meeting_span(day: str, result: dict) -> tuple[datetime, datetime] | None:
    """A meeting's start and end as aware datetimes, or None for all-day meetings."""
    if not result.get("start_time") or not result.get("end_time"):
        return None
    tz = ZoneInfo(result["timezone"])
    d = date.fromisoformat(day)
    return (datetime.combine(d, time.fromisoformat(result["start_time"]), tzinfo=tz),
            datetime.combine(d, time.fromisoformat(result["end_time"]), tzinfo=tz))
