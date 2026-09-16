"""Registering the company receiver's real UPI handle, without weakening matching.

Production ran with ``COMPANY_PAYMENT_UPI_IDS`` unset, so
``configured_company_upi_ids()`` fell through to the placeholder
``company@upi`` (features/company_payment_verification.py). Every genuine
PhonePe receipt paid to the company owner therefore showed a receiver name that
matched the registry ("J Ravinder") next to a UPI handle that did not, which is
exactly the ``receiver_identifier_conflict`` case: a visible, valid identifier
suppresses the name-only fallback on purpose, so the payment resolved to an
unknown receiver and the booking was refused.

That refusal is the anti-fraud rule working. Nothing here relaxes it. These
tests pin the two halves of the contract:

  * an identifier that IS registered for the company verifies, and a split
    payment made of several such receipts adds up to the fee;
  * an identifier that is NOT registered still fails, even when the receiver
    name looks right, and near-miss handles are never folded together.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.public_slot_api import install_public_slot_routes
from features import candidate_store as cs
from features import payment_verification_engine as engine
from features import pending_slot_payment as pending

# The approved company receiver, as configured in production. Registering it is
# a configuration act; the matching rules below are unchanged.
COMPANY_NAME = "J Ravinder"
COMPANY_ALIAS = "Jollu Ravinder"
APPROVED_UPI = "raviarvind1111@ybl"
PLACEHOLDER_UPI = "company@upi"
FEE = 5000


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch, tmp_path):
    """No ambient referrer/company data leaks into these assertions."""
    registry = tmp_path / "payment_receiver_accounts.json"
    registry.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(registry))
    monkeypatch.setenv("REFERRER_REGISTRY_FILE", str(tmp_path / "referrers.json"))
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))


def _register_company(monkeypatch, *upi_ids: str) -> None:
    """Configure the company receiver through the authoritative env config."""
    monkeypatch.setenv("COMPANY_PAYMENT_UPI_IDS", ",".join(upi_ids))
    monkeypatch.setenv(
        "COMPANY_PAYMENT_RECEIVER_NAMES", f"{COMPANY_NAME},{COMPANY_ALIAS}"
    )


def _receipt(**patch):
    """A genuine PhonePe receipt paid to the company owner."""
    row = {
        "amount": 2000,
        "receiver_name": COMPANY_NAME,
        "receiver_upi_id": APPROVED_UPI,
        "receiver_phone": "",
        "receiver_account": "",
        "utr_number": "787230090653",
        "transaction_id": "T26081815525497955624761",
        "reference_number": "",
        "payment_date": "2026-08-18",
        "payment_time": "03:53 PM",
        "status": "success",
        "confidence_score": 98,
        "is_payment_screenshot": True,
        "primary_model": "qwen3-vl:8b-instruct",
    }
    row.update(patch)
    return row


def _install_extractor(monkeypatch, value):
    monkeypatch.setattr(
        "features.ollama_payment_extract.extract_payment_with_ollama",
        lambda _raw, _mime, **_kw: dict(value),
    )


def _verify(monkeypatch, receipt):
    _install_extractor(monkeypatch, receipt)
    return engine.verify_payment_screenshot(
        b"screenshot-bytes",
        "image/jpeg",
        source_module="test_company_receiver_upi_registration",
        expected_amount=0,
        entity_name="Some Candidate",
        purpose="candidate_payment",
        payment_scope="ROUND",
        create_ledger=False,
    )


# ── registration makes the real handle verify ────────────────────────────────


class TestApprovedHandleAccepted:
    def test_registered_company_upi_is_accepted(self, monkeypatch):
        _register_company(monkeypatch, APPROVED_UPI)
        result = _verify(monkeypatch, _receipt())

        assert result["receiver_type"] == "company"
        assert result["receiver_match"] == "upi"
        assert result["receiver_identifier_conflict"] is False
        assert result["verification_state"] == "VERIFIED_COMPANY_PAYMENT"
        assert result["reason_codes"] == []
        assert result["booking_eligible"] is True

    def test_classify_receiver_reports_the_company_record(self, monkeypatch):
        _register_company(monkeypatch, APPROVED_UPI)
        receiver = engine.classify_receiver(
            {"receiver_name": COMPANY_NAME, "receiver_upi_id": APPROVED_UPI}
        )

        assert receiver["receiver_type"] == "company"
        assert receiver["receiver_match"] == "upi"
        assert receiver["receiver_account_active"] is True
        assert receiver["receiver_account_verified"] is True
        assert receiver["receiver_identifier_conflict"] is False

    def test_alias_name_on_the_same_registered_handle_is_accepted(self, monkeypatch):
        """The bank name sometimes prints as the alias; the handle still rules."""
        _register_company(monkeypatch, APPROVED_UPI)
        result = _verify(monkeypatch, _receipt(receiver_name=COMPANY_ALIAS))

        assert result["receiver_match"] == "upi"
        assert result["booking_eligible"] is True


# ── the defect that caused the rejection ─────────────────────────────────────


class TestUnregisteredHandleStillRejected:
    def test_placeholder_only_config_rejects_the_real_handle(self, monkeypatch):
        """Reproduces production before the fix: only the placeholder registered."""
        _register_company(monkeypatch, PLACEHOLDER_UPI)
        result = _verify(monkeypatch, _receipt())

        assert result["receiver_type"] == "unknown"
        assert result["receiver_identifier_conflict"] is True
        assert "RECEIVER_IDENTIFIER_CONFLICT" in result["reason_codes"]
        assert result["booking_eligible"] is False

    def test_matching_name_with_a_different_upi_is_still_refused(self, monkeypatch):
        """The whole point of the rule: a right-looking name cannot carry a
        stranger's handle into an approval."""
        _register_company(monkeypatch, APPROVED_UPI)
        result = _verify(
            monkeypatch, _receipt(receiver_upi_id="attacker9999@ybl")
        )

        assert result["receiver_type"] == "unknown"
        assert result["receiver_identifier_conflict"] is True
        assert "RECEIVER_IDENTIFIER_CONFLICT" in result["reason_codes"]
        assert result["booking_eligible"] is False

    def test_placeholder_is_not_allowlisted_once_the_real_handle_is_configured(
        self, monkeypatch
    ):
        """`configured or {DEFAULT}` means configuring real values removes the
        placeholder entirely; a payment to company@upi must stop verifying."""
        _register_company(monkeypatch, APPROVED_UPI)

        from features.company_payment_verification import configured_company_upi_ids

        assert configured_company_upi_ids() == {APPROVED_UPI}
        assert PLACEHOLDER_UPI not in configured_company_upi_ids()

        result = _verify(monkeypatch, _receipt(receiver_upi_id=PLACEHOLDER_UPI))
        assert result["booking_eligible"] is False
        assert result["receiver_identifier_conflict"] is True

    def test_near_miss_handles_are_never_folded_together(self, monkeypatch):
        """OCR confuses l/I/1 and o/0. Normalization must not paper over that:
        each of these is a different account."""
        _register_company(monkeypatch, APPROVED_UPI)
        for lookalike in (
            "raviarvind1111@ybI",   # capital I instead of l
            "raviarvindllll@ybl",   # letters instead of digits
            "raviarvind111@ybl",    # one digit short
            "raviarvind11111@ybl",  # one digit extra
            "raviarvind1111@yb1",   # digit instead of l
        ):
            receiver = engine.classify_receiver(
                {"receiver_name": COMPANY_NAME, "receiver_upi_id": lookalike}
            )
            assert receiver["receiver_match"] != "upi", lookalike
            assert receiver["receiver_type"] == "unknown", lookalike


# ── normalization is safe, not lax ───────────────────────────────────────────


class TestNormalizationOfTheApprovedHandle:
    @pytest.mark.parametrize(
        "printed",
        [
            "raviarvind1111@ybl",
            "RaviArvind1111@YBL",
            "RAVIARVIND1111@YBL",
            "  raviarvind1111@ybl  ",
            "raviarvind1111 @ ybl",
            "ravi arvind1111@ybl",
            "\traviarvind1111@ybl\n",
        ],
    )
    def test_case_and_spacing_variants_resolve_to_the_company(
        self, monkeypatch, printed
    ):
        _register_company(monkeypatch, APPROVED_UPI)
        receiver = engine.classify_receiver(
            {"receiver_name": COMPANY_NAME, "receiver_upi_id": printed}
        )

        assert receiver["receiver_match"] == "upi", printed
        assert receiver["receiver_type"] == "company", printed
        assert receiver["receiver_identifier_conflict"] is False, printed

    def test_configuration_side_is_normalized_the_same_way(self, monkeypatch):
        """A handle typed into config with stray case/space still matches."""
        _register_company(monkeypatch, "  RaviArvind1111@YBL ")
        receiver = engine.classify_receiver(
            {"receiver_name": COMPANY_NAME, "receiver_upi_id": APPROVED_UPI}
        )

        assert receiver["receiver_match"] == "upi"
        assert receiver["receiver_type"] == "company"

    def test_receiver_name_casing_and_spacing_is_normalized(self, monkeypatch):
        _register_company(monkeypatch, APPROVED_UPI)
        for printed_name in ("j ravinder", "J  RAVINDER", " J Ravinder "):
            receiver = engine.classify_receiver(
                {"receiver_name": printed_name, "receiver_upi_id": APPROVED_UPI}
            )
            assert receiver["receiver_type"] == "company", printed_name


# ── existing protections stay intact ─────────────────────────────────────────


class TestExistingProtectionsIntact:
    def test_failed_transaction_is_never_eligible(self, monkeypatch):
        _register_company(monkeypatch, APPROVED_UPI)
        result = _verify(monkeypatch, _receipt(status="failed"))

        assert result["booking_eligible"] is False
        assert "TRANSACTION_FAILED" in result["reason_codes"]

    def test_missing_transaction_reference_is_never_eligible(self, monkeypatch):
        _register_company(monkeypatch, APPROVED_UPI)
        result = _verify(
            monkeypatch, _receipt(utr_number="", transaction_id="", reference_number="")
        )

        assert result["booking_eligible"] is False
        assert "TRANSACTION_REFERENCE_MISSING" in result["reason_codes"]

    def test_low_extraction_confidence_is_never_eligible(self, monkeypatch):
        _register_company(monkeypatch, APPROVED_UPI)
        result = _verify(monkeypatch, _receipt(confidence_score=40))

        assert result["booking_eligible"] is False
        assert "LOW_EXTRACTION_CONFIDENCE" in result["reason_codes"]

    def test_non_receipt_image_is_never_eligible(self, monkeypatch):
        _register_company(monkeypatch, APPROVED_UPI)
        result = _verify(monkeypatch, _receipt(is_payment_screenshot=False))

        assert result["booking_eligible"] is False

    def test_a_mask_the_registry_can_check_and_does_not_match_is_a_conflict(
        self, monkeypatch
    ):
        """PhonePe prints XXXXXX4573@ybl, and this used to fall back to the name.

        That was right while masks were unmatchable: a redaction is not a
        disagreement, and manufacturing a conflict from one refused genuine
        payments. It stopped being right once masks began resolving against the
        registry. With an @ybl handle on file for this payee, the digits the
        mask leaves visible *are* checkable, and 4573 is not 1111 -- so the
        receipt names an account this payee is not known to hold. Crediting it
        on the name alone would accept a payment to any account whose owner
        shares a registered name.

        The operational answer is to register the handle, which is what was
        done in production: both raviarvind1111@ybl and xxxxxx4573@ybl are on
        file there, and this receipt resolves at score 100 through
        masked_upi_alias rather than through the name.
        """
        _register_company(monkeypatch, APPROVED_UPI)
        receiver = engine.classify_receiver(
            {"receiver_name": COMPANY_NAME, "receiver_upi_id": "XXXXXX4573@ybl"}
        )

        assert receiver["receiver_identifier_masked"] is True
        assert receiver["receiver_identifier_conflict"] is True
        assert receiver["receiver_match"] != "name"

    def test_registering_the_second_handle_resolves_it(self, monkeypatch):
        """Production's actual configuration, and the intended remedy."""
        _register_company(monkeypatch, APPROVED_UPI, "xxxxxx4573@ybl")
        receiver = engine.classify_receiver(
            {"receiver_name": COMPANY_NAME, "receiver_upi_id": "XXXXXX4573@ybl"}
        )

        assert receiver["receiver_type"] == "company"
        assert receiver["receiver_match"] == "masked_upi_alias"
        assert receiver["receiver_identifier_conflict"] is False

    def test_a_mask_the_registry_cannot_check_still_falls_back_to_the_name(
        self, monkeypatch
    ):
        """No handle on that provider means nothing to contradict, so the
        payee's name remains the only evidence there is -- and still counts."""
        _register_company(monkeypatch, APPROVED_UPI)
        receiver = engine.classify_receiver(
            {"receiver_name": COMPANY_NAME, "receiver_upi_id": "XXXXXX4573@okhdfcbank"}
        )

        assert receiver["receiver_identifier_conflict"] is False
        assert receiver["receiver_type"] == "company"
        assert receiver["receiver_match"] == "name"


# ── the real split payment, end to end ───────────────────────────────────────

PHONE = "9876543210"


def _client(monkeypatch, tmp_path, receipts: dict[bytes, dict]) -> TestClient:
    """Endpoint wired to the REAL receiver validation; only extraction is stubbed."""
    candidate_file = tmp_path / "candidates.json"
    candidate_file.write_text(json.dumps({"candidates": []}), encoding="utf-8")
    monkeypatch.setattr(cs, "_FILE", str(candidate_file))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr(pending, "PENDING_PAYMENT_DIR", str(tmp_path / "pending"))
    monkeypatch.setattr(
        pending, "PENDING_PAYMENT_INDEX", str(tmp_path / "pending" / "index.json")
    )
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)
    # Each screenshot reads back as its own transfer. verify_payment_screenshot
    # itself is NOT stubbed, so classify_receiver really runs per instalment.
    monkeypatch.setattr(
        "features.ollama_payment_extract.extract_payment_with_ollama",
        lambda image, _mime, **_kw: dict(receipts[image]),
    )
    monkeypatch.setattr(
        "features.ollama_payment_extract.generate_payment_narrative",
        lambda *_a, **_kw: "Verified payment",
    )
    monkeypatch.setattr(
        "features.payment_proof_validator.validate_interview_invite",
        lambda *_a, **_kw: (True, ""),
    )
    app = FastAPI()
    install_public_slot_routes(app)
    return TestClient(app)


# The three genuine receipts: 2,000 + 2,000 + 1,000 = 5,000, distinct UTRs.
REAL_INSTALMENTS = {
    b"pay-3-53-pm": _receipt(
        amount=2000, utr_number="787230090653",
        transaction_id="T26081815525497955624761", payment_time="03:53 PM",
    ),
    b"pay-9-13-am": _receipt(
        amount=2000, utr_number="577518842055",
        transaction_id="T26081809133541351574991", payment_time="09:13 AM",
    ),
    b"pay-9-16-am": _receipt(
        amount=1000, utr_number="126725048729",
        transaction_id="T26081809165191055435761", payment_time="09:16 AM",
    ),
}


def _upload(client, files, existing=""):
    return client.post(
        "/public/slots/payment-proof",
        data={
            "name": "Venkat",
            "service_type": "round_wise",
            "phone": PHONE,
            "technology": "Java",
            "interview_round": "L1",
            "existing_proof_ids": existing,
        },
        files=files,
    )


class TestApprovedReceiverSplitPayment:
    def test_two_thousand_two_thousand_one_thousand_reaches_the_fee(
        self, monkeypatch, tmp_path
    ):
        _register_company(monkeypatch, APPROVED_UPI)
        client = _client(monkeypatch, tmp_path, REAL_INSTALMENTS)

        response = _upload(
            client,
            [
                ("files", ("pay1.jpg", b"pay-3-53-pm", "image/jpeg")),
                ("files", ("pay2.jpg", b"pay-9-13-am", "image/jpeg")),
                ("files", ("pay3.jpg", b"pay-9-16-am", "image/jpeg")),
            ],
        )

        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["rejected"] == []
        assert body["proof_count"] == 3
        assert body["verified_total"] == FEE
        assert body["amount_due"] == FEE
        assert body["remaining_due"] == 0
        assert body["payment_complete"] is True

    def test_confirm_accepts_the_booking_on_those_three_proofs(
        self, monkeypatch, tmp_path
    ):
        _register_company(monkeypatch, APPROVED_UPI)
        client = _client(monkeypatch, tmp_path, REAL_INSTALMENTS)
        upload = _upload(
            client,
            [
                ("files", ("pay1.jpg", b"pay-3-53-pm", "image/jpeg")),
                ("files", ("pay2.jpg", b"pay-9-13-am", "image/jpeg")),
                ("files", ("pay3.jpg", b"pay-9-16-am", "image/jpeg")),
            ],
        )
        assert upload.status_code == 200
        proof_ids = upload.json()["proof_ids"]
        assert len(proof_ids) == 3

        confirm = client.post(
            "/bookings/confirm",
            data={
                "name": "Venkat",
                "service_type": "round_wise",
                "phone": PHONE,
                "technology": "Java",
                "interview_round": "L1",
                "date": "2026-09-20",
                "time": "15:00",
                "time_end": "16:00",
                "payment_proof_ids": ",".join(proof_ids),
                "idempotency_key": "venkat-approved-receiver-split",
            },
            files={"file": ("invite.jpg", b"interview-invite", "image/jpeg")},
        )

        assert confirm.status_code == 200, confirm.json()
        assert confirm.json()["status"] == "ok"

    def test_instalments_short_of_the_fee_still_cannot_book(
        self, monkeypatch, tmp_path
    ):
        _register_company(monkeypatch, APPROVED_UPI)
        client = _client(monkeypatch, tmp_path, REAL_INSTALMENTS)
        upload = _upload(
            client, [("files", ("pay1.jpg", b"pay-3-53-pm", "image/jpeg"))]
        )

        assert upload.status_code == 200
        assert upload.json()["verified_total"] == 2000
        assert upload.json()["payment_complete"] is False

        confirm = client.post(
            "/bookings/confirm",
            data={
                "name": "Venkat",
                "service_type": "round_wise",
                "phone": PHONE,
                "technology": "Java",
                "interview_round": "L1",
                "date": "2026-09-20",
                "time": "15:00",
                "payment_proof_ids": ",".join(upload.json()["proof_ids"]),
                "idempotency_key": "venkat-short-split",
            },
            files={"file": ("invite.jpg", b"interview-invite", "image/jpeg")},
        )
        assert confirm.status_code == 400
        assert confirm.json()["payment_due"] is True

    def test_split_payment_to_an_unregistered_handle_is_refused(
        self, monkeypatch, tmp_path
    ):
        """Same three amounts, handle not registered: none may be saved."""
        _register_company(monkeypatch, PLACEHOLDER_UPI)
        client = _client(monkeypatch, tmp_path, REAL_INSTALMENTS)

        response = _upload(
            client,
            [
                ("files", ("pay1.jpg", b"pay-3-53-pm", "image/jpeg")),
                ("files", ("pay2.jpg", b"pay-9-13-am", "image/jpeg")),
                ("files", ("pay3.jpg", b"pay-9-16-am", "image/jpeg")),
            ],
        )

        assert response.status_code == 400
        body = response.json()
        assert body["verified_total"] == 0
        assert len(body["rejected"]) == 3
        assert "does not match" in " ".join(
            item["message"] for item in body["rejected"]
        )


class TestDuplicateProtectionsIntact:
    def test_the_same_screenshot_twice_is_not_counted_twice(
        self, monkeypatch, tmp_path
    ):
        _register_company(monkeypatch, APPROVED_UPI)
        client = _client(monkeypatch, tmp_path, REAL_INSTALMENTS)

        response = _upload(
            client,
            [
                ("files", ("pay1.jpg", b"pay-3-53-pm", "image/jpeg")),
                ("files", ("pay1-again.jpg", b"pay-3-53-pm", "image/jpeg")),
            ],
        )

        assert response.status_code == 200
        body = response.json()
        assert body["verified_total"] == 2000
        assert body["proof_count"] == 1
        assert any(
            "same transaction" in item["message"] for item in body["rejected"]
        )

    def test_a_reused_utr_on_a_different_image_is_not_counted_twice(
        self, monkeypatch, tmp_path
    ):
        """Cropping or re-exporting changes the bytes but not the transaction."""
        _register_company(monkeypatch, APPROVED_UPI)
        receipts = dict(REAL_INSTALMENTS)
        receipts[b"pay-3-53-pm-cropped"] = _receipt(
            amount=2000,
            utr_number="787230090653",
            transaction_id="T26081815525497955624761",
        )
        client = _client(monkeypatch, tmp_path, receipts)

        response = _upload(
            client,
            [
                ("files", ("pay1.jpg", b"pay-3-53-pm", "image/jpeg")),
                ("files", ("pay1-crop.jpg", b"pay-3-53-pm-cropped", "image/jpeg")),
            ],
        )

        assert response.status_code == 200
        body = response.json()
        assert body["verified_total"] == 2000
        assert body["proof_count"] == 1

    def test_three_distinct_utrs_are_each_counted_once(self, monkeypatch, tmp_path):
        _register_company(monkeypatch, APPROVED_UPI)
        client = _client(monkeypatch, tmp_path, REAL_INSTALMENTS)

        first = _upload(
            client, [("files", ("pay1.jpg", b"pay-3-53-pm", "image/jpeg"))]
        )
        ids = first.json()["proof_ids"]
        second = _upload(
            client,
            [
                ("files", ("pay2.jpg", b"pay-9-13-am", "image/jpeg")),
                ("files", ("pay3.jpg", b"pay-9-16-am", "image/jpeg")),
            ],
            existing=",".join(ids),
        )

        assert second.status_code == 200
        assert second.json()["verified_total"] == FEE
        assert second.json()["proof_count"] == 3
        assert second.json()["payment_complete"] is True


# ── the CRED handle: a second provider for the same approved receiver ────────
#
# Production, 2026-09-16: a genuine ₹5,000 PhonePe receipt paid to
# "JOLLU RAVINDER" at a masked ``XXXXXX4573@yescred`` was refused as
# "More Payment Details Required". The probe against the live engine returned
# receiver_match='name' at score 90 -- the payee name matched, no identifier
# could be checked, and a name-only match is deliberately not creditable.
#
# The company holds accounts at more than one provider. Only ``…1111@ybl`` was
# registered, so nothing could confirm the CRED account belonged to the company.
# Registering that account is a configuration act; every rule below is unchanged,
# and the refusals are pinned harder than the acceptance.

CRED_UPI = "jolluravinder4573@yescred"      # shaped like the real handle; the
                                            # real VPA is registered in the env
MASKED_CRED = "XXXXXX4573@yescred"


def _cred_receipt(**patch):
    values = {
        "amount": 5000,
        "receiver_name": "JOLLU RAVINDER",
        "receiver_upi_id": MASKED_CRED,
        "utr_number": "269080108616",
        "transaction_id": "T2609161245508194570195",
    }
    values.update(patch)
    return _receipt(**values)


class TestTheCredHandleOnceRegistered:
    def test_the_five_thousand_receipt_verifies(self, monkeypatch):
        _register_company(monkeypatch, APPROVED_UPI, CRED_UPI)

        result = _verify(monkeypatch, _cred_receipt())

        assert result["verification_state"] == "VERIFIED_COMPANY_PAYMENT"
        assert result["booking_eligible"] is True
        assert result["receiver_match"] == "masked_upi_alias"
        assert result["receiver_match_score"] == 100

    def test_the_handle_it_resolved_to_is_the_registered_one(self, monkeypatch):
        _register_company(monkeypatch, APPROVED_UPI, CRED_UPI)
        assert engine._masked_upi_alias_match(MASKED_CRED, (APPROVED_UPI, CRED_UPI)) == CRED_UPI

    def test_the_older_ybl_handle_still_verifies_too(self, monkeypatch):
        """Registering a second account must not unregister the first."""
        _register_company(monkeypatch, APPROVED_UPI, CRED_UPI)

        result = _verify(monkeypatch, _receipt(receiver_upi_id="XXXXXX1111@ybl"))

        assert result["verification_state"] == "VERIFIED_COMPANY_PAYMENT"


class TestTheRefusalsThatMustSurvive:
    def test_without_registration_the_same_receipt_is_still_refused(self, monkeypatch):
        """The production bug, pinned as intended behaviour: the name matches,
        the identifier cannot be checked, and nothing is credited."""
        _register_company(monkeypatch, APPROVED_UPI)

        result = _verify(monkeypatch, _cred_receipt())

        assert result["verification_state"] == "INCOMPLETE_PAYMENT_EVIDENCE"
        assert result["booking_eligible"] is not True
        assert result["receiver_match"] == "name"

    def test_the_company_name_alone_never_credits_a_payment(self, monkeypatch):
        """Someone else can be called Ravinder. The name is not an identifier."""
        _register_company(monkeypatch, APPROVED_UPI, CRED_UPI)

        result = _verify(monkeypatch, _cred_receipt(receiver_upi_id="", receiver_phone="", receiver_account=""))

        assert result["booking_eligible"] is not True
        assert result["verification_state"] == "INCOMPLETE_PAYMENT_EVIDENCE"

    def test_a_contradicting_tail_at_the_registered_provider_is_refused(self, monkeypatch):
        """Same provider, same name, different account: the digits the mask
        left are evidence, and they disagree."""
        _register_company(monkeypatch, APPROVED_UPI, CRED_UPI)

        result = _verify(monkeypatch, _cred_receipt(receiver_upi_id="XXXXXX9999@yescred"))

        assert result["booking_eligible"] is not True

    def test_an_unregistered_provider_is_still_refused(self, monkeypatch):
        _register_company(monkeypatch, APPROVED_UPI, CRED_UPI)

        result = _verify(monkeypatch, _cred_receipt(receiver_upi_id="XXXXXX4573@okaxis"))

        assert result["booking_eligible"] is not True

    def test_an_unmasked_handle_nobody_registered_is_refused(self, monkeypatch):
        _register_company(monkeypatch, APPROVED_UPI, CRED_UPI)

        result = _verify(monkeypatch, _cred_receipt(receiver_upi_id="someone.else@okicici"))

        assert result["booking_eligible"] is not True


class TestOneIdentifierBelongsToOneOwner:
    """A company identifier registered again under a referrer makes every future
    receipt to it ambiguous, and an ambiguous receiver is refused -- so a real,
    approved account silently stops being creditable. It is refused at
    registration instead."""

    def test_a_referrer_cannot_take_a_company_upi(self, monkeypatch):
        from features import referrer_registry

        _register_company(monkeypatch, APPROVED_UPI, CRED_UPI)
        referrer_registry.materialize_current_referrers(actor="test")
        referrer = referrer_registry.list_referrers()[0] if referrer_registry.list_referrers() else None
        if referrer is None:
            pytest.skip("no referrer available in this isolated registry")

        with pytest.raises(ValueError, match="company receiver identifier"):
            referrer_registry.add_payment_account(
                referrer["id"], {"upi_id": CRED_UPI, "account_holder_name": "Someone"}, actor="test")

    def test_an_unrelated_referrer_account_is_still_allowed(self, monkeypatch):
        from features import referrer_registry

        _register_company(monkeypatch, APPROVED_UPI, CRED_UPI)
        referrer_registry.materialize_current_referrers(actor="test")
        referrers = referrer_registry.list_referrers()
        if not referrers:
            pytest.skip("no referrer available in this isolated registry")

        added = referrer_registry.add_payment_account(
            referrers[0]["id"], {"upi_id": "different.person@ybl", "account_holder_name": "Someone"}, actor="test")

        assert added["id"]
