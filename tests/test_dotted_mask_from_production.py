"""The mask that actually arrives is dots, not bullets.

Google Pay draws the payee as "••••1111@ybl". The extractor transcribes those
bullets as full stops, so what reaches the validator is ".....1111@ybl" -- five
U+002E, read straight out of the production rejection log for Sowmya's upload:

    receiver_name='J RAVINDER' receiver_upi='.....1111@ybl' receiver_account=''
    sender_upi='.....2761@okaxis' amount=5000 status='SUCCESS'
    txn='661139834383' match='' conflict=True

The mask class did not include the period, so the string was not recognised as
redacted. Dots are legal inside a real VPA local part, so `_valid_upi` then
accepted ".....1111@ybl" as a genuine handle -- one that matches no registered
account while the receiver NAME matches one, which is exactly the state that
raises a receiver-identity conflict. A payment to a registered account was
refused for not being to a registered account.

Every payload here is the logged one. Nothing is transcribed from a screenshot
by eye, which is how the two previous attempts at this went wrong.
"""

from __future__ import annotations

import pytest

from features import payment_verification_engine as eng


# Read from `docker logs`, not from the image.
PRODUCTION_RECEIVER_UPI = ".....1111@ybl"
PRODUCTION_RECEIVER_UPI_WITH_PROVIDER_LABEL = "....1111@ybl on PhonePe"
PRODUCTION_SENDER_UPI = ".....2761@okaxis"
REGISTERED = ("raviarvind1111@ybl",)


class TestTheDottedMaskIsRecognised:
    def test_the_exact_string_production_logged(self):
        assert eng._is_masked_identifier(PRODUCTION_RECEIVER_UPI) is True

    def test_it_resolves_to_the_registered_company_handle(self):
        assert eng._masked_upi_alias_match(PRODUCTION_RECEIVER_UPI, REGISTERED) == (
            "raviarvind1111@ybl"
        )

    def test_provider_label_is_removed_before_matching_the_mask(self):
        assert eng._strip_recognized_upi_provider_label(
            PRODUCTION_RECEIVER_UPI_WITH_PROVIDER_LABEL
        ) == "....1111@ybl"

    @pytest.mark.parametrize("mask", ["••••1111@ybl", "XXXXXX1111@ybl", "****1111@ybl",
                                      "####1111@ybl", "····1111@ybl"])
    def test_the_other_mask_styles_still_work(self, mask):
        """Different apps redact differently; all of them must resolve."""
        assert eng._is_masked_identifier(mask) is True
        assert eng._masked_upi_alias_match(mask, REGISTERED) == "raviarvind1111@ybl"


class TestARealHandleIsNotMistakenForAMask:
    @pytest.mark.parametrize("handle", [
        "john.doe@okhdfcbank",
        "raviarvind1111@ybl",
        "a.b@ybl",
        "first.last.name@paytm",
    ])
    def test_single_dots_are_ordinary_in_a_vpa(self, handle):
        """Three in a row is the threshold precisely so that ordinary handles,
        which routinely contain dots, keep being matched on their own terms."""
        assert eng._is_masked_identifier(handle) is False

    def test_a_two_dot_run_is_still_a_real_handle(self):
        assert eng._is_masked_identifier("a..b@ybl") is False


class TestTheSendersMaskStillResolvesToNothing:
    def test_the_payers_handle_does_not_match_the_company(self):
        """.....2761@okaxis is Pavan Kalyan's registered referrer account. It is
        masked too, and it must not resolve against the company's handles --
        separating the two sides is the whole point."""
        assert eng._is_masked_identifier(PRODUCTION_SENDER_UPI) is True
        assert eng._masked_upi_alias_match(PRODUCTION_SENDER_UPI, REGISTERED) == ""


class TestTheWholeReceiptNowClassifies:
    @pytest.fixture
    def registry(self, monkeypatch):
        company = eng._receiver_record(
            "company-j-ravinder-upi", "company", "J Ravinder",
            upi_ids=list(REGISTERED),
            aliases=["Jollu Ravinder", "J RAVINDER"],
            verification_status="VERIFIED", verified_by="test",
        )
        monkeypatch.setattr(eng, "receiver_registry", lambda **_kw: [company])

    def test_the_logged_extraction_resolves_without_conflict(self, registry):
        """The exact field values from the rejection log."""
        result = eng.classify_receiver({
            "receiver_name": "J RAVINDER",
            "receiver_upi_id": PRODUCTION_RECEIVER_UPI,
            "receiver_account": "",
            "receiver_phone_number": "",
        })
        assert result["receiver_identifier_conflict"] is False
        assert result["receiver_type"] == "company"
        assert result["receiver_match"] == "masked_upi_alias"
        assert result["receiver_match_score"] == 100

    def test_google_pay_shape_with_phonepe_label_resolves_without_conflict(self, registry):
        """Sowmya's exact stored receiver field must not be rejected as name-only."""
        result = eng.classify_receiver({
            "receiver_name": "J RAVINDER",
            "receiver_upi_id": PRODUCTION_RECEIVER_UPI_WITH_PROVIDER_LABEL,
            "receiver_account": "",
            "receiver_phone_number": "",
        })
        assert result["receiver_identifier_conflict"] is False
        assert result["receiver_type"] == "company"
        assert result["receiver_match"] == "masked_upi_alias"
        assert result["receiver_match_score"] == 100

    @pytest.mark.parametrize("masked", ["....9999@ybl on PhonePe", "....1111@okaxis on PhonePe"])
    def test_provider_label_does_not_weaken_domain_or_suffix_checks(self, registry, masked):
        result = eng.classify_receiver({
            "receiver_name": "J RAVINDER",
            "receiver_upi_id": masked,
            "receiver_account": "",
            "receiver_phone_number": "",
        })
        assert result["receiver_match"] != "masked_upi_alias"

    def test_an_unregistered_dotted_mask_is_still_refused(self, registry):
        """Recognising dots as a mask must not make every dotted handle pass."""
        result = eng.classify_receiver({
            "receiver_name": "J RAVINDER",
            "receiver_upi_id": ".....9999@ybl",
            "receiver_account": "",
            "receiver_phone_number": "",
        })
        assert result["receiver_match"] != "masked_upi_alias"
