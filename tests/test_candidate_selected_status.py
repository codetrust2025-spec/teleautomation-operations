"""★ Selected: the final status of a successful profile-wise candidate.

A profile-wise candidate whose stage is Closed / Completed is shown as
Selected on every page. The rule is derived, never stored:

* it holds for a profile-wise candidate with stage `completed`, and for nobody
  else -- not a round-wise candidate who completed, not an open, rejected or
  dropped profile;
* every candidate and interview row carries the answer (`candidate_selected`),
  so the Candidates page, Daily Ops and Bookings Analytics read one rule;
* the stored stage, service type, payments and interview history are untouched
  -- a Save that sends the derived field back stores nothing new;
* the Candidates page can filter, search and count by it, and the existing
  "completed" filter and amounts still mean what they meant;
* Bookings Analytics marks a candidate Selected whatever the period, since it
  is the candidate's outcome rather than one interview's.

Names are invented.
"""
from __future__ import annotations

import asyncio

import pytest

from api.routers import candidates as route
from features import candidate_store as cs


def row(cid, name, **overrides):
    base = {
        "id": cid, "name": name, "phone": f"98{abs(hash(name)) % 10**8:08d}",
        "stage": "in_progress", "task": "in_progress", "service_type": "profile_service",
        "reference": "Ravinder", "technology": "Python", "payment": 0,
        "date": "2026-09-10", "time": "10:00", "time_end": "11:00", "slot_confirmed": True,
        "interview_round": "L1", "interview_attendee": "Bhavana", "logged_date": "2026-09-01",
        "ctc_percentage": 8,
    }
    base.update(overrides)
    return base


PLACED = row("placed", "Asha Fakename", stage="completed", payment=20000)
ROUND_DONE = row("round", "Ravi Testcase", stage="completed", service_type="round_wise", payment=4000)
OPEN = row("open", "Meena Sample")
REJECTED = row("rejected", "Kiran Mockdata", stage="fail")
DROPPED = row("dropped", "Divya Example", stage="dropped")
# A profile row from before service_type was stored: the server reads it as
# profile service, so a completed one is Selected too.
LEGACY = row("legacy", "Gopal Fixture", stage="completed")
LEGACY.pop("service_type")


@pytest.fixture
def store(monkeypatch):
    data = {"candidates": [dict(r) for r in (PLACED, ROUND_DONE, OPEN, REJECTED, DROPPED, LEGACY)]}
    monkeypatch.setattr(cs, "_load", lambda *args, **kwargs: data)
    monkeypatch.setattr(cs, "_save", lambda payload: data.update(payload))
    monkeypatch.setattr(route, "_viewer_reference", lambda request: None)
    return data


def listed(**filters):
    payload = asyncio.run(route.candidates_list(
        request=None, stage=filters.get("stage"), task=None, search=filters.get("search"),
        month=None, pending_only=False, reference=None, service_type=filters.get("service_type"),
        ai_filter=None,
    ))
    assert payload["status"] == "ok"
    return payload["candidates"]


@pytest.fixture(autouse=True)
def _no_reference_scope(monkeypatch):
    from core import dashboard_access
    monkeypatch.setattr(dashboard_access, "handler_reference_scope", lambda request, reference: reference)
    monkeypatch.setattr(dashboard_access, "handler_payout_reference_scope", lambda request, reference: reference)


# ── The rule ────────────────────────────────────────────────────────────────

def test_selected_is_a_completed_profile_wise_candidate_and_nobody_else():
    assert cs.candidate_is_selected(PLACED)
    assert cs.candidate_is_selected(LEGACY)
    for other in (ROUND_DONE, OPEN, REJECTED, DROPPED):
        assert not cs.candidate_is_selected(other), other["id"]


def test_it_is_the_rule_the_closure_complimentary_already_pays_on():
    # One definition: the ₹5,000 profile-closure complimentary has always been
    # paid on exactly the candidates that now read Selected.
    for r in (PLACED, ROUND_DONE, OPEN, REJECTED, DROPPED, LEGACY):
        assert cs.candidate_is_selected(r) == cs.is_closed_profile_service(r)


# ── Every row carries it; nothing stores it ─────────────────────────────────

def test_every_candidate_row_carries_the_answer(store):
    by_id = {r["id"]: r["candidate_selected"] for r in listed()}
    assert by_id == {"placed": True, "legacy": True, "round": False, "open": False,
                     "rejected": False, "dropped": False}


def test_interview_rows_carry_it_too_so_daily_ops_reads_the_same_rule(store):
    payload = cs.interview_monitor("2026-09-01", "2026-09-30")
    flags = {r["id"]: r.get("candidate_selected") for r in payload["interviews"]}
    assert flags.get("placed") is True
    assert flags.get("round") is False
    assert flags.get("open") is False


def test_the_stored_row_is_unchanged_and_a_save_cannot_store_the_flag(store):
    before = {r["id"]: dict(r) for r in store["candidates"]}
    listed()
    cs.stats()
    cs.interview_global_summary("2000-01-01", "2100-12-31")
    assert {r["id"]: r for r in store["candidates"]} == before
    assert all("candidate_selected" not in r for r in store["candidates"])
    # A Save sends whole rows back; the derived field is not a stored one.
    cleaned = cs._normalise({**PLACED, "candidate_selected": True}, existing=PLACED)
    assert "candidate_selected" not in cleaned
    assert cleaned["stage"] == "completed"
    assert cleaned["service_type"] == "profile_service"


# ── Filters and search on the Candidates page ───────────────────────────────

def test_the_selected_filter_lists_the_selected(store):
    assert sorted(r["id"] for r in listed(stage="selected")) == ["legacy", "placed"]


def test_closed_completed_lists_the_other_completed(store):
    assert [r["id"] for r in listed(stage="closed_completed")] == ["round"]


def test_completed_still_means_every_completed_candidate(store):
    assert sorted(r["id"] for r in listed(stage="completed")) == ["legacy", "placed", "round"]


def test_the_filters_combine_with_service_type(store):
    assert listed(stage="selected", service_type="round_wise") == []
    assert sorted(r["id"] for r in listed(stage="selected", service_type="profile_service")) == ["legacy", "placed"]


@pytest.mark.parametrize("query", ["selected", "Selected", "★ Selected", " selected "])
def test_searching_the_word_finds_the_selected(store, query):
    assert sorted(r["id"] for r in listed(search=query)) == ["legacy", "placed"]


def test_a_name_search_is_unchanged(store):
    assert [r["id"] for r in listed(search="meena")] == ["open"]


# ── Counters ────────────────────────────────────────────────────────────────

def test_the_counters_name_the_selected(store):
    stats = cs.stats()
    assert stats["selected_count"] == 2
    assert stats["by_stage"]["completed"] == 3
    ravinder = next(p for p in stats["top_performers"] if p["name"] == "Ravinder")
    assert ravinder["selected"] == 2
    assert ravinder["completed"] == 3


def test_selected_changes_no_other_figure(store, monkeypatch):
    # The same stats with nobody Selected: every amount, count and balance is
    # identical, and only the two Selected counts differ.
    def without_selected(payload):
        payload = {k: v for k, v in payload.items() if k not in {"selected_count", "updated_at"}}
        payload["top_performers"] = [{k: v for k, v in p.items() if k != "selected"} for p in payload["top_performers"]]
        return payload

    with_rule = without_selected(cs.stats())
    monkeypatch.setattr(cs, "candidate_is_selected", lambda r: False)
    assert without_selected(cs.stats()) == with_rule
    assert with_rule["revenue_total"] == 24000


# ── Bookings Analytics ──────────────────────────────────────────────────────

def test_bookings_analytics_marks_each_candidate(store):
    summary = cs.interview_global_summary("2000-01-01", "2100-12-31")
    overview = {c["name"]: c["selected"] for c in summary["booking_overview"]["by_candidate"]}
    options = {c["name"]: c["selected"] for c in summary["interviews"]["by_candidate"]}
    assert overview == options
    assert overview["Asha Fakename"] is True
    assert overview["Gopal Fixture"] is True
    assert overview["Ravi Testcase"] is False
    assert overview["Meena Sample"] is False


def test_the_mark_is_the_candidates_whatever_the_period(store):
    # A later booking for a placed candidate, in a range that does not hold the
    # row their completion was recorded on, still reads Selected.
    store["candidates"].append(row("placed-2", "Asha Fakename", stage="completed", date="2026-10-20"))
    summary = cs.interview_global_summary("2026-10-01", "2026-10-31")
    names = {c["name"]: c["selected"] for c in summary["booking_overview"]["by_candidate"]}
    assert names == {"Asha Fakename": True}
