"""A fee settled in instalments: several screenshots, one booking.

Round-wise booking costs ₹5,000. Paying it as ₹2,000 + ₹1,000 + ₹2,000 is
ordinary, so the upload boundary has to accept every screenshot and the booking
boundary has to add them up. What must not change is that each receipt is still
verified on its own, and that instalments which do not reach the fee cannot
book a slot.
"""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.public_slot_api import install_public_slot_routes
from features import candidate_store as cs
from features import pending_slot_payment as pending

PHONE = "9876543210"
FEE = 5000


def _verified_payment(amount: int, utr: str) -> dict:
    return {
        "booking_eligible": True,
        "company_payment_verified": True,
        "verification_state": "VERIFIED_COMPANY_PAYMENT",
        "is_payment_screenshot": True,
        "status": "success",
        "amount": amount,
        "amount_sufficient": True,
        "confidence_score": 99,
        "utr_number": utr,
        "transaction_id": f"T{utr}",
        "receiver_type": "company",
        "receiver_upi_id": "company@ybl",
        "deterministic_reasons": [],
    }


def _rejected_payment(reason: str) -> dict:
    return {
        "booking_eligible": False,
        "verification_state": "UNKNOWN_RECEIVER",
        "is_payment_screenshot": True,
        "status": "success",
        "amount": 2000,
        "confidence_score": 99,
        "utr_number": "700000000009",
        "receiver_type": "unknown",
        "deterministic_reasons": [reason],
    }


def _client(monkeypatch, tmp_path, *, verifications: dict[bytes, dict]) -> TestClient:
    candidate_file = tmp_path / "candidates.json"
    candidate_file.write_text(json.dumps({"candidates": []}), encoding="utf-8")
    monkeypatch.setattr(cs, "_FILE", str(candidate_file))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr(pending, "PENDING_PAYMENT_DIR", str(tmp_path / "pending"))
    monkeypatch.setattr(pending, "PENDING_PAYMENT_INDEX", str(tmp_path / "pending" / "index.json"))
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)
    # Each screenshot reads back as its own transfer, which is what makes the
    # instalments distinguishable at all.
    monkeypatch.setattr(
        "features.payment_verification_engine.verify_payment_screenshot",
        lambda image, *_args, **_kwargs: dict(verifications[image]),
    )
    monkeypatch.setattr(
        "features.ollama_payment_extract.generate_payment_narrative",
        lambda *_args, **_kwargs: "Verified payment",
    )
    monkeypatch.setattr(
        "features.payment_proof_validator.validate_interview_invite",
        lambda *_args, **_kwargs: (True, ""),
    )
    app = FastAPI()
    install_public_slot_routes(app)
    return TestClient(app)


def _instalments(*amounts: int) -> dict[bytes, dict]:
    return {
        f"screenshot-{index}".encode(): _verified_payment(amount, f"70000000000{index}")
        for index, amount in enumerate(amounts)
    }


def _upload(client, images, *, existing=(), field="files"):
    return client.post(
        "/public/slots/payment-proof",
        data={
            "name": "Aniket",
            "service_type": "round_wise",
            "phone": PHONE,
            "technology": "Testing",
            "interview_round": "L2",
            "existing_proof_ids": ",".join(existing),
        },
        files=[(field, (f"pay-{index}.jpg", image, "image/jpeg")) for index, image in enumerate(images)],
    )


def _confirm(client, proof_ids):
    return client.post(
        "/bookings/confirm",
        data={
            "name": "Aniket", "service_type": "round_wise", "phone": PHONE,
            "technology": "Testing", "interview_round": "L2", "date": "2026-09-02",
            "time": "03:00 PM", "time_end": "04:00 PM",
            "payment_proof_ids": ",".join(proof_ids),
            "idempotency_key": "aniket-split-2026-09-02",
        },
        files={"file": ("invite.jpg", b"interview-invite", "image/jpeg")},
    )


def test_three_instalments_add_up_to_the_fee_and_book_the_slot(monkeypatch, tmp_path):
    verifications = _instalments(2000, 1000, 2000)
    client = _client(monkeypatch, tmp_path, verifications=verifications)

    upload = _upload(client, list(verifications))
    assert upload.status_code == 200
    body = upload.json()
    assert len(body["proof_ids"]) == 3
    assert body["verified_total"] == FEE
    assert body["remaining_due"] == 0
    assert body["payment_complete"] is True
    # Every screenshot keeps its own AI reading, so the form can show all three.
    assert [ai["amount"] for ai in body["ai_extractions"]] == [2000, 1000, 2000]

    confirmed = _confirm(client, body["proof_ids"])
    assert confirmed.status_code == 200
    row = confirmed.json()["candidate"]
    assert row["payment"] == FEE
    assert len(cs.list_proofs(row["id"])) == 3


def test_instalments_short_of_the_fee_are_saved_but_cannot_book(monkeypatch, tmp_path):
    verifications = _instalments(2000, 1000)
    client = _client(monkeypatch, tmp_path, verifications=verifications)

    upload = _upload(client, list(verifications))
    assert upload.status_code == 200
    body = upload.json()
    # Each ₹2,000 transfer is a real, fully verified payment — it is refused as
    # a *booking*, not as a receipt.
    assert body["verified_total"] == 3000
    assert body["remaining_due"] == 2000
    assert body["payment_complete"] is False

    confirmed = _confirm(client, body["proof_ids"])
    assert confirmed.status_code == 400
    refusal = confirmed.json()
    assert refusal["payment_due"] is True
    assert refusal["balance_due"] == 2000
    assert not cs.list_candidates(stage="all", month="all")


def test_the_same_screenshot_twice_is_one_payment(monkeypatch, tmp_path):
    verifications = _instalments(2000)
    image = next(iter(verifications))
    client = _client(monkeypatch, tmp_path, verifications=verifications)

    upload = _upload(client, [image, image])
    assert upload.status_code == 200
    body = upload.json()
    assert body["verified_total"] == 2000
    assert len(body["proof_ids"]) == 1
    assert "same transaction" in body["rejected"][0]["message"]


def test_instalments_uploaded_one_at_a_time_accumulate(monkeypatch, tmp_path):
    verifications = _instalments(2000, 1000, 2000)
    images = list(verifications)
    client = _client(monkeypatch, tmp_path, verifications=verifications)

    proof_ids: list[str] = []
    totals = []
    for image in images:
        body = _upload(client, [image], existing=proof_ids).json()
        proof_ids = body["proof_ids"]
        totals.append(body["verified_total"])
    assert totals == [2000, 3000, 5000]
    assert _confirm(client, proof_ids).status_code == 200


def test_one_unverifiable_screenshot_does_not_discard_the_others(monkeypatch, tmp_path):
    verifications = _instalments(2000, 3000)
    verifications[b"screenshot-bad"] = _rejected_payment("Receiver is not registered.")
    client = _client(monkeypatch, tmp_path, verifications=verifications)

    body = _upload(client, [b"screenshot-0", b"screenshot-bad", b"screenshot-1"]).json()
    assert body["verified_total"] == FEE
    assert len(body["proof_ids"]) == 2
    assert body["rejected"] == [
        {"filename": "pay-1.jpg", "message": "Receiver is not registered."}
    ]


def test_a_single_screenshot_covering_the_fee_still_books_unchanged(monkeypatch, tmp_path):
    verifications = _instalments(FEE)
    client = _client(monkeypatch, tmp_path, verifications=verifications)

    # The pre-split shape: one `file` part, one `payment_proof_id` on confirm.
    upload = _upload(client, list(verifications), field="file")
    assert upload.status_code == 200
    body = upload.json()
    assert body["proof_id"]
    assert body["payment_complete"] is True

    confirmed = client.post(
        "/bookings/confirm",
        data={
            "name": "Aniket", "service_type": "round_wise", "phone": PHONE,
            "technology": "Testing", "interview_round": "L2", "date": "2026-09-02",
            "time": "03:00 PM", "time_end": "04:00 PM",
            "payment_proof_id": body["proof_id"],
            "idempotency_key": "aniket-single-2026-09-02",
        },
        files={"file": ("invite.jpg", b"interview-invite", "image/jpeg")},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["candidate"]["payment"] == FEE
