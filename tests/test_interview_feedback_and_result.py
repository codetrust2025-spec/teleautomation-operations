"""The "Mark as Attended" modal records a graded feedback and a result.

Feedback moved from a two-value scale (positive / negative) to a graded one —
Excellent, Good, Average, Needs Improvement, Negative — and a separate
interview *result* was added: Awaiting result, Next round, Selected, Rejected.

Two things have to hold at once:
  * the new values round-trip through the store and the attendance write path, and
  * nothing already recorded is rewritten or rejected — a row saved as
    "positive" before the grades existed stays "positive", and old callers that
    send it keep working.

Both feedback and result belong to an *attended* interview: they are kept while
the round stays attended and cleared when it is not, exactly as feedback always
behaved. Names and values below are invented.
"""
from __future__ import annotations

import asyncio

import pytest

from features import candidate_store as cs
from api.routers import candidates as route


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "_load_cache", None, raising=False)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0, raising=False)
    return cs


def add(store, rid, **extra):
    data = store._load()
    row = {
        "id": rid, "name": "Result Probe", "phone": "9000000701", "technology": "Java",
        "stage": "in_progress", "service_type": "profile_service",
        "date": "2026-09-01", "slot_confirmed": True,
    }
    row.update(extra)
    data.setdefault("candidates", []).append(row)
    store._save(data)


def stored(store, rid):
    return next(r for r in store._load(force=True)["candidates"] if r["id"] == rid)


# ── Feedback: the new graded scale ──────────────────────────────────────────

@pytest.mark.parametrize("grade", ["excellent", "good", "average", "needs_improvement", "negative"])
def test_each_feedback_grade_round_trips(store, grade):
    add(store, "c1")
    cs.set_interview_attendance("c1", status="attended", feedback=grade, remark="n", by="A", allow_future=True)
    assert stored(store, "c1")["interview_feedback"] == grade


@pytest.mark.parametrize(("typed", "canonical"), [
    ("Excellent", "excellent"),
    ("Needs Improvement", "needs_improvement"),
    ("needs-improvement", "needs_improvement"),
    ("  Average  ", "average"),
])
def test_feedback_is_normalised_from_what_the_form_sends(store, typed, canonical):
    add(store, "c1")
    cs.set_interview_attendance("c1", status="attended", feedback=typed, remark="n", by="A", allow_future=True)
    assert stored(store, "c1")["interview_feedback"] == canonical


def test_an_unknown_feedback_is_refused(store):
    with pytest.raises(ValueError):
        cs.normalise_interview_feedback("spectacular")


# ── Feedback: legacy compatibility, the thing that must not break ───────────

def test_the_legacy_positive_value_is_still_accepted_and_kept(store):
    # A caller still sending the old two-value scale must keep working, and the
    # value must be stored unchanged — not remapped, not rejected.
    add(store, "c1")
    cs.set_interview_attendance("c1", status="attended", feedback="positive", remark="n", by="A", allow_future=True)
    assert stored(store, "c1")["interview_feedback"] == "positive"


def test_a_row_saved_as_positive_survives_a_later_attendance_resave(store):
    # Re-saving attendance without naming feedback (feedback=None) must keep the
    # stored legacy value rather than wiping it.
    add(store, "c1", interview_attendance_status="attended", interview_feedback="positive")
    cs.set_interview_attendance("c1", status="attended", remark="still attended", by="A", allow_future=True)
    assert stored(store, "c1")["interview_feedback"] == "positive"


def test_negative_is_shared_between_the_old_and_new_scales(store):
    add(store, "c1")
    cs.set_interview_attendance("c1", status="attended", feedback="negative", remark="n", by="A", allow_future=True)
    assert stored(store, "c1")["interview_feedback"] == "negative"


# ── Result: the new field ───────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["awaiting_result", "next_round", "selected", "rejected"])
def test_each_result_value_round_trips(store, value):
    add(store, "c1")
    cs.set_interview_attendance("c1", status="attended", feedback="good", result=value, remark="n", by="A", allow_future=True)
    assert stored(store, "c1")["interview_result"] == value


@pytest.mark.parametrize(("typed", "canonical"), [
    ("Awaiting result", "awaiting_result"),
    ("Next round", "next_round"),
    ("Selected", "selected"),
    ("Rejected", "rejected"),
])
def test_result_is_normalised_from_what_the_form_sends(store, typed, canonical):
    add(store, "c1")
    cs.set_interview_attendance("c1", status="attended", feedback="good", result=typed, remark="n", by="A", allow_future=True)
    assert stored(store, "c1")["interview_result"] == canonical


def test_an_unknown_result_is_refused(store):
    with pytest.raises(ValueError):
        cs.normalise_interview_result("maybe someday")


def test_result_is_cleared_when_the_round_is_not_attended(store):
    add(store, "c1", interview_attendance_status="attended",
        interview_feedback="good", interview_result="selected")
    cs.set_interview_attendance("c1", status="not_attended", remark="no show", by="A", allow_future=True)
    row = stored(store, "c1")
    assert row["interview_result"] == ""
    assert row["interview_feedback"] == ""


def test_result_survives_a_resave_that_does_not_name_it(store):
    add(store, "c1", interview_attendance_status="attended",
        interview_feedback="good", interview_result="next_round")
    cs.set_interview_attendance("c1", status="attended", remark="unchanged", by="A", allow_future=True)
    assert stored(store, "c1")["interview_result"] == "next_round"


def test_result_survives_an_unrelated_candidate_edit(store):
    # Same guarantee feedback has: an edit that does not touch the result keeps it.
    add(store, "c1", interview_attendance_status="attended",
        interview_feedback="excellent", interview_result="selected")
    cs.update_candidate("c1", {"email": "probe@example.test"})
    row = stored(store, "c1")
    assert row["interview_result"] == "selected"
    assert row["interview_feedback"] == "excellent"


# ── The route carries both fields ───────────────────────────────────────────

def test_the_attendance_route_passes_feedback_and_result_through(store, monkeypatch):
    add(store, "c1")
    # The route reads the viewer/actor and row access from the request; stub
    # those so the test drives the real handler with a plain body.
    monkeypatch.setattr(route, "_ops_by", lambda request: "Tester")
    from core import dashboard_access
    monkeypatch.setattr(dashboard_access, "assert_candidate_row_access", lambda request, row: None)
    monkeypatch.setattr(dashboard_access, "operator_profile", lambda request: {"role": "admin"})

    payload = asyncio.run(route.candidates_interview_attendance(
        cid="c1", request=None,
        body={"status": "attended", "feedback": "Excellent", "result": "Selected",
              "remark": "cleared the round", "attendee": "Bhavana"},
    ))

    assert payload["status"] == "ok"
    row = stored(store, "c1")
    assert row["interview_feedback"] == "excellent"
    assert row["interview_result"] == "selected"
