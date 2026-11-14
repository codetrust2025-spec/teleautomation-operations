"""No specified round, no status update (owner, 14 Nov).

Rows reached Daily Ops reading "Round not specified" and could still be marked
Attended, Not attended, Rescheduled or Cancelled, so the outcome was stored
against an interview nobody could name. The owner's rule: block every status
update while the round is missing, say so, and do not ask for a round during
the update -- it is corrected separately, through the interview edit, by an
admin. Through the real API routes and the real store:

    - every status write on a roundless row is refused with the owner's
      message, and nothing on the row changes -- a round sent along with it
      is not accepted either (no prompt-and-save path, no bypass);
    - a round the server does not recognise counts as missing, and is never
      guessed or stored;
    - rows that carry a round record statuses exactly as before;
    - the round is set or corrected only through the slot edit, only by an
      admin, and every change records who and when; status, feedback,
      attendee, notes and history stay as they were.

Names and numbers are invented.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from api.routers import candidates as route
from core import dashboard_access
from features import candidate_store as cs

MESSAGE = "Interview round is missing. Update the round before changing the status."
STATUSES = ["attended", "not_attended", "rescheduled", "cancelled", "technical_issue", ""]


def booking(cid, **over):
    base = {
        "id": cid, "name": "Ravi Fakename", "phone": "9000000801",
        "service_type": "profile_service", "stage": "in_progress", "slot_confirmed": True,
        "date": "2026-11-10", "time": "12:30", "time_end": "13:00", "interview_round": "",
        "interview_attendee": "Bhavana", "technology": "React JS", "reference": "Referrer One",
        "logged_date": "2026-11-01", "created_at": "2026-11-01T10:00:00+00:00",
    }
    base.update(over)
    return base


@pytest.fixture()
def role(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "_load_cache", None, raising=False)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0, raising=False)
    monkeypatch.setattr(cs, "reconcile_resume_metadata", lambda: 0)
    monkeypatch.setattr(dashboard_access, "assert_candidate_row_access", lambda request, row: None)
    current = {"role": "admin", "username": "admin1"}
    monkeypatch.setattr(dashboard_access, "operator_profile", lambda request: dict(current))
    monkeypatch.setattr(route, "_ops_by", lambda request: current["username"])
    return current


def seed(rows):
    with open(cs._FILE, "w", encoding="utf-8") as handle:
        json.dump({"candidates": rows, "updated_at": None}, handle)
    cs._load_cache = None


def stored(cid):
    cs._load_cache = None
    return next(r for r in cs._load(force=True)["candidates"] if r["id"] == cid)


def mark(cid, **body):
    return asyncio.run(route.candidates_interview_attendance(cid, None, body))


def edit_slot(cid, **body):
    body = {"date": "2026-11-10", "time": "12:30", "time_end": "13:00", "technology": "React JS", **body}
    return asyncio.run(route.candidates_interviews_slots_update(cid, None, body))


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("who", ["admin", "handler"])
def test_every_status_update_on_a_roundless_row_is_refused(role, status, who):
    role.update(role=who, username=f"{who}1")
    seed([booking("c1", interview_attendance_status="attended", interview_feedback="good",
                  interview_attendance_remark="went well")])
    before = stored("c1")
    out = mark("c1", status=status, remark="note", attendee="Bhavana", feedback="good")
    assert out == {"status": "error", "code": "round_missing", "message": MESSAGE}
    assert stored("c1") == before


@pytest.mark.parametrize("status", ["attended", "not_attended", "rescheduled", "cancelled"])
def test_a_round_sent_with_the_status_is_not_a_way_round_it(role, status):
    seed([booking("c1")])
    before = stored("c1")
    out = mark("c1", status=status, remark="note", attendee="Bhavana", feedback="good", interview_round="L2")
    assert out["code"] == "round_missing"
    assert stored("c1") == before


def test_an_unrecognised_round_counts_as_missing_and_is_never_guessed(role):
    seed([booking("c1", interview_round="Technical round")])
    assert mark("c1", status="attended", remark="x", attendee="Bhavana", feedback="good")["code"] == "round_missing"
    assert edit_slot("c1", interview_round="Round 7")["status"] == "error"
    assert stored("c1")["interview_round"] == "Technical round"
    assert "interview_round_history" not in stored("c1")


@pytest.mark.parametrize("status", ["attended", "not_attended", "rescheduled", "cancelled"])
def test_rows_with_a_round_record_statuses_as_before(role, status):
    seed([booking("c1", interview_round="L1")])
    out = mark("c1", status=status, remark="note", attendee="Bhavana", feedback="good")
    assert out["status"] == "ok"
    row = stored("c1")
    assert row["interview_attendance_status"] == status
    assert row["interview_round"] == "L1"
    assert "interview_round_history" not in row


def test_an_admin_sets_the_round_in_the_edit_then_the_status_saves(role):
    seed([booking("c1")])
    assert mark("c1", status="attended", remark="x", attendee="Bhavana", feedback="good")["code"] == "round_missing"
    out = edit_slot("c1", interview_round="L2")
    assert out["status"] == "ok" and out["candidate"]["interview_round"] == "L2"
    [entry] = stored("c1")["interview_round_history"]
    assert entry["from"] == "" and entry["to"] == "L2" and entry["by"] == "admin1"
    assert entry["source"] == "slot-edit" and entry["at"]
    assert mark("c1", status="attended", remark="x", attendee="Bhavana", feedback="good")["status"] == "ok"


@pytest.mark.parametrize("current", ["", "L1"])
def test_only_an_admin_sets_or_corrects_a_round(role, current):
    seed([booking("c1", interview_round=current)])
    role.update(role="handler", username="handler1")
    out = edit_slot("c1", interview_round="L2")
    assert out == {"status": "error", "message": "Only an admin can set or change the interview round."}
    assert stored("c1")["interview_round"] == current
    # A handler's slot edit that re-sends the round the row holds still saves.
    if current:
        assert edit_slot("c1", interview_round=current, time="13:00", time_end="13:30")["status"] == "ok"
        assert stored("c1")["time"] == "13:00"


def test_correcting_a_round_keeps_status_feedback_and_history(role):
    seed([booking("c1", interview_round="L1", interview_attendance_status="attended",
                  interview_feedback="good", interview_attendance_remark="went well",
                  interview_attended_by="admin1", interview_result="next_round",
                  sitting_history=[{"date": "2026-11-01", "status": "rescheduled"}])])
    before = stored("c1")
    assert edit_slot("c1", interview_round="L2")["status"] == "ok"
    after = stored("c1")
    for key in ("interview_attendance_status", "interview_feedback", "interview_attendance_remark",
                "interview_attended_by", "interview_result", "interview_attendee", "sitting_history",
                "date", "time", "time_end", "technology", "slot_confirmed"):
        assert after.get(key) == before.get(key), key
    assert after["interview_round"] == "L2"
    assert after["interview_round_history"][-1]["from"] == "L1"
    assert after["interview_round_history"][-1]["to"] == "L2"


def test_a_slot_edit_without_a_round_leaves_it_alone(role):
    seed([booking("c1", interview_round="HR")])
    assert asyncio.run(route.candidates_interviews_slots_update(
        "c1", None, {"date": "2026-11-11", "time": "12:30", "time_end": "13:00", "technology": "React JS"},
    ))["status"] == "ok"
    assert stored("c1")["interview_round"] == "HR"
    assert "interview_round_history" not in stored("c1")


def test_the_route_is_registered_on_the_app(role):
    """The API requests themselves -- through the app, not only the handler."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(route.router)
    seed([booking("c1"), booking("c2", interview_round="L1")])
    client = TestClient(app)
    refused = client.post("/candidates/c1/interview-attendance", json={"status": "cancelled", "remark": "x", "interview_round": "L1"})
    assert refused.json() == {"status": "error", "code": "round_missing", "message": MESSAGE}
    assert not stored("c1").get("interview_attendance_status")
    saved = client.post("/candidates/c2/interview-attendance", json={"status": "cancelled", "remark": "x"})
    assert saved.json()["status"] == "ok"
