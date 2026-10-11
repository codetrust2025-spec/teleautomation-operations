"""Booking a profile candidate's interview slot while money is owed (11 Oct).

    - first slot: may be booked with the balance pending; the amount is shown as
      a reminder;
    - second and later slots: refused while anything is outstanding --
      "₹X payment pending. Clear the balance to book another interview slot.";
    - allowed again as soon as verified payments bring the balance to ₹0.

The rule is one function (`candidate_store.booking_payment_lock`) and every
booking path asks it: the public form and /bookings/confirm, the dashboard's
booking API (which used to book around every payment rule), slot confirmation
and booking from interview emails (which asked for ₹10,000 before any slot, the
first included). Round-wise rounds keep their per-round tariff. Invented names.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.public_slot_api import install_public_slot_routes
from features import candidate_store as cs
from features import pending_slot_payment as pending

NAME = "Asha Fakename"
PENDING = "payment pending. Clear the balance to book another interview slot."


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
    app = FastAPI()
    install_public_slot_routes(app)
    return TestClient(app)


def candidate(*, paid=0, booked=0, cancelled=0):
    """A profile candidate (₹20,000 expected) with `booked` interviews that stand
    and `cancelled` ones that do not."""
    row = cs.create_candidate({
        "name": NAME, "phone": "9000000611", "service_type": "profile_service", "reference": "Owner One",
        "technology": "Java", "ctc_percentage": 10, "expected_payment": 20000, "payment": paid,
    }, allow_slot_without_rules=True)
    for index in range(booked + cancelled):
        cs.assign_interview_slot(
            candidate_id=str(row["id"]), date=f"2026-10-0{index + 1}", time="10:00", time_end="11:00",
            interview_round=f"L{index + 1}",
        )
    if cancelled:
        for stored in cs._load(force=True)["candidates"]:
            if stored.get("date") == f"2026-10-0{booked + cancelled}":
                stored["interview_attendance_status"] = "cancelled"
        cs._save(cs._load())
    return row


def book(client, key="slot-new"):
    return client.post(
        "/bookings/confirm",
        data={"name": NAME, "service_type": "profile_service", "interview_round": "L3", "company": "Contoso",
              "date": "2026-11-20", "time": "16:00", "time_end": "17:00", "idempotency_key": key},
        files={"file": ("invite.jpg", b"invite", "image/jpeg")},
    )


def requirement(client):
    return client.get("/public/slots/payment-requirement", params={"service_type": "profile_service", "name": NAME}).json()


# --- the public form and /bookings/confirm -------------------------------------------

@pytest.mark.parametrize("paid", [0, 10000], ids=["unpaid", "partially paid"])
def test_the_first_slot_books_with_the_balance_pending_and_shows_it(client, paid):
    candidate(paid=paid)
    owed = 20000 - paid
    info = requirement(client)
    assert (info["first_slot"], info["payment_required"], info["amount_due"]) == (True, False, owed)
    assert info["message"] == f"₹{owed:,} payment pending."
    response = book(client)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("paid", [0, 10000], ids=["unpaid", "partially paid"])
def test_a_second_slot_is_refused_while_anything_is_owed(client, paid):
    candidate(paid=paid, booked=1)
    owed = 20000 - paid
    info = requirement(client)
    assert (info["first_slot"], info["booking_blocked"], info["payment_required"]) == (False, True, True)
    response = book(client)
    assert response.status_code == 400
    body = response.json()
    assert body["message"] == f"₹{owed:,} {PENDING}"
    assert body["payment_due"] is True and body["balance_due"] == owed
    assert sum(1 for row in cs._load(force=True)["candidates"] if cs.slot_still_stands(row)) == 1


def test_a_fully_paid_candidate_books_any_slot(client):
    candidate(paid=20000, booked=2)
    info = requirement(client)
    assert (info["booking_blocked"], info["amount_due"], info["message"]) == (False, 0, "")
    assert book(client).status_code == 200


def test_a_cancelled_interview_does_not_make_the_next_one_a_second_slot(client):
    candidate(paid=0, cancelled=1)
    assert requirement(client)["first_slot"] is True
    assert book(client).status_code == 200


def test_the_booking_form_list_says_who_is_locked(client):
    candidate(paid=5000, booked=1)
    [listed] = [r for r in cs.interview_slot_picker_rows() if r["name"] == NAME]
    assert listed["payment_blocked"] is True and listed["needs_payment_proof"] is True
    assert listed["payment_message"] == f"₹15,000 {PENDING}"


# --- the dashboard's booking API: no way around the rule ------------------------------

def dashboard_book(monkeypatch, cid):
    from api.routers import candidates as route
    from core import dashboard_access

    monkeypatch.setattr(dashboard_access, "assert_candidate_row_access", lambda request, row: None)
    return asyncio.run(route.candidates_interviews_slots_create(None, {
        "candidate_id": cid, "date": "2026-11-21", "time": "12:00", "time_end": "13:00", "interview_round": "L2",
    }))


def test_the_dashboard_api_refuses_a_second_slot_while_owed(client, monkeypatch):
    row = candidate(paid=10000, booked=1)
    result = dashboard_book(monkeypatch, str(row["id"]))
    assert result["status"] == "error"
    assert result["message"] == f"₹10,000 {PENDING}"
    assert result["payment_blocked"] is True


def test_the_dashboard_api_books_a_first_slot_with_a_reminder(client, monkeypatch):
    row = candidate(paid=0)
    result = dashboard_book(monkeypatch, str(row["id"]))
    assert result["status"] == "ok"
    assert result["payment_reminder"] == "₹20,000 payment pending."


def test_the_dashboard_api_books_again_once_paid(client, monkeypatch):
    row = candidate(paid=20000, booked=1)
    assert dashboard_book(monkeypatch, str(row["id"]))["status"] == "ok"


# --- slot confirmation and booking from interview emails ------------------------------

def test_slot_confirmation_follows_the_same_rule(client):
    row = candidate(paid=0)
    preview = dict(cs._with_computed(row), slots_group_posted=True, date="2026-11-22")
    assert cs.slot_confirm_block_reason(preview) is None          # first slot: no Rs 10,000 needed any more
    cs.assign_interview_slot(candidate_id=str(row["id"]), date="2026-10-01", time="10:00", time_end="11:00")
    later = dict(cs._with_computed(cs._load(force=True)["candidates"][0]), slots_group_posted=True)
    assert cs.slot_confirm_block_reason(later) == f"₹20,000 {PENDING}"


def test_a_round_wise_round_keeps_its_tariff_rule(client):
    row = {"name": "Ravi Kolla", "service_type": "round_wise", "slots_group_posted": True, "reference": "Owner One",
           "payment": 0, "expected_payment": 5000, "date": "2026-11-22"}
    assert cs.slot_confirm_block_reason(row).startswith("Record at least ₹5,000")
