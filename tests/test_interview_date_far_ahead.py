"""An interview date more than 180 days ahead is refused, and flagged (11 Oct audit).

Three confirmed slots were found dated 2027, 8-14 months after they were
logged -- almost certainly a mistyped year. Nothing stopped it: every booking
path accepted any calendar day. Such a slot cannot have attendance recorded
(the store refuses a future slot) and counts as a standing booking for the
second-slot payment lock.

    - a new or changed date that far ahead is refused on every path;
    - a date already on record is left alone, so the row can still be edited
      (and its date corrected);
    - Pending Works lists each standing slot dated that far ahead, without
      changing it: the right date is not known.

Names and numbers are invented.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from features import candidate_store as cs

TODAY = date.today()
FAR = (TODAY + timedelta(days=cs.INTERVIEW_DATE_MAX_DAYS_AHEAD + 1)).isoformat()
EDGE = (TODAY + timedelta(days=cs.INTERVIEW_DATE_MAX_DAYS_AHEAD)).isoformat()
SOON = (TODAY + timedelta(days=3)).isoformat()


@pytest.fixture
def store(monkeypatch):
    data = {"candidates": []}
    monkeypatch.setattr(cs, "_load", lambda *a, **k: data)
    monkeypatch.setattr(cs, "_save", lambda payload: data.update(payload))
    return data


def slot(cid, name, day, **over):
    row = {"id": cid, "name": name, "phone": "90000007" + cid[-2:].rjust(2, "0"), "service_type": "profile_service",
           "stage": "in_progress", "reference": "Owner One", "technology": "Java", "date": day, "time": "10:00",
           "time_end": "10:30", "slot_confirmed": True, "slots_group_posted": True, "expected_payment": 20000,
           "payment": 20000, "resumes": [], "payment_proofs": []}
    row.update(over)
    return row


@pytest.mark.parametrize("day, refused", [
    (EDGE, False), (FAR, True), (SOON, False), ("2026-01-05", False), ("", False), ("not a date", False),
])
def test_only_the_far_future_is_a_problem(day, refused):
    assert bool(cs.interview_date_problem(day)) is refused


def test_the_message_names_the_date_and_the_cause():
    assert cs.interview_date_problem("2099-06-19") == (
        f"The interview date 19 Jun 2099 is more than {cs.INTERVIEW_DATE_MAX_DAYS_AHEAD} days ahead. Check the year.")


def test_moving_a_slot_that_far_ahead_is_refused(store):
    store["candidates"].append(slot("a1", "Asha Fakename", SOON))
    with pytest.raises(ValueError, match="Check the year"):
        cs.update_candidate("a1", {"date": FAR})
    assert store["candidates"][0]["date"] == SOON


def test_a_row_already_holding_such_a_date_can_still_be_edited_and_corrected(store):
    store["candidates"].append(slot("b1", "Ravi Kolla", FAR))
    cs.update_candidate("b1", {"technology": "Python"})
    assert store["candidates"][0]["technology"] == "Python"
    cs.update_candidate("b1", {"date": SOON})
    assert store["candidates"][0]["date"] == SOON


def test_creating_a_slot_that_far_ahead_is_refused(store):
    with pytest.raises(ValueError, match="Check the year"):
        cs.create_interview_slot(name="Neha Fakename", date=FAR, time="10:00", time_end="10:30", reference="Owner One")
    with pytest.raises(ValueError, match="Check the year"):
        cs.create_candidate(slot("c1", "Neha Fakename", FAR))
    assert store["candidates"] == []


def test_pending_works_lists_each_standing_far_dated_slot_and_changes_nothing(store):
    store["candidates"] += [
        slot("d1", "Kiran Fakename", FAR),
        slot("d2", "Kiran Fakename", SOON),
        slot("d3", "Lata Fakename", FAR, interview_attendance_status="cancelled", slot_confirmed=False),
    ]
    before = [dict(r) for r in store["candidates"]]
    result = cs._pending_works_core([cs._with_computed(r) for r in store["candidates"]])
    flagged = [w for w in result["works"] if w["kind"] == "interview_date_far_ahead"]
    assert [(w["candidate_name"], w["date"]) for w in flagged] == [("Kiran Fakename", FAR)]
    assert flagged[0]["label"] == "Check interview date"
    assert "wrong year" in flagged[0]["detail"]
    assert store["candidates"] == before
