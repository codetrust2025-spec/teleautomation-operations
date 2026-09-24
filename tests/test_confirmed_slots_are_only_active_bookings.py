"""Confirmed upcoming slots lists only interviews still to be sat.

The page and the sidebar badge both say "Confirmed upcoming slots", yet a
booking marked Attended or Not attended in Daily Ops stayed on the list until
its day was over: on 21 Sep six of the thirteen entries were interviews already
sat. The list now keeps to the rule Daily Ops Upcoming uses -- a booking leaves
once it has an outcome or will not be sat -- and the booking itself stays
exactly as it was, in Daily Ops and in its history.
"""

from __future__ import annotations

import copy
from datetime import date, timedelta

import pytest

from features import candidate_store as cs

TODAY = date.today().isoformat()
LATER = (date.today() + timedelta(days=2)).isoformat()
ATTENDANCE_FIELDS = {"interview_attendance_status", "interview_attended", "interview_attendance_remark",
                     "interview_attended_at", "interview_attended_by", "interview_feedback",
                     "interview_attendee", "updated_at"}


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


def listed():
    return [(slot["date"], slot["time"]) for slot in cs.public_booked_interview_slots(days=30)["slots"]]


@pytest.mark.parametrize("outcome", ["attended", "not_attended"])
def test_todays_booking_leaves_once_it_has_an_outcome(store, outcome):
    store["candidates"] += [row(), row(id="later", date=LATER, time="11:00", time_end="11:30")]
    assert listed() == [(TODAY, "15:00"), (LATER, "11:00")]
    before = copy.deepcopy(store["candidates"][0])

    cs.set_interview_attendance("sitting", status=outcome, remark="interview over",
                                by="operations_admin", allow_future=True)

    assert listed() == [(LATER, "11:00")]
    assert cs.public_booked_interview_slots(days=30)["count"] == 1
    # The booking is still there, changed only by the outcome Daily Ops recorded.
    after = store["candidates"][0]
    assert {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)} <= ATTENDANCE_FIELDS
    roster = cs.daily_interview_roster(TODAY)
    assert [r["id"] for r in roster["interviews"]] == ["sitting"]
    assert roster[f"{outcome}_count"] == 1


@pytest.mark.parametrize("status", [
    "attended", "not_attended", "re_service",
    "cancelled", "rescheduled", cs.RELEASED_FOR_RESCHEDULE_STATUS,
])
def test_only_bookings_still_to_be_sat_are_listed(store, status):
    store["candidates"] += [row(interview_attendance_status=status),
                            row(id="still-to-sit", time="16:00", time_end="16:30")]

    assert listed() == [(TODAY, "16:00")]


def test_a_replaced_booking_is_not_listed(store):
    store["candidates"] += [row(superseded_by_booking_id="replacement"),
                            row(id="replacement", date=LATER, time="11:00", time_end="11:30")]

    assert listed() == [(LATER, "11:00")]


def test_a_booking_from_earlier_today_stays_until_it_has_an_outcome(store):
    store["candidates"].append(row(time="00:05", time_end="00:35"))

    assert listed() == [(TODAY, "00:05")]


def test_listing_changes_nothing(store):
    store["candidates"] += [row(interview_attendance_status="attended"),
                            row(id="still-to-sit", time="16:00", time_end="16:30")]
    before = copy.deepcopy(store)

    cs.public_booked_interview_slots(days=30)

    assert store == before


def test_the_live_route_stops_listing_a_booking_once_it_has_an_outcome(monkeypatch, tmp_path):
    from tests.test_public_slot_booking_flow import _booking, _client, _confirm, _upload

    client = _client(monkeypatch, tmp_path)
    ahead = (date.today() + timedelta(days=3)).isoformat()
    booking = _booking(_upload(client))
    booking.update({"date": ahead, "idempotency_key": f"raju-outcome-{ahead}"})
    assert _confirm(client, booking).status_code == 200

    def mine():
        return [s for s in client.get("/public/slots/booked").json()["slots"]
                if s["date"] == ahead and "Raju" in s["name"]]

    assert len(mine()) == 1
    booked = next(r for r in cs.all_booking_rows() if str(r.get("date") or "")[:10] == ahead)

    cs.set_interview_attendance(booked["id"], status="attended", remark="interview over",
                                by="operations_admin", allow_future=True)

    assert mine() == []
    kept = cs.get_candidate(booked["id"])
    assert (kept["date"][:10], kept["time"], cs.row_interview_attendance_status(kept)) == (
        ahead, booked["time"], "attended")


def test_canonical_candidate_name_capitalization():
    assert cs.canonical_candidate_name("sakthivek") == "Sakthivek"
    assert cs.canonical_candidate_name("pujitha") == "Pujitha"
    assert cs.canonical_candidate_name("CHINTHALA PAVAN") == "Chinthala Pavan"
    assert cs.canonical_candidate_name("konduru Sai Srinivas") == "Konduru Sai Srinivas"
    assert cs.canonical_candidate_name("perla abhilash") == "Abilash Perla"
    assert cs.canonical_candidate_name("ram charan m s") == "Ram Charan M S"
    assert cs.canonical_candidate_name("Tejas M. Shinde") == "Tejas M. Shinde"


def test_normalise_interview_round_numeric_and_technical():
    assert cs.normalise_interview_round("1") == "L1"
    assert cs.normalise_interview_round("Round 1") == "L1"
    assert cs.normalise_interview_round("r1") == "L1"
    assert cs.normalise_interview_round("2") == "L2"
    assert cs.normalise_interview_round("Round 2") == "L2"
    assert cs.normalise_interview_round("Technical") == "Technical"
    assert cs.normalise_interview_round("Technical round") == "Technical"
    assert cs.normalise_interview_round("Screening") == "Screening"
    assert cs.normalise_interview_round("L1") == "L1"
    assert cs.normalise_interview_round("") == ""

