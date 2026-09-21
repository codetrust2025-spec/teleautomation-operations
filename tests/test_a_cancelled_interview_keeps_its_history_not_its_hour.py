"""Cancelling an interview leaves the schedule behind, and that has to be safe.

Daily Ops can mark a round Cancelled, and the row keeps its date and time --
deliberately, because the record of what was booked is worth having. The
release path is the other one: `cancel_interview_slot` empties date, time and
confirmation, and that is what every reader used to key on.

So a booking cancelled the first way stayed "confirmed" everywhere, which was
found on Pavan's 27 Aug row: the hour still blocked the public form, the
booking service still offered it as a duplicate to refuse a re-invitation
against, and Mail Alerts still reported "Automatically Booked" for an
interview Daily Ops showed as cancelled.

`slot_still_stands` is the one answer to "is this booking still on". The
different question -- does this row already hold a booking of its own -- is
still `candidate_has_confirmed_slot`, and the payment and slot-clone paths
depend on it staying that way: a cancelled row must not be written over.
"""

from __future__ import annotations

import pytest

from core import recruitment_mail_store as ms
from features import candidate_store as cs


def slot(**overrides):
    row = {"id": "slot-1", "name": "Rahul", "date": "2026-09-21", "time": "14:00",
           "time_end": "14:30", "slot_confirmed": True, "interview_attendee": "Bhavana"}
    row.update(overrides)
    return row


# ── What "still on" means ───────────────────────────────────────────────────

@pytest.mark.parametrize(("row", "expected"), [
    (slot(), True),
    (slot(interview_attendance_status="cancelled"), False),
    # Canceled, the American spelling the store also accepts.
    (slot(interview_attendance_status="canceled"), False),
    (slot(interview_attendance_status="attended"), True),
    (slot(interview_attendance_status="not_attended"), True),
    (slot(interview_attendance_status="rescheduled"), False),
    (slot(slot_confirmed=False), False),
    (slot(date=""), False),
])
def test_only_a_sitting_that_will_not_be_sat_stops_standing(row, expected):
    """Attended and not-attended interviews happened: they keep their hour and
    stay the booking a later mail is about. A cancelled or rescheduled sitting
    will not be sat, and ends."""
    assert cs.slot_still_stands(row) is expected


def test_a_cancelled_row_still_holds_a_booking_of_its_own():
    """The payment and slot-clone paths ask this instead, and must keep seeing
    a cancelled row as occupied -- otherwise a new booking would be written
    over the cancelled interview's own record."""
    assert cs.candidate_has_confirmed_slot(slot(interview_attendance_status="cancelled")) is True


# ── The hour is free again ──────────────────────────────────────────────────

@pytest.fixture
def roster(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(cs, "_load", lambda *args, **kwargs: {"candidates": rows})
    return rows


def test_a_cancelled_interview_does_not_clash_with_a_new_booking(roster):
    roster.append(slot(interview_attendance_status="cancelled"))

    assert cs.find_interview_slot_conflicts("2026-09-21", "14:00", "14:30") == []


def test_an_interview_that_is_still_on_clashes_as_it_did(roster):
    roster.append(slot())

    conflicts = cs.find_interview_slot_conflicts("2026-09-21", "14:00", "14:30")

    assert [c["id"] for c in conflicts] == ["slot-1"]


def test_an_attended_interview_keeps_its_hour(roster):
    """Somebody sat it; the hour was used."""
    roster.append(slot(interview_attendance_status="attended"))

    assert [c["id"] for c in cs.find_interview_slot_conflicts("2026-09-21", "14:00", "14:30")] == ["slot-1"]


# ── A mail cannot report a cancelled interview as booked ────────────────────

@pytest.fixture
def bookings(monkeypatch):
    rows = {
        "still-on": slot(id="still-on"),
        "cancelled": slot(id="cancelled", interview_attendance_status="cancelled"),
    }
    monkeypatch.setattr(cs, "get_candidate", lambda cid: rows.get(str(cid)))
    return rows


def test_a_claim_on_a_cancelled_interview_reads_as_cancelled(bookings):
    """Not "AI Retry Pending": nothing is going to book this again."""
    rows = [{"id": "n1", "booking_status": "Auto Booked", "booking_id": "cancelled",
             "candidate_status": "Interview Automatically Booked"}]

    ms.reconcile_booking_claims(rows)

    assert rows[0]["booking_status"] == ms.CANCELLED_BOOKING_STATUS
    assert rows[0]["booking_claim_cancelled"] is True
    assert rows[0]["candidate_status"] == "Interview Cancelled"
    assert rows[0]["historical_candidate_status"] == "Interview Automatically Booked"


def test_a_claim_on_a_booking_that_vanished_still_reads_as_released(bookings, monkeypatch):
    """The other way a claim goes stale: the slot itself was taken away."""
    monkeypatch.setattr(cs, "get_candidate", lambda cid: {"id": "emptied", "date": "", "time": "",
                                                          "slot_confirmed": False})
    rows = [{"id": "n1", "booking_status": "Auto Booked", "booking_id": "emptied"}]

    ms.reconcile_booking_claims(rows)

    assert rows[0]["booking_status"] == ms.RELEASED_BOOKING_STATUS
    assert rows[0]["booking_claim_released"] is True


def test_a_claim_on_an_interview_that_is_still_on_is_left_alone(bookings):
    rows = [{"id": "n1", "booking_status": "Auto Booked", "booking_id": "still-on"}]

    ms.reconcile_booking_claims(rows)

    assert rows[0]["booking_status"] == "Auto Booked"
    assert "booking_claim_released" not in rows[0]
