"""Test records in production stay stored but are not counted as money.

13 Nov 2026: four fixtures had reached the live store. One was a "Ram Charan M S"
with placeholder phone 9000000009, carrying another client's real payment
reference. The other three were "Round Person" rows (ids lead, booked, earlier)
with a fake ₹5,000 proof. They were counted in revenue, handler earnings and the
lists. Deleting them would lose the evidence, so they stay; the figures skip the
ids marked in candidate_test_record_exclusions.

Names and numbers below are invented.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

from api.routers import candidates as route
from features import candidate_store as cs
from features import payment_mismatch_report as pmr
from features import test_record_exclusions as tre


def row(cid, name, **overrides):
    base = {
        "id": cid, "name": name, "phone": "9876500001", "stage": "in_progress", "task": "in_progress",
        "service_type": "profile_service", "reference": "Ravinder", "technology": "Python",
        "payment": 20000, "expected_payment": 20000, "date": "2026-09-10", "time": "10:00",
        "time_end": "11:00", "slot_confirmed": True, "interview_round": "L1", "logged_date": "2026-09-01",
    }
    base.update(overrides)
    return base


GENUINE = row("genuine", "Asha Fakename", phone="9876500001", payment=20000)
GENUINE_ROUND = row("round-real", "Ravi Testcase", phone="9876500002", service_type="round_wise", payment=4000)
FIXTURE = row("fixture1", "Asha Fakename Two", phone="9000000009", payment=20000, date="", time="")
ROUND_FIXTURE = row("lead", "Round Person", phone="9000000601", service_type="round_wise",
                    reference="Test Owner", payment=5000, expected_payment=5000)


@pytest.fixture
def store(monkeypatch):
    rows = [GENUINE, GENUINE_ROUND, FIXTURE, ROUND_FIXTURE]
    data = {"candidates": [dict(r) for r in rows]}
    saved = []
    monkeypatch.setattr(cs, "_load", lambda *args, **kwargs: data)
    monkeypatch.setattr(cs, "_save", lambda payload: saved.append(payload))
    monkeypatch.setattr(cs, "reconcile_resume_metadata", lambda *a, **k: None)
    from core import dashboard_access
    monkeypatch.setattr(dashboard_access, "handler_reference_scope", lambda request, reference: reference)
    yield data, saved
    tre.set_for_tests(None)


def figures():
    s = cs.stats(month="all", _skip_pending_works=True)
    return s["direct_revenue"] + s.get("consultancy_revenue", 0), s["total"]


def listed():
    payload = asyncio.run(route.candidates_list(
        request=None, stage="all", task=None, search=None, month="all", pending_only=False,
        reference=None, service_type=None, ai_filter=None,
    ))
    return {r["id"] for r in payload["candidates"]}


def test_marked_test_records_leave_the_totals_and_the_list(store):
    tre.set_for_tests(set())
    revenue_before, total_before = figures()
    assert {"fixture1", "lead"} <= listed()

    tre.set_for_tests({"fixture1", "lead"})
    revenue_after, total_after = figures()

    assert revenue_before - revenue_after == 25000
    assert total_before - total_after == 2
    assert listed() == {"genuine", "round-real"}


def test_genuine_rows_and_the_stored_records_are_untouched(store):
    data, saved = store
    before = [dict(r) for r in data["candidates"]]
    tre.set_for_tests({"fixture1", "lead"})

    figures()
    listed()
    cs.pending_works()

    assert data["candidates"] == before
    assert saved == []
    assert {r["id"] for r in data["candidates"]} == {"genuine", "round-real", "fixture1", "lead"}


def test_lookups_still_see_every_row(store):
    tre.set_for_tests({"fixture1", "lead"})
    assert {"fixture1", "lead"} <= {r["id"] for r in cs.list_candidates(stage="all", month="all")}


def test_the_payment_mismatch_report_skips_them(store, monkeypatch):
    seen = []
    real = pmr._group_key
    monkeypatch.setattr(pmr, "_group_key", lambda r: seen.append(r["id"]) or real(r))
    tre.set_for_tests({"fixture1", "lead"})
    pmr.mismatch_report()
    assert set(seen) == {"genuine", "round-real"}


def test_nothing_marked_means_nothing_changes(store):
    tre.set_for_tests(set())
    assert tre.without_test_records([GENUINE, FIXTURE]) == [GENUINE, FIXTURE]


def test_an_unreadable_exclusion_table_counts_everything(monkeypatch):
    from core.db import connection

    tre.set_for_tests(None)
    monkeypatch.setattr(tre, "_cache", None)
    monkeypatch.setattr(connection, "use_postgres", lambda: True)

    def broken():
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(connection, "get_connection", broken)
    assert tre.excluded_ids() == frozenset()
    monkeypatch.setattr(tre, "_cache", None)


def test_the_migration_is_additive_and_names_only_the_four_audited_rows():
    sql = (pathlib.Path(__file__).resolve().parents[1] / "core/migrations/041_test_record_exclusions.sql").read_text(
        encoding="utf-8")
    upper = "\n".join(line for line in sql.upper().splitlines() if not line.lstrip().startswith("--"))
    for verb in ("DELETE", "UPDATE ", "DROP", "ALTER", "TRUNCATE"):
        assert verb not in upper, verb
    assert "candidates_store" not in sql.split("INSERT INTO")[1]
    for cid in ("'40c31f4597'", "'lead'", "'booked'", "'earlier'"):
        assert cid in sql
    assert "ON CONFLICT (candidate_id) DO NOTHING" in sql
    # Ram Charan's genuine bookings are named only as evidence, never marked.
    marked = [line.strip() for line in sql.splitlines() if line.strip().startswith("('")]
    assert marked == ["('40c31f4597',", "('lead',", "('booked',", "('earlier',"]
