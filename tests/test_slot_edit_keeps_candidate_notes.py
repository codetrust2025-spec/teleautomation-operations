"""A Daily Ops slot edit leaves the candidate's own notes alone.

The Edit dialog never sends `notes`, and the slot route used to turn the
missing field into "" -- so every date/time/round edit cleared whatever the
team had written in the candidate's notes. Only a request that sends `notes`
may change them now.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.candidates import router as candidates_router
from core.public_slot_api import install_public_slot_routes
from features import candidate_store as cs
from features import pending_slot_payment as pending

NOTES = "Prefers evening calls"


@pytest.fixture(autouse=True)
def _dates_in_2099_stay_bookable(monkeypatch):
    monkeypatch.setattr(cs, "INTERVIEW_DATE_MAX_DAYS_AHEAD", 10 ** 6)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "candidate-proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr(pending, "PENDING_PAYMENT_DIR", str(tmp_path / "pending"))
    monkeypatch.setattr(pending, "PENDING_PAYMENT_INDEX", str(tmp_path / "pending" / "index.json"))
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)
    app = FastAPI()
    install_public_slot_routes(app)
    app.include_router(candidates_router)
    return TestClient(app)


def _booked(client: TestClient) -> str:
    person = cs.create_candidate({
        "name": "Synthetic Notes Candidate", "phone": "9000000456", "service_type": "profile_service",
        "technology": "ServiceNow", "reference": "Owner", "stage": "in_progress",
        "payment": 20000, "expected_payment": 20000, "notes": NOTES,
    })
    booked = client.post("/candidates/interviews/slots", json={
        "candidate_id": person["id"], "date": "2099-09-18", "time": "14:15", "time_end": "14:45",
        "interview_round": "L1",
    }).json()
    assert booked["status"] == "ok", booked
    return booked["candidate"]["id"]


def _stored(cid: str) -> dict:
    return next(row for row in cs._load(force=True)["candidates"] if str(row.get("id")) == cid)


def test_a_slot_edit_without_notes_keeps_the_candidates_notes(client):
    cid = _booked(client)
    assert _stored(cid).get("notes") == NOTES

    res = client.patch(f"/candidates/interviews/slots/{cid}", json={
        "date": "2099-09-19", "time": "15:00", "time_end": "15:30", "interview_round": "L2", "technology": "ServiceNow",
    }).json()

    assert res["status"] == "ok", res
    row = _stored(cid)
    assert (row["date"], row["time"], row["interview_round"]) == ("2099-09-19", "15:00", "L2")
    assert row.get("notes") == NOTES


def test_notes_sent_with_the_edit_still_change(client):
    cid = _booked(client)

    res = client.patch(f"/candidates/interviews/slots/{cid}", json={
        "date": "2099-09-18", "time": "14:15", "time_end": "14:45", "interview_round": "L1",
        "technology": "ServiceNow", "notes": "Moved to the afternoon",
    }).json()

    assert res["status"] == "ok", res
    assert _stored(cid).get("notes") == "Moved to the afternoon"
