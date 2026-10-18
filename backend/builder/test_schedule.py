from datetime import date

import pytest

from builder import schedule as S

TERM = {"weeks": 10, "fixed_start": 1, "fixed_end": 1}
UNITS = [
    {"key": "u1", "title": "One", "start_week": 2, "end_week": 4,
     "assignments": [{"id": "essay", "role": "major", "selected": True},
                     {"id": "short", "role": "short_term", "selected": False},
                     {"id": "act", "role": "activity", "selected": True}]},
    {"key": "u2", "title": "Two", "start_week": 5, "end_week": 9,
     "assignments": [{"id": "project", "role": "major", "selected": True}]},
]


def sched(**kw):
    base = {"start_date": "2027-01-04", "meeting_days": ["Mon", "Wed"], "no_class": [],
            "timezone": "America/Los_Angeles"}
    base.update(kw)
    return base


def run(term=TERM, units=UNITS, **kw):
    return S.compute(term, units, sched(**kw))


def meeting_dates(result):
    return [m["date"] for m in result["meetings"]]


# ---------------------------------------------------------------- basics

def test_no_schedule_returns_none():
    assert S.compute(TERM, UNITS, None) is None
    assert S.compute(TERM, UNITS, {}) is None
    assert S.compute(TERM, UNITS, {"meeting_days": ["Mon"]}) is None
    assert S.for_state({}) is None
    assert S.for_state({"term": TERM, "units": UNITS}) is None


def test_invalid_start_date_is_reported_not_raised():
    r = S.compute(TERM, UNITS, sched(start_date="next Monday"))
    assert r["ok"] is False
    assert "start date" in r["warnings"][0]


def test_weeks_and_meetings_for_a_plain_term():
    r = run()
    assert r["ok"]
    assert len(r["weeks"]) == 10
    w1, w2 = r["weeks"][0], r["weeks"][1]
    assert (w1["start"], w1["end"], w1["label"]) == ("2027-01-04", "2027-01-08", "Jan 4–8")
    assert [m["date"] for m in w1["meetings"]] == ["2027-01-04", "2027-01-06"]
    assert w2["start"] == "2027-01-11"
    assert len(r["meetings"]) == 20
    assert [m["n"] for m in r["meetings"]] == list(range(1, 21))
    assert r["term_start"] == "2027-01-04" and r["term_end"] == "2027-03-12"
    assert r["warnings"] == [] and r["notes"] == []


def test_weeks_know_their_unit_or_fixed_label():
    r = run()
    assert r["weeks"][0]["fixed"] == S.ORIENTATION and r["weeks"][0]["unit"] is None
    assert r["weeks"][1]["unit"] == "u1"
    assert r["weeks"][8]["unit"] == "u2"
    assert r["weeks"][9]["fixed"] == S.FINAL
    assert r["meetings"][2]["unit"] == "u1" and r["meetings"][2]["week"] == 2


def test_unit_ranges_and_meetings():
    r = run()
    u1 = r["units"]["u1"]
    assert (u1["start"], u1["end"]) == ("2027-01-11", "2027-01-29")
    assert u1["label"] == "Jan 11–29"
    assert u1["first_meeting"] == "2027-01-11" and u1["last_meeting"] == "2027-01-27"
    assert len(u1["meetings"]) == 6
    assert r["units"]["u2"]["label"] == "Feb 1 – Mar 5"


def test_day_names_are_forgiving():
    r = run(meeting_days=["monday", "WED", "Thurs", "fri", "Funday"])
    assert r["meeting_days"] == ["Mon", "Wed", "Thu", "Fri"]
    assert any("Funday" in w for w in r["warnings"])
    assert len(r["weeks"][0]["meetings"]) == 4


def test_weekend_meetings_extend_the_week():
    r = run(meeting_days=["Sat"])
    assert r["weeks"][0]["meetings"][0]["date"] == "2027-01-09"
    assert r["weeks"][0]["end"] == "2027-01-09"


# ---------------------------------------------------------------- no-class days

def test_single_holiday_removes_one_meeting_and_keeps_week_numbers():
    r = run(no_class=["2027-01-18"])
    assert "2027-01-18" not in meeting_dates(r)
    assert len(r["meetings"]) == 19
    w3 = r["weeks"][2]
    assert w3["n"] == 3 and w3["start"] == "2027-01-18"
    assert w3["cancelled"] == [{"date": "2027-01-18", "day": "Mon", "label": "Jan 18"}]
    assert r["term_end"] == "2027-03-12"
    assert r["breaks"] == []


def test_labelled_single_date():
    r = run(no_class=[{"date": "2027-01-18", "label": "MLK Day"}])
    assert r["weeks"][2]["cancelled"][0]["label"] == "MLK Day"


def test_break_week_is_skipped_and_later_weeks_shift():
    r = run(no_class=[{"start": "2027-02-15", "end": "2027-02-19", "label": "Winter break"}])
    assert len(r["weeks"]) == 10
    assert r["breaks"] == [{"start": "2027-02-15", "end": "2027-02-19", "label": "Winter break",
                            "after_week": 6, "dates": "Feb 15–19"}]
    assert r["weeks"][5]["start"] == "2027-02-08"  # week 6
    assert r["weeks"][6]["start"] == "2027-02-22"  # week 7 comes after the break
    assert r["term_end"] == "2027-03-19"
    assert len(r["meetings"]) == 20
    assert r["units"]["u2"]["breaks"] == ["Winter break"]
    assert r["units"]["u2"]["label"] == "Feb 1 – Mar 12"


def test_range_over_the_weekend_also_skips():
    r = run(no_class=[{"start": "2027-02-13", "end": "2027-02-21", "label": "Break"}])
    assert len(r["breaks"]) == 1 and r["breaks"][0]["start"] == "2027-02-15"


def test_partial_range_only_cancels_meetings():
    # Wednesday to Friday: Monday still meets, so the week keeps its number.
    r = run(no_class=[{"start": "2027-01-20", "end": "2027-01-22", "label": "Retreat"}])
    assert r["breaks"] == []
    w3 = r["weeks"][2]
    assert [m["date"] for m in w3["meetings"]] == ["2027-01-18"]
    assert w3["cancelled"][0]["label"] == "Retreat"


def test_range_covering_only_the_meeting_days_warns_but_keeps_the_week():
    r = run(no_class=[{"start": "2027-01-18", "end": "2027-01-20", "label": "Conference"}])
    assert r["breaks"] == []
    assert r["weeks"][2]["meetings"] == []
    assert any("Week 3 has no class meetings (Conference)" in w for w in r["warnings"])


def test_single_dates_never_skip_a_week():
    r = run(no_class=["2027-01-18", "2027-01-20"])
    assert r["breaks"] == []
    assert r["weeks"][2]["meetings"] == []
    assert len(r["weeks"]) == 10


def test_overlapping_ranges_warn_and_act_as_one():
    r = run(no_class=[{"start": "2027-02-15", "end": "2027-02-17", "label": "A"},
                      {"start": "2027-02-17", "end": "2027-02-19", "label": "B"}])
    assert any("A and B overlap" in w for w in r["warnings"])
    assert len(r["breaks"]) == 1
    assert r["breaks"][0]["label"] == "A / B"


def test_backwards_range_is_swapped():
    r = run(no_class=[{"start": "2027-02-19", "end": "2027-02-15", "label": "Break"}])
    assert any("swapped" in w for w in r["warnings"])
    assert len(r["breaks"]) == 1


def test_holiday_outside_the_term_warns():
    r = run(no_class=["2026-12-25", {"start": "2027-06-01", "end": "2027-06-05", "label": "Summer"}])
    assert sum("outside the term" in w for w in r["warnings"]) == 2
    assert len(r["meetings"]) == 20


def test_holiday_on_a_non_class_day_is_a_note():
    r = run(no_class=["2027-01-19"])  # a Tuesday
    assert r["warnings"] == []
    assert any("doesn't fall on a class day" in n for n in r["notes"])


def test_bad_no_class_entries_are_skipped():
    r = run(no_class=["soon", {"date": "nope", "label": "X"}, {"start": "bad", "end": "worse"}, 42, None])
    assert len(r["warnings"]) == 3
    assert len(r["meetings"]) == 20


def test_runaway_break_stops():
    r = run(no_class=[{"start": "2027-01-11", "end": "2030-01-01", "label": "Forever"}])
    assert len(r["weeks"]) == 1
    assert any("leave room for only 1" in w for w in r["warnings"])


# ---------------------------------------------------------------- start dates and meeting days

def test_start_date_midweek():
    r = run(start_date="2027-01-06")
    w1 = r["weeks"][0]
    assert w1["start"] == "2027-01-06" and w1["label"] == "Jan 6–8"
    assert [m["date"] for m in w1["meetings"]] == ["2027-01-06"]
    assert r["weeks"][1]["start"] == "2027-01-11"
    assert any("is a Wed" in n for n in r["notes"])


def test_start_date_after_the_weeks_class_days():
    r = run(start_date="2027-01-07")  # Thursday; Mon/Wed class
    assert r["weeks"][0]["meetings"] == []
    assert any("Week 1 has no class meetings" in w for w in r["warnings"])


def test_weekend_start_rolls_to_monday():
    r = run(start_date="2027-01-02")  # Saturday
    assert r["weeks"][0]["start"] == "2027-01-04"
    assert r["meetings"][0]["date"] == "2027-01-04"
    assert any("week 1 starts Mon, Jan 4" in n for n in r["notes"])


def test_weekend_start_with_weekend_classes_stays():
    r = run(start_date="2027-01-02", meeting_days=["Sat"])
    assert r["weeks"][0]["start"] == "2027-01-02"
    assert r["meetings"][0]["date"] == "2027-01-02"


def test_no_meeting_days_still_dates_weeks_and_units():
    r = run(meeting_days=[])
    assert r["ok"] and r["meetings"] == []
    assert any("Choose the days" in w for w in r["warnings"])
    assert r["units"]["u1"]["label"] == "Jan 11–29"
    assert r["units"]["u1"]["last_meeting"] is None
    assert r["units"]["u1"]["due"] == {"essay": "2027-01-29"}  # the unit's last Friday


def test_break_in_week_one_moves_the_start():
    r = run(no_class=[{"start": "2027-01-04", "end": "2027-01-08", "label": "Snow"}])
    assert r["weeks"][0]["start"] == "2027-01-11"
    assert r["breaks"][0]["after_week"] == 0


# ---------------------------------------------------------------- due dates

def test_due_dates_default_to_the_units_last_meeting():
    r = run()
    due = {d["id"]: d for d in r["due"]}
    assert set(due) == {"essay", "project"}  # unselected and activity items get none
    assert due["essay"]["date"] == "2027-01-27"
    assert due["essay"]["label"] == "Wed, Jan 27"
    assert due["essay"]["overridden"] is False
    assert due["project"]["date"] == "2027-03-03"


def test_due_date_skips_a_cancelled_last_meeting():
    r = run(no_class=["2027-01-27"])
    assert r["units"]["u1"]["due"]["essay"] == "2027-01-25"


def test_short_term_assignments_get_due_dates_when_selected():
    units = [{**UNITS[0], "assignments": [{"id": "short", "role": "short_term", "selected": True}]}]
    r = run(units=units)
    assert r["due"][0]["id"] == "short"


def test_due_date_override():
    r = run(due_dates={"essay": "2027-02-01", "project": "garbage"})
    due = {d["id"]: d for d in r["due"]}
    assert due["essay"]["date"] == "2027-02-01" and due["essay"]["overridden"] is True
    assert due["essay"]["suggested"] == "2027-01-27"
    assert due["project"]["overridden"] is False


# ---------------------------------------------------------------- odd terms and units

def test_units_without_weeks_are_left_undated():
    units = [{"key": "x", "title": "X", "start_week": None, "end_week": None, "assignments": []}]
    r = run(units=units)
    assert r["units"] == {}


def test_units_past_the_end_of_the_term_are_still_dated():
    units = [{"key": "long", "title": "Long", "start_week": 2, "end_week": 12, "assignments": []}]
    r = run(units=units)
    assert len(r["weeks"]) == 13  # 12 unit weeks plus the final week
    assert r["weeks"][12]["fixed"] == S.FINAL
    assert r["units"]["long"]["end"] == "2027-03-26"


def test_week_owners_match_the_builder_grid():
    owners = S.week_owners({"weeks": 6, "fixed_start": 1, "fixed_end": 1},
                           [{"key": "a", "start_week": 2, "end_week": 3}])
    assert owners == {1: ("fixed", S.ORIENTATION), 2: ("unit", "a"), 3: ("unit", "a"), 6: ("fixed", S.FINAL)}


def test_missing_term_keys_fall_back():
    r = S.compute({}, [], sched())
    assert len(r["weeks"]) == 16


# ---------------------------------------------------------------- time zones and times

def test_unknown_time_zone_falls_back_to_utc():
    r = run(timezone="Mars/Olympus_Mons")
    assert r["timezone"] == "UTC"
    assert any("time zone" in w for w in r["warnings"])


def test_missing_time_zone_is_quietly_utc():
    r = S.compute(TERM, UNITS, {"start_date": "2027-01-04", "meeting_days": ["Mon"]})
    assert r["timezone"] == "UTC" and r["warnings"] == []


def test_due_datetime_carries_the_right_offset():
    assert S.due_datetime("2027-01-27", "America/Los_Angeles").isoformat() == "2027-01-27T23:59:00-08:00"
    assert S.due_datetime("2027-04-14", "America/Los_Angeles").isoformat() == "2027-04-14T23:59:00-07:00"


def test_class_times():
    r = run(start_time="10:00", end_time="10:50")
    assert (r["start_time"], r["end_time"]) == ("10:00", "10:50")
    start, end = S.meeting_span("2027-01-04", r)
    assert start.isoformat() == "2027-01-04T10:00:00-08:00" and end.hour == 10 and end.minute == 50


@pytest.mark.parametrize("st,et", [("10:00", "9:00"), ("ten", "11:00"), ("10:00", "")])
def test_bad_or_partial_class_times_mean_all_day(st, et):
    r = run(start_time=st, end_time=et)
    assert r["start_time"] is None and r["end_time"] is None
    assert S.meeting_span("2027-01-04", r) is None


# ---------------------------------------------------------------- labels and helpers

@pytest.mark.parametrize("a,b,label", [
    (date(2027, 1, 4), date(2027, 1, 4), "Jan 4"),
    (date(2027, 1, 4), date(2027, 1, 8), "Jan 4–8"),
    (date(2027, 1, 25), date(2027, 2, 12), "Jan 25 – Feb 12"),
    (date(2026, 12, 28), date(2027, 1, 8), "Dec 28, 2026 – Jan 8, 2027"),
    (date(2027, 1, 8), date(2027, 1, 4), "Jan 4–8"),
])
def test_range_label(a, b, label):
    assert S.range_label(a, b) == label


def test_spread():
    assert S.spread([1, 2, 3], 6) == [[1], [], [2], [], [3], []]
    assert S.spread(list("abcdefgh"), 6) == [["a", "b"], ["c"], ["d"], ["e", "f"], ["g"], ["h"]]
    assert S.spread([1], 0) == []
    assert S.spread([], 3) == [[], [], []]
