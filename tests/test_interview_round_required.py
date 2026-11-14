"""A status is never saved on an interview without a round (14 Nov).

Rows reached Daily Ops reading "Round not specified" and an admin could still
mark them Attended, Not attended, Rescheduled or Cancelled, so the outcome was
stored against an interview nobody could name. Through the real routes and the
real store:

    - recording any status on a row with no round is refused unless the
      request names the round; with it, both save and the round is audited;
    - the round is never guessed: an unknown label is refused, not stored;
    - a round already set is changed only by an admin, through the round
      endpoint, the slot edit or the status update alike;
    - changing the round leaves status, feedback, attendee, notes and the
      slot exactly as they were, and every change records who and when;
    - Pending (no status) and rows that already carry a round save as before.

Names and numbers are invented.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from api.routers import candidates as route
from core import dashboard_access
from features import candidate_store as cs


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


def set_round(cid, value):
    return asyncio.run(route.candidates_interview_round(cid, None, {"interview_round": value}))


def edit_slot(cid, **body):
    return asyncio.run(route.candidates_interviews_slots_update(cid, None, body))


@pytest.mark.parametrize("status", ["attended", "not_attended", "rescheduled", "cancelled"])
def test_a_status_without_a_round_is_refused_and_nothing_changes(role, status):
    seed([booking("c1")])
    before = stored("c1")
    out = mark("c1", status=status, remark="note", attendee="Bhavana", feedback="good")
    assert out["status"] == "error"
    assert out["code"] == "round_required"
    assert stored("c1") == before


@pytest.mark.parametrize("status", ["attended", "not_attended", "rescheduled", "cancelled"])
def test_a_status_with_the_round_saves_both_and_audits_the_round(role, status):
    seed([booking("c1")])
    out = mark("c1", status=status, remark="note", attendee="Bhavana", feedback="good", interview_round="L2")
    assert out["status"] == "ok"
    row = stored("c1")
    assert row["interview_attendance_status"] == status
    assert row["interview_round"] == "L2"
    [entry] = row["interview_round_history"]
    assert entry["from"] == "" and entry["to"] == "L2" and entry["by"] == "admin1" and entry["at"]


def test_a_handler_may_fill_a_missing_round_while_recording_a_status(role):
    role.update(role="handler", username="handler1")
    seed([booking("c1")])
    assert mark("c1", status="not_attended", remark="no show", attendee="Bhavana", interview_round="HR")["status"] == "ok"
    assert stored("c1")["interview_round"] == "HR"
    assert stored("c1")["interview_round_history"][-1]["by"] == "handler1"


def test_the_round_is_never_guessed(role):
    seed([booking("c1")])
    out = mark("c1", status="attended", remark="note", attendee="Bhavana", feedback="good", interview_round="Technical round")
    assert out["status"] == "error"
    assert stored("c1")["interview_round"] == ""
    assert set_round("c1", "")["status"] == "error"
    assert set_round("c1", "Round 7")["status"] == "error"
    assert "interview_round_history" not in stored("c1")


def test_pending_and_rows_with_a_round_save_as_before(role):
    seed([booking("c1"), booking("c2", interview_round="L1")])
    assert mark("c1", status="")["status"] == "ok"
    assert mark("c2", status="attended", remark="went well", attendee="Bhavana", feedback="good")["status"] == "ok"
    assert stored("c2")["interview_round"] == "L1"
    assert "interview_round_history" not in stored("c2")


def test_correcting_a_round_keeps_status_feedback_and_booking(role):
    seed([booking("c1", interview_round="L1", interview_attendance_status="attended",
                  interview_feedback="good", interview_attendance_remark="went well",
                  interview_attended_by="admin1", interview_result="next_round")])
    before = stored("c1")
    out = set_round("c1", "L2")
    assert out["status"] == "ok"
    after = stored("c1")
    assert after["interview_round"] == "L2"
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed - {"updated_at", "_store_updated_at"} == {"interview_round", "interview_round_history"}
    assert after["interview_round_history"] == [
        {**after["interview_round_history"][0], "from": "L1", "to": "L2", "by": "admin1", "source": "dashboard"}
    ]


def test_only_an_admin_corrects_a_round_already_set(role):
    seed([booking("c1", interview_round="L1", interview_attendance_status="attended", interview_feedback="good")])
    role.update(role="handler", username="handler1")
    assert set_round("c1", "L2")["status"] == "error"
    assert edit_slot("c1", date="2026-11-10", time="12:30", time_end="13:00", technology="React JS", interview_round="L2")["status"] == "error"
    assert mark("c1", status="attended", remark="x", attendee="Bhavana", feedback="good", interview_round="L2")["status"] == "error"
    assert stored("c1")["interview_round"] == "L1"
    # Re-sending the same round is not a correction.
    assert set_round("c1", "L1")["status"] == "ok"
    role.update(role="admin", username="admin1")
    assert set_round("c1", "Final")["status"] == "ok"
    assert stored("c1")["interview_round"] == "Final"


def test_a_handler_may_set_a_missing_round_on_its_own(role):
    role.update(role="handler", username="handler1")
    seed([booking("c1", interview_attendance_status="attended", interview_feedback="good")])
    assert set_round("c1", "L3")["status"] == "ok"
    row = stored("c1")
    assert row["interview_round"] == "L3"
    assert row["interview_attendance_status"] == "attended" and row["interview_feedback"] == "good"


def test_the_slot_edit_audits_a_round_change(role):
    seed([booking("c1", interview_round="L1")])
    out = edit_slot("c1", date="2026-11-10", time="12:30", time_end="13:00", technology="React JS", interview_round="HR")
    assert out["status"] == "ok"
    row = stored("c1")
    assert row["interview_round"] == "HR"
    assert row["interview_round_history"][-1] | {"at": ""} == {"from": "L1", "to": "HR", "by": "admin1", "at": "", "source": "slot-edit"}
    # A slot edit that does not send a round leaves it alone.
    assert edit_slot("c1", date="2026-11-10", time="12:30", time_end="13:00", technology="React JS")["status"] == "ok"
    assert stored("c1")["interview_round"] == "HR"
    assert len(stored("c1")["interview_round_history"]) == 1
