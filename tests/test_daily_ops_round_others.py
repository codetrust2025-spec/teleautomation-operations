"""Daily Ops round filter: "Others" (owner, 15 Nov).

The Interview Round filter lists Screening, L1, L2, HR and Final, each on its
own, and then Others: every booking whose round is not one of those five -- no
round recorded, Assessment, a retired L3 (owner, 24 Nov), or any other label a
row carries. Others is an
alternative like any round (L1 or Others), and still ANDs with the other
filters.

Assessment is now a round a booking can store. Before, every save path ran the
label through normalise_interview_round, which knew only the six, so an
Assessment round was saved as blank.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from features import candidate_store
from tests.test_daily_ops_multi_select_filters import DAY, SPAN, names, seed


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(candidate_store, "_load_cache", None, raising=False)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0, raising=False)
    return candidate_store


ROWS = [
    {"name": "Screen Person", "interview_round": "Screening", "interview_attendee": "Bhavana"},
    {"name": "Lone One", "interview_round": "L1", "interview_attendee": "Bhavana"},
    {"name": "Two Person", "interview_round": "L2", "interview_attendee": "Nikhila"},
    {"name": "Three Person", "interview_round": "L3", "interview_attendee": "Bhavana"},
    {"name": "Hr Person", "interview_round": "HR", "interview_attendee": "Bhavana"},
    {"name": "Final Person", "interview_round": "Final", "interview_attendee": "Bhavana"},
    {"name": "Assess Person", "interview_round": "Assessment", "interview_attendee": "Bhavana"},
    # A legacy label stored before rounds were normalised on save.
    {"name": "Tech Person", "interview_round": "Technical", "interview_attendee": "Nikhila"},
    {"name": "Blank Person", "interview_round": "", "interview_attendee": "Bhavana"},
    {"name": "Missing Person", "interview_attendee": "Bhavana"},
]
STANDARD = {"Screen Person", "Lone One", "Two Person", "Hr Person", "Final Person"}
# L3 is retired (owner, 24 Nov): an interview that still says L3 is under Others.
OTHERS = {"Three Person", "Assess Person", "Tech Person", "Blank Person", "Missing Person"}


def test_others_is_every_round_off_the_six(store):
    seed(store, ROWS)
    assert names(store.daily_interview_roster(DAY, filter_round=["other"])) == OTHERS


def test_the_six_stay_separate(store):
    seed(store, ROWS)
    # L3 is no longer offered, but asking for it still finds the old L3 rows.
    for name, rnd in [("Screen Person", "Screening"), ("Lone One", "L1"), ("Two Person", "L2"),
                      ("Three Person", "L3"), ("Hr Person", "HR"), ("Final Person", "Final")]:
        assert names(store.daily_interview_roster(DAY, filter_round=[rnd])) == {name}, rnd


def test_others_combines_with_rounds_as_an_alternative(store):
    seed(store, ROWS)
    assert names(store.daily_interview_roster(DAY, filter_round=["L1", "other"])) == OTHERS | {"Lone One"}
    every = ["Screening", "L1", "L2", "HR", "Final", "other"]
    assert names(store.daily_interview_roster(DAY, filter_round=every)) == STANDARD | OTHERS


def test_others_still_ands_with_the_other_filters(store):
    seed(store, ROWS)
    monitor = store.interview_monitor(*SPAN, filter_round=["other"], filter_attendee=["Nikhila"])
    assert names(monitor) == {"Tech Person"}


def test_the_label_others_is_read_as_the_token(store):
    seed(store, ROWS)
    assert names(store.daily_interview_roster(DAY, filter_round="Others")) == OTHERS


def test_an_unknown_value_still_matches_nothing(store):
    seed(store, ROWS)
    assert names(store.daily_interview_roster(DAY, filter_round=["Nonsense"])) == set()


def test_the_summary_counts_others_the_same_way(store):
    seed(store, ROWS)
    summary = store.interview_global_summary(*SPAN, filter_round=["other"])
    assert summary["booking_overview"]["total"] == len(OTHERS)


def test_the_route_passes_others_through(store, monkeypatch):
    seed(store, ROWS)
    from api.routers import candidates as router_module
    from fastapi import FastAPI

    monkeypatch.setattr(router_module, "_viewer_reference", lambda request: None)
    app = FastAPI()
    app.include_router(router_module.router)
    client = TestClient(app)
    response = client.get(
        "/candidates/interviews/daily", params=[("date", DAY), ("round", "L2"), ("round", "other")],
    )
    assert response.status_code == 200, response.text
    assert names(response.json()) == OTHERS | {"Two Person"}


@pytest.mark.parametrize("raw", ["Assessment", "assessment", "ASSESSMENT", "  Assessment "])
def test_assessment_is_a_round_a_booking_can_store(raw):
    assert candidate_store.normalise_interview_round(raw) == "Assessment"


@pytest.mark.parametrize("raw", ["Candidate ID: 123", "Technical", "Assessment round 2", "coding test"])
def test_free_text_is_still_not_a_round(raw):
    # Keeping arbitrary text is what turned "Candidate ID: 123" into L1 once.
    assert candidate_store.normalise_interview_round(raw) == ""


def test_the_standard_rounds_no_longer_include_l3():
    assert candidate_store.STANDARD_INTERVIEW_ROUNDS == {"L1", "L2", "HR", "Final", "Screening"}
    assert candidate_store.RETIRED_INTERVIEW_ROUNDS == {"L3"}
    assert candidate_store.VALID_INTERVIEW_ROUNDS == candidate_store.STANDARD_INTERVIEW_ROUNDS | {"Assessment"}
