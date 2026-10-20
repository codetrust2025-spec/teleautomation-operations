"""A Save of one candidate's record never rewrites another person's records.

Saving a profile record copies its shared fields (name, phone, email, stage,
technology, referrer, ...) onto that person's other slot records, so the
profile reads the same on every row. The records it copied onto were every
profile record sharing the saved row's phone *or* its name.

21 Oct audit of production (11 Oct incident): one candidate's record had been
given another person's phone. The Save that put his own number back copied his
name, phone, email, stage, technology and referrer onto all 13 of her records.
Daily Ops then listed her April and May interviews as his -- a profile created
on 25 Sep with 12 "historical" interviews -- and her verified payment proof,
now on "his" profile, was deleted as a duplicate, which reversed its credit.

What has to hold, through the real PATCH route, the real store and the real
Daily Ops routes:

    - the reported case: the record is corrected; the other person's records,
      and the interviews Daily Ops counts for each of them, stay theirs;
    - two people with the same name on different phones: saving one does not
      touch the other;
    - one person's records still follow a Save: a new phone, a corrected name,
      a spelling variant on the same phone, a legacy record with no phone;
    - a round-wise booking is still never rewritten by a profile Save.

Names and numbers are invented (the 90000xxxxx block).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from api.routers import candidates as route
from core import dashboard_access
from features import candidate_store as cs

HERS = "9000000998"
HIS = "9000000777"
ALL_TIME = ("2000-01-01", "2100-12-31")
IDENTITY = ("name", "phone", "email", "stage", "technology", "reference")


def record(cid, name, phone, **over):
    base = {
        "id": cid, "name": name, "phone": phone, "service_type": "profile_service",
        "stage": "completed", "task": "completed", "slot_confirmed": True,
        "interview_attendance_status": "attended", "interview_attendee": "Bhavana",
        "interview_round": "L1", "time": "15:00", "time_end": "15:30",
        "expected_payment": 20000, "payment": 0, "ctc_percentage": 12,
        "logged_date": "2026-04-15", "created_at": "2026-06-13T20:17:15+00:00",
    }
    base.update(over)
    return base


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """The real JSON store on a temporary file: real _load, real _save."""
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "_load_cache", None, raising=False)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0, raising=False)
    monkeypatch.setattr(cs, "reconcile_resume_metadata", lambda: 0)
    # The route's sign-in and role scoping are not under test; everything after
    # them is the code production runs.
    monkeypatch.setattr(dashboard_access, "assert_candidate_row_access", lambda request, row: None)
    monkeypatch.setattr(dashboard_access, "prepare_candidate_body", lambda request, body: dict(body))
    monkeypatch.setattr(route, "_viewer_reference", lambda request: None)
    from features import operator_tasks_store

    monkeypatch.setattr(operator_tasks_store, "list_tasks", lambda **kwargs: [])
    return cs


def seed(store, rows):
    with open(store._FILE, "w", encoding="utf-8") as handle:
        json.dump({"candidates": rows, "updated_at": None}, handle)
    store._load_cache = None


def stored(store):
    store._load_cache = None
    return {r["id"]: r for r in store._load(force=True)["candidates"]}


def save(cid, body):
    return asyncio.run(route.candidates_update(cid, None, body))


def daily_ops_summary(candidate=None):
    return asyncio.run(route.candidates_interviews_global(
        None, from_date=ALL_TIME[0], to_date=ALL_TIME[1], attendee=None, search=None,
        channel=None, round=None, technology=None, candidate=candidate,
        upcoming_only=False, unresolved_only=False,
    ))


def daily_ops_roster(candidate):
    return asyncio.run(route.candidates_interviews_monitor(
        None, from_date=ALL_TIME[0], to_date=ALL_TIME[1], attendee=None, search=None,
        channel=None, round=None, technology=None, candidate=candidate,
        upcoming_only=False, unresolved_only=False,
    ))


# Her 13 records from the spring: 11 confirmed interviews and 2 unconfirmed
# entries, all attended, on her phone with her email, technology and referrer.
HER_DATES = [
    ("2026-04-15", "13:00", False), ("2026-04-15", "15:00", True), ("2026-04-15", "19:00", False),
    ("2026-04-23", "15:00", True), ("2026-04-30", "11:00", True), ("2026-05-11", "11:15", True),
    ("2026-05-11", "15:00", True), ("2026-05-14", "09:30", True), ("2026-05-19", "14:30", True),
    ("2026-05-19", "17:00", True), ("2026-05-20", "10:00", True), ("2026-05-20", "14:30", True),
    ("2026-05-21", "15:00", True),
]


def reported_case():
    hers = [
        record(f"her{i}", "Asha Fakename", HERS, date=day, time=at, slot_confirmed=confirmed,
               email="asha@example.test", technology="Power BI", reference="Referrer One",
               payment=20000 if i in (0, 2, 12) else 0)
        for i, (day, at, confirmed) in enumerate(HER_DATES)
    ]
    # His one record, created in September; an earlier Save had given it her
    # phone, and an old email and technology.
    his = record("his", "Ravi Kolla", HERS, stage="in_progress", task="not_started",
                 date="2026-10-15", time="12:30", time_end="13:00",
                 email="ravi.old@example.test", technology="SAP Sales", reference="Referrer Two",
                 payment=20000, logged_date="2026-09-25", created_at="2026-09-25T13:01:40+00:00")
    return hers + [his]


# The Save that corrected his record (11 Oct, 07:31): his own phone, email and
# technology back, as the edit form sends them.
CORRECTION = {
    "name": "Ravi Kolla", "phone": HIS, "email": "ravi@example.test", "technology": "React JS",
    "stage": "in_progress", "reference": "Referrer Two", "expected_payment": 20000,
}


def test_correcting_one_record_leaves_the_other_persons_records_as_they_were(store):
    seed(store, reported_case())
    before = stored(store)
    result = save("his", dict(CORRECTION))
    assert result["status"] == "ok"
    after = stored(store)
    assert {k: after["his"][k] for k in ("name", "phone", "email", "technology")} == {
        "name": "Ravi Kolla", "phone": HIS, "email": "ravi@example.test", "technology": "React JS",
    }
    rewritten = {
        cid: {k: (before[cid].get(k), after[cid].get(k)) for k in IDENTITY if before[cid].get(k) != after[cid].get(k)}
        for cid in before if cid != "his"
    }
    assert {cid: change for cid, change in rewritten.items() if change} == {}


def test_daily_ops_keeps_each_persons_interviews_after_the_correction(store):
    seed(store, reported_case())
    assert save("his", dict(CORRECTION))["status"] == "ok"

    by_candidate = {b["name"]: b for b in daily_ops_summary()["interviews"]["by_candidate"]}
    assert by_candidate["Ravi Kolla"]["scheduled"] == 1
    assert by_candidate["Asha Fakename"]["scheduled"] == 11
    assert by_candidate["Asha Fakename"]["attended"] == 11

    his = daily_ops_roster("Ravi Kolla")
    assert [(r["id"], r["date"]) for r in his["interviews"]] == [("his", "2026-10-15")]
    selected = daily_ops_summary("Ravi Kolla")
    assert selected["interviews"]["count"] == 1
    assert selected["booking_overview"]["total"] == 1

    hers = daily_ops_roster("Asha Fakename")
    assert hers["count"] == 11
    assert {r["phone"] for r in hers["interviews"]} == {HERS}


def test_two_people_with_one_name_keep_their_own_details(store):
    seed(store, [
        record("a1", "Vijay Fakename", "9000000111", email="vijay.one@example.test", technology="Java",
               reference="Referrer One", date="2026-05-02"),
        record("a2", "Vijay Fakename", "9000000111", email="vijay.one@example.test", technology="Java",
               reference="Referrer One", date="2026-05-09"),
        record("b1", "Vijay Fakename", "9000000222", email="vijay.two@example.test", technology="SAP FICO",
               reference="Referrer Two", stage="in_progress", date="2026-10-01"),
    ])
    assert save("b1", {"name": "Vijay Fakename", "email": "vijay.v2@example.test", "technology": "SAP MM"})["status"] == "ok"
    after = stored(store)
    assert (after["b1"]["email"], after["b1"]["technology"]) == ("vijay.v2@example.test", "SAP MM")
    for cid in ("a1", "a2"):
        assert (after[cid]["email"], after[cid]["technology"], after[cid]["phone"]) == (
            "vijay.one@example.test", "Java", "9000000111",
        )


def test_one_persons_records_still_take_a_new_phone_and_a_corrected_name(store):
    seed(store, [
        record("m1", "Kiran", "9000000333", date="2026-05-09"),
        record("m2", "Kiran", "9000000333", date="2026-07-02"),
        record("m3", "Kiran", "+91 90000 00333", date="2026-09-26", stage="in_progress"),
    ])
    body = {"name": "Kiran Fakename", "phone": "9000000444", "email": "kiran@example.test"}
    assert save("m3", body)["status"] == "ok"
    after = stored(store)
    for cid in ("m1", "m2", "m3"):
        assert (after[cid]["name"], after[cid]["phone"], after[cid]["email"]) == (
            "Kiran Fakename", "9000000444", "kiran@example.test",
        )


def test_a_spelling_variant_on_the_same_phone_is_still_the_same_person(store):
    seed(store, [
        record("v1", "Asha Fakenam", HERS, date="2026-05-09"),
        record("v2", "Asha Fakename", HERS, date="2026-09-26", stage="in_progress"),
    ])
    assert save("v2", {"name": "Asha Fakename", "email": "asha@example.test"})["status"] == "ok"
    after = stored(store)
    assert (after["v1"]["name"], after["v1"]["email"]) == ("Asha Fakename", "asha@example.test")


def test_a_legacy_record_without_a_phone_still_follows_its_person(store):
    seed(store, [
        record("l1", "Kiran Fakename", "", date="2026-05-09"),
        record("l2", "Kiran Fakename", "9000000333", date="2026-09-26", stage="in_progress"),
    ])
    assert save("l2", {"name": "Kiran Fakename", "technology": "Python"})["status"] == "ok"
    assert stored(store)["l1"]["technology"] == "Python"


def test_a_round_wise_booking_on_the_phone_is_never_rewritten(store):
    seed(store, [
        record("rw", "Rama Fakename", HERS, service_type="round_wise", technology="Testing",
               reference="Referrer Three", date="2026-09-17"),
        *reported_case(),
    ])
    assert save("his", dict(CORRECTION))["status"] == "ok"
    after = stored(store)
    assert (after["rw"]["name"], after["rw"]["phone"], after["rw"]["technology"]) == (
        "Rama Fakename", HERS, "Testing",
    )
