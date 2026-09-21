"""A sitting marked Rescheduled in Daily Ops leaves Confirmed slots at once.

On 21 Sep an operator booked a candidate's replacement interview at 16:45 and
marked the 15:00 sitting Rescheduled ("panel not available"), yet Confirmed
slots went on listing 15:00 as a live booking. `rescheduled` still counted as
standing -- a rule kept for bookings moved to a new time in place, which no
longer keep the marker. Now every sitting that will not be sat leaves the
active list: cancelled, rescheduled, awaiting a new slot, or replaced. The
row itself stays, with its schedule and the operator's note, in Daily Ops; a
booking that is moved stands at its new time; and mail about a Rescheduled
interview still acts on that row.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from core import recruitment_mail_store as ms
from features import candidate_store as cs
from services import interview_auto_booking as booking
from tests.test_interview_auto_booking import (  # noqa: F401 - shared harness
    CAPGEMINI_ATS_UID, execute, execute_event, install_store_fakes, result, slot_writer,
)

TODAY = date.today().isoformat()
LATER = (date.today() + timedelta(days=3)).isoformat()


def row(**overrides):
    base = {"id": "sitting", "name": "Rahul", "date": TODAY, "time": "15:00", "time_end": "15:30",
            "slot_confirmed": True, "interview_attendee": "Bhavana", "stage": "in_progress"}
    base.update(overrides)
    return base


@pytest.fixture
def store(monkeypatch):
    """A roster that lives in memory for the length of one test."""
    data = {"candidates": []}
    monkeypatch.setattr(cs, "_load", lambda *args, **kwargs: data)
    monkeypatch.setattr(cs, "_save", lambda payload: data.update(payload))
    return data


def confirmed_slots():
    return [(slot["date"], slot["time"]) for slot in cs.public_booked_interview_slots(days=30)["slots"]]


# ── What Confirmed slots lists, status by status ────────────────────────────

@pytest.mark.parametrize(("status", "listed"), [
    ("", True),                                   # Pending
    ("attended", True),                           # today's outcome, listed as before
    ("not_attended", True),
    ("re_service", True),
    ("cancelled", False),
    ("rescheduled", False),
    (cs.RELEASED_FOR_RESCHEDULE_STATUS, False),   # Awaiting new slot
])
def test_confirmed_slots_by_daily_ops_status(store, status, listed):
    store["candidates"].append(row(interview_attendance_status=status))

    assert (confirmed_slots() == [(TODAY, "15:00")]) is listed


def test_a_replaced_sitting_is_not_listed(store):
    store["candidates"] += [row(superseded_by_booking_id="replacement"),
                            row(id="replacement", time="16:45", time_end="17:15")]

    assert confirmed_slots() == [(TODAY, "16:45")]


# ── The 21 Sep case ─────────────────────────────────────────────────────────

def test_a_sitting_marked_rescheduled_leaves_confirmed_slots_at_once(store):
    store["candidates"] += [row(), row(id="replacement", time="16:45", time_end="17:15")]
    assert confirmed_slots() == [(TODAY, "15:00"), (TODAY, "16:45")]

    cs.set_interview_attendance("sitting", status="rescheduled", remark="panel not available",
                                by="operations_admin")

    assert confirmed_slots() == [(TODAY, "16:45")]
    # Nothing is deleted: the row keeps what was booked and what the operator said.
    kept = store["candidates"][0]
    assert (kept["date"], kept["time"], kept["time_end"]) == (TODAY, "15:00", "15:30")
    assert (kept["interview_attendance_status"], kept["interview_attendance_remark"]) == (
        "rescheduled", "panel not available")
    # Its hour is free again, and asking for that slot again is a new booking.
    assert cs.find_interview_slot_conflicts(TODAY, "15:00", "15:30") == []
    assert cs.same_standing_booking("sitting", date=TODAY, time="15:00", time_end="15:30") is None


def test_daily_ops_still_shows_it_under_rescheduled(store):
    store["candidates"] += [row(interview_attendance_status="rescheduled"),
                            row(id="replacement", time="16:45", time_end="17:15")]

    roster = cs.daily_interview_roster(TODAY)

    assert sorted(r["id"] for r in roster["interviews"]) == ["replacement", "sitting"]
    assert roster["rescheduled_count"] == 1
    assert roster["pending_count"] == 1


def test_a_rescheduled_booking_moved_to_its_new_time_is_listed_there(store):
    store["candidates"].append(row(interview_attendance_status="rescheduled",
                                   interview_attendance_remark="panel not available"))

    cs.update_interview_slot(candidate_id="sitting", date=LATER, time="11:00", time_end="11:30")

    assert confirmed_slots() == [(LATER, "11:00")]


# ── Mail about a Rescheduled interview still acts on its row ────────────────

MARKED = {"id": "slot-old", "name": "Rahul", "slot_confirmed": True, "date": "2099-07-20",
          "time": "15:00", "time_end": "15:30", "interview_attendance_status": "rescheduled",
          "interview_source_message_id": "invite-a", "interview_source_thread_id": "thread-a"}
OTHER = {"id": "slot-other", "name": "Rahul", "slot_confirmed": True, "date": "2099-07-25",
         "time": "12:00", "time_end": "12:30", "interview_source_message_id": "invite-b",
         "interview_source_thread_id": "thread-b"}


def test_a_calendar_update_still_moves_the_sitting_marked_rescheduled(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    marked = {**MARKED, "interview_calendar_uid": CAPGEMINI_ATS_UID}
    install_store_fakes(monkeypatch, rows=[marked, dict(OTHER)])
    moved = []
    monkeypatch.setattr(booking.candidate_store, "update_interview_slot", slot_writer(capture=moved))
    value = result(date="2099-07-22", time="11:00 AM", end_time="11:30 AM")
    value["calendar"] = {"uid": CAPGEMINI_ATS_UID, "method": "REQUEST", "sequence": 3}

    outcome = execute(value)

    assert outcome["status"] == "Rescheduled"
    assert [m["candidate_id"] for m in moved] == ["slot-old"]


@pytest.mark.parametrize("others", [[OTHER], []], ids=["one-other-booking", "no-other-booking"])
def test_a_reschedule_mail_moves_the_rescheduled_sitting_never_another_booking(monkeypatch, others):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch, rows=[dict(MARKED), *[dict(o) for o in others]])
    moved = []
    monkeypatch.setattr(booking.candidate_store, "update_interview_slot", slot_writer(capture=moved))

    outcome = execute_event(result(classification="interview_rescheduled", date="2099-07-22",
                                   time="11:00 AM", end_time="11:30 AM"),
                            message_id="resched-mail", thread_id="thread-a")

    assert outcome["status"] == "Rescheduled"
    assert [m["candidate_id"] for m in moved] == ["slot-old"]


def test_a_cancellation_mail_still_lands_on_the_rescheduled_sitting(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch, rows=[dict(MARKED), dict(OTHER)])
    cancelled = []
    monkeypatch.setattr(booking.candidate_store, "cancel_interview_slot",
                        lambda candidate_id: cancelled.append(candidate_id) or {"id": candidate_id})

    outcome = execute_event(result(classification="interview_cancelled"),
                            message_id="cancel-mail", thread_id="thread-a")

    assert outcome["status"] == "Cancelled"
    assert cancelled == ["slot-old"]


def test_a_replaced_rescheduled_sitting_is_not_mails_to_act_on(monkeypatch):
    install_store_fakes(monkeypatch, rows=[{**MARKED, "superseded_by_booking_id": "slot-other"}, dict(OTHER)])

    assert [r["id"] for r in booking._confirmed_slots({"id": "c1"})] == ["slot-other"]


# ── Mail Alerts agrees with Daily Ops and Confirmed slots ───────────────────

def test_a_mail_claiming_a_booking_marked_rescheduled_reads_as_rescheduled(monkeypatch):
    monkeypatch.setattr(cs, "get_candidate", lambda cid: {
        "id": "b1", "date": TODAY, "time": "15:00", "slot_confirmed": True,
        "interview_attendance_status": "rescheduled"})
    rows = [{"id": "n1", "booking_status": "Auto Booked", "booking_id": "b1",
             "candidate_status": "Interview Automatically Booked"}]

    ms.reconcile_booking_claims(rows)

    assert rows[0]["booking_status"] == ms.RESCHEDULED_BOOKING_STATUS
    assert rows[0]["candidate_status"] == "Interview Rescheduled"
