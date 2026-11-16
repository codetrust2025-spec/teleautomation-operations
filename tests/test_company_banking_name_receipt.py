"""Company-owned PhonePe receipts: recover the payee without weakening verification.

A PhonePe details receipt may label the recipient "Banking Name" and show
"Sent to XXXXXX4573@yescred". The sender's "Debited from" bank account is
not a recipient account. The candidate's selected referrer is not the owner
of a company payment.
"""
import json

import pytest

from features import ollama_payment_extract as extractor
from features import payment_verification_engine as engine


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(tmp_path / "accounts.json"))
    (tmp_path / "accounts.json").write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv("REFERRER_REGISTRY_FILE", str(tmp_path / "referrers.json"))
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("COMPANY_PAYMENT_RECEIVER_NAMES", "J Ravinder,Jollu Ravinder")
    monkeypatch.setenv("COMPANY_PAYMENT_UPI_IDS", "raviarvind1111@ybl")
    monkeypatch.setenv("COMPANY_PAYMENT_PHONE_NUMBERS", "8639074573")
    monkeypatch.setenv("COMPANY_PAYMENT_ACCOUNT_NUMBERS", "")


def receipt(**change):
    row = {
        "amount": 20000,
        "receiver_name": "Jollu Ravinder",
        "receiver_upi_id": "XXXXXX4573@yescred",
        "sender_account_identifier": "XXXXXXXX4871",
        "utr_number": "792481192171",
        "transaction_id": "T2611040945183436131068",
        "payment_status": "SUCCESS",
        "payment_date": "2026-11-16",
        "payment_time": "09:46 PM",
        "status": "success",
        "confidence_score": 98,
        "is_payment_screenshot": True,
    }
    row.update(change)
    return row


def verify(monkeypatch, row):
    monkeypatch.setattr(extractor, "extract_payment_with_ollama", lambda *args, **kwargs: dict(row))
    return engine.verify_payment_screenshot(
        b"receipt-image-fixture",
        "image/jpeg",
        source_module="test_company_banking_name_receipt",
        expected_amount=0,
        entity_name="Example Candidate",
        referrer_hint="Ravinder",
        referrer_id="referrer-other",
        purpose="candidate_payment",
        payment_scope="ROUND",
        create_ledger=False,
    )


def test_prompt_explicitly_identifies_phonepe_banking_name_and_sent_to():
    prompt = extractor._get_payment_prompt()
    for term in ("Banking Name", "Sent to", "receiver_name", "XXXXXX4573@yescred", "Debited from"):
        assert term in prompt


def test_primary_missing_payee_is_recovered_only_on_same_transaction(monkeypatch):
    monkeypatch.setattr(extractor, "ocr_enabled", lambda: False)
    monkeypatch.setattr(extractor, "_is_ollama_available", lambda: True)
    monkeypatch.setattr(extractor, "OLLAMA_VISION_MODEL", "first-vision")
    monkeypatch.setattr(extractor, "OLLAMA_BACKUP_VISION_MODEL", "second-vision")
    calls = []
    first = {
        "payment_status": "SUCCESS", "amount_text": "₹20,000", "amount": 20000,
        "utr": "792481192171", "transaction_id": "T2611040945183436131068",
        "receiver_name": "", "receiver_upi_id": "", "warnings": [],
        "is_payment_screenshot": True,
    }
    second = {
        **first, "receiver_name": "Jollu Ravinder",
        "receiver_upi_id": "XXXXXX4573@yescred",
    }

    def _vision(model, *args, **kwargs):
        calls.append(model)
        return json.dumps(first if model == "first-vision" else second)

    monkeypatch.setattr(extractor, "_call_vision_model", _vision)
    result = extractor.extract_payment_with_ollama(b"fake-image-data")
    assert calls == ["first-vision", "second-vision"]
    assert result["receiver_name"] == "Jollu Ravinder"
    assert result["receiver_upi_id"] == "XXXXXX4573@yescred"
    assert result["utr_number"] == first["utr"]
    assert result["amount"] == 20000


def test_backup_with_different_reference_cannot_supply_company_payee(monkeypatch):
    monkeypatch.setattr(extractor, "ocr_enabled", lambda: False)
    monkeypatch.setattr(extractor, "_is_ollama_available", lambda: True)
    monkeypatch.setattr(extractor, "OLLAMA_VISION_MODEL", "first-vision")
    monkeypatch.setattr(extractor, "OLLAMA_BACKUP_VISION_MODEL", "second-vision")
    first = {
        "payment_status": "SUCCESS", "amount": 20000,
        "utr": "792481192171", "transaction_id": "T2611040945183436131068",
        "receiver_name": "", "receiver_upi_id": "", "is_payment_screenshot": True,
    }
    second = {
        **first, "utr": "111111111111", "transaction_id": "T2601010101010101010101",
        "receiver_name": "Jollu Ravinder", "receiver_upi_id": "XXXXXX4573@yescred",
    }
    monkeypatch.setattr(
        extractor, "_call_vision_model",
        lambda model, *args, **kwargs: json.dumps(first if model == "first-vision" else second),
    )
    result = extractor.extract_payment_with_ollama(b"fake-image-data")
    assert not result["receiver_name"]
    assert not result["receiver_upi_id"]
    assert result["utr_number"] == first["utr"]


def test_company_payment_matches_registered_phone_not_selected_referrer(isolated, monkeypatch):
    result = verify(monkeypatch, receipt())
    assert result["verification_state"] == "VERIFIED_COMPANY_PAYMENT"
    assert result["receiver_type"] == "company"
    assert result["receiver_match"] == "masked_upi_alias"
    assert result["booking_eligible"] is True


def test_missing_payee_never_verifies_and_does_not_ask_to_map_company_to_referrer(isolated, monkeypatch):
    result = verify(monkeypatch, receipt(receiver_name=""))
    assert result["booking_eligible"] is False
    reasons = " ".join(result["deterministic_reasons"])
    assert "Banking Name" in reasons
    assert "registered to Ravinder" not in reasons
    assert "map this receiver to the referrer" not in reasons


def test_company_name_with_wrong_mask_stays_unverified(isolated, monkeypatch):
    result = verify(monkeypatch, receipt(receiver_upi_id="XXXXXX9999@yescred"))
    assert result["booking_eligible"] is False
    assert result["verification_state"] != "VERIFIED_COMPANY_PAYMENT"


def test_company_name_alone_never_verifies(isolated, monkeypatch):
    result = verify(monkeypatch, receipt(receiver_upi_id=""))
    assert result["booking_eligible"] is False
    assert result["verification_state"] != "VERIFIED_COMPANY_PAYMENT"
