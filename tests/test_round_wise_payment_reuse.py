import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.public_slot_api import install_public_slot_routes
from features import candidate_store as cs
from features import pending_slot_payment as pending
from features.payment_fraud_detection import (
    PAYMENT_REUSE_ALLOWED_MESSAGE,
    PAYMENT_REUSE_BLOCKED_MESSAGE,
    assess_payment_proof,
)

UTR = "686823328238"
PHONE = "9876543210"


def _verified_payment(*, utr: str = UTR) -> dict:
    return {
        "booking_eligible": True,
        "company_payment_verified": True,
        "verification_state": "VERIFIED_COMPANY_PAYMENT",
        "is_payment_screenshot": True,
        "status": "success",
        "amount": 5000,
        "amount_sufficient": True,
        "confidence_score": 99,
        "utr_number": utr,
        "transaction_id": "T2607292205248431704930" if utr else "",
        "receiver_type": "company",
        "receiver_upi_id": "company@ybl",
        "deterministic_reasons": [],
    }


def _previous_booking(*, status: str, phone: str = PHONE, stage: str = "in_progress") -> dict:
    return {
        "id": "previous-booking", "name": "Aniket", "phone": phone,
        "technology": "Testing", "interview_round": "L1", "reference": "Thrilok",
        "service_type": "round_wise", "interview_scope": "external", "stage": stage,
        "task": "in_progress", "expected_payment": 5000, "payment": 5000,
        "slot_confirmed": True, "slots_group_posted": True, "date": "2026-07-29",
        "time": "03:00 PM", "time_end": "04:00 PM",
        "interview_attendance_status": status,
        "payment_proofs": [{
            "id": "payment-proof-1", "filename": "old-payment.jpg",
            "attachment_type": "payment_proof", "utr_number": UTR,
            "transaction_id": "T2607292205248431704930",
            "company_payment_verified": True, "booking_eligible": True,
        }],
        "slot_screenshot_proofs": [],
        "created_at": "2026-07-29T10:00:00+00:00",
        "updated_at": "2026-07-29T10:00:00+00:00",
    }


def _client(monkeypatch, tmp_path, previous: dict, *, verification: dict | None = None) -> TestClient:
    candidate_file = tmp_path / "candidates.json"
    candidate_file.write_text(json.dumps({"candidates": [previous]}), encoding="utf-8")
    monkeypatch.setattr(cs, "_FILE", str(candidate_file))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr(pending, "PENDING_PAYMENT_DIR", str(tmp_path / "pending"))
    monkeypatch.setattr(pending, "PENDING_PAYMENT_INDEX", str(tmp_path / "pending" / "index.json"))
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)
    monkeypatch.setattr("features.payment_verification_engine.verify_payment_screenshot", lambda *_args, **_kwargs: verification or _verified_payment())
    monkeypatch.setattr("features.ollama_payment_extract.generate_payment_narrative", lambda *_args, **_kwargs: "Verified payment")
    monkeypatch.setattr("features.payment_proof_validator.validate_interview_invite", lambda *_args, **_kwargs: (True, ""))
    app = FastAPI()
    install_public_slot_routes(app)
    return TestClient(app)


def _upload(client: TestClient, *, phone: str = PHONE, candidate_id: str = ""):
    return client.post(
        "/public/slots/payment-proof",
        data={"name": "Aniket", "service_type": "round_wise", "phone": phone, "candidate_id": candidate_id, "technology": "Testing", "interview_round": "L2"},
        files={"file": ("renamed.jpg", b"same-payment-new-file", "image/jpeg")},
    )


def _confirm(client: TestClient, proof_id: str, *, phone: str = PHONE, candidate_id: str = ""):
    return client.post(
        "/bookings/confirm",
        data={"name": "Aniket", "service_type": "round_wise", "phone": phone, "candidate_id": candidate_id, "technology": "Testing", "interview_round": "L2", "date": "2026-08-02", "time": "03:00 PM", "time_end": "04:00 PM", "payment_proof_id": proof_id, "idempotency_key": "aniket-rebook-2026-08-02"},
        files={"file": ("invite.jpg", b"interview-invite", "image/jpeg")},
    )


@pytest.mark.parametrize("status", ["cancelled", "not_attended"])
def test_cancelled_or_not_attended_payment_can_rebook_once(monkeypatch, tmp_path, status):
    client = _client(monkeypatch, tmp_path, _previous_booking(status=status))
    upload = _upload(client)
    assert upload.status_code == 200
    assert upload.json()["message"] == PAYMENT_REUSE_ALLOWED_MESSAGE
    assert len(cs.list_candidates(stage="all", month="all")) == 1
    first = _confirm(client, upload.json()["proof_id"])
    second = _confirm(client, upload.json()["proof_id"])
    assert first.status_code == second.status_code == 200
    rows = cs.list_candidates(stage="all", month="all")
    assert len(rows) == 2
    new_booking = next(row for row in rows if row["id"] != "previous-booking")
    previous = next(row for row in rows if row["id"] == "previous-booking")
    assert new_booking["previousBookingId"] == "previous-booking"
    assert new_booking["reusedPaymentId"] == "payment-proof-1"
    assert previous["paymentReusedByBookingId"] == new_booking["id"]
    assert first.json()["candidate"]["id"] == second.json()["candidate"]["id"]


@pytest.mark.parametrize(("status", "stage"), [("", "in_progress"), ("attended", "completed")])
def test_active_or_completed_booking_blocks_payment_reuse(monkeypatch, tmp_path, status, stage):
    client = _client(monkeypatch, tmp_path, _previous_booking(status=status, stage=stage))
    response = _upload(client)
    assert response.status_code == 400
    assert response.json()["message"] == PAYMENT_REUSE_BLOCKED_MESSAGE


def test_different_candidate_and_missing_phone_block_reuse(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, _previous_booking(status="cancelled", phone="9123456780"))
    assert _upload(client, phone=PHONE).json()["message"] == PAYMENT_REUSE_BLOCKED_MESSAGE
    assert _upload(client, phone="").json()["message"] == PAYMENT_REUSE_BLOCKED_MESSAGE
    assert len(cs.list_candidates(stage="all", month="all")) == 1


def test_candidate_id_can_match_when_phone_changed(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, _previous_booking(status="cancelled", phone="9123456780"))
    upload = _upload(client, phone="9000000001", candidate_id="previous-booking")
    assert upload.status_code == 200
    response = _confirm(client, upload.json()["proof_id"], phone="9000000001", candidate_id="previous-booking")
    assert response.status_code == 200


def test_already_rebooked_payment_is_blocked(monkeypatch, tmp_path):
    previous = _previous_booking(status="cancelled")
    previous["paymentReusedByBookingId"] = "existing-rebooking"
    response = _upload(_client(monkeypatch, tmp_path, previous))
    assert response.status_code == 400
    assert response.json()["message"] == PAYMENT_REUSE_BLOCKED_MESSAGE


def test_invalid_transaction_reference_is_rejected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, _previous_booking(status="cancelled"), verification=_verified_payment(utr=""))
    response = _upload(client)
    assert response.status_code == 400
    # The refusal names the screen that carries the reference, because a
    # payment summary does not have one to read more carefully.
    message = response.json()["message"]
    assert "UTR" in message and "View Details" in message


def test_fraud_detector_accepts_phone_argument_and_normal_payment():
    result = assess_payment_proof(
        b"new-payment",
        _verified_payment(utr="999999999999"),
        candidate_id="",
        candidate_name="New Candidate",
        candidate_phone="9876500000",
    )
    assert result["decision"] == "verified"
    assert result["reuse_allowed"] is False
