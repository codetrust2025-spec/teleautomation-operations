"""A verified company payment is a ledger credit, whichever path verified it (12 Oct).

Four verified company payments were missing from the ledger:

* the booking form reads a receipt before the booking exists and deliberately
  posts nothing then -- and nothing posted it once the booking was confirmed;
* an administrator's receiver confirmation turned a proof into a verified
  company payment on the candidate's row and left the ledger holding it as
  unconfirmed, with no credit.

And the same invite submitted twice from a fresh page booked the same
interview twice: the form only knew a repeat by its own form token.

Only the receipt reading is stubbed; verification, the ledger writer, the
booking form and the candidate store all run for real. Names are invented.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.public_slot_api import install_public_slot_routes
from features import candidate_store as cs
from features import payment_verification_engine as engine
from features import pending_slot_payment as pending

PAYEE = "Example Payee"
PAYEE_UPI = "examplepayee@ybl"
PHONE = "9000000456"


def _receipt(**patch):
    row = {
        "amount": 5000, "receiver_name": PAYEE, "receiver_upi_id": PAYEE_UPI,
        "receiver_phone": "", "receiver_account": "", "utr_number": "612300004444",
        "transaction_id": "T26100112000011112222", "reference_number": "",
        "payment_date": "2026-10-01", "payment_time": "11:00 AM", "status": "success",
        "confidence_score": 98, "is_payment_screenshot": True, "primary_model": "test-model",
    }
    row.update(patch)
    return row


RECEIPTS = {
    b"paid-once": _receipt(),
    b"paid-again": _receipt(utr_number="612300005555", transaction_id="T26100112000033334444"),
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    registry = tmp_path / "payment_receiver_accounts.json"
    registry.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(registry))
    monkeypatch.setenv("REFERRER_REGISTRY_FILE", str(tmp_path / "referrers.json"))
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("COMPANY_PAYMENT_UPI_IDS", PAYEE_UPI)
    monkeypatch.setenv("COMPANY_PAYMENT_RECEIVER_NAMES", PAYEE)
    candidate_file = tmp_path / "candidates.json"
    candidate_file.write_text(json.dumps({"candidates": []}), encoding="utf-8")
    monkeypatch.setattr(cs, "_FILE", str(candidate_file))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr(cs, "INTERVIEW_DATE_MAX_DAYS_AHEAD", 10 ** 6)
    monkeypatch.setattr(pending, "PENDING_PAYMENT_DIR", str(tmp_path / "pending"))
    monkeypatch.setattr(pending, "PENDING_PAYMENT_INDEX", str(tmp_path / "pending" / "index.json"))
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)
    monkeypatch.setattr(
        "features.ollama_payment_extract.extract_payment_with_ollama",
        lambda image, _mime, **_kw: dict(RECEIPTS[image]),
    )
    monkeypatch.setattr("features.ollama_payment_extract.generate_payment_narrative", lambda *_a, **_kw: "Verified payment")
    monkeypatch.setattr("features.payment_proof_validator.validate_interview_invite", lambda *_a, **_kw: (True, ""))
    app = FastAPI()
    install_public_slot_routes(app)
    return TestClient(app)


def _upload(client, image):
    response = client.post(
        "/public/slots/payment-proof",
        data={"name": "Asha Fakename", "service_type": "round_wise", "phone": PHONE,
              "technology": "Java", "interview_round": "L1"},
        files=[("files", ("pay.jpg", image, "image/jpeg"))],
    )
    assert response.status_code == 200, response.json()
    return response.json()["proof_ids"]


def _confirm(client, proof_ids, key):
    return client.post(
        "/bookings/confirm",
        data={"name": "Asha Fakename", "service_type": "round_wise", "phone": PHONE,
              "technology": "Java", "interview_round": "L1", "company": "Example Client",
              "date": "2099-12-20", "time": "15:00", "time_end": "16:00",
              "payment_proof_ids": ",".join(proof_ids), "idempotency_key": key},
        files={"file": ("invite.jpg", b"interview-invite", "image/jpeg")},
    )


def _entries():
    return engine.ledger_entries()


def _standing():
    return [r for r in cs._load(force=True)["candidates"] if cs.slot_still_stands(r) and str(r.get("date"))[:10] == "2099-12-20"]


def test_a_form_booking_posts_its_verified_payment_to_the_ledger(client):
    confirm = _confirm(client, _upload(client, b"paid-once"), "first-try")
    assert confirm.status_code == 200, confirm.json()

    [entry] = _entries()
    assert entry["transaction_type"] == "CANDIDATE_FEE_RECEIVED_BY_COMPANY"
    assert entry["gross_amount_minor"] == 500000
    assert entry["external_transaction_id"].endswith("4444") or "612300004444" in json.dumps(entry)
    [row] = _standing()
    [proof] = cs.list_attachments(row["id"], "payment_proof")
    assert proof["ledger_entry_id"] == entry["ledger_entry_id"]
    assert proof["ledger_status"] == "posted"


def test_the_same_invite_sent_again_from_a_fresh_page_is_the_same_booking(client):
    first = _confirm(client, _upload(client, b"paid-once"), "first-page")
    assert first.status_code == 200, first.json()
    second_proofs = _upload(client, b"paid-again")

    again = _confirm(client, second_proofs, "second-page")

    body = again.json()
    assert again.status_code == 200, body
    assert body["already_booked"] is True
    assert "Nothing new was created" in body["message"]
    assert len(_standing()) == 1
    # The receipt sent with the repeat was neither spent nor posted.
    assert len(_entries()) == 1
    assert not pending.utilized_by(pending._load()["proofs"][second_proofs[0]])


def test_only_a_verified_proof_is_posted(client):
    assert engine.credit_confirmed_payment(
        {"verification_state": "INCOMPLETE_PAYMENT_EVIDENCE", "verified_amount": 5000, "utr_number": "1"},
        source_module="test", candidate_id="c1",
    ) == {}
    assert engine.credit_confirmed_payment(
        {"verification_state": "VERIFIED_COMPANY_PAYMENT", "verified_amount": 0, "utr_number": "1"},
        source_module="test", candidate_id="c1",
    ) == {}
    assert _entries() == []


def test_a_confirmed_receiver_is_posted_once_and_linked(client):
    # A receipt read with only the payee's name visible: verified as far as it
    # goes, refused a credit, recorded in the ledger as unconfirmed.
    RECEIPTS[b"name-only"] = _receipt(receiver_upi_id="", utr_number="612300006666", transaction_id="")
    try:
        result = engine.verify_payment_screenshot(
            b"name-only", "image/jpeg", source_module="candidate_payment_proof",
            entity_id="cand-1", entity_name="Asha Fakename", candidate_id="cand-1",
            purpose="candidate_payment", payment_scope="ROUND",
        )
    finally:
        RECEIPTS.pop(b"name-only", None)
    assert result["verification_state"] == "INCOMPLETE_PAYMENT_EVIDENCE"
    assert _entries() == []
    proof = {
        "id": "proof-1", "attachment_type": "payment_proof", "verification_state": result["verification_state"],
        "verified_amount": 5000, "utr_number": "612300006666", "sha256": "ab" * 32, "payment_id": result["payment_id"],
        "evidence_id": result["evidence_id"], "receiver_name": PAYEE, "payment_scope": "ROUND",
    }
    store = json.loads(open(cs._FILE, encoding="utf-8").read())
    store["candidates"].append({"id": "cand-1", "name": "Asha Fakename", "phone": PHONE, "service_type": "round_wise",
                                "stage": "in_progress", "payment_proofs": [proof]})
    open(cs._FILE, "w", encoding="utf-8").write(json.dumps(store))
    cs._load_cache = None

    confirmed = cs.confirm_proof_receiver("cand-1", "proof-1", receiver_type="company", receiver_name=PAYEE,
                                          reason="Owner confirmed the payee account", reviewer="test reviewer")

    [entry] = _entries()
    assert entry["payment_id"] == result["payment_id"]
    assert confirmed["verification_state"] == "VERIFIED_COMPANY_PAYMENT"
    assert confirmed["ledger_entry_id"] == entry["ledger_entry_id"]
    # The ledger's own payment row is the one credited, not a second row.
    payments = engine._load_ledger()["payments"]
    assert len(payments) == 1 and payments[0]["verification_state"] == "VERIFIED_COMPANY_PAYMENT"
    # Posting it again lands on the same credit.
    again = engine.credit_confirmed_payment(confirmed, source_module="test", candidate_id="cand-1")
    assert again["ledger_entry_id"] == entry["ledger_entry_id"]
    assert len(_entries()) == 1
