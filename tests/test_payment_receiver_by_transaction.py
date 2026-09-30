"""One payment, seen from both sides, is one payment with one known receiver.

A payer-side receipt shows "paid to J Ravinder" with a registered UPI handle.
The receiver-side receipt of the very same transaction says "received from
<payer>" and names the payee only by a masked bank account, which no registry can
match. It read as UNKNOWN_RECEIVER and counted for nothing, while the candidate
whose payment it was showed "Verified proofs ₹0, ₹20,000 unevidenced".

Every test drives verify_payment_screenshot, the entry point production calls.
"""

import json

import pytest

from features import payment_verification_engine as engine

UTR = "900000000001"
TXN = "T2600000000000000000001"


@pytest.fixture(autouse=True)
def _isolated_ledger(monkeypatch, tmp_path):
    registry_path = tmp_path / "payment_receiver_accounts.json"
    registry_path.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(registry_path))
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("COMPANY_PAYMENT_RECEIVER_NAMES", "Test Company Payee")
    monkeypatch.setenv("COMPANY_PAYMENT_UPI_IDS", "testpayee@ybl")


def _payer_side(**patch):
    row = {
        "amount": 20000, "direction": "PAID_TO",
        "receiver_name": "Test Company Payee", "receiver_upi_id": "testpayee@ybl",
        "utr_number": UTR, "transaction_id": TXN,
        "payment_date": "2026-09-13", "payment_time": "06:12 PM",
        "status": "success", "confidence_score": 100,
        "is_payment_screenshot": True, "primary_model": "qwen3-vl:8b-instruct",
    }
    row.update(patch)
    return row


def _receiver_side(**patch):
    row = {
        "amount": 20000, "direction": "RECEIVED_FROM",
        "sender_name": "TEST PAYER", "receiver_name": "",
        "receiver_upi_id": "", "receiver_account": "XXXXXXXXXX00221",
        "receiver_account_identifier": "XXXXXXXXXX00221",
        "credited_to_identifier": "XXXXXXXXXX00221",
        "utr_number": UTR, "transaction_id": TXN,
        "payment_date": "2026-09-13", "payment_time": "06:12 PM",
        "status": "success", "confidence_score": 83,
        "is_payment_screenshot": True, "primary_model": "qwen3-vl:8b-instruct",
    }
    row.update(patch)
    return row


def _install(monkeypatch, value):
    monkeypatch.setattr(
        "features.ollama_payment_extract.extract_payment_with_ollama",
        lambda _raw, _mime, **kwargs: dict(value),
    )


def _verify(monkeypatch, extraction, *, entity, image):
    _install(monkeypatch, extraction)
    return engine.verify_payment_screenshot(
        image, source_module="candidate_payment_proof", expected_amount=0,
        entity_id=entity, candidate_id=entity, entity_name=f"Name {entity}",
    )


def _ledger(tmp_path):
    return json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))


def _payer_side_then_released(monkeypatch):
    """A verified payer-side payment whose credit has been taken back, so only
    the receiver rule (not the one-credit rule) decides the next receipt."""
    _verify(monkeypatch, _payer_side(), entity="cand-a", image=b"payer")
    engine.release_payment_credit(
        entity_ids=["cand-a"], references=[UTR], actor="system", reason="released",
    )


def test_the_receiver_side_receipt_alone_stays_unverified(monkeypatch):
    """Nothing is inferred from a receipt that stands alone: an unregistered
    masked account with no earlier record of the transaction is still unknown."""
    result = _verify(monkeypatch, _receiver_side(), entity="cand-a", image=b"recv")
    assert result["verification_state"] == "UNKNOWN_RECEIVER"
    assert result["booking_eligible"] is False


def test_the_receiver_side_receipt_is_verified_once_the_payer_side_established_the_payee(
    monkeypatch, tmp_path
):
    payer = _verify(monkeypatch, _payer_side(), entity="cand-a", image=b"payer")
    assert payer["company_payment_verified"] is True
    engine.release_payment_credit(
        entity_ids=["cand-a"], references=[UTR], actor="system", reason="released",
    )

    result = _verify(monkeypatch, _receiver_side(), entity="cand-b", image=b"recv")

    assert result["verification_state"] == "VERIFIED_COMPANY_PAYMENT"
    assert result["receiver_match"] == "transaction"
    credited = [
        e for e in _ledger(tmp_path)["entries"]
        if e["entity_id"] == "cand-b" and e["status"] == "posted"
        and e["transaction_type"] != "REVERSAL"
    ]
    assert len(credited) == 1 and credited[0]["gross_amount"] == 20000


def test_a_different_amount_does_not_borrow_the_receiver(monkeypatch):
    _payer_side_then_released(monkeypatch)
    result = _verify(
        monkeypatch, _receiver_side(amount=25000), entity="cand-b", image=b"recv-2"
    )
    assert result["verification_state"] == "UNKNOWN_RECEIVER"


def test_a_different_date_does_not_borrow_the_receiver(monkeypatch):
    _payer_side_then_released(monkeypatch)
    result = _verify(
        monkeypatch, _receiver_side(payment_date="2026-09-14"), entity="cand-b", image=b"recv-3"
    )
    assert result["verification_state"] == "UNKNOWN_RECEIVER"


def test_a_different_transaction_does_not_borrow_the_receiver(monkeypatch):
    _payer_side_then_released(monkeypatch)
    result = _verify(
        monkeypatch,
        _receiver_side(utr_number="900000000002", transaction_id="T2600000000000000000002"),
        entity="cand-b", image=b"recv-4",
    )
    assert result["verification_state"] == "UNKNOWN_RECEIVER"


def test_the_same_money_still_cannot_be_credited_to_two_candidates(monkeypatch, tmp_path):
    """Recognising the receiver must not open a second credit for a payment
    that is already credited."""
    _verify(monkeypatch, _payer_side(), entity="cand-a", image=b"payer")
    second = _verify(monkeypatch, _receiver_side(), entity="cand-b", image=b"recv")
    assert second["verification_state"] == "DUPLICATE_PAYMENT"
    owners = {
        e["entity_id"] for e in _ledger(tmp_path)["entries"]
        if e["status"] == "posted" and e["transaction_type"] != "REVERSAL"
    }
    assert owners == {"cand-a"}


def test_releasing_the_credit_reverses_it_and_frees_the_rightful_candidate(
    monkeypatch, tmp_path
):
    _verify(monkeypatch, _payer_side(), entity="cand-a", image=b"payer")
    released = engine.release_payment_credit(
        entity_ids=["cand-a"], references=[TXN], actor="system", reason="wrong candidate",
    )
    assert len(released) == 1

    ledger = _ledger(tmp_path)
    credit = next(e for e in ledger["entries"] if e["transaction_type"] != "REVERSAL")
    reversal = next(e for e in ledger["entries"] if e["transaction_type"] == "REVERSAL")
    assert reversal["reversal_of_entry_id"] == credit["ledger_entry_id"]
    assert reversal["gross_amount"] == -credit["gross_amount"]

    again = engine.release_payment_credit(
        entity_ids=["cand-a"], references=[TXN], actor="system", reason="again",
    )
    assert again == [], "releasing twice must not reverse twice"

    rightful = _verify(monkeypatch, _receiver_side(), entity="cand-b", image=b"recv")
    assert rightful["company_payment_verified"] is True


def test_release_only_touches_the_named_candidates_payment(monkeypatch, tmp_path):
    _verify(monkeypatch, _payer_side(), entity="cand-a", image=b"payer")
    released = engine.release_payment_credit(
        entity_ids=["someone-else"], references=[UTR], actor="system", reason="no",
    )
    assert released == []
    assert not [e for e in _ledger(tmp_path)["entries"] if e["transaction_type"] == "REVERSAL"]


# --- deleting a proof takes back its credit -----------------------------------

@pytest.fixture()
def store(tmp_path, monkeypatch):
    from features import candidate_store

    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setenv("PAYMENT_RECALCULATION_AUDIT_FILE", str(tmp_path / "recalc_audit.json"))
    monkeypatch.setattr(candidate_store, "_load_cache", None)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0)
    return candidate_store


def _row_with_proof(store, row_id, phone, *, proof_id="pf1"):
    data = store._load()
    data.setdefault("candidates", []).append({
        "id": row_id, "name": "Test Payer", "phone": phone, "stage": "in_progress",
        "service_type": "profile_service", "expected_payment": 20000, "payment": 20000,
        "payment_proof_controlled": True, "date": "2026-09-30",
        "payment_proofs": [{
            "id": proof_id, "attachment_type": "payment_proof", "filename": f"{proof_id}.jpg",
            "utr_number": UTR, "transaction_id": TXN, "verified_amount": 20000,
            "verification_state": "VERIFIED_COMPANY_PAYMENT",
        }],
    })
    store._save(data)


def _live_credits(tmp_path):
    return [
        e for e in _ledger(tmp_path)["entries"]
        if e["status"] == "posted" and e["transaction_type"] != "REVERSAL"
        and not any(r.get("reversal_of_entry_id") == e["ledger_entry_id"] for r in _ledger(tmp_path)["entries"])
    ]


def test_deleting_the_only_proof_releases_the_credit(store, monkeypatch, tmp_path):
    _row_with_proof(store, "slot-1", "9000000401")
    _verify(monkeypatch, _payer_side(), entity="slot-1", image=b"payer")
    assert len(_live_credits(tmp_path)) == 1

    assert store.delete_proof("slot-1", "pf1") is True

    assert _live_credits(tmp_path) == []


def test_the_credit_on_a_sibling_slot_is_released_when_the_proof_is_deleted_elsewhere(
    store, monkeypatch, tmp_path
):
    _row_with_proof(store, "slot-1", "9000000402")
    data = store._load()
    data["candidates"].append({
        "id": "slot-2", "name": "Test Payer", "phone": "9000000402", "stage": "in_progress",
        "service_type": "profile_service", "expected_payment": 20000, "payment": 0,
        "date": "2026-09-29", "payment_proofs": [],
    })
    store._save(data)
    _verify(monkeypatch, _payer_side(), entity="slot-2", image=b"payer")

    assert store.delete_proof("slot-1", "pf1") is True

    assert _live_credits(tmp_path) == []


def test_the_credit_stays_while_another_slot_still_holds_the_proof(store, monkeypatch, tmp_path):
    _row_with_proof(store, "slot-1", "9000000403", proof_id="pf1")
    _row_with_proof(store, "slot-2", "9000000403", proof_id="pf2")
    _verify(monkeypatch, _payer_side(), entity="slot-1", image=b"payer")

    assert store.delete_proof("slot-1", "pf1") is True

    assert len(_live_credits(tmp_path)) == 1


def test_another_persons_credit_is_never_released(store, monkeypatch, tmp_path):
    _row_with_proof(store, "slot-1", "9000000404")
    _row_with_proof(store, "other-1", "9000000405", proof_id="pfo")
    _verify(monkeypatch, _payer_side(), entity="other-1", image=b"payer")

    # slot-1's proof carries the same transaction, but the credit is other-1's.
    assert store.delete_proof("slot-1", "pf1") is True

    assert len(_live_credits(tmp_path)) == 1


def test_reverifying_the_same_candidates_receipt_updates_its_payment_row(monkeypatch, tmp_path):
    """The receipt was stored as UNKNOWN_RECEIVER; once the payee is recognisable
    the same candidate's re-verification must leave the payment credited, or the
    one-credit-per-payment rule could not see it."""
    first = _verify(monkeypatch, _receiver_side(), entity="cand-b", image=b"recv")
    assert first["verification_state"] == "UNKNOWN_RECEIVER"
    _payer_side_then_released(monkeypatch)

    again = _verify(monkeypatch, _receiver_side(), entity="cand-b", image=b"recv")

    assert again["verification_state"] == "VERIFIED_COMPANY_PAYMENT"
    mine = [p for p in _ledger(tmp_path)["payments"] if p["source_entity_id"] == "cand-b"]
    assert [p["verification_state"] for p in mine] == ["VERIFIED_COMPANY_PAYMENT"]
    third = _verify(monkeypatch, _payer_side(), entity="cand-c", image=b"payer-3")
    assert third["verification_state"] == "DUPLICATE_PAYMENT", "the credit must now be visible"
