"""Selecting one candidate scopes the whole Daily Ops view to that person.

The Candidate dropdown is a selection, not a search: picking "Ram Charan M S"
must show that person's bookings and nobody else's -- not "Rama Krishna",
which a loose match once let through on the shared "ram".

Two things have to agree with the selection and one must not:

* the roster table (`interview_monitor`) lists only the chosen person's rows;
* every counter the dashboard shows for the selection -- the status tabs and
  the "Bookings by candidate" overview beside them (its total, its per-
  candidate slices, its level and technology breakdowns) -- counts only that
  person. The overview used to be read off the unfiltered rows, so it kept
  saying "all candidates" above a table cut down to one;
* the Candidate dropdown itself keeps listing everyone, because the selection
  was made from it and has to stay changeable.
"""
from __future__ import annotations

import json

import pytest

from features import candidate_store


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
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
            "interview_round": "L2", "interview_attendee": "Bhavana",
            "service_type": "profile_service", "logged_date": "2026-06-01",
        }
        base.update(row)
        payload["candidates"].append(base)
    with open(store._FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    store._load_cache = None


# Two different people whose names share the leading "ram", on two dates in the
# range -- the shape the screenshot showed.
ROWS = [
    {"name": "Ram Charan M S", "date": "2026-07-03", "time": "12:30",
     "time_end": "13:00", "technology": "Angular"},
    {"name": "Rama Krishna", "date": "2026-09-17", "time": "17:00",
     "time_end": "18:00", "technology": "Automation Testing"},
    {"name": "Ram Charan M S", "date": "2026-09-20", "time": "10:00",
     "time_end": "11:00", "technology": "Angular"},
]

SPAN = ("2026-07-01", "2026-09-30")


def test_the_table_lists_only_the_selected_candidate(store):
    seed(store, ROWS)
    monitor = store.interview_monitor(*SPAN, filter_candidate="Ram Charan M S")
    names = {row["name"] for row in monitor["interviews"]}
    assert names == {"Ram Charan M S"}
    assert monitor["count"] == 2


def test_the_dropdown_label_suffix_is_not_part_of_the_selection(store):
    # The option reads "Ram Charan M S (1)" on screen; the parenthesised count
    # is a label, and the person is still the one the key resolves to.
    seed(store, ROWS)
    monitor = store.interview_monitor(*SPAN, filter_candidate="Ram Charan M S (1)")
    assert {row["name"] for row in monitor["interviews"]} == {"Ram Charan M S"}


def test_the_status_counters_count_only_the_selected_candidate(store):
    seed(store, ROWS)
    summary = store.interview_global_summary(*SPAN, filter_candidate="Ram Charan M S")
    interviews = summary["interviews"]
    assert interviews["count"] == 2
    assert interviews["pending_count"] == 2


def test_the_booking_overview_describes_only_the_selected_candidate(store):
    # This is the regression: the pie, its total and its slices were read off
    # the rows before the candidate filter, so they stayed on every candidate.
    seed(store, ROWS)
    summary = store.interview_global_summary(*SPAN, filter_candidate="Ram Charan M S")
    overview = summary["booking_overview"]
    assert overview["total"] == 2
    assert [c["name"] for c in overview["by_candidate"]] == ["Ram Charan M S"]
    assert all("Rama Krishna" not in c["name"] for c in overview["by_candidate"])
    # The level and technology breakdowns beside the pie are the selection's too.
    assert {lvl["name"] for lvl in overview["by_level"]} == {"L2"}
    assert {tech["name"] for tech in overview["by_technology"]} == {"Angular"}


def test_the_candidate_dropdown_still_lists_everyone_while_one_is_selected(store):
    # The selection must stay changeable: narrowing the view must not empty the
    # list the view was narrowed from.
    seed(store, ROWS)
    summary = store.interview_global_summary(*SPAN, filter_candidate="Ram Charan M S")
    dropdown = {c["name"] for c in summary["interviews"]["by_candidate"]}
    assert {"Ram Charan M S", "Rama Krishna"} <= dropdown


def test_with_no_candidate_selected_the_whole_period_is_counted(store):
    # The wide view is unchanged: no selection, everyone counted.
    seed(store, ROWS)
    summary = store.interview_global_summary(*SPAN)
    assert summary["booking_overview"]["total"] == 3
    assert summary["interviews"]["count"] == 3
    names = {c["name"] for c in summary["booking_overview"]["by_candidate"]}
    assert names == {"Ram Charan M S", "Rama Krishna"}
