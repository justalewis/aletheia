"""A course calendar (.ics, RFC 5545) for a build with a schedule.

Events:
- one per class meeting, titled with the course and unit, listing the readings due;
- an all-day event per suggested due date;
- an all-day "No class" event per break and per cancelled meeting.

UIDs are derived from the course title plus the meeting number, assignment id, or date, so
importing an updated calendar again updates the same events instead of adding copies. Timed
meetings are written in UTC (converted from the course time zone date by date, so daylight
saving time is right), which avoids shipping VTIMEZONE definitions.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from builder import schedule as sched

PRODID = "-//Aletheia//Course calendar//EN"
SEQUENCE_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def escape_text(value: str) -> str:
    """TEXT escaping (RFC 5545 3.3.11)."""
    return (str(value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n"))


def fold(line: str, limit: int = 75) -> str:
    """Fold a content line to at most 75 octets per physical line (RFC 5545 3.1), never
    splitting a UTF-8 character. Continuation lines start with one space."""
    parts, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            parts.append(cur)
            cur, size = " ", 1
        cur += ch
        size += n
    parts.append(cur)
    return "\r\n".join(parts)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _day(d: date | str) -> str:
    return (date.fromisoformat(d) if isinstance(d, str) else d).strftime("%Y%m%d")


def _uid(course: str, *parts: str) -> str:
    return f"{uuid.uuid5(uuid.NAMESPACE_URL, 'aletheia:' + ':'.join([course, *parts]))}@aletheia"


class _Writer:
    def __init__(self):
        self.lines: list[str] = []

    def prop(self, name: str, value: str, params: str = "") -> None:
        self.lines.append(fold(f"{name}{params}:{value}"))

    def text(self, name: str, value: str) -> None:
        self.prop(name, escape_text(value))

    def output(self) -> str:
        return "\r\n".join(self.lines) + "\r\n"


def _reading_line(r) -> str:
    url = getattr(r, "best_url", "") or ""
    return f"- {r.title}" + (f" ({url})" if url else "")


def build_ics(ctx: dict, now: datetime | None = None) -> str:
    """The calendar for a resolved, annotated build (see dated.annotate). Raises ValueError
    when the build has no usable schedule."""
    result = ctx.get("schedule")
    if not result:
        raise ValueError("Set a start date for the course before exporting a calendar.")
    now = now or datetime.now(timezone.utc)
    stamp = _utc(now)
    sequence = str(max(int((now - SEQUENCE_EPOCH).total_seconds() // 60), 0))
    title = ctx["title"]
    course = title
    w = _Writer()
    for name, value in [("BEGIN", "VCALENDAR"), ("VERSION", "2.0"), ("PRODID", PRODID),
                        ("CALSCALE", "GREGORIAN"), ("METHOD", "PUBLISH")]:
        w.prop(name, value)
    w.text("X-WR-CALNAME", " · ".join(x for x in (title, ctx.get("term_label", "")) if x))
    w.text("X-WR-TIMEZONE", result["timezone"])

    def event(uid: str, summary: str, description: str, start: str, end: str, all_day: bool) -> None:
        w.prop("BEGIN", "VEVENT")
        w.prop("UID", uid)
        w.prop("DTSTAMP", stamp)
        w.prop("SEQUENCE", sequence)
        if all_day:
            w.prop("DTSTART", start, ";VALUE=DATE")
            w.prop("DTEND", end, ";VALUE=DATE")
            w.prop("TRANSP", "TRANSPARENT")
        else:
            w.prop("DTSTART", start)
            w.prop("DTEND", end)
        w.text("SUMMARY", summary)
        if description:
            w.text("DESCRIPTION", description)
        w.prop("END", "VEVENT")

    for m in ctx.get("meetings", []):
        k, of = m["of_week"]
        lines = [f"{title}. Week {m['week']}, class {k} of {of}."]
        if m["readings"]:
            lines.append("Readings due:")
            lines += [_reading_line(r) for r in m["readings"]]
        span = sched.meeting_span(m["date"], result)
        nxt = _day(date.fromisoformat(m["date"]) + timedelta(days=1))
        start, end = (_utc(span[0]), _utc(span[1])) if span else (_day(m["date"]), nxt)
        event(_uid(course, "meeting", str(m["n"])), f"{title}: {m['title']}", "\n".join(lines), start, end, not span)

    for u in ctx["units"]:
        for a in u.assignments:
            if not getattr(a, "due_date", ""):
                continue
            lines = [f"{title}. {u.title}."]
            if getattr(a, "best_url", ""):
                lines.append(f"Assignment: {a.best_url}")
            d = date.fromisoformat(a.due_date)
            event(_uid(course, "due", a.id), f"Due: {a.title}", "\n".join(lines), _day(d),
                  _day(d + timedelta(days=1)), True)

    for b in result["breaks"]:
        end = date.fromisoformat(b["end"]) + timedelta(days=1)
        event(_uid(course, "break", b["start"]), f"{title}: no class ({b['label']})", "", _day(b["start"]),
              _day(end), True)
    for wk in result["weeks"]:
        for c in wk["cancelled"]:
            d = date.fromisoformat(c["date"])
            event(_uid(course, "off", c["date"]), f"{title}: no class ({c['label']})", "", _day(d),
                  _day(d + timedelta(days=1)), True)

    w.prop("END", "VCALENDAR")
    return w.output()
