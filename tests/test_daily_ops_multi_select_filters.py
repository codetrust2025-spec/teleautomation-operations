"""Daily Ops filters take several values per field.

The Filters panel lets an operator tick HR + L2 + L3 together. The rule the
roster, the counters and the pie all follow:

* values inside one field are alternatives (Level HR OR L2 OR L3);
* different fields must all match (that level AND that attendee AND ...);
* a single string still works, so older callers and links are unchanged.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from features import candidate_store


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(candidate_store, "_load_cache", None, raising=False)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0, raising=False)
    return candidate_store


ROWS = [
    {"name": "Asha Varma", "interview_round": "HR", "interview_attendee": "Bhavana", "technology": "Python"},
    {"name": "Bala Kumar", "interview_round": "L2", "interview_attendee": "Nikhila", "technology": "Java"},
    {"name": "Chitra Rao", "interview_round": "L3", "interview_attendee": "Bhavana", "technology": "Java"},
    {"name": "Dev Anand Test", "interview_round": "L1", "interview_attendee": "Bhavana", "technology": "Python"},
    {"name": "Esha Pillai", "interview_round": "Final", "interview_attendee": "Tool", "technology": "Angular"},
]

DAY = "2026-09-20"
SPAN = ("2026-09-01", "2026-09-30")


def seed(store, rows=ROWS):
    payload = {"candidates": [], "updated_at": None}
    for index, row in enumerate(rows):
        base = {
            "id": f"row{index}", "phone": f"90000001{index:02d}", "stage": "in_progress",
            "task": "in_progress", "slot_confirmed": True,
            "service_type": "profile_service", "logged_date": "2026-09-01",
            "date": DAY, "time": f"{10 + index:02d}:00", "time_end": f"{10 + index:02d}:30",
        }
        base.update(row)
        payload["candidates"].append(base)
    with open(store._FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    store._load_cache = None


def names(payload):
    return {row["name"] for row in payload["interviews"]}


def test_several_levels_are_alternatives(store):
    seed(store)
    roster = store.daily_interview_roster(DAY, filter_round=["HR", "L2", "L3"])
    assert names(roster) == {"Asha Varma", "Bala Kumar", "Chitra Rao"}


def test_different_fields_must_all_match(store):
    seed(store)
    monitor = store.interview_monitor(*SPAN, filter_round=["HR", "L2", "L3"], filter_attendee=["Bhavana"])
    assert names(monitor) == {"Asha Varma", "Chitra Rao"}


def test_every_field_takes_several_values(store):
    seed(store)
    monitor = store.interview_monitor(
        *SPAN,
        filter_attendee=["Bhavana", "Tool"],
        filter_technology=["Python", "Angular"],
        filter_candidate=["Asha Varma", "Esha Pillai", "Bala Kumar"],
    )
    # Bala is a chosen candidate but neither his attendee nor his technology is.
    assert names(monitor) == {"Asha Varma", "Esha Pillai"}


def test_a_single_string_still_filters_as_before(store):
    seed(store)
    assert names(store.daily_interview_roster(DAY, filter_round="L2")) == {"Bala Kumar"}
    assert names(store.daily_interview_roster(DAY, filter_attendee="nikhila")) == {"Bala Kumar"}


def test_no_values_means_no_filter(store):
    seed(store)
    assert len(store.daily_interview_roster(DAY, filter_round=[])["interviews"]) == len(ROWS)
    assert len(store.daily_interview_roster(DAY, filter_round=["", "  "])["interviews"]) == len(ROWS)


def test_an_unknown_level_matches_nothing_rather_than_unrounded_rows(store):
    seed(store, ROWS + [{"name": "Farah Khan", "interview_round": ""}])
    assert names(store.daily_interview_roster(DAY, filter_round=["Nonsense"])) == set()


def test_the_summary_counts_the_same_selection(store):
    seed(store)
    summary = store.interview_global_summary(
        *SPAN, filter_round=["HR", "L3"], filter_candidate=["Asha Varma", "Chitra Rao", "Bala Kumar"],
    )
    overview = summary["booking_overview"]
    assert overview["total"] == 2
    # The Candidate list stays the full vocabulary of the range and level choice.
    listed = {item["name"] for item in summary["interviews"]["by_candidate"]}
    assert {"Asha Varma", "Chitra Rao"} <= listed


def test_filter_values_cleans_what_it_is_given():
    assert candidate_store.filter_values(None) == []
    assert candidate_store.filter_values(object()) == []
    assert candidate_store.filter_values(" HR ") == ["HR"]
    assert candidate_store.filter_values(["HR", "", "HR", " L2", 3]) == ["HR", "L2"]


def test_the_routes_read_repeated_query_parameters(store, monkeypatch):
    seed(store)
    from api.routers import candidates as router_module
    from fastapi import FastAPI

    monkeypatch.setattr(router_module, "_viewer_reference", lambda request: None)
    app = FastAPI()
    app.include_router(router_module.router)
    client = TestClient(app)

    daily = client.get(f"/candidates/interviews/daily?date={DAY}&round=HR&round=L2&round=L3&attendee=Bhavana").json()
    assert names(daily) == {"Asha Varma", "Chitra Rao"}

    monitor = client.get(
        f"/candidates/interviews/monitor?from={SPAN[0]}&to={SPAN[1]}&technology=Java&technology=Angular"
    ).json()
    assert names(monitor) == {"Bala Kumar", "Chitra Rao", "Esha Pillai"}

    summary = client.get(
        f"/candidates/interviews/global?from={SPAN[0]}&to={SPAN[1]}&candidate=Asha+Varma&candidate=Esha+Pillai"
    ).json()
    assert summary["booking_overview"]["total"] == 2


# ── Profile + Round: the Profile list must not shrink to what is ticked ──────
# 15 Nov, real data: the list was read off rows already narrowed by the profile
# and round selections, so ticking React JS left React JS as the only profile
# (23 became 1) and a second one could never be ticked; ticking L1 hid profiles
# too. technology_options is read before those two selections.

def options(summary):
    return summary["interviews"]["technology_options"]


def test_ticking_profiles_keeps_every_profile_on_the_list(store):
    seed(store)
    everything = options(store.interview_global_summary(*SPAN))
    assert everything == ["Angular", "Java", "Python"]
    assert options(store.interview_global_summary(*SPAN, filter_technology=["Java"])) == everything
    assert options(store.interview_global_summary(*SPAN, filter_technology=["Java", "Python"])) == everything


def test_ticking_rounds_keeps_every_profile_on_the_list(store):
    seed(store)
    everything = options(store.interview_global_summary(*SPAN))
    # HR is Asha (Python) alone, yet Java and Angular stay offered.
    assert options(store.interview_global_summary(*SPAN, filter_round=["HR"])) == everything
    assert options(store.interview_global_summary(*SPAN, filter_round=["HR"], filter_technology=["Python"])) == everything


def test_the_other_filters_still_narrow_the_profile_list(store):
    seed(store)
    # Tool attends only Esha (Angular): the list describes the attendee chosen.
    assert options(store.interview_global_summary(*SPAN, filter_attendee=["Tool"])) == ["Angular"]
    assert options(store.interview_global_summary(*SPAN, filter_candidate=["Bala Kumar"])) == ["Java"]


def test_the_counts_still_describe_the_selection(store):
    seed(store)
    summary = store.interview_global_summary(*SPAN, filter_technology=["Java"], filter_round=["L2", "L3"])
    assert summary["booking_overview"]["total"] == 2
    assert {t["name"] for t in summary["interviews"]["by_technology"]} == {"Java"}
    assert summary["interviews"]["count"] == 2


def test_every_listed_profile_selects_the_rows_counted_under_it(store):
    # A Tool candidate with no technology is listed (and counted) under the
    # Data Analyst profile; ticking that profile must find the row. Matching
    # the raw stored value ("" -> Unspecified) found nothing.
    seed(store, ROWS + [{"name": "Keerthana Demo", "interview_round": "L1", "interview_attendee": "Tool", "technology": ""}])
    summary = store.interview_global_summary(*SPAN)
    assert store.TOOL_PROFILE_CANDIDATE_TECHNOLOGY in options(summary)
    for profile in options(summary):
        counted = next(t["scheduled"] for t in summary["interviews"]["by_technology"] if t["name"] == profile)
        picked = store.interview_monitor(*SPAN, filter_technology=[profile])
        assert len(picked["interviews"]) == counted, profile
