"""Two people never share a profile through a phone number.

11 Oct: a Save gave one candidate the phone that 13 records of another person
carry. The Candidates table groups profile records by phone, so it showed one
row under the first name holding both people's payments (2 proofs, Rs 40,000
against 20,000 expected), the other person vanished from the list, and the edit
form (which uses the same grouping) showed the other person's proof too.

    - records of different people on one phone stay separate profiles; one
      person's records under spelling variants or aliases still group;
    - a Save or a new candidate may not give a profile candidate a phone another
      person's profile records carry (round-wise bookings are not grouped by
      phone and are not affected); a phone already shared can still be saved
      unchanged, so the record can be corrected;
    - the edit form is told, field by field, when a phone or email is another
      person's.

Names and numbers are invented (the 90000xxxxx block).
"""
from __future__ import annotations

import asyncio

import pytest

from api.routers import candidates as route
from core import dashboard_access
from features import candidate_store as cs

SHARED = "9000000998"


def proof(pid, amount=20000):
    return {"id": pid, "attachment_type": "payment_proof", "verification_state": "VERIFIED_COMPANY_PAYMENT",
            "verified_amount": amount, "url": f"/x/{pid}", "original_name": f"{pid}.png"}


def row(cid, name, phone=SHARED, **over):
    base = {"id": cid, "name": name, "phone": phone, "service_type": "profile_service", "stage": "completed",
            "date": "2026-05-20", "expected_payment": 20000, "payment": 0, "payment_proofs": [],
            "updated_at": "2026-05-20T10:00:00+00:00", "technology": "Java", "reference": "Test Owner"}
    base.update(over)
    return base


@pytest.fixture
def store(monkeypatch):
    data = {"candidates": []}
    monkeypatch.setattr(cs, "_load", lambda *a, **k: data)
    monkeypatch.setattr(cs, "_save", lambda payload: data.update(payload))
    return data


def table(store):
    return cs._collapse_profile_candidates([cs._with_computed(r) for r in store["candidates"]])


def reported_case(store):
    store["candidates"] += [
        row(f"fa{i}", "Asha Fakename", date=f"2026-05-{10 + i:02d}") for i in range(12)
    ] + [
        row("fa-paid", "Asha Fakename", payment=20000, payment_proofs=[proof("p-asha")]),
        row("rk", "Ravi Kolla", stage="in_progress", date="2026-09-25", payment=20000,
            payment_proofs=[proof("p-ravi")], updated_at="2026-10-11T07:05:00+00:00"),
    ]


# --- the table and the edit form keep two people apart -------------------------------

def test_two_people_on_one_phone_are_two_rows_each_with_only_their_own_proof(store):
    reported_case(store)
    rows = {r["name"]: r for r in table(store)}
    assert set(rows) == {"Asha Fakename", "Ravi Kolla"}
    assert rows["Ravi Kolla"]["proof_count"] == 1
    assert [p["id"] for p in rows["Ravi Kolla"]["payment_proofs"]] == ["p-ravi"]
    assert rows["Ravi Kolla"]["slot_count"] == 1
    assert rows["Asha Fakename"]["slot_count"] == 13
    assert [p["id"] for p in rows["Asha Fakename"]["payment_proofs"]] == ["p-asha"]


def test_the_edit_form_shows_only_that_persons_proofs(store, monkeypatch):
    reported_case(store)
    monkeypatch.setattr(cs, "reconcile_resume_metadata", lambda: 0)
    detail = cs.get_candidate_detail("rk")
    assert detail["name"] == "Ravi Kolla"
    assert [p["id"] for p in detail["payment_proofs"]] == ["p-ravi"]
    assert detail["proof_count"] == 1


def test_proof_credit_is_released_only_within_one_persons_records(store):
    reported_case(store)
    phones = cs._phones_by_name_key(store["candidates"])
    keys = {r["id"]: cs._profile_group_key(r, phones) for r in store["candidates"]}
    assert keys["rk"] != keys["fa-paid"]
    assert keys["fa0"] == keys["fa-paid"]


@pytest.mark.parametrize("variant", ["Asha  FAKENAME", "Asha Fakenam", "Fakename Asha"])
def test_one_persons_spellings_on_one_phone_still_group(store, variant):
    store["candidates"] += [row("a1", "Asha Fakename", payment_proofs=[proof("p1")]),
                            row("a2", variant, payment_proofs=[proof("p2")])]
    rows = table(store)
    assert len(rows) == 1
    assert rows[0]["proof_count"] == 2


def test_an_alias_on_one_phone_still_groups(store):
    store["candidates"] += [row("p1", "Perla Abhilash"), row("p2", "Abilash Perla")]
    assert len(table(store)) == 1


# --- a phone is not given to another person ---------------------------------------------

@pytest.fixture
def route_env(store, monkeypatch):
    writes = []
    monkeypatch.setattr(dashboard_access, "assert_candidate_row_access", lambda request, r: None)
    monkeypatch.setattr(dashboard_access, "prepare_candidate_body", lambda request, body: dict(body))
    monkeypatch.setattr(cs, "get_candidate", lambda cid: next((dict(r) for r in store["candidates"] if r["id"] == cid), None))
    monkeypatch.setattr(cs, "validate_profile_ctc_percentage", lambda prepared, existing=None: prepared.get("ctc_percentage"))
    monkeypatch.setattr(cs, "update_candidate", lambda cid, patch: writes.append(patch) or {"id": cid, **patch})
    monkeypatch.setattr(cs, "create_candidate", lambda body: writes.append(body) or {"id": "new", **body})
    return writes


def save(cid, body):
    return asyncio.run(route.candidates_update(cid, None, body))


def test_a_save_may_not_give_a_profile_candidate_another_persons_phone(store, route_env):
    store["candidates"] += [row(f"fa{i}", "Asha Fakename") for i in range(3)]
    store["candidates"].append(row("rk", "Ravi Kolla", phone="9000000777", stage="in_progress"))
    result = save("rk", {"name": "Ravi Kolla", "phone": "+91 90000 00998"})
    assert result["status"] == "error"
    assert result["phone_taken"] is True
    assert result["message"].startswith("+91 90000 00998 is already the phone of Asha Fakename (3 records).")
    assert route_env == []


def test_the_same_person_under_another_spelling_may_take_the_number(store, route_env):
    store["candidates"].append(row("fa0", "Asha Fakename"))
    store["candidates"].append(row("a2", "Asha Fakenam", phone="9000000777", stage="in_progress"))
    assert save("a2", {"name": "Asha Fakenam", "phone": SHARED})["status"] == "ok"


def test_a_round_wise_booking_is_not_affected(store, route_env):
    store["candidates"].append(row("fa0", "Asha Fakename"))
    store["candidates"].append(row("rw", "Ravi Kolla", phone="9000000777", service_type="round_wise", stage="in_progress"))
    assert save("rw", {"name": "Ravi Kolla", "phone": SHARED})["status"] == "ok"


def test_a_record_that_already_shares_a_phone_can_still_be_saved_and_corrected(store, route_env):
    reported_case(store)
    assert save("rk", {"name": "Ravi Kolla", "phone": SHARED, "stage": "in_progress"})["status"] == "ok"
    assert save("rk", {"name": "Ravi Kolla", "phone": "9000000777"})["status"] == "ok"


def test_a_new_candidate_may_not_take_another_persons_phone(store, route_env):
    store["candidates"].append(row("fa0", "Asha Fakename"))
    result = asyncio.run(route.candidates_create(None, {"name": "Ravi Kolla", "phone": SHARED, "service_type": "profile_service"}))
    assert result["phone_taken"] is True
    ok = asyncio.run(route.candidates_create(None, {"name": "Ravi Kolla", "phone": SHARED, "service_type": "round_wise"}))
    assert ok["status"] == "ok"


# --- the edit form is told -----------------------------------------------------------------

def test_the_detail_warns_when_the_phone_or_email_is_another_persons(store, route_env, monkeypatch):
    reported_case(store)
    store["candidates"][0]["email"] = "shared@example.test"
    store["candidates"][-1]["email"] = "shared@example.test"
    monkeypatch.setattr(cs, "reconcile_resume_metadata", lambda: 0)
    payload = asyncio.run(route.candidates_get("rk", None))
    warnings = payload["candidate"]["identity_warnings"]
    assert warnings["phone"] == "Also the phone of Asha Fakename (13 records)"
    assert warnings["email"] == "Also the email of Asha Fakename"


def test_no_warning_for_ones_own_records(store, route_env, monkeypatch):
    store["candidates"] += [row("a1", "Asha Fakename", email="asha@example.test"),
                            row("a2", "Fakename Asha", email="asha@example.test")]
    monkeypatch.setattr(cs, "reconcile_resume_metadata", lambda: 0)
    assert asyncio.run(route.candidates_get("a1", None))["candidate"]["identity_warnings"] == {}
