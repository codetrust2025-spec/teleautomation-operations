"""The "Bookings by candidate" pie counts the rows the roster shows.

On 12 Oct the Upcoming roster listed 5 interviews while the pie above it said
6: the pie's summary read Upcoming by date alone and kept a sitting from
earlier that day whose time had passed, which the roster had already dropped.
Both now use the roster's rule -- pending sittings whose time has not passed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from core.ist_time import IST
from features import candidate_store as cs

TODAY = date(2026, 10, 12)
NOON = datetime(2026, 10, 12, 12, 0, tzinfo=IST)


def _row(cid, name, day, start, end, status=""):
    return {
        "id": cid, "name": name, "phone": "9000000" + cid[-3:], "date": day, "time": start, "time_end": end,
        "slot_confirmed": True, "interview_attendance_status": status, "stage": "in_progress",
        "technology": "Python", "interview_round": "L1", "interview_attendee": "Bhavana",
    }


@pytest.fixture
def store(monkeypatch):
    rows = [
        _row("r-001", "Morning Sitting", TODAY.isoformat(), "11:00", "11:30"),        # passed at noon
        _row("r-002", "Afternoon Sitting", TODAY.isoformat(), "14:30", "15:00"),
        _row("r-003", "Afternoon Sitting", TODAY.isoformat(), "16:00", "16:30"),
        _row("r-004", "Next Week Sitting", (TODAY + timedelta(days=7)).isoformat(), "10:00", "10:30"),
        _row("r-005", "Already Attended", TODAY.isoformat(), "17:00", "17:30", status="attended"),
    ]
    monkeypatch.setattr(cs, "_load", lambda force=False: {"candidates": rows})
    monkeypatch.setattr("core.ist_time.ist_now", lambda now=None: NOON)
    return rows


def _range():
    return TODAY.isoformat(), (TODAY + timedelta(days=30)).isoformat()


def test_upcoming_pie_counts_the_rows_the_roster_shows(store):
    roster = cs.interview_monitor(*_range(), upcoming_only=True)
    summary = cs.interview_global_summary(*_range(), upcoming_only=True)

    shown = sorted(row["id"] for row in roster["interviews"])
    assert shown == ["r-002", "r-003", "r-004"]
    assert summary["booking_overview"]["total"] == len(shown)
    assert summary["interviews"]["count"] == len(shown)
    assert {c["name"]: c["count"] for c in summary["booking_overview"]["by_candidate"]} == {
        "Afternoon Sitting": 2, "Next Week Sitting": 1,
    }


def test_all_unresolved_still_counts_every_waiting_sitting(store):
    summary = cs.interview_global_summary(*_range(), unresolved_only=True)
    roster = cs.interview_monitor(*_range(), unresolved_only=True)

    assert summary["booking_overview"]["total"] == len(roster["interviews"]) == 4
