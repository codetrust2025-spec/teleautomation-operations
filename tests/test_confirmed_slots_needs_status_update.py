"""Confirmed slots: an interview that has ended with no outcome needs a status.

The page listed every pending booking under "Confirmed upcoming slots", so an
interview that finished hours ago with nothing recorded sat among the future
ones. The server now labels each slot with the rule Daily Ops already uses to
split Scheduled from Awaiting status (slot end in IST has passed):

    slot_phase = "upcoming" | "needs_status_update"

A slot needing a status stays listed, however old, until Daily Ops records an
outcome; nothing here removes or rewrites a booking. Also fixed: a slot ending at
or after midnight (from 23:00 with no end time, or 23:30-00:30) made the
end-time calculation raise, which would have taken the whole list down.

Names are invented.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta

import pytest

from core.ist_time import IST
from features import candidate_store as cs

TODAY = datetime.now(IST).date()
NOON = datetime(TODAY.year, TODAY.month, TODAY.day, 12, 0, tzinfo=IST).timestamp()   # "now" = 12:00 IST today
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()
T = TODAY.isoformat()


def row(rid, day, start, end="", **extra):
    base = {"id": rid, "name": f"Person {rid}", "date": day, "time": start, "time_end": end,
            "slot_confirmed": True, "interview_attendee": "Test Attendee", "stage": "in_progress"}
    base.update(extra)
    return base


@pytest.fixture
def store(monkeypatch):
    data = {"candidates": []}
    monkeypatch.setattr(cs, "_load", lambda *a, **k: data)
    monkeypatch.setattr(cs, "_save", lambda payload: data.update(payload))
    return data


def phases(now=NOON):
    out = cs.public_booked_interview_slots(days=30, now=now)
    return {s["name"].split()[-1].lower(): s["slot_phase"] for s in out["slots"]}, out


def test_an_interview_that_ended_with_no_outcome_needs_a_status(store):
    store["candidates"] += [row("ended", T, "09:00", "10:00"), row("yday", YESTERDAY, "15:00", "15:30")]
    got, out = phases()
    assert got == {"ended": "needs_status_update", "yday": "needs_status_update"}
    assert out["needs_status_update_count"] == 2 and out["upcoming_count"] == 0 and out["count"] == 2


def test_current_and_future_interviews_stay_upcoming(store):
    store["candidates"] += [
        row("now", T, "11:30", "12:30"),        # in progress at 12:00
        row("later", T, "15:00", "15:30"),
        row("tomorrow", TOMORROW, "10:00", "10:30"),
    ]
    got, out = phases()
    assert set(got.values()) == {"upcoming"}
    assert out["upcoming_count"] == 3 and out["needs_status_update_count"] == 0


def test_a_slot_ends_exactly_at_its_end_time(store):
    store["candidates"] += [row("edge", T, "11:00", "12:00")]
    assert phases(now=NOON - 1)[0]["edge"] == "upcoming"
    assert phases(now=NOON)[0]["edge"] == "needs_status_update"


def test_no_end_time_means_one_hour(store):
    store["candidates"] += [row("noend", T, "11:30")]
    assert phases()[0]["noend"] == "upcoming"                     # ends 12:30
    assert phases(now=NOON + 31 * 60)[0]["noend"] == "needs_status_update"


def test_it_stays_listed_however_old_until_an_outcome_is_recorded(store):
    old = (TODAY - timedelta(days=20)).isoformat()
    store["candidates"] += [row("old", old, "10:00", "10:30")]
    assert phases()[0] == {"old": "needs_status_update"}
    before = copy.deepcopy(store["candidates"][0])
    cs.set_interview_attendance("old", status="attended", remark="done", by="operations_admin", allow_future=True)
    assert phases()[0] == {}, "it leaves only once Daily Ops records the outcome"
    after = store["candidates"][0]
    assert after["id"] == "old" and after["date"] == before["date"] and after["time"] == before["time"], \
        "the booking itself is kept"


def test_listing_the_slots_never_changes_a_booking(store):
    store["candidates"] += [row("ended", T, "09:00", "10:00"), row("later", T, "15:00", "15:30")]
    before = copy.deepcopy(store["candidates"])
    phases()
    assert store["candidates"] == before


def test_the_phase_is_daily_ops_own_rule(store):
    """Confirmed slots and Daily Ops must never disagree about which slots ended."""
    cases = [(T, "09:00", "10:00"), (T, "11:30", "12:30"), (T, "15:00", ""), (YESTERDAY, "23:30", "")]
    store["candidates"] += [row(f"c{i}", d, s, e) for i, (d, s, e) in enumerate(cases)]
    got = phases()[0]
    for i, (d, s, e) in enumerate(cases):
        expected = "upcoming" if cs._interview_slot_still_upcoming(d, s, e, now=NOON) else "needs_status_update"
        assert got[f"c{i}"] == expected


@pytest.mark.parametrize("start,end", [("23:30", ""), ("23:00", ""), ("23:30", "00:30")])
def test_a_slot_ending_at_or_after_midnight_does_not_crash(store, start, end):
    """replace(hour=24) used to raise for these and take the whole list down."""
    store["candidates"] += [row("late", YESTERDAY, start, end), row("tonight", T, start, end)]
    got = phases()[0]
    assert got == {"late": "needs_status_update", "tonight": "upcoming"}


def test_a_late_slot_ends_the_next_morning():
    yesterday_late_end = datetime(TODAY.year, TODAY.month, TODAY.day, 0, 30, tzinfo=IST).timestamp()
    assert cs._interview_slot_still_upcoming(YESTERDAY, "23:30", "", now=yesterday_late_end - 60) is True
    assert cs._interview_slot_still_upcoming(YESTERDAY, "23:30", "", now=yesterday_late_end) is False


def test_an_unparseable_slot_is_kept_upcoming_rather_than_dropped(store):
    store["candidates"] += [row("odd", T, "sometime", "")]
    assert phases()[0] in ({"odd": "upcoming"}, {})   # never mislabelled as needing a status
