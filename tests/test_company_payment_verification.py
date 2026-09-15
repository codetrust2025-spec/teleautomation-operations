import json

from features.company_payment_verification import (
    configured_company_account_numbers,
    configured_company_phone_numbers,
    configured_company_upi_ids,
    verify_company_payment,
)
from features.ollama_payment_extract import _extract_amount_from_text, _ocr_regex_extraction

import pytest


@pytest.fixture(autouse=True)
def _isolated_receiver_registry(monkeypatch, tmp_path):
    # Isolate from real seed data so tests never depend on production identifiers.
    registry_path = tmp_path / "payment_receiver_accounts.json"
    registry_path.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(registry_path))


def _receipt(**patch):
    receipt = {
        "is_payment_screenshot": True,
        "amount": 5000,
        "receiver_name": "Sample Receiver",
        "receiver_upi_id": "company@upi",
        "utr_number": "482681255068",
        "status": "success",
    }
    receipt.update(patch)
    return receipt


def test_company_payment_accepts_configured_company_upi():
    verdict = verify_company_payment(
        _receipt(), 5000,
        accepted_upi_ids={"company@upi"},
        accepted_phone_numbers={"9000000001"},
    )

    assert verdict["verified"] is True
    assert verdict["reasons"] == []


def test_official_company_payment_defaults_survive_config_loader_failure(monkeypatch):
    monkeypatch.delenv("COMPANY_PAYMENT_UPI_IDS", raising=False)
    monkeypatch.delenv("COMPANY_PAYMENT_PHONE_NUMBERS", raising=False)
    monkeypatch.delenv("COMPANY_PAYMENT_PHONE", raising=False)

    assert configured_company_upi_ids() == {"company@upi"}
    # No placeholder phone: 9000000001 was never the company's number, and an
    # unconfigured phone must not allow-list one.
    assert configured_company_phone_numbers() == set()


def test_configured_company_phones_are_still_honoured(monkeypatch):
    monkeypatch.setenv("COMPANY_PAYMENT_PHONE_NUMBERS", "+91 98765 00001, 9876500002")
    monkeypatch.setenv("COMPANY_PAYMENT_PHONE", "9876500009")
    assert configured_company_phone_numbers() == {"9876500001", "9876500002"}

    # The older single-number setting, used exactly as before: only when the
    # list is not configured.
    monkeypatch.delenv("COMPANY_PAYMENT_PHONE_NUMBERS")
    assert configured_company_phone_numbers() == {"9876500009"}


def _empty_registries(monkeypatch, tmp_path):
    referrers = tmp_path / "referrers.json"
    referrers.write_text(json.dumps({"version": 1, "referrers": []}), encoding="utf-8")
    accounts = tmp_path / "accounts.json"
    accounts.write_text(json.dumps({"accounts": []}), encoding="utf-8")
    monkeypatch.setenv("REFERRER_REGISTRY_FILE", str(referrers))
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(accounts))
    monkeypatch.setenv("COMPANY_PAYMENT_RECEIVER_NAMES", "SAMPLE COMPANY")
    monkeypatch.setenv("COMPANY_PAYMENT_UPI_IDS", "company@upi")


def test_a_receipt_to_the_old_placeholder_is_not_a_company_payment(monkeypatch, tmp_path):
    from features.payment_verification_engine import classify_receiver, receiver_registry

    _empty_registries(monkeypatch, tmp_path)
    monkeypatch.delenv("COMPANY_PAYMENT_PHONE_NUMBERS", raising=False)
    monkeypatch.delenv("COMPANY_PAYMENT_PHONE", raising=False)

    company = next(record for record in receiver_registry() if record["type"] == "company")
    assert company["phones"] == []

    # Paid to the placeholder under the company's name: the name resembles a
    # registered receiver but the identifier does not match -- manual review,
    # never a company credit.
    impostor = classify_receiver({"receiver_name": "SAMPLE COMPANY", "receiver_phone": "+919000000001"})
    assert impostor["receiver_type"] == "unknown"
    assert impostor["receiver_identifier_conflict"] is True

    # Paid to the placeholder under any other name: simply not a registered receiver.
    stranger = classify_receiver({"receiver_name": "Somebody Else", "receiver_phone": "+919000000001"})
    assert stranger["receiver_type"] == "unknown"


def test_real_company_identifiers_still_verify(monkeypatch, tmp_path):
    from features.payment_verification_engine import classify_receiver

    _empty_registries(monkeypatch, tmp_path)
    monkeypatch.setenv("COMPANY_PAYMENT_PHONE_NUMBERS", "9876500001")

    by_upi = classify_receiver({"receiver_name": "SAMPLE COMPANY", "receiver_upi_id": "company@upi"})
    assert (by_upi["receiver_type"], by_upi["receiver_match"]) == ("company", "upi")

    by_phone = classify_receiver({"receiver_name": "SAMPLE COMPANY", "receiver_phone": "+919876500001"})
    assert (by_phone["receiver_type"], by_phone["receiver_match"]) == ("company", "phone")


def test_registered_referrer_payment_is_accepted(monkeypatch, tmp_path):
    # Self-contained dummy referrer + account registry (no real identifiers).
    referrers = tmp_path / "referrers.json"
    referrers.write_text(
        json.dumps(
            {
                "version": 1,
                "referrers": [
                    {"id": "referrer-sample", "name": "SAMPLE REFERRER",
                     "aliases": [], "is_active": True}
                ],
            }
        ),
        encoding="utf-8",
    )
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "id": "acct-sample",
                        "owner_type": "REFERRER",
                        "referrer_id": "referrer-sample",
                        "account_holder_name": "SAMPLE REFERRER",
                        "upi_id": "referrer@upi",
                        "verification_status": "VERIFIED",
                        "is_active": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("REFERRER_REGISTRY_FILE", str(referrers))
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(accounts))

    verdict = verify_company_payment(
        _receipt(
            receiver_name="SAMPLE REFERRER",
            receiver_upi_id="referrer@upi",
        ),
        5000,
        accepted_upi_ids={"company@upi"},
        accepted_phone_numbers={"9000000001"},
    )

    assert verdict["verified"] is True
    assert verdict["receiver_type"] == "referrer"
    assert verdict["reasons"] == []
    assert verdict["receiver_type"] == "referrer"
    assert verdict["reasons"] == []


def test_receipt_without_visible_payee_fails_closed():
    verdict = verify_company_payment(
        _receipt(receiver_upi_id=""),
        5000,
        accepted_upi_ids={"company@upi"},
        accepted_phone_numbers={"9000000001"},
    )

    assert verdict["verified"] is False
    assert "receiving UPI ID or phone number is not visible" in " ".join(verdict["reasons"])


def test_failed_or_partial_company_payment_is_rejected():
    verdict = verify_company_payment(
        _receipt(amount=3000, status="failed"),
        5000,
        accepted_upi_ids={"company@upi"},
        accepted_phone_numbers={"9000000001"},
    )

    assert verdict["verified"] is False
    reasons = " ".join(verdict["reasons"])
    assert "successful, completed" in reasons
    assert "full ₹5,000" in reasons


def test_compact_company_upi_success_without_utr_is_allowed():
    verdict = verify_company_payment(
        _receipt(utr_number=""),
        5000,
        accepted_upi_ids={"company@upi"},
        accepted_phone_numbers={"9000000001"},
    )

    assert verdict["verified"] is True


def test_company_payment_phone_is_allowed_without_upi():
    verdict = verify_company_payment(
        _receipt(
            amount=16000,
            receiver_name="SAMPLE RECEIVER",
            receiver_upi_id="",
            receiver_phone="+919000000001",
        ),
        16000,
        accepted_upi_ids={"company@upi"},
        accepted_phone_numbers={"9000000001"},
    )

    assert verdict["verified"] is True
    assert verdict["receiver_phone"] == "9000000001"


def test_stored_company_payment_accepts_configured_bank_account(monkeypatch):
    from features.company_payment_verification import stored_proof_is_verified_company_payment

    monkeypatch.setenv("COMPANY_PAYMENT_ACCOUNT_NUMBERS", "1234567896367")
    assert configured_company_account_numbers() == {"1234567896367"}
    assert stored_proof_is_verified_company_payment({
        "company_payment_verified": True,
        "receiver_account": "XXXXXX6367",
    })


def test_ocr_fast_path_extracts_company_upi_from_compact_receipt():
    result = _ocr_regex_extraction(
        "Transaction Successful\n₹15,000.00\nPaid to SAMPLE RECEIVER\n"
        "PhonePe • company@upi\n30 June 2026, 8:01pm"
    )

    assert result is not None
    assert result["receiver_upi_id"] == "company@upi"


def test_ocr_fast_path_extracts_company_payment_phone():
    result = _ocr_regex_extraction(
        "Transaction Successful\nPaid to SAMPLE RECEIVER\n+919000000001\n"
        "₹16,000\nUTR: 633424783763"
    )

    assert result is not None
    assert result["receiver_phone"] == "+919000000001"


def test_ocr_amount_survives_when_rupee_symbol_is_dropped():
    text = "Paid to ravindra job hunter\nUTR: 265087185302\n15,000\n15,000"

    assert _extract_amount_from_text(text) == 15000
    result = _ocr_regex_extraction(text)
    assert result is not None
    assert result["amount"] == 15000
