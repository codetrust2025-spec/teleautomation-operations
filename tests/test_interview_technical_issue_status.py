"""Technical issue is an attendance status of its own (21 Oct).

A sitting that went ahead but failed for a technical reason -- UltraViewer,
the microphone, the connection -- had nowhere to go: it was recorded as
Attended with the problem in the note, which counted a failed sitting as an
attended one. Sending any other value to the attendance endpoint quietly
stored Pending, because the store did not know it.

Through the real attendance route, the real store and the real Daily Ops
routes:

    - Technical issue is stored, with who attended and the note; it is not
      Attended, and feedback (which describes an attended interview) is cleared;
    - it has its own counter and its own bucket in every breakdown, and leaves
      Attended and Pending alone;
    - like Attended and Not attended it cannot be logged before the interview;
    - Awaiting new slot and every other status still save exactly as before;
    - the candidate's timeline names it.

Names and numbers are invented.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from api.routers import candidates as route
from core import dashboard_access
from features import candidate_store as cs
from services import candidate_timeline

ALL_TIME = ("2000-01-01", "2100-12-31")
PHONES = {"Ravi Fakename": "9000000701", "Asha Fakename": "9000000702", "Kiran Fakename": "9000000703"}


def booking(cid, name, day, **over):
    base = {
        "id": cid, "name": name, "phone": PHONES[name],
        "service_type": "profile_service", "stage": "in_progress", "slot_confirmed": True,
        "date": day, "time": "12:30", "time_end": "13:00", "interview_round": "L1",
        "interview_attendee": "Bhavana", "technology": "React JS", "reference": "Referrer One",
        "logged_date": "2026-09-25", "created_at": "2026-09-25T10:00:00+00:00",
    }
    base.update(over)
    return base


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "_load_cache", None, raising=False)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0, raising=False)
    monkeypatch.setattr(cs, "reconcile_resume_metadata", lambda: 0)
    monkeypatch.setattr(dashboard_access, "assert_candidate_row_access", lambda request, row: None)
    role = {"role": "admin"}
    monkeypatch.setattr(dashboard_access, "operator_profile", lambda request: dict(role))
    monkeypatch.setattr(route, "_ops_by", lambda request: "Operations Admin")
    monkeypatch.setattr(route, "_viewer_reference", lambda request: None)
    from features import operator_tasks_store

    monkeypatch.setattr(operator_tasks_store, "list_tasks", lambda **kwargs: [])
    monkeypatch.setattr(cs, "_test_role", role, raising=False)
    return cs


def seed(store, rows):
    with open(store._FILE, "w", encoding="utf-8") as handle:
        json.dump({"candidates": rows, "updated_at": None}, handle)
    store._load_cache = None


def stored(store, cid):
    store._load_cache = None
    return next(r for r in store._load(force=True)["candidates"] if r["id"] == cid)


def mark(cid, **body):
    return asyncio.run(route.candidates_interview_attendance(cid, None, body))


def summary():
    return asyncio.run(route.candidates_interviews_global(
        None, from_date=ALL_TIME[0], to_date=ALL_TIME[1], attendee=None, search=None,
        channel=None, round=None, technology=None, candidate=None,
        upcoming_only=False, unresolved_only=False,
    ))


NOTE = "UltraViewer not working, mic issue"


def test_technical_issue_is_stored_with_who_attended_and_the_note(store):
    seed(store, [booking("t1", "Ravi Fakename", "2026-10-15", interview_feedback="negative",
                         interview_attendance_status="attended", interview_attended=True)])
    result = mark("t1", status="technical_issue", remark=NOTE, attendee="Nikhila", feedback="")
    assert result["status"] == "ok"
    row = stored(store, "t1")
    assert row["interview_attendance_status"] == "technical_issue"
    assert row["interview_attendance_remark"] == NOTE
    assert row["interview_attendee"] == "Nikhila"
    assert row["interview_attended"] is False
    assert row["interview_feedback"] == ""
    assert row["interview_attended_by"] == "Operations Admin"
    assert row["interview_attended_at"]


def test_it_has_its_own_counter_and_bucket_and_leaves_the_others_alone(store):
    seed(store, [
        booking("t1", "Ravi Fakename", "2026-10-15"),
        booking("a1", "Asha Fakename", "2026-10-14", interview_attendance_status="attended", interview_attended=True),
        booking("p1", "Kiran Fakename", "2026-10-16"),
    ])
    assert mark("t1", status="technical_issue", remark=NOTE, attendee="Bhavana")["status"] == "ok"
    interviews = summary()["interviews"]
    assert interviews["count"] == 3
    assert interviews["technical_issue_count"] == 1
    assert interviews["attended_count"] == 1
    assert interviews["pending_count"] == 1
    ravi = next(b for b in interviews["by_candidate"] if b["name"] == "Ravi Fakename")
    assert (ravi["technical_issue"], ravi["attended"], ravi["pending"]) == (1, 0, 0)
    bhavana = next(b for b in interviews["by_attendee"] if b["name"] == "Bhavana")
    assert bhavana["technical_issue"] == 1
    roster = asyncio.run(route.candidates_interviews_monitor(
        None, from_date=ALL_TIME[0], to_date=ALL_TIME[1], attendee=None, search=None,
        channel=None, round=None, technology=None, candidate=None,
        upcoming_only=False, unresolved_only=False,
    ))
    assert roster["technical_issue_count"] == 1
    assert {r["id"]: r.get("interview_attendance_status") for r in roster["interviews"]}["t1"] == "technical_issue"


def test_it_is_no_longer_waiting_for_an_outcome(store):
    seed(store, [booking("t1", "Ravi Fakename", "2026-10-15")])
    assert mark("t1", status="technical_issue", remark=NOTE, attendee="Bhavana")["status"] == "ok"
    unresolved = asyncio.run(route.candidates_interviews_monitor(
        None, from_date=ALL_TIME[0], to_date=ALL_TIME[1], attendee=None, search=None,
        channel=None, round=None, technology=None, candidate=None,
        upcoming_only=False, unresolved_only=True,
    ))
    assert unresolved["count"] == 0


def test_it_cannot_be_logged_before_the_interview(store):
    store._test_role["role"] = "attender"
    seed(store, [booking("f1", "Ravi Fakename", "2099-01-01")])
    result = mark("f1", status="technical_issue", remark=NOTE, attendee="Bhavana")
    assert result["status"] == "error"
    assert "on or after the interview date" in result["message"]
    assert stored(store, "f1").get("interview_attendance_status", "") == ""


@pytest.mark.parametrize("status", ["attended", "not_attended", "cancelled", "rescheduled", "released_for_reschedule"])
def test_every_existing_status_still_saves_as_itself(store, status):
    seed(store, [booking("s1", "Asha Fakename", "2026-10-14")])
    body = {"status": status, "remark": "note", "attendee": "Bhavana"}
    if status == "attended":
        body["feedback"] = "positive"
    assert mark("s1", **body)["status"] == "ok"
    assert stored(store, "s1")["interview_attendance_status"] == status


def test_the_timeline_names_it(store):
    seed(store, [booking("t1", "Ravi Fakename", "2026-10-15")])
    assert mark("t1", status="technical_issue", remark=NOTE, attendee="Bhavana")["status"] == "ok"
    entries = candidate_timeline._booking_entries([stored(store, "t1")])
    titles = [e["title"] for e in entries if e]
    assert "Marked technical issue" in titles
    detail = next(e["detail"] for e in entries if e and e["title"] == "Marked technical issue")
    assert NOTE in detail
