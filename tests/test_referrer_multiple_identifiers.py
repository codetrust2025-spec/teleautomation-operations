"""A referrer is paid at more than one identifier, and any verified one counts.

Production, 2026-09-15: two expense receipts to a referrer whose account carried
only a verified phone were refused because the receipts showed his UPI handle
instead -- extraction was perfect, confidence 1.0 on every field, and the payee
was the registered account holder. The account now carries both identifiers.

The rule these pin is that a payment resolves through *an identifier*, never
through a name: the same holder name with a handle nobody registered still
fails, and so does a receipt with no identifier at all. Values here are
synthetic.
"""
from __future__ import annotations

import json

import pytest

from features import payment_verification_engine as engine

HOLDER = "A Testpayee"
REGISTERED_UPI = "testpayee9@examplebank"
REGISTERED_PHONE = "+919812345678"


@pytest.fixture(autouse=True)
def registry(monkeypatch, tmp_path):
    """One referrer holding a verified phone and a verified UPI."""
    accounts = tmp_path / "payment_receiver_accounts.json"
    accounts.write_text(json.dumps({"accounts": [{
        "id": "receiver-test", "owner_type": "REFERRER", "referrer_id": "referrer-test",
        "account_holder_name": HOLDER, "upi_id": REGISTERED_UPI,
        "payment_phone_number": REGISTERED_PHONE, "verification_status": "VERIFIED",
        "is_active": True}]}), encoding="utf-8")
    referrers = tmp_path / "referrers.json"
    referrers.write_text(json.dumps({"referrers": [
        {"id": "referrer-test", "name": "Testpayee", "is_active": True}]}), encoding="utf-8")
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(accounts))
    monkeypatch.setenv("REFERRER_REGISTRY_FILE", str(referrers))
    monkeypatch.setenv("COMPANY_PAYMENT_UPI_IDS", "company.test@examplebank")
    monkeypatch.setenv("COMPANY_PAYMENT_RECEIVER_NAMES", "Test Company")
    monkeypatch.delenv("COMPANY_PAYMENT_PHONE_NUMBERS", raising=False)


def resolve(name, *, upi="", phone=""):
    result = engine.classify_receiver(
        {"receiver_name": name, "receiver_upi_id": upi, "receiver_phone_number": phone})
    stable = (result.get("receiver_type") in {"company", "referrer"}
              and result.get("receiver_match") in {"upi", "phone", "account", "masked_upi_alias"}
              and int(result.get("receiver_match_score") or 0) >= 100)
    return result, stable


class TestEitherVerifiedIdentifierIsEnough:
    def test_the_registered_upi_resolves_the_payee(self):
        result, stable = resolve(HOLDER, upi=REGISTERED_UPI)
        assert stable and result["receiver_match"] == "upi"

    def test_the_registered_phone_resolves_the_same_payee(self):
        result, stable = resolve(HOLDER, phone=REGISTERED_PHONE)
        assert stable and result["receiver_match"] == "phone"

    def test_both_routes_reach_one_account(self):
        by_upi, _ = resolve(HOLDER, upi=REGISTERED_UPI)
        by_phone, _ = resolve(HOLDER, phone=REGISTERED_PHONE)
        assert by_upi["receiver_registry_id"] == by_phone["receiver_registry_id"]

    def test_the_phone_is_matched_however_the_receipt_writes_it(self):
        for written in ("+919812345678", "919812345678", "9812345678", "+91 98123 45678"):
            _, stable = resolve(HOLDER, phone=written)
            assert stable, written


class TestNothingElseResolves:
    def test_the_holder_name_with_an_unknown_upi_fails(self):
        result, stable = resolve(HOLDER, upi="someone.else@examplebank")
        assert not stable and result["receiver_type"] == "unknown"

    def test_the_holder_name_with_an_unknown_phone_fails(self):
        result, stable = resolve(HOLDER, phone="+919000000002")
        assert not stable and result["receiver_type"] == "unknown"

    def test_the_holder_name_alone_fails(self):
        result, stable = resolve(HOLDER)
        assert not stable

    def test_a_near_miss_handle_is_not_folded_together(self):
        """One character out is a different account, not a typo to forgive."""
        _, stable = resolve(HOLDER, upi="testpayee8@examplebank")
        assert not stable

    def test_the_same_handle_at_another_provider_fails(self):
        _, stable = resolve(HOLDER, upi="testpayee9@otherbank")
        assert not stable


class TestTheIdentifierIsTheAuthority:
    def test_a_registered_identifier_identifies_its_owner_whatever_name_is_printed(self):
        """A UPI app prints whatever display name the payer's contact list holds.
        The money still reached the registered account, so the account decides."""
        result, stable = resolve("Someone Else Entirely", upi=REGISTERED_UPI)
        assert stable and result["receiver_registry_id"] == "receiver-test"
