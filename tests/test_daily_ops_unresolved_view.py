"""Daily Ops "All unresolved": every interview still waiting for an outcome, at any date.

Today, Upcoming, This week and a picked month all answer "what is on around
now". None of them answers "what have we never closed" — an interview booked in
May and forgotten is outside every range an operator would think to pick, so it
was invisible in Daily Ops however long it sat there. `unresolved_only` reads
the whole roster instead and returns exactly the rows still waiting: no stored
status, and the booking still standing. Setting a status takes a row out of the
list, because the list is defined by not having one.

The rule itself is the existing one (`_filter_upcoming_only_rows`), so nothing
here invents a second opinion about which sittings are still live.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from api.routers import candidates as route
from features import candidate_store as cs

TODAY = date.today().isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()
LAST_WEEK = (date.today() - timedelta(days=7)).isoformat()
MAY = "2026-05-04"
JUNE = "2026-06-18"
THIS_WEEK = (TODAY, (date.today() + timedelta(days=6)).isoformat())


def row(cid, day, **overrides):
    base = {
        "id": cid, "name": cid.title(), "date": day, "time": "10:00", "time_end": "11:00",
        "slot_confirmed": True, "stage": "in_progress", "interview_attendance_status": "",
        "interview_attendee": "Bhavana", "technology": "Python", "interview_round": "L1",
    }
    base.update(overrides)
    return base


@pytest.fixture
def store(monkeypatch):
    """A roster that lives in memory for the length of one test."""
    data = {"candidates": []}
    monkeypatch.setattr(cs, "_load", lambda *args, **kwargs: data)
    monkeypatch.setattr(cs, "_save", lambda payload: data.update(payload))
    return data


def unresolved(**kwargs):
    """The view, asked for through the same narrow range the screen was on."""
    return cs.interview_monitor(*THIS_WEEK, unresolved_only=True, **kwargs)


def listed(**kwargs):
    return [r["id"] for r in unresolved(**kwargs)["interviews"]]


# ── The forgotten interviews this view exists for ───────────────────────────

def test_an_interview_nobody_closed_in_may_is_listed(store):
    store["candidates"] += [row("may", MAY), row("june", JUNE), row("today", TODAY)]

    assert listed() == ["may", "june", "today"]


def test_the_requested_range_is_ignored_and_the_payload_says_so(store):
    store["candidates"].append(row("may", MAY))

    payload = unresolved()

    assert [r["id"] for r in payload["interviews"]] == ["may"]
    assert payload["unresolved_only"] is True
    assert (payload["from"], payload["to"]) == cs._ALL_TIME_SPAN


def test_the_dated_view_still_only_answers_about_its_range(store):
    """The flag off is the old behaviour, exactly."""
    store["candidates"] += [row("may", MAY), row("today", TODAY)]

    assert [r["id"] for r in cs.interview_monitor(*THIS_WEEK)["interviews"]] == ["today"]


# ── What "unresolved" means ─────────────────────────────────────────────────

@pytest.mark.parametrize(("status", "waiting"), [
    ("", True),                                   # no outcome recorded: the whole point
    ("attended", False),
    ("not_attended", False),
    ("cancelled", False),
    ("rescheduled", False),
    (cs.RELEASED_FOR_RESCHEDULE_STATUS, False),   # Awaiting new slot
    ("re_service", False),
])
def test_only_a_sitting_with_no_outcome_is_waiting(store, status, waiting):
    store["candidates"].append(row("old", MAY, interview_attendance_status=status))

    assert (listed() == ["old"]) is waiting


def test_a_replaced_sitting_is_not_waiting(store):
    """It carries no status of its own, but the interview moved to another row."""
    store["candidates"] += [row("old", MAY, superseded_by_booking_id="replacement"),
                            row("replacement", TOMORROW)]

    assert listed() == ["replacement"]


def test_an_unconfirmed_slot_is_not_a_booking(store):
    store["candidates"].append(row("draft", MAY, slot_confirmed=False))

    assert listed() == []


def test_a_dropped_candidate_is_not_listed(store):
    store["candidates"].append(row("gone", MAY, stage="dropped"))

    assert listed() == []


# ── Updating a status resolves the row ──────────────────────────────────────

@pytest.mark.parametrize("status", ["attended", "not_attended", "cancelled", "rescheduled",
                                    cs.RELEASED_FOR_RESCHEDULE_STATUS, "re_service"])
def test_setting_a_status_takes_the_row_out_of_the_list(store, status):
    """Through the real write path the row dropdown posts to."""
    store["candidates"] += [row("old", MAY), row("other", JUNE)]
    assert listed() == ["old", "other"]

    cs.set_interview_attendance("old", status=status, remark="closed from the pending list",
                                by="tests")

    assert listed() == ["other"]


def test_the_row_itself_survives_being_resolved(store):
    """Out of the list, not out of the roster: Daily Ops and history keep it."""
    store["candidates"].append(row("old", MAY))

    cs.set_interview_attendance("old", status="attended", remark="went ahead", by="tests")

    kept = store["candidates"][0]
    assert (kept["id"], kept["date"], kept["interview_attendance_status"]) == ("old", MAY, "attended")
    assert len(store["candidates"]) == 1


# ── Order, counts and the other filters ─────────────────────────────────────

def test_oldest_first(store):
    store["candidates"] += [row("today", TODAY), row("june", JUNE), row("may", MAY),
                            row("last_week", LAST_WEEK)]

    assert listed() == ["may", "june", "last_week", "today"]


def test_the_counters_describe_the_rows(store):
    store["candidates"] += [row("may", MAY), row("june", JUNE),
                            row("done", MAY, interview_attendance_status="attended")]

    payload = unresolved()

    assert payload["count"] == len(payload["interviews"]) == payload["pending_count"] == 2
    assert payload["attended_count"] == 0
    # One list, not two: nothing is split off into the Awaiting outcome section.
    assert (payload["awaiting_interviews"], payload["awaiting_count"]) == ([], 0)


def test_it_wins_over_the_upcoming_view(store):
    """Both flags set is one question, not two overlapping lists."""
    store["candidates"] += [row("may", MAY), row("tomorrow", TOMORROW)]

    payload = cs.interview_monitor(*THIS_WEEK, upcoming_only=True, unresolved_only=True)

    assert [r["id"] for r in payload["interviews"]] == ["may", "tomorrow"]
    assert payload["awaiting_interviews"] == []


def test_the_non_date_filters_still_apply(store):
    store["candidates"] += [row("mine", MAY, interview_attendee="Nikhila"),
                            row("theirs", JUNE, interview_attendee="Bhavana")]

    assert listed(filter_attendee="Nikhila") == ["mine"]
    assert listed(filter_search="Theirs") == ["theirs"]


def test_the_summary_above_the_table_counts_the_same_rows(store):
    """The KPI cards read this; they may not disagree with the list."""
    store["candidates"] += [row("may", MAY), row("june", JUNE),
                            row("closed", LAST_WEEK, interview_attendance_status="attended")]

    summary = cs.interview_global_summary(*THIS_WEEK, unresolved_only=True)

    assert summary["interviews"]["pending_count"] == len(unresolved()["interviews"]) == 2
    assert summary["interviews"]["count"] == 2
    assert summary["interviews"]["attended_count"] == 0
    assert sum(item["count"] for item in summary["booking_overview"]["by_candidate"]) == 2


# ── The route carries the flag ──────────────────────────────────────────────

def test_the_monitor_route_passes_the_flag_through(store, monkeypatch):
    monkeypatch.setattr(route, "_viewer_reference", lambda request: None)
    store["candidates"] += [row("may", MAY), row("tomorrow", TOMORROW)]

    payload = asyncio.run(route.candidates_interviews_monitor(
        request=None, from_date=THIS_WEEK[0], to_date=THIS_WEEK[1], attendee=None, search=None,
        channel=None, round=None, technology=None, upcoming_only=False, unresolved_only=True,
    ))

    assert payload["status"] == "ok"
    assert [r["id"] for r in payload["interviews"]] == ["may", "tomorrow"]
