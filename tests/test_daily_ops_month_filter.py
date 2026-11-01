"""The Daily Ops month filter offers only months an interview really sits in.

A month in that dropdown is a promise that selecting it shows something. The
options are therefore built from confirmed interview rows, and a row whose date
is not a real calendar day cannot contribute one.
"""
from __future__ import annotations

import json

import pytest

from features import candidate_store


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    # The store memoises reads, so a scratch file is only honoured once the
    # cache from any earlier test is dropped.
    monkeypatch.setattr(candidate_store, "_load_cache", None, raising=False)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0, raising=False)
    return candidate_store


def seed(store, rows):
    payload = {"candidates": [], "updated_at": None}
    for index, row in enumerate(rows):
        base = {
            "id": f"row{index}", "name": f"Candidate {index}",
            "phone": f"90000000{index:02d}", "stage": "in_progress",
            "task": "in_progress", "slot_confirmed": True,
            "interview_round": "L1", "interview_attendee": "Bhavana",
            "service_type": "profile_service", "logged_date": "2026-06-01",
        }
        base.update(row)
        payload["candidates"].append(base)
    with open(store._FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    store._load_cache = None


def months(store):
    summary = store.interview_global_summary("2026-08-01", "2026-08-31")
    return [m["value"] for m in summary["available_months"]]


def test_a_month_is_offered_for_every_real_scheduled_interview(store):
    seed(store, [
        {"date": "2026-04-10", "time": "10:00"},
        {"date": "2026-06-19", "time": "14:00"},
        {"date": "2026-08-04", "time": "11:30"},
    ])
    assert {"2026-04", "2026-06", "2026-08"} <= set(months(store))


def test_a_genuine_future_interview_keeps_its_month(store):
    seed(store, [{"date": "2026-12-15", "time": "10:00"}])
    assert "2026-12" in months(store)


def test_an_impossible_date_cannot_create_a_month(store):
    # "2027-13-40" is not a day anyone can be interviewed on, and the old shape
    # check (7 characters with a dash in the middle) happily offered "2027-13".
    seed(store, [
        {"date": "2027-13-40", "time": "10:00"},
        {"date": "2026-08-04", "time": "11:30"},
    ])
    offered = months(store)
    assert "2027-13" not in offered
    assert not any(m.startswith("2027") for m in offered)


@pytest.mark.parametrize("bad", ["", "   ", "not-a-date", "2026-02-30", "20260804",
                                 "2026-1-4", "0000-00-00", "9999-99-99"])
def test_malformed_dates_are_ignored_entirely(store, bad):
    seed(store, [{"date": bad, "time": "10:00"}, {"date": "2026-08-04", "time": "11:30"}])
    assert months(store) and all(len(m) == 7 and m[4] == "-" for m in months(store))


def test_cancelled_and_failed_records_do_not_open_a_month(store):
    seed(store, [
        {"date": "2098-09-09", "time": "10:00", "stage": "dropped"},
        {"date": "2098-10-10", "time": "10:00", "stage": "fail"},
        {"date": "2026-08-04", "time": "11:30"},
    ])
    offered = months(store)
    assert "2098-09" not in offered
    assert "2098-10" not in offered


def test_an_unconfirmed_slot_does_not_open_a_month(store):
    # The probe month must sit outside the current and previous months, which are
    # always offered by design (see
    # test_the_current_and_previous_month_are_always_available). A date inside
    # either of them is offered for that reason alone, so this test would quietly
    # stop checking the unconfirmed rule once the calendar caught up with it.
    seed(store, [
        {"date": "2098-11-11", "time": "10:00", "slot_confirmed": False},
        {"date": "2026-08-04", "time": "11:30"},
    ])
    assert "2098-11" not in months(store)


def test_the_current_and_previous_month_are_always_available(store):
    # An operator must be able to switch to this month right after adding a row.
    seed(store, [{"date": "2026-04-10", "time": "10:00"}])
    offered = months(store)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    assert now.strftime("%Y-%m") in offered


def test_months_are_unique_and_newest_first(store):
    seed(store, [
        {"date": "2026-04-10", "time": "10:00"},
        {"date": "2026-04-11", "time": "11:00"},
        {"date": "2026-06-19", "time": "14:00"},
        {"date": "2026-06-20", "time": "15:00"},
    ])
    offered = months(store)
    assert len(offered) == len(set(offered))
    assert offered == sorted(offered, reverse=True)


def test_each_month_reports_how_many_interviews_back_it(store):
    seed(store, [
        {"date": "2026-06-19", "time": "14:00"},
        {"date": "2026-06-20", "time": "15:00"},
    ])
    summary = store.interview_global_summary("2026-08-01", "2026-08-31")
    june = next(m for m in summary["available_months"] if m["value"] == "2026-06")
    assert june["count"] == 2
