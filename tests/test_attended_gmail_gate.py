"""Profile-wise L2, HR and Final: Attended needs a Gmail with Monitoring Active (owner, 26 Nov).

Through the real attendance route and the real candidate store, with the
mailbox list and the candidate-id resolver stubbed (no Postgres here):

    - one Gmail with Monitoring Active is enough, even beside others that need
      reconnection, are paused or have ended -- no reconnection is asked for;
    - with no such Gmail the update is refused with the owner's message and
      nothing on the row changes;
    - the match is by Candidate ID, including ids linked to it, never by name
      or by another candidate's Gmail;
    - Round-wise bookings, other rounds and other outcomes are not affected.

Names, numbers and addresses are invented.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from api.routers import candidates as route
from core import dashboard_access
from features import attendance_gmail_gate as gate
from features import candidate_store as cs

REQUIRED = gate.GMAIL_MONITORING_REQUIRED_MESSAGE


def booking(cid, **over):
    base = {
        "id": cid, "name": "Ravi Fakename", "phone": "9000000801",
        "service_type": "profile_service", "stage": "in_progress", "slot_confirmed": True,
        "date": "2026-11-10", "time": "12:30", "time_end": "13:00", "interview_round": "L2",
        "interview_attendee": "Bhavana", "technology": "React JS", "reference": "Referrer One",
        "logged_date": "2026-11-01", "created_at": "2026-11-01T10:00:00+00:00",
    }
    base.update(over)
    return base


def mailbox(candidate_id, *, canonical=None, status="CONNECTED", enabled=True, error="", excluded=False, email="x@example.test"):
    return {
        "id": f"mb-{candidate_id}-{email}", "candidate_id": candidate_id,
        "canonical_candidate_id": canonical or candidate_id, "email_address": email,
        "connection_status": status, "monitoring_enabled": enabled,
        "last_error_message": error, "monitoring_excluded": excluded,
    }


ACTIVE = mailbox("c1", email="active@example.test")
EXPIRED = mailbox("c1", status="ERROR", error="Gmail authorization expired or was revoked", email="old@example.test")
PAUSED = mailbox("c1", enabled=False, email="paused@example.test")
ENDED = mailbox("c1", excluded=True, email="ended@example.test")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "_load_cache", None, raising=False)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0, raising=False)
    monkeypatch.setattr(cs, "reconcile_resume_metadata", lambda: 0)
    monkeypatch.setattr(dashboard_access, "assert_candidate_row_access", lambda request, row: None)
    monkeypatch.setattr(dashboard_access, "operator_profile", lambda request: {"role": "admin", "username": "admin1"})
    monkeypatch.setattr(route, "_ops_by", lambda request: "admin1")
    state = {"mailboxes": [], "links": {}}
    monkeypatch.setattr(gate, "_mailbox_rows", lambda: list(state["mailboxes"]))
    monkeypatch.setattr(
        cs, "candidate_identity_ids",
        lambda cid, include_name_matches=True: [cid, *state["links"].get(cid, [])],
    )
    return state


def seed(rows):
    with open(cs._FILE, "w", encoding="utf-8") as handle:
        json.dump({"candidates": rows, "updated_at": None}, handle)
    cs._load_cache = None


def stored(cid):
    cs._load_cache = None
    return next(r for r in cs._load(force=True)["candidates"] if r["id"] == cid)


def mark(cid, status="attended", **body):
    return asyncio.run(route.candidates_interview_attendance(
        cid, None, {"status": status, "remark": "joined on time", "attendee": "Bhavana", "feedback": "good", **body}))


@pytest.mark.parametrize("round_name", ["L2", "HR", "Final"])
def test_one_monitored_gmail_is_enough_even_beside_others_needing_reconnection(env, round_name):
    env["mailboxes"] = [EXPIRED, PAUSED, ENDED, ACTIVE]
    seed([booking("c1", interview_round=round_name)])
    out = mark("c1")
    assert out["status"] == "ok", out
    assert stored("c1")["interview_attendance_status"] == "attended"


@pytest.mark.parametrize("round_name", ["L2", "HR", "Final"])
@pytest.mark.parametrize("mailboxes", [[], [EXPIRED], [PAUSED], [ENDED], [EXPIRED, PAUSED, ENDED]])
def test_no_monitored_gmail_blocks_attended_and_changes_nothing(env, round_name, mailboxes):
    env["mailboxes"] = mailboxes
    seed([booking("c1", interview_round=round_name)])
    before = stored("c1")
    out = mark("c1")
    assert out == {"status": "error", "code": gate.GMAIL_MONITORING_REQUIRED, "message": REQUIRED}
    assert stored("c1") == before


def test_matched_by_candidate_id_including_linked_ids_never_by_name_or_another_candidate(env):
    seed([booking("c1"), booking("c2", name="Ravi Fakename", phone="9000000802")])
    # Another candidate's Gmail -- even one with the same name -- does not count.
    env["mailboxes"] = [mailbox("c2", email="other@example.test")]
    assert mark("c1")["code"] == gate.GMAIL_MONITORING_REQUIRED
    # A Gmail connected under a linked (legacy) Candidate ID does.
    env["links"] = {"c1": ["legacy-c1"]}
    env["mailboxes"] = [mailbox("legacy-c1", email="mine@example.test")]
    assert mark("c1")["status"] == "ok"


def test_a_gmail_whose_canonical_id_is_the_candidate_counts(env):
    seed([booking("c1")])
    env["mailboxes"] = [mailbox("alias-9", canonical="c1")]
    assert mark("c1")["status"] == "ok"


@pytest.mark.parametrize("row", [
    booking("c1", service_type="round_wise", interview_round="L2"),
    booking("c1", service_type="round_wise", interview_round="Final"),
    booking("c1", interview_round="L1"),
    booking("c1", interview_round="Screening"),
])
def test_round_wise_bookings_and_other_rounds_are_unaffected(env, row):
    env["mailboxes"] = []
    seed([row])
    assert mark("c1")["status"] == "ok"


@pytest.mark.parametrize("status", ["not_attended", "technical_issue", "rescheduled", "cancelled", ""])
def test_other_outcomes_are_unaffected(env, status):
    env["mailboxes"] = []
    seed([booking("c1", interview_round="HR")])
    assert mark("c1", status=status)["status"] == "ok"


def test_a_failed_mailbox_lookup_blocks_rather_than_letting_it_through(env, monkeypatch):
    def broken():
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(gate, "_mailbox_rows", broken)
    seed([booking("c1", interview_round="Final")])
    out = mark("c1")
    assert out["code"] == gate.GMAIL_MONITORING_REQUIRED and out["message"] == gate.GMAIL_MONITORING_UNKNOWN_MESSAGE
    assert stored("c1").get("interview_attendance_status", "") == ""


def test_summary_endpoint_reports_requirement_and_monitored_gmail(env):
    env["mailboxes"] = [EXPIRED, ACTIVE]
    seed([booking("c1", interview_round="HR"), booking("c2", service_type="round_wise")])
    one = asyncio.run(route.candidates_attended_gmail("c1", None))
    assert one == {"status": "ok", "required": True, "active": True, "monitored": ["active@example.test"]}
    two = asyncio.run(route.candidates_attended_gmail("c2", None))
    assert two["required"] is False and two["active"] is False


def test_monitoring_active_matches_the_mail_tab_badge():
    assert gate.mailbox_monitoring_active(ACTIVE)
    for row in (EXPIRED, PAUSED, ENDED, mailbox("c1", status="PENDING"), mailbox("c1", error="token revoked"), None):
        assert not gate.mailbox_monitoring_active(row)
