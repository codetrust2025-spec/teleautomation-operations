"""Regression tests for Daily Ops awaiting outcome section and interview_monitor counts.

Verifies:
1. interview_monitor with upcoming_only=True computes counts strictly from the filtered rows (Requirement 6).
2. interview_monitor's Upcoming holds only sittings still to come; a past-due one leaves it for
   All unresolved (the Awaiting outcome section under the table was retired on 28 Sep 2026).
3. Resolved interviews (attended, cancelled, etc.) appear in neither list.
4. Date boundaries: a slot in the future is Upcoming; one whose end time has passed is not.
5. interview_upcoming returns scheduled_interviews and awaiting_interviews matching counts.
"""

from datetime import date, datetime, timedelta
import pytest
from core.ist_time import ist_now
from features import candidate_store as cs


def _make_candidate(
    cid: str,
    name: str,
    date_str: str,
    time_str: str = "10:00 AM",
    time_end_str: str = "11:00 AM",
    status: str = "",
    slot_confirmed: bool = True,
    superseded_by: str | None = None,
) -> dict:
    return {
        "id": cid,
        "name": name,
        "date": date_str,
        "time": time_str,
        "time_end": time_end_str,
        "slot_confirmed": slot_confirmed,
        "interview_attendance_status": status,
        "superseded_by_booking_id": superseded_by or "",
        "stage": "in_progress",
        "technology": "Python",
        "interview_round": "L1",
    }


def test_interview_monitor_counts_match_filtered_rows(monkeypatch):
    """Requirement 6: returned counts in interview_monitor must match the filtered rows."""
    today = date.today()
    future_date = (today + timedelta(days=2)).isoformat()

    mock_candidates = [
        # Future pending slot
        _make_candidate("c1", "Candidate Future", future_date, "04:00 PM", "05:00 PM", status=""),
        # Future attended slot (should be excluded by upcoming_only)
        _make_candidate("c2", "Candidate Attended", future_date, "02:00 PM", "03:00 PM", status="attended"),
    ]

    monkeypatch.setattr(cs, "_load", lambda: {"candidates": mock_candidates})

    result = cs.interview_monitor(
        today.isoformat(),
        (today + timedelta(days=7)).isoformat(),
        upcoming_only=True,
    )

    # Only c1 should be in interviews
    assert len(result["interviews"]) == 1
    assert result["interviews"][0]["id"] == "c1"
    assert result["count"] == 1

    # Counts must match filtered rows: 1 pending, 0 attended
    assert result["pending_count"] == 1
    assert result["attended_count"] == 0


def test_a_past_pending_interview_simply_leaves_upcoming(monkeypatch):
    """It used to be handed to an Awaiting outcome section under the table.
    That section is gone: All unresolved is the one list for them now."""
    today = date.today()
    past_date = (today - timedelta(days=5)).isoformat()
    future_date = (today + timedelta(days=2)).isoformat()

    mock_candidates = [
        # Future pending slot -> upcoming
        _make_candidate("c_future", "Future Candidate", future_date, "03:00 PM", "04:00 PM", status=""),
        # Past slot without status -> awaiting outcome
        _make_candidate("c_past_pending", "Overdue Candidate", past_date, "11:00 AM", "12:00 PM", status=""),
        # Past slot with attended status -> resolved, neither upcoming nor awaiting
        _make_candidate("c_past_done", "Attended Candidate", past_date, "10:00 AM", "11:00 AM", status="attended"),
        # Past slot with cancelled status -> resolved
        _make_candidate("c_past_cancelled", "Cancelled Candidate", past_date, "01:00 PM", "02:00 PM", status="cancelled"),
        # Past slot superseded by another booking -> not active
        _make_candidate("c_past_superseded", "Superseded Candidate", past_date, "02:00 PM", "03:00 PM", status="", superseded_by="other_booking"),
    ]

    monkeypatch.setattr(cs, "_load", lambda: {"candidates": mock_candidates})

    result = cs.interview_monitor(
        today.isoformat(),
        (today + timedelta(days=7)).isoformat(),
        upcoming_only=True,
    )

    # Upcoming list has only the future slot
    upcoming_ids = [r["id"] for r in result["interviews"]]
    assert upcoming_ids == ["c_future"]
    assert result["count"] == 1

    # Nothing is split off, and the overdue sitting is not quietly in Upcoming.
    assert (result["awaiting_interviews"], result["awaiting_count"]) == ([], 0)
    assert "c_past_pending" not in upcoming_ids

    # It is under All unresolved, with every other sitting still waiting.
    unresolved = cs.interview_monitor(
        today.isoformat(), (today + timedelta(days=7)).isoformat(), unresolved_only=True,
    )
    assert [r["id"] for r in unresolved["interviews"]] == ["c_past_pending", "c_future"]


def test_interview_upcoming_returns_split_lists(monkeypatch):
    """interview_upcoming provides both scheduled_interviews and awaiting_interviews."""
    today = date.today()
    past_date = (today - timedelta(days=3)).isoformat()
    future_date = (today + timedelta(days=3)).isoformat()

    mock_candidates = [
        _make_candidate("c1", "Upcoming 1", future_date, "02:00 PM", "03:00 PM", status=""),
        _make_candidate("c2", "Overdue 1", past_date, "11:00 AM", "12:00 PM", status=""),
    ]

    monkeypatch.setattr(cs, "_load", lambda: {"candidates": mock_candidates})

    result = cs.interview_upcoming(days=14, lookback_days=30)

    assert result["scheduled_count"] == 1
    assert result["awaiting_status_count"] == 1
    assert result["pending_count"] == 2
    assert [r["id"] for r in result["scheduled_interviews"]] == ["c1"]
    assert [r["id"] for r in result["awaiting_interviews"]] == ["c2"]


def test_date_boundary_same_day_slot_timing(monkeypatch):
    """Slot earlier today whose end time has passed is awaiting outcome, later today is upcoming."""
    now_ist = ist_now()
    today_str = now_ist.strftime("%Y-%m-%d")

    # Slot that ended 2 hours ago
    past_hour = max(0, now_ist.hour - 2)
    past_time = f"{past_hour:02d}:00"
    past_end = f"{past_hour:02d}:30"

    # Slot that ends 2 hours from now
    future_hour = min(23, now_ist.hour + 2)
    future_time = f"{future_hour:02d}:00"
    future_end = f"{future_hour:02d}:30"

    mock_candidates = [
        _make_candidate("c_today_past", "Ended Today", today_str, past_time, past_end, status=""),
        _make_candidate("c_today_future", "Future Today", today_str, future_time, future_end, status=""),
    ]

    monkeypatch.setattr(cs, "_load", lambda: {"candidates": mock_candidates})

    scheduled, awaiting = cs._split_pending_interviews_by_slot_phase(mock_candidates)

    if past_hour < now_ist.hour:
        assert any(r["id"] == "c_today_past" for r in awaiting)
    if future_hour > now_ist.hour:
        assert any(r["id"] == "c_today_future" for r in scheduled)


# ---------------------------------------------------------------------------
# New regression tests for time-aware interview_monitor fix
# (upcoming_only uses _split_pending_interviews_by_slot_phase, not
#  _filter_upcoming_only_rows, so slot end-time is respected intra-day)
# ---------------------------------------------------------------------------


def test_monitor_future_slot_today_stays_in_upcoming(monkeypatch):
    """A pending slot whose end time is still in the future today must appear
    in interview_monitor.interviews (Upcoming), NOT in awaiting_interviews."""
    # The clock is pinned to 10:00 IST. Reading the real one made this fail every
    # night after 22:30 IST: the "future" slot was capped at 22:00-22:30 and had
    # already ended, and date.today() (UTC on CI) is not the IST date after 18:30 UTC.
    now_ist = ist_now().replace(hour=10, minute=0, second=0, microsecond=0)
    monkeypatch.setattr("core.ist_time.ist_now", lambda *_args, **_kwargs: now_ist)
    today_str = now_ist.strftime("%Y-%m-%d")
    today = now_ist.date()

    # A slot that ends 2 hours from now (guaranteed future)
    future_hour = now_ist.hour + 2
    slot_time = f"{future_hour:02d}:00"
    slot_end = f"{future_hour:02d}:30"

    mock_candidates = [
        _make_candidate("c_future_today", "Future Today", today_str, slot_time, slot_end, status=""),
    ]
    monkeypatch.setattr(cs, "_load", lambda: {"candidates": mock_candidates})

    result = cs.interview_monitor(
        today.isoformat(),
        (today + timedelta(days=30)).isoformat(),
        upcoming_only=True,
    )

    upcoming_ids = [r["id"] for r in result["interviews"]]
    awaiting_ids = [r["id"] for r in result["awaiting_interviews"]]
    assert "c_future_today" in upcoming_ids, "Future slot should be in Upcoming"
    assert "c_future_today" not in awaiting_ids, "Future slot must NOT be in Awaiting"


def test_monitor_slot_ended_minutes_ago_leaves_upcoming(monkeypatch):
    """A pending slot whose end time passed today leaves Upcoming on the hour,
    without waiting for the date to roll over, and is in All unresolved."""
    now_ist = ist_now()
    today_str = now_ist.strftime("%Y-%m-%d")
    today = date.today()

    # A slot that ended 2 hours ago — safely in the past
    past_hour = max(0, now_ist.hour - 2)
    slot_time = f"{past_hour:02d}:00"
    slot_end = f"{past_hour:02d}:30"

    if past_hour >= now_ist.hour:
        pytest.skip("Cannot construct a past slot (running too early in the day)")

    mock_candidates = [
        _make_candidate("c_past_today", "Ended Today", today_str, slot_time, slot_end, status=""),
    ]
    monkeypatch.setattr(cs, "_load", lambda: {"candidates": mock_candidates})

    result = cs.interview_monitor(
        today.isoformat(),
        (today + timedelta(days=30)).isoformat(),
        upcoming_only=True,
    )

    upcoming_ids = [r["id"] for r in result["interviews"]]
    assert "c_past_today" not in upcoming_ids, "Ended slot must NOT be in Upcoming"
    assert result["awaiting_interviews"] == [], "Upcoming no longer splits its rows"

    unresolved = cs.interview_monitor(
        today.isoformat(), (today + timedelta(days=30)).isoformat(), unresolved_only=True,
    )
    assert "c_past_today" in [r["id"] for r in unresolved["interviews"]]


def test_monitor_future_date_slot_always_upcoming(monkeypatch):
    """A pending slot on a future date (not today) is always Upcoming."""
    today = date.today()
    future_date = (today + timedelta(days=5)).isoformat()

    mock_candidates = [
        _make_candidate("c_future_date", "Future Date", future_date, "10:00 AM", "11:00 AM", status=""),
    ]
    monkeypatch.setattr(cs, "_load", lambda: {"candidates": mock_candidates})

    result = cs.interview_monitor(
        today.isoformat(),
        (today + timedelta(days=30)).isoformat(),
        upcoming_only=True,
    )

    upcoming_ids = [r["id"] for r in result["interviews"]]
    awaiting_ids = [r["id"] for r in result["awaiting_interviews"]]
    assert "c_future_date" in upcoming_ids
    assert "c_future_date" not in awaiting_ids


def test_upcoming_and_all_unresolved_account_for_every_pending_sitting(monkeypatch):
    """The single-source-of-truth assertion, in its new shape: Upcoming holds
    the sittings still to come, All unresolved holds every sitting with no
    outcome, and none falls between the two."""
    now_ist = ist_now()
    today = date.today()
    today_str = now_ist.strftime("%Y-%m-%d")
    past_date = (today - timedelta(days=3)).isoformat()
    future_date = (today + timedelta(days=3)).isoformat()

    # Slot ended 2 hours ago
    past_hour = max(0, now_ist.hour - 2)
    slot_time = f"{past_hour:02d}:00"
    slot_end = f"{past_hour:02d}:30"

    mock_candidates = [
        # Clearly future slot
        _make_candidate("c1", "Future 1", future_date, "02:00 PM", "03:00 PM", status=""),
        # Past date slot, unresolved
        _make_candidate("c2", "Overdue 1", past_date, "11:00 AM", "12:00 PM", status=""),
        # Today slot ended (only if we can safely build it)
        *(
            [_make_candidate("c3", "Ended Today", today_str, slot_time, slot_end, status="")]
            if past_hour < now_ist.hour
            else []
        ),
        # Resolved — must not appear in either
        _make_candidate("c4", "Attended", future_date, "04:00 PM", "05:00 PM", status="attended"),
    ]
    monkeypatch.setattr(cs, "_load", lambda: {"candidates": mock_candidates})

    monitor_result = cs.interview_monitor(
        today.isoformat(),
        (today + timedelta(days=30)).isoformat(),
        upcoming_only=True,
    )
    unresolved_result = cs.interview_monitor(
        today.isoformat(), (today + timedelta(days=30)).isoformat(), unresolved_only=True,
    )
    upcoming_result = cs.interview_upcoming(days=30, lookback_days=30)

    # Upcoming is exactly the scheduled half, and splits nothing off.
    assert monitor_result["count"] == upcoming_result["scheduled_count"]
    assert monitor_result["awaiting_count"] == 0
    # All unresolved holds every pending sitting, Upcoming's included, and the
    # resolved one ("c4", attended) is in neither.
    assert unresolved_result["count"] == upcoming_result["pending_count"]
    assert ({r["id"] for r in monitor_result["interviews"]}
            <= {r["id"] for r in unresolved_result["interviews"]})
    assert "c4" not in {r["id"] for r in unresolved_result["interviews"]}

