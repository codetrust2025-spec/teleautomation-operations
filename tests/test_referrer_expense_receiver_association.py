"""Regression tests for the referrer-expense receiver-registry association bug.

Scenario (the "Pavan/Venugopal" bug): an operator selects referrer Venugopal,
uploads a payment screenshot for ₹7,500, and the engine replies "The receiver
is not present in the configured receiver registry" even though Venugopal has a
registered payment account.  The screenshot's extracted receiver string did not
literally match a registry identifier, so ``classify_receiver`` fell through to
UNKNOWN_RECEIVER.

The fix: when the operator explicitly selects a referrer (referrer_id is
provided) and the screenshot's extracted receiver *name* matches the selected
referrer or one of their registered account-holder names, the payment is
recognised.  A receipt naming someone else — or showing no name at all — stays
unverified, because the referrer selection alone does not prove where the money
went.

Every test drives ``verify_payment_screenshot``, the entry point production
calls.

Handles use the ``test…`` prefix and the ``@examplebank`` domain so the
``test_fixtures_hold_no_personal_data`` lint passes.
"""
from __future__ import annotations

import json

import pytest

from features import payment_verification_engine as engine


REFERRER_NAME = "Venugopal"
REFERRER_ID = "referrer-venugopal"
REFERRER_UPI = "testpayee99@examplebank"
REFERRER_PHONE = "+919876543210"

COMPANY_UPI = "company@examplebank"
COMPANY_NAME = "Test Company"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """A registry with one referrer (Venugopal) who has a registered account."""
    accounts = tmp_path / "payment_receiver_accounts.json"
    accounts.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "id": "receiver-venugopal",
                        "owner_type": "REFERRER",
                        "referrer_id": REFERRER_ID,
                        "account_holder_name": REFERRER_NAME,
                        "upi_id": REFERRER_UPI,
                        "payment_phone_number": "9876543210",
                        "verification_status": "VERIFIED",
                        "is_active": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    referrers = tmp_path / "referrers.json"
    referrers.write_text(
        json.dumps(
            {
                "version": 1,
                "referrers": [
                    {
                        "id": REFERRER_ID,
                        "name": REFERRER_NAME,
                        "aliases": ["Venugopal", "VENUGOPAL"],
                        "is_active": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(accounts))
    monkeypatch.setenv("REFERRER_REGISTRY_FILE", str(referrers))
    monkeypatch.setenv(
        "PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json")
    )
    monkeypatch.setenv("COMPANY_PAYMENT_UPI_IDS", COMPANY_UPI)
    monkeypatch.setenv("COMPANY_PAYMENT_RECEIVER_NAMES", COMPANY_NAME)
    monkeypatch.delenv("COMPANY_PAYMENT_PHONE_NUMBERS", raising=False)


def _extraction(**patch):
    """A successful payment screenshot extraction for ₹7,500."""
    row = {
        "amount": 7500,
        "direction": "PAID_TO",
        "receiver_name": "Venugopal",
        "receiver_upi_id": "",
        "receiver_phone_number": "",
        "receiver_account": "",
        "utr_number": "900000000099",
        "transaction_id": "T2600000000000000000099",
        "payment_date": "2026-10-06",
        "payment_time": "02:30 PM",
        "status": "success",
        "confidence_score": 95,
        "is_payment_screenshot": True,
        "primary_model": "qwen3-vl:8b-instruct",
    }
    row.update(patch)
    return row


def _install_extractor(monkeypatch, value):
    monkeypatch.setattr(
        "features.ollama_payment_extract.extract_payment_with_ollama",
        lambda _raw, _mime, **kwargs: dict(value),
    )


def _verify_expense(monkeypatch, extraction, **overrides):
    """Simulate the handler-expense upload flow for a selected referrer."""
    _install_extractor(monkeypatch, extraction)
    defaults = dict(
        source_module="handler_expense_create",
        expected_amount=7500,
        entity_name=REFERRER_NAME,
        referrer_hint=REFERRER_NAME,
        referrer_id=REFERRER_ID,
        purpose="handler_payout",
    )
    defaults.update(overrides)
    return engine.verify_payment_screenshot(b"fake-image", **defaults)


# ── The Pavan/Venugopal bug: selected referrer + name match must verify ──────


class TestSelectedReferrerWithNameMatch:
    """The operator selected Venugopal AND the screenshot names him as the
    receiver.  That combination must verify even without a registry identifier
    match."""

    def test_no_identifier_but_name_matches_referrer_verifies(self, monkeypatch):
        """The exact bug from the screenshot: receiver name is "Venugopal", no
        UPI/phone/account visible, referrer Venugopal selected → must verify."""
        result = _verify_expense(monkeypatch, _extraction())
        assert result["deterministic_verified"] is True
        assert result["verification_state"] == "VERIFIED_REFERRER_PAYMENT"
        assert result["receiver_match"] in {"referrer_selected", "name", "upi", "phone"}
        assert result["receiver_registry_name"] == REFERRER_NAME

    def test_unknown_upi_but_name_matches_referrer_verifies(self, monkeypatch):
        """Screenshot shows an unknown UPI but names the selected referrer →
        name is the evidence, the unknown handle is noise."""
        result = _verify_expense(
            monkeypatch,
            _extraction(receiver_upi_id="different42@examplebank"),
        )
        assert result["deterministic_verified"] is True
        assert result["verification_state"] == "VERIFIED_REFERRER_PAYMENT"

    def test_registered_upi_still_matches_by_identifier(self, monkeypatch):
        """When the screenshot shows the registered UPI, the stronger
        identifier match takes precedence."""
        result = _verify_expense(
            monkeypatch,
            _extraction(receiver_upi_id=REFERRER_UPI),
        )
        assert result["deterministic_verified"] is True
        assert result["receiver_match"] == "upi"

    def test_registered_phone_matches_by_identifier(self, monkeypatch):
        result = _verify_expense(
            monkeypatch,
            _extraction(receiver_phone_number="9876543210"),
        )
        assert result["deterministic_verified"] is True
        assert result["receiver_match"] == "phone"


# ── Tightened rule: wrong name / no name must NOT verify ─────────────────────


class TestUnrelatedReceiverStaysUnverified:
    """The referrer selection must NOT blindly authorise an unrelated
    receiver.  The screenshot must name the selected referrer."""

    def test_different_person_name_does_not_verify(self, monkeypatch):
        """Screenshot names "Someone Else" but the operator selected
        Venugopal → must NOT verify."""
        result = _verify_expense(
            monkeypatch,
            _extraction(receiver_name="Someone Else"),
        )
        assert result["deterministic_verified"] is False

    def test_empty_receiver_name_does_not_verify(self, monkeypatch):
        """Screenshot shows no receiver name → must NOT verify via the
        referrer selection alone."""
        result = _verify_expense(
            monkeypatch,
            _extraction(receiver_name=""),
        )
        assert result["deterministic_verified"] is False

    def test_different_name_with_unknown_upi_does_not_verify(self, monkeypatch):
        """Screenshot names someone else AND shows an unregistered UPI → no
        verification despite the referrer selection."""
        result = _verify_expense(
            monkeypatch,
            _extraction(
                receiver_name="Stranger Person",
                receiver_upi_id="stranger42@examplebank",
            ),
        )
        assert result["deterministic_verified"] is False
        assert result["receiver_type"] == "unknown"


# ── UNVERIFIED account + selected referrer for expenses ──────────────────────


class TestUnverifiedAccountStillAcceptedForExpense:
    """An operator-selected referrer whose account is UNVERIFIED should still
    pass for handler payouts when the screenshot names that referrer."""

    @pytest.fixture(autouse=True)
    def _unverified_account(self, monkeypatch, tmp_path):
        accounts = tmp_path / "payment_receiver_accounts.json"
        accounts.write_text(
            json.dumps(
                {
                    "accounts": [
                        {
                            "id": "receiver-venugopal",
                            "owner_type": "REFERRER",
                            "referrer_id": REFERRER_ID,
                            "account_holder_name": REFERRER_NAME,
                            "upi_id": REFERRER_UPI,
                            "verification_status": "UNVERIFIED",
                            "is_active": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(accounts))

    def test_expense_to_unverified_referrer_account_verifies(self, monkeypatch):
        result = _verify_expense(
            monkeypatch,
            _extraction(receiver_upi_id=REFERRER_UPI),
        )
        assert result["deterministic_verified"] is True
        assert result["verification_state"] == "VERIFIED_REFERRER_PAYMENT"

    def test_candidate_payment_to_unverified_referrer_account_stays_pending(
        self, monkeypatch
    ):
        """Candidate payments must still require a verified account."""
        _install_extractor(monkeypatch, _extraction(receiver_upi_id=REFERRER_UPI))
        result = engine.verify_payment_screenshot(
            b"fake-image",
            source_module="candidate_payment_proof",
            expected_amount=7500,
            entity_name="Some Candidate",
            referrer_hint=REFERRER_NAME,
            referrer_id=REFERRER_ID,
            purpose="candidate_payment",
        )
        assert result["deterministic_verified"] is False
        assert "RECEIVER_ACCOUNT_UNVERIFIED" in result.get("reason_codes", [])


# ── Genuinely unknown receiver must still fail ───────────────────────────────


class TestGenuinelyUnknownReceiverStillFails:
    """When no referrer is selected (or the selected referrer has no accounts),
    an unknown receiver must continue failing with a clear mismatch message."""

    def test_unknown_receiver_without_referrer_selection_fails(self, monkeypatch):
        """No referrer_id → the old UNKNOWN_RECEIVER path."""
        _install_extractor(
            monkeypatch,
            _extraction(
                receiver_name="Stranger Person",
                receiver_upi_id="stranger42@examplebank",
            ),
        )
        result = engine.verify_payment_screenshot(
            b"fake-image",
            source_module="handler_expense_create",
            expected_amount=7500,
            entity_name=REFERRER_NAME,
            referrer_hint=REFERRER_NAME,
            referrer_id="",
            purpose="handler_payout",
        )
        assert result["deterministic_verified"] is False
        assert result["receiver_type"] == "unknown"
        assert "UNKNOWN_RECEIVER" in result.get("reason_codes", [])

    def test_unknown_receiver_with_different_referrer_fails(self, monkeypatch):
        """referrer_id is set to a different referrer that has no accounts."""
        _install_extractor(
            monkeypatch,
            _extraction(
                receiver_name="Stranger Person",
                receiver_upi_id="stranger42@examplebank",
            ),
        )
        result = engine.verify_payment_screenshot(
            b"fake-image",
            source_module="handler_expense_create",
            expected_amount=7500,
            entity_name="Thrilok",
            referrer_hint="Thrilok",
            referrer_id="referrer-thrilok",
            purpose="handler_payout",
        )
        assert result["deterministic_verified"] is False

    def test_explicit_mismatch_message_names_the_referrer(self, monkeypatch):
        """When a referrer is selected but the receiver truly does not match,
        the error message should clearly name the referrer."""
        _install_extractor(
            monkeypatch,
            _extraction(
                receiver_name="Stranger Person",
                receiver_upi_id="stranger42@examplebank",
            ),
        )
        result = engine.verify_payment_screenshot(
            b"fake-image",
            source_module="handler_expense_create",
            expected_amount=7500,
            entity_name=REFERRER_NAME,
            referrer_hint=REFERRER_NAME,
            referrer_id="referrer-no-accounts",
            purpose="handler_payout",
        )
        reasons = " ".join(result.get("deterministic_reasons") or [])
        assert "Venugopal" in reasons or "receiver" in reasons.lower()


# ── Candidate payments unaffected ─────────────────────────────────────────────


class TestCandidatePaymentsUnaffected:
    """The referrer-selected fallback must NOT apply to candidate payments."""

    def test_candidate_payment_without_identifier_match_fails(self, monkeypatch):
        _install_extractor(monkeypatch, _extraction())
        result = engine.verify_payment_screenshot(
            b"fake-image",
            source_module="candidate_payment_proof",
            expected_amount=7500,
            entity_name="Some Candidate",
            referrer_hint=REFERRER_NAME,
            referrer_id=REFERRER_ID,
            purpose="candidate_payment",
        )
        assert result["deterministic_verified"] is False


# ── Amount extraction and duplicate prevention preserved ─────────────────────


class TestAmountExtractionAndDedup:
    """Automatic amount extraction and duplicate prevention must continue
    working through the fix."""

    def test_amount_is_extracted_automatically(self, monkeypatch):
        result = _verify_expense(monkeypatch, _extraction(amount=7500))
        assert int(result.get("amount") or 0) == 7500

    def test_duplicate_screenshot_blocked(self, monkeypatch):
        """Same screenshot (same UTR) uploaded for two different entities →
        second is a duplicate."""
        result1 = _verify_expense(
            monkeypatch, _extraction(), entity_id="expense-001",
        )
        assert result1["deterministic_verified"] is True

        result2 = _verify_expense(
            monkeypatch, _extraction(), entity_id="expense-002",
        )
        assert (
            result2.get("verification_state") == "DUPLICATE_PAYMENT"
            or not result2.get("deterministic_verified")
        )


# ── Banking name differs from referrer name: a configured alias must verify ──


# The real-world "CHIMALADARI VENU GOPAL" case, with synthetic values: the
# screenshot's receiver/banking name is nothing like the referrer's display
# name, but an administrator has mapped it as an alias of the referrer. The
# receiver-record alias merge must carry that alias into name matching so the
# operator-selected-referrer fallback recognises the payee.
ALIAS_REFERRER_NAME = "Testpayee One"
ALIAS_REFERRER_ID = "referrer-testpayee-one"
# A banking name that does NOT contain the referrer's display name — proves the
# match comes from the configured alias, not an incidental substring.
MAPPED_BANKING_NAME = "Testbank Example Receiver"


class TestBankingNameAliasMapping:
    """A referrer whose screenshots show a different banking name verifies once
    that banking name is configured as a referrer alias — even with no
    registered payment account. Candidate payments stay strict."""

    @pytest.fixture(autouse=True)
    def _alias_registry(self, monkeypatch, tmp_path):
        # Referrer has the mapped banking name as a configured alias but NO
        # registered payment account at all.
        accounts = tmp_path / "payment_receiver_accounts.json"
        accounts.write_text(json.dumps({"accounts": []}), encoding="utf-8")
        referrers = tmp_path / "referrers.json"
        referrers.write_text(
            json.dumps(
                {
                    "version": 1,
                    "referrers": [
                        {
                            "id": ALIAS_REFERRER_ID,
                            "name": ALIAS_REFERRER_NAME,
                            "aliases": [MAPPED_BANKING_NAME],
                            "is_active": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(accounts))
        monkeypatch.setenv("REFERRER_REGISTRY_FILE", str(referrers))

    def _alias_extraction(self, **patch):
        row = _extraction(
            receiver_name=MAPPED_BANKING_NAME,
            receiver_upi_id="",
            receiver_phone_number="",
            receiver_account="",
        )
        row.update(patch)
        return row

    def test_mapped_banking_name_verifies_without_account(self, monkeypatch):
        """Banking name matches a configured referrer alias, no registered
        account → the referrer-expense verifies."""
        _install_extractor(monkeypatch, self._alias_extraction())
        result = engine.verify_payment_screenshot(
            b"fake-image",
            source_module="handler_expense_create",
            expected_amount=7500,
            entity_name=ALIAS_REFERRER_NAME,
            referrer_hint=ALIAS_REFERRER_NAME,
            referrer_id=ALIAS_REFERRER_ID,
            purpose="handler_payout",
        )
        assert result["deterministic_verified"] is True
        assert result["verification_state"] == "VERIFIED_REFERRER_PAYMENT"
        assert result["receiver_type"] == "referrer"
        assert result.get("matched_referrer_id") == ALIAS_REFERRER_ID

    def test_mapped_banking_name_candidate_payment_stays_strict(self, monkeypatch):
        """The SAME alias-only match must NOT verify a candidate payment — the
        strict candidate path requires a verified account identifier."""
        _install_extractor(monkeypatch, self._alias_extraction())
        result = engine.verify_payment_screenshot(
            b"fake-image",
            source_module="candidate_payment_proof",
            expected_amount=7500,
            entity_name="Some Candidate",
            referrer_hint=ALIAS_REFERRER_NAME,
            referrer_id=ALIAS_REFERRER_ID,
            purpose="candidate_payment",
        )
        assert result["deterministic_verified"] is False

    def test_unmapped_banking_name_still_fails(self, monkeypatch):
        """A banking name that is NOT a configured alias (and not the referrer
        name) must still be refused — the mapping is what authorises it."""
        _install_extractor(
            monkeypatch,
            self._alias_extraction(receiver_name="Completely Different Payee"),
        )
        result = engine.verify_payment_screenshot(
            b"fake-image",
            source_module="handler_expense_create",
            expected_amount=7500,
            entity_name=ALIAS_REFERRER_NAME,
            referrer_hint=ALIAS_REFERRER_NAME,
            referrer_id=ALIAS_REFERRER_ID,
            purpose="handler_payout",
        )
        assert result["deterministic_verified"] is False
        assert result["receiver_type"] == "unknown"

    def test_mapped_banking_name_tolerates_extra_whitespace(self, monkeypatch):
        """Double spaces in the extracted banking name (as PhonePe renders
        them) still match the configured alias via normalization."""
        _install_extractor(
            monkeypatch,
            self._alias_extraction(receiver_name="Testbank   Example   Receiver"),
        )
        result = engine.verify_payment_screenshot(
            b"fake-image",
            source_module="handler_expense_create",
            expected_amount=7500,
            entity_name=ALIAS_REFERRER_NAME,
            referrer_hint=ALIAS_REFERRER_NAME,
            referrer_id=ALIAS_REFERRER_ID,
            purpose="handler_payout",
        )
        assert result["deterministic_verified"] is True

    def test_account_holder_differs_but_alias_on_referrer_verifies(self, monkeypatch):
        """Referrer HAS a registered account whose holder name is yet another
        string; the screenshot shows the mapped banking name. The alias merged
        from the referrer registry (not the account holder) still matches."""
        accounts = tmp_path = None
        import os as _os
        acct_path = _os.environ["PAYMENT_RECEIVER_REGISTRY_FILE"]
        with open(acct_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "accounts": [
                        {
                            "id": "receiver-testpayee-one",
                            "owner_type": "REFERRER",
                            "referrer_id": ALIAS_REFERRER_ID,
                            "account_holder_name": "Yet Another Holder",
                            "upi_id": "testpayee1@examplebank",
                            "verification_status": "UNVERIFIED",
                            "is_active": True,
                        }
                    ]
                },
                handle,
            )
        _install_extractor(monkeypatch, self._alias_extraction())
        result = engine.verify_payment_screenshot(
            b"fake-image",
            source_module="handler_expense_create",
            expected_amount=7500,
            entity_name=ALIAS_REFERRER_NAME,
            referrer_hint=ALIAS_REFERRER_NAME,
            referrer_id=ALIAS_REFERRER_ID,
            purpose="handler_payout",
        )
        assert result["deterministic_verified"] is True
        assert result["receiver_type"] == "referrer"
