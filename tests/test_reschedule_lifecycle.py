"""A rescheduled interview: what holds the hour, and what is only history.

Two different things were both called "rescheduled".

The automatic path moves a booking to its new time **in place**: same row, new
date, and it is the live interview. Daily Ops has a Rescheduled status an
operator sets by hand, and production showed it always meant the opposite --
the sitting is off, the row keeps the old time, and the replacement, if there
is one, is a different row. All four rows carrying it were that, and two had a
replacement beside them.

A row moved in place no longer keeps the marker: it is the live booking again,
and the marker from the sitting it used to describe moves into its own
history. So the marker now only ever says what the operator meant, and a
sitting carrying it has ended -- see
test_rescheduled_sitting_leaves_confirmed_slots.py. Two explicit states end a
sitting too:

  * `superseded_by_booking_id` -- the booking that replaced this one;
  * `released_for_reschedule` -- the hour is given up and no replacement has
    been booked yet.

All of them keep the old date and time, because the record of what was booked
is the point.
"""

from __future__ import annotations

import pytest

from core import recruitment_mail_store as ms
from features import candidate_store as cs
from services import interview_auto_booking as booking

from tests.test_interview_auto_booking import (  # noqa: F401 - shared harness
    CAPGEMINI_ATS_UID, execute, install_store_fakes, result, slot_writer,
)

IST_DAY = "2099-08-06"


def row(**overrides):
    base = {
        "id": "slot-old", "name": "Rahul", "date": IST_DAY, "time": "15:30",
        "time_end": "16:00", "slot_confirmed": True, "interview_attendee": "Bhavana",
        "interview_calendar_uid": CAPGEMINI_ATS_UID, "interview_round": "L1",
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


def standing(data):
    return [r["id"] for r in data["candidates"] if cs.slot_still_stands(r)]


# ── The hour is given up while the new invitation is awaited ────────────────

def test_a_released_slot_keeps_its_schedule_and_frees_its_hour(store):
    store["candidates"].append(row())

    released = cs.release_slot_for_reschedule("slot-old", by="operations_admin",
                                              remark="panel not available")

    assert (released["date"], released["time"], released["time_end"]) == (IST_DAY, "15:30", "16:00")
    assert released["interview_attendance_status"] == cs.RELEASED_FOR_RESCHEDULE_STATUS
    assert released["interview_attendance_remark"] == "panel not available"
    # It claims no replacement, because there is none yet.
    assert not released.get("superseded_by_booking_id")
    assert standing(store) == []
    assert cs.find_interview_slot_conflicts(IST_DAY, "15:30", "16:00") == []


def test_a_released_slot_is_out_of_upcoming_and_pending(store):
    store["candidates"].append(row())
    cs.release_slot_for_reschedule("slot-old", by="operations_admin")

    upcoming = cs.interview_upcoming(days=365 * 80, lookback_days=0)

    assert [r["id"] for r in upcoming["interviews"]] == []


# ── A replacement, and the link back to what it replaced ────────────────────

def test_a_replacement_booking_links_the_slot_it_replaced(store):
    store["candidates"].extend([row(), row(id="slot-new", date="2099-08-09", time="11:00",
                                          time_end="11:30")])
    cs.release_slot_for_reschedule("slot-old", by="operations_admin")

    linked = cs.mark_slot_superseded("slot-old", by_booking_id="slot-new")

    assert linked["superseded_by_booking_id"] == "slot-new"
    assert linked["superseded_at"]
    # History intact, hour free, and the replacement is the one that stands.
    assert (linked["date"], linked["time"]) == (IST_DAY, "15:30")
    assert standing(store) == ["slot-new"]


def test_the_first_replacement_keeps_the_link(store):
    store["candidates"].append(row(superseded_by_booking_id="slot-new", superseded_at="2099-01-01"))

    assert cs.mark_slot_superseded("slot-old", by_booking_id="slot-newer") is None
    assert store["candidates"][0]["superseded_by_booking_id"] == "slot-new"


def test_a_superseded_slot_stops_standing_even_without_a_status(store):
    """Nobody marked it; the replacement is what says it is over."""
    store["candidates"].extend([row(superseded_by_booking_id="slot-new"),
                                row(id="slot-new", date="2099-08-09", time="11:00", time_end="11:30")])

    assert standing(store) == ["slot-new"]
    assert cs.find_interview_slot_conflicts(IST_DAY, "15:30", "16:00") == []
    assert [r["id"] for r in cs.interview_upcoming(days=365 * 80, lookback_days=0)["interviews"]] == ["slot-new"]


def test_cancelling_the_replacement_does_not_revive_the_slot_it_replaced(store):
    store["candidates"].extend([row(superseded_by_booking_id="slot-new"),
                                row(id="slot-new", date="2099-08-09", time="11:00", time_end="11:30")])

    cs.set_interview_attendance("slot-new", status="cancelled", by="operations_admin")

    # The hour the old row held was given up long ago; a cancelled replacement
    # does not hand it back.
    assert standing(store) == []
    assert store["candidates"][0]["superseded_by_booking_id"] == "slot-new"


# ── A booking moved in place is the live interview again ────────────────────

def test_moving_a_slot_in_place_clears_the_marker_and_keeps_the_history(store):
    store["candidates"].append(row(interview_attendance_status="rescheduled",
                                   interview_attendance_remark="panel not available",
                                   interview_attended_by="operations_admin"))

    moved = cs.update_interview_slot(candidate_id="slot-old", date="2099-08-09",
                                     time="11:00", time_end="11:30")

    assert (moved["date"], moved["time"], moved["time_end"]) == ("2099-08-09", "11:00", "11:30")
    assert moved["interview_attendance_status"] == ""
    assert cs.slot_still_stands(moved) is True
    past = moved["interview_previous_sittings"]
    assert [(p["date"], p["time"], p["interview_attendance_status"], p["interview_attendance_remark"])
            for p in past] == [(IST_DAY, "15:30", "rescheduled", "panel not available")]
    assert [r["id"] for r in cs.interview_upcoming(days=365 * 80, lookback_days=0)["interviews"]] == ["slot-old"]


def test_a_move_that_changes_nothing_leaves_the_marker_alone(store):
    store["candidates"].append(row(interview_attendance_status="rescheduled"))

    same = cs.update_interview_slot(candidate_id="slot-old", date=IST_DAY, time="15:30",
                                    time_end="16:00")

    assert same["interview_attendance_status"] == "rescheduled"
    assert same.get("interview_previous_sittings") == []


def test_a_sitting_marked_rescheduled_frees_its_hour(store):
    """A move carries the marker into history, so a row that still has it is a
    sitting that will not be sat -- the hour is free, like a cancelled one."""
    store["candidates"].append(row(interview_attendance_status="rescheduled"))

    assert cs.slot_still_stands(store["candidates"][0]) is False
    assert cs.find_interview_slot_conflicts(IST_DAY, "15:30", "16:00") == []


# ── The booking service, end to end ─────────────────────────────────────────

def test_a_re_invitation_after_the_slot_was_released_books_again(monkeypatch):
    """The released row keeps the calendar event's id, so the duplicate check
    would have matched it and refused the replacement."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    released = {"id": "slot-old", "name": "Rahul", "slot_confirmed": True,
                "date": "2099-07-20", "time": "15:00", "time_end": "15:30",
                "interview_calendar_uid": CAPGEMINI_ATS_UID,
                "interview_attendance_status": cs.RELEASED_FOR_RESCHEDULE_STATUS}
    install_store_fakes(monkeypatch, rows=[released])
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", slot_writer("slot-new"))
    linked = []
    monkeypatch.setattr(booking.candidate_store, "mark_slot_superseded",
                        lambda cid, **kwargs: linked.append((cid, kwargs["by_booking_id"])))
    value = result()
    value["calendar"] = {"uid": CAPGEMINI_ATS_UID, "method": "REQUEST", "sequence": 4}

    outcome = execute(value)

    assert outcome["status"] == "Auto Booked"
    assert outcome["booking"]["id"] == "slot-new"
    # And the replacement says what it replaced.
    assert linked == [("slot-old", "slot-new")]


def test_a_superseded_slot_is_not_offered_to_the_booking_service(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    superseded = {"id": "slot-old", "name": "Rahul", "slot_confirmed": True,
                  "date": "2099-07-20", "time": "15:00", "time_end": "15:30",
                  "interview_calendar_uid": CAPGEMINI_ATS_UID,
                  "superseded_by_booking_id": "slot-new"}
    install_store_fakes(monkeypatch, rows=[superseded])

    assert booking._confirmed_slots({"id": "c1"}) == []


def test_a_booking_that_stands_is_still_linked_to_nothing(monkeypatch):
    """A first booking replaces nothing, so it claims nothing."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch)
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot", slot_writer("slot-new"))
    linked = []
    monkeypatch.setattr(booking.candidate_store, "mark_slot_superseded",
                        lambda cid, **kwargs: linked.append(cid))
    value = result()
    value["calendar"] = {"uid": CAPGEMINI_ATS_UID, "method": "REQUEST", "sequence": 1}

    assert execute(value)["status"] == "Auto Booked"
    assert linked == []


# ── What Mail Alerts says about it ──────────────────────────────────────────

@pytest.mark.parametrize("booked", [
    {"id": "b1", "date": IST_DAY, "time": "15:30", "slot_confirmed": True,
     "superseded_by_booking_id": "slot-new"},
    {"id": "b1", "date": IST_DAY, "time": "15:30", "slot_confirmed": True,
     "interview_attendance_status": "released_for_reschedule"},
])
def test_a_mail_claiming_a_replaced_booking_reads_as_rescheduled(monkeypatch, booked):
    monkeypatch.setattr(cs, "get_candidate", lambda cid: booked)
    rows = [{"id": "n1", "booking_status": "Auto Booked", "booking_id": "b1",
             "candidate_status": "Interview Automatically Booked"}]

    ms.reconcile_booking_claims(rows)

    assert rows[0]["booking_status"] == ms.RESCHEDULED_BOOKING_STATUS
    assert rows[0]["candidate_status"] == "Interview Rescheduled"
    assert rows[0]["historical_candidate_status"] == "Interview Automatically Booked"


def test_a_mail_claiming_a_cancelled_booking_still_reads_as_cancelled(monkeypatch):
    monkeypatch.setattr(cs, "get_candidate", lambda cid: {
        "id": "b1", "date": IST_DAY, "time": "15:30", "slot_confirmed": True,
        "interview_attendance_status": "cancelled"})
    rows = [{"id": "n1", "booking_status": "Auto Booked", "booking_id": "b1"}]

    ms.reconcile_booking_claims(rows)

    assert rows[0]["booking_status"] == ms.CANCELLED_BOOKING_STATUS
