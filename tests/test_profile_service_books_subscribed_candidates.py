"""Book Interview Slot: Profile Service books an existing, subscribed candidate.

A Profile Service booking is for someone already on Candidates with profile
service, In progress -- the people the form's picker lists. The form no longer
takes a typed client name for it, and the booking boundary enforces the same
rule, so no client of the endpoint can get round it:

* a name that is not a subscribed Profile Service candidate is refused, and
  nothing is created -- not a duplicate of someone on file, not a new person;
* a candidate who is not subscribed any more (Selected, dropped) or is
  round-wise cannot be booked as Profile Service;
* a subscribed candidate books onto their own record, chosen by name or by the
  id the picker sends; an id that belongs to someone else is refused;
* the picker is read from the live store, so a newly subscribed candidate
  appears without any configuration, and the others never do;
* round-wise booking is unchanged.

Names are invented.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.public_slot_api import install_public_slot_routes
from features import candidate_store as cs
from features import pending_slot_payment as pending


PHONES = iter(f"90000{n:05d}" for n in range(1, 100))


def person(cid, name, **overrides):
    base = {
        "id": cid, "name": name, "phone": next(PHONES),
        "stage": "in_progress", "task": "in_progress", "service_type": "profile_service",
        "reference": "Ravinder", "technology": "React JS", "payment": 20000,
        "ctc_percentage": 8, "logged_date": "2026-09-01", "created_at": "2026-09-01T10:00:00Z",
    }
    base.update(overrides)
    return base


SUBSCRIBED = person("asha", "Asha Fakename")
SELECTED = person("placed", "Ravi Testcase", stage="completed")
DROPPED = person("gone", "Meena Sample", stage="dropped")
ROUND = person("round", "Kiran Mockdata", service_type="round_wise", payment=4000)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "candidate-proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr(pending, "PENDING_PAYMENT_DIR", str(tmp_path / "pending"))
    monkeypatch.setattr(pending, "PENDING_PAYMENT_INDEX", str(tmp_path / "pending" / "index.json"))
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)
    monkeypatch.setattr("features.payment_proof_validator.validate_interview_invite", lambda *_a, **_k: (True, ""))
    with open(cs._FILE, "w", encoding="utf-8") as handle:
        json.dump({"candidates": [dict(r) for r in (SUBSCRIBED, SELECTED, DROPPED, ROUND)], "updated_at": None}, handle)
    cs._load_cache = None
    app = FastAPI()
    install_public_slot_routes(app)
    return TestClient(app)


def stored_rows():
    with open(cs._FILE, encoding="utf-8") as handle:
        return json.load(handle)["candidates"]


def book(client, name, **extra):
    data = {
        "name": name, "service_type": "profile_service", "interview_round": "L1",
        "company": "Capgemini", "date": "2026-11-12", "time": "16:00", "time_end": "17:00",
        "idempotency_key": f"{name}-{extra.get('candidate_id', '')}",
    }
    data.update(extra)
    return client.post("/bookings/confirm", data=data, files={"file": ("invite.jpg", b"invite", "image/jpeg")})


def picker(client):
    payload = client.get("/public/slots/candidates").json()
    assert payload["status"] == "ok"
    return payload["candidates"]


# ── What the picker offers ──────────────────────────────────────────────────

def test_the_picker_lists_only_subscribed_profile_candidates(client):
    assert [c["name"] for c in picker(client)] == ["Asha Fakename"]


def test_a_newly_subscribed_candidate_appears_without_configuration(client):
    cs.create_candidate({
        "name": "Divya Example", "phone": "9000000099", "technology": "Python", "reference": "Ravinder",
        "stage": "in_progress", "service_type": "profile_service", "ctc_percentage": 8,
    })
    assert [c["name"] for c in picker(client)] == ["Asha Fakename", "Divya Example"]


# ── What the booking accepts ────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["Someone New", "asha fake", "Ravi Testcase", "Meena Sample", "Kiran Mockdata"])
def test_anyone_but_a_subscribed_profile_candidate_is_refused_and_nothing_is_created(client, name):
    before = stored_rows()
    response = book(client, name)
    assert response.status_code == 400
    body = response.json()
    assert body["not_subscribed"] is True
    assert "Profile Service list" in body["message"]
    assert stored_rows() == before


def test_a_subscribed_candidate_books_onto_their_own_record(client):
    response = book(client, "Asha Fakename", candidate_id="asha")
    assert response.status_code == 200, response.json()
    rows = stored_rows()
    asha = [r for r in rows if r["name"] == "Asha Fakename"]
    # Their own record took the slot; no second Asha was made.
    assert [r["id"] for r in asha] == ["asha"]
    assert asha[0]["date"] == "2026-11-12"
    assert asha[0]["slot_confirmed"] is True
    assert len(rows) == 4


def test_chosen_by_name_alone_still_books_the_same_record(client):
    # The form sends the id; any other caller of the endpoint is held to the
    # same list by name.
    response = book(client, "Asha Fakename")
    assert response.status_code == 200, response.json()
    assert [r["id"] for r in stored_rows() if r["name"] == "Asha Fakename"] == ["asha"]


def test_an_id_that_belongs_to_someone_else_is_refused(client):
    response = book(client, "Asha Fakename", candidate_id="placed")
    assert response.status_code == 400
    assert response.json()["not_subscribed"] is True


def test_round_wise_booking_is_unchanged(client):
    # A round-wise client types their own name and phone, and pays per round:
    # still refused for want of a payment, not for want of a subscription.
    response = client.post(
        "/bookings/confirm",
        data={"name": "Someone New", "service_type": "round_wise", "phone": "9000000098",
              "technology": "ETL", "interview_round": "L1", "company": "Capgemini",
              "date": "2026-11-12", "time": "16:00", "time_end": "17:00"},
        files={"file": ("invite.jpg", b"invite", "image/jpeg")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body.get("payment_due") is True
    assert "not_subscribed" not in body
