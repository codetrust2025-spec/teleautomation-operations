import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from features import payment_verification_engine as engine


@pytest.fixture(autouse=True)
def _isolated_receiver_registry(monkeypatch, tmp_path):
    registry_path = tmp_path / "payment_receiver_accounts.json"
    registry_path.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv(
        "PAYMENT_RECEIVER_REGISTRY_FILE",
        str(registry_path),
    )


def _extraction(**patch):
    row = {
        "amount": 5000,
        "receiver_name": "SAMPLE RECEIVER",
        "receiver_upi_id": "company@upi",
        "receiver_phone": "",
        "receiver_account": "",
        "utr_number": "123456789012",
        "transaction_id": "",
        "reference_number": "",
        "payment_date": "2026-07-27",
        "payment_time": "11:40 AM",
        "status": "success",
        "confidence_score": 98,
        "is_payment_screenshot": True,
        "primary_model": "qwen3-vl:8b-instruct",
        "receiver_type": "company",
    }
    row.update(patch)
    return row


def _install_extractor(monkeypatch, value):
    calls = {}

    def fake_extract(_raw, _mime, **kwargs):
        calls.update(kwargs)
        return dict(value)

    monkeypatch.setattr(
        "features.ollama_payment_extract.extract_payment_with_ollama",
        fake_extract,
    )
    return calls


def _write_referrer_registry(tmp_path, *names):
    """Register placeholder referrer names so resolve_referrer() verifies them,
    keeping tests self-contained (no dependency on ambient/real referrer data)."""
    entries = [
        entry if isinstance(entry, tuple) else ("referrer-%d" % i, entry)
        for i, entry in enumerate(names)
    ]
    path = tmp_path / "referrers.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "referrers": [
                    {"id": rid, "name": n, "aliases": [], "is_active": True}
                    for rid, n in entries
                ],
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def test_company_payment_uses_ollama_only_and_posts_credit(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    calls = _install_extractor(monkeypatch, _extraction())

    result = engine.verify_payment_screenshot(
        b"receipt",
        source_module="test_upload",
        expected_amount=5000,
        entity_name="Candidate",
    )

    assert calls["use_ocr"] is False
    assert result["company_payment_verified"] is True
    assert result["receiver_type"] == "company"
    assert result["ledger_action"] == "company_credit"
    assert result["ledger_status"] == "posted"
    assert result["ocr_used"] is False


def test_exact_verified_identifier_overrides_only_model_confidence(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    _install_extractor(monkeypatch, _extraction(confidence_score=20))

    result = engine.verify_payment_screenshot(
        b"low-confidence-exact-receipt",
        source_module="handler_expense_create",
        expected_amount=5000,
        entity_name="SAMPLE RECEIVER",
        purpose="handler_payout",
    )

    assert result["receiver_match"] == "upi"
    assert result["receiver_match_score"] == 100
    assert result["deterministic_verified"] is True
    assert "LOW_EXTRACTION_CONFIDENCE" not in result["reason_codes"]


def test_referrer_sponsored_payment_creates_future_commission_recovery(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps([{"name": "Thrilok", "upi_ids": ["thrilok@upi"]}]),
    )
    # A referrer-sponsored payment verifies only when the receiver account
    # links to a canonical registered referrer, so register it here too.
    monkeypatch.setenv(
        "REFERRER_REGISTRY_FILE", _write_referrer_registry(tmp_path, "Thrilok"
        )
    )
    _install_extractor(
        monkeypatch,
        _extraction(
            receiver_name="Thrilok",
            receiver_upi_id="thrilok@upi",
            receiver_type="referrer",
            utr_number="REFERRER1234",
        ),
    )

    result = engine.verify_payment_screenshot(
        b"sponsored",
        source_module="candidate_payment_proof",
        expected_amount=5000,
        entity_name="Candidate",
        referrer_hint="Thrilok",
        purpose="candidate_payment",
    )

    assert result["referrer_sponsored"] is True
    assert result["ledger_action"] == "referrer_recovery"
    assert result["recover_from_future_commission"] is True
    assert result["verification_state"] == "VERIFIED_REFERRER_PAYMENT"
    assert result["booking_eligible"] is True
    summary = engine.recovery_summary_by_referrer(month="2026-07")
    assert summary["thrilok"]["total"] == 5000
    assert summary["thrilok"]["commission_already_received"] == 2500
    assert summary["thrilok"]["recoverable_company_share"] == 2500


def test_unknown_receiver_is_pending_not_credited(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    _install_extractor(
        monkeypatch,
        _extraction(
            receiver_name="Unregistered Person",
            receiver_upi_id="unknown@upi",
            utr_number="UNKNOWN123456",
        ),
    )

    result = engine.verify_payment_screenshot(
        b"unknown",
        source_module="candidate_payment_proof",
        expected_amount=5000,
    )

    assert result["deterministic_verified"] is False
    assert result["receiver_type"] == "unknown"
    assert result["ledger_action"] == "unknown_pending"
    assert result["ledger_status"] == "pending"


def test_common_ledger_is_idempotent_across_modules(monkeypatch, tmp_path):
    ledger_path = tmp_path / "ledger.json"
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(ledger_path))
    _install_extractor(monkeypatch, _extraction())

    first = engine.verify_payment_screenshot(
        b"same-receipt",
        source_module="public_slot_payment_extract",
    )
    second = engine.verify_payment_screenshot(
        b"same-receipt",
        source_module="public_slot_payment_proof",
    )

    assert first["ledger_entry_id"] == second["ledger_entry_id"]
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert len(data["entries"]) == 1
    assert data["entries"][0]["source_modules"] == [
        "public_slot_payment_extract",
        "public_slot_payment_proof",
    ]


def test_referrer_name_only_is_manual_review(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    _install_extractor(
        monkeypatch,
        _extraction(
            receiver_name="Thrilok",
            receiver_upi_id="",
            receiver_type="referrer",
            utr_number="NAMEONLY1234",
        ),
    )

    result = engine.verify_payment_screenshot(
        b"name-only",
        source_module="candidate_payment_proof",
        expected_amount=5000,
        entity_id="candidate-1",
        entity_name="Candidate",
        referrer_hint="Thrilok",
        purpose="candidate_payment",
    )

    assert result["verification_state"] == "INCOMPLETE_PAYMENT_EVIDENCE"
    assert result["booking_eligible"] is False
    assert "STABLE_RECEIVER_IDENTIFIER_REQUIRED" in result["reason_codes"]


def test_referrer_allocation_uses_configurable_split(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps([{"name": "Referrer One", "upi_ids": ["pawan@upi"]}]),
    )
    monkeypatch.setenv(
        "PAYMENT_COMMISSION_RULES_JSON", json.dumps({"Referrer One": 40})
    )
    monkeypatch.setenv(
        "REFERRER_REGISTRY_FILE", _write_referrer_registry(tmp_path, "Referrer One")
    )
    _install_extractor(
        monkeypatch,
        _extraction(
            amount=20000,
            receiver_name="Referrer One",
            receiver_upi_id="pawan@upi",
            utr_number="PAWAN20000",
        ),
    )

    result = engine.verify_payment_screenshot(
        b"twenty-thousand",
        source_module="public_slot_payment_proof",
        expected_amount=20000,
        entity_id="candidate-20k",
        entity_name="Candidate",
        referrer_hint="Referrer One",
        payment_scope="PROFILE",
    )
    entry = next(
        row
        for row in engine.ledger_entries()
        if row.get("ledger_entry_id") == result["ledger_entry_id"]
    )

    assert entry["referrer_share"] == 8000
    assert entry["amount_already_received_by_referrer"] == 8000
    assert entry["company_share"] == 12000
    assert entry["recoverable_company_share"] == 12000
    assert entry["total_payout_adjustment"] == 20000
    entitlement = engine.entitlement_for_payment(result["payment_id"])
    assert entitlement["reusable"] is True


def test_duplicate_reference_cannot_fund_different_candidate(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    _install_extractor(monkeypatch, _extraction(utr_number="SAME-UTR-100"))
    first = engine.verify_payment_screenshot(
        b"receipt-a",
        source_module="candidate_payment_proof",
        entity_id="candidate-a",
        candidate_id="candidate-a",
    )
    second = engine.verify_payment_screenshot(
        b"receipt-b",
        source_module="candidate_payment_proof",
        entity_id="candidate-b",
        candidate_id="candidate-b",
    )

    assert first["deterministic_verified"] is True
    assert second["verification_state"] == "DUPLICATE_PAYMENT"
    assert second["deterministic_verified"] is False
    assert len(engine.ledger_entries()) == 1


def test_negative_settlement_becomes_carry_forward_not_cash_payment(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps([{"name": "Thrilok", "upi_ids": ["thrilok@upi"]}]),
    )
    # A referrer-sponsored payment verifies only when the receiver account
    # links to a canonical registered referrer, so register it here too.
    monkeypatch.setenv(
        "REFERRER_REGISTRY_FILE", _write_referrer_registry(tmp_path, "Thrilok"
        )
    )
    _install_extractor(
        monkeypatch,
        _extraction(
            receiver_name="Thrilok",
            receiver_upi_id="thrilok@upi",
            utr_number="RECOVERY5000",
        ),
    )
    engine.verify_payment_screenshot(
        b"recovery",
        source_module="candidate_payment_proof",
        expected_amount=5000,
        entity_name="Candidate",
        referrer_hint="Thrilok",
    )

    statement = engine.settlement_statement(
        "Thrilok", month="2026-07", gross_commission=2500
    )
    assert statement["commission_already_received_directly"] == 2500
    assert statement["recoverable_company_share"] == 2500
    assert statement["net_payable"] == -2500
    assert statement["cash_payout"] == 0
    assert statement["carry_forward_receivable"] == 2500


def test_pawan_kalyan_exact_upi_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps(
            [
                {
                    "id": "referrer-pawan-kalyan",
                    "referrer_id": "pawan-kalyan",
                    "name": "Referrer One",
                    "account_holder_name": "SAMPLE REFERRER",
                    "upi_ids": ["referrer@upi"],
                    "verification_status": "VERIFIED",
                    "active": True,
                }
            ]
        ),
    )
    monkeypatch.setenv(
        "REFERRER_REGISTRY_FILE", _write_referrer_registry(tmp_path, "Referrer One")
    )
    _install_extractor(
        monkeypatch,
        _extraction(
            amount=1000,
            receiver_name="SAMPLE REFERRER",
            receiver_upi_id="referrer@upi",
            transaction_id="T2607152036338548026419",
            utr_number="727950697571",
            payment_date="2026-07-15",
            payment_time="08:36 PM",
        ),
    )

    result = engine.verify_payment_screenshot(
        b"pawan-phonepe-receipt",
        source_module="public_slot_payment_proof",
        expected_amount=1000,
        entity_id="candidate-pawan",
        candidate_id="candidate-pawan",
        entity_name="Candidate",
        payment_scope="ROUND",
    )

    assert result["verification_state"] == "VERIFIED_REFERRER_PAYMENT"
    assert result["referrer_id"] == "referrer-0"
    assert result["booking_eligible"] is True
    assert result["ledger_status"] == "posted"
    row = engine.ledger_entries()[0]
    assert row["transaction_type"] == "CANDIDATE_FEE_RECEIVED_BY_REFERRER"
    assert row["commission_already_received_minor"] == 50000
    assert row["company_share_recoverable_minor"] == 50000


@pytest.mark.parametrize("amount", [958, 1000, 20000])
def test_pavan_receiver_classification_does_not_depend_on_amount(
    monkeypatch, tmp_path, amount
):
    monkeypatch.setenv(
        "PAYMENT_VERIFICATION_LEDGER_FILE",
        str(tmp_path / f"ledger-{amount}.json"),
    )
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps(
            [
                {
                    "id": "referrer-pavan-account",
                    "referrer_id": "referrer-pavan-kalyan",
                    "name": "SAMPLE REFERRER",
                    "upi_ids": ["referrer@upi"],
                    "verification_status": "VERIFIED",
                    "active": True,
                }
            ]
        ),
    )
    # A referrer-sponsored payment verifies only when the receiver account
    # links to a canonical registered referrer, so register it here too.
    monkeypatch.setenv(
        "REFERRER_REGISTRY_FILE", _write_referrer_registry(tmp_path, ("referrer-pavan-kalyan", "SAMPLE REFERRER")
        )
    )
    _install_extractor(
        monkeypatch,
        _extraction(
            amount=amount,
            receiver_name="SAMPLE REFERRER",
            receiver_upi_id="REFERRER@UPI",
            transaction_id=f"PAVAN-{amount}",
            utr_number=f"UTR-{amount}",
        ),
    )

    result = engine.verify_payment_screenshot(
        f"pavan-{amount}".encode(),
        source_module="amount_invariance_test",
        expected_amount=amount,
    )

    assert result["verification_state"] == "VERIFIED_REFERRER_PAYMENT"
    assert result["receiver_match"] == "upi"
    assert result["receiver_match_score"] == 100
    assert result["referrer_id"] == "referrer-pavan-kalyan"


def test_name_match_with_upi_mismatch_is_pending(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps([{"name": "Referrer One", "upi_ids": ["pawan@okaxis"]}]),
    )
    _install_extractor(
        monkeypatch,
        _extraction(
            receiver_name="Referrer One",
            receiver_upi_id="different@okaxis",
            utr_number="MISMATCH-UPI",
        ),
    )
    result = engine.verify_payment_screenshot(
        b"mismatch",
        source_module="test",
        expected_amount=5000,
    )
    assert result["verification_state"] == "PENDING_MANUAL_REVIEW"
    assert "RECEIVER_IDENTIFIER_CONFLICT" in result["reason_codes"]


@pytest.mark.parametrize(
    ("registry_patch", "expected_code"),
    [
        ({"active": False}, "RECEIVER_ACCOUNT_INACTIVE"),
        ({"verification_status": "UNVERIFIED"}, "RECEIVER_ACCOUNT_UNVERIFIED"),
    ],
)
def test_inactive_or_unverified_referrer_is_pending(
    monkeypatch, tmp_path, registry_patch, expected_code
):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    record = {"name": "Pawan", "upi_ids": ["pawan@upi"], **registry_patch}
    monkeypatch.setenv("PAYMENT_REFERRER_RECEIVERS_JSON", json.dumps([record]))
    _install_extractor(
        monkeypatch,
        _extraction(receiver_name="Pawan", receiver_upi_id="pawan@upi", utr_number=expected_code),
    )
    result = engine.verify_payment_screenshot(
        expected_code.encode(),
        source_module="test",
        expected_amount=5000,
    )
    assert result["verification_state"] == "PENDING_MANUAL_REVIEW"
    assert expected_code in result["reason_codes"]


def test_duplicate_registry_ownership_is_ambiguous(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps(
            [
                {"id": "ref-1", "name": "One", "upi_ids": ["shared@upi"]},
                {"id": "ref-2", "name": "Two", "upi_ids": ["shared@upi"]},
            ]
        ),
    )
    _install_extractor(
        monkeypatch,
        _extraction(receiver_name="One", receiver_upi_id="shared@upi", utr_number="SHARED"),
    )
    result = engine.verify_payment_screenshot(b"shared", source_module="test")
    assert result["verification_state"] == "PENDING_MANUAL_REVIEW"
    assert "AMBIGUOUS_RECEIVER" in result["reason_codes"]
    assert engine.receiver_registry_conflicts()[0]["identifier"] == "shared@upi"


def test_upi_normalization_is_exact_not_partial(monkeypatch):
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps([{"name": "Pawan", "upi_ids": ["referrer@upi"]}]),
    )
    exact = engine.classify_receiver(
        {"receiver_upi_id": " Referrer @ Upi "}
    )
    partial = engine.classify_receiver({"receiver_upi_id": "ferrer@upi"})
    assert exact["receiver_type"] == "referrer"
    assert exact["receiver_match"] == "upi"
    assert partial["receiver_type"] == "unknown"


@pytest.mark.parametrize("amount", [1000, 5000, 7000, 8000, 9000, 10000, 20000])
def test_default_half_split_for_variable_amounts(monkeypatch, tmp_path, amount):
    monkeypatch.setenv(
        "PAYMENT_VERIFICATION_LEDGER_FILE",
        str(tmp_path / f"ledger-{amount}.json"),
    )
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps([{"name": "Referrer One", "upi_ids": ["pawan@upi"]}]),
    )
    monkeypatch.setenv(
        "REFERRER_REGISTRY_FILE", _write_referrer_registry(tmp_path, "Referrer One")
    )
    _install_extractor(
        monkeypatch,
        _extraction(
            amount=amount,
            receiver_name="Referrer One",
            receiver_upi_id="pawan@upi",
            utr_number=f"AMOUNT-{amount}",
        ),
    )
    result = engine.verify_payment_screenshot(
        str(amount).encode(),
        source_module="test",
        expected_amount=amount,
        entity_id=f"candidate-{amount}",
        payment_scope="PROFILE" if amount == 20000 else "ROUND",
    )
    row = next(r for r in engine.ledger_entries() if r["ledger_entry_id"] == result["ledger_entry_id"])
    assert row["referrer_share_minor"] == amount * 50
    assert row["company_share_recoverable_minor"] == amount * 50
    assert row["total_payout_adjustment_minor"] == amount * 100


def test_failed_and_low_confidence_payments_do_not_book(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    calls = {"count": 0}

    def fake_extract(*_args, **_kwargs):
        calls["count"] += 1
        return _extraction(
            status="failed" if calls["count"] == 1 else "success",
            confidence_score=40,
            utr_number=f"STATE-{calls['count']}",
        )

    monkeypatch.setattr(
        "features.ollama_payment_extract.extract_payment_with_ollama",
        fake_extract,
    )
    failed = engine.verify_payment_screenshot(b"failed", source_module="test")
    low = engine.verify_payment_screenshot(b"low", source_module="test")
    assert failed["verification_state"] == "FAILED_PAYMENT"
    assert low["verification_state"] == "PENDING_MANUAL_REVIEW"
    assert not failed["booking_eligible"] and not low["booking_eligible"]


def test_extraction_failure_is_retriable_not_unknown_receiver(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    _install_extractor(
        monkeypatch,
        {
            **_extraction(),
            "amount": 0,
            "is_payment_screenshot": False,
            "status": "unknown",
            "confidence_score": 0,
            "extraction_source": "vision_failed",
            "warnings": ["All configured Ollama Vision models failed"],
        },
    )
    result = engine.verify_payment_screenshot(b"failure", source_module="test")
    assert result["verification_state"] == "EXTRACTION_FAILED"
    assert result["ledger_status"] == "rejected"


def test_same_hash_with_changed_reference_cannot_create_second_payment(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    counter = {"value": 0}

    def fake_extract(*_args, **_kwargs):
        counter["value"] += 1
        return _extraction(utr_number=f"HASH-{counter['value']}")

    monkeypatch.setattr(
        "features.ollama_payment_extract.extract_payment_with_ollama",
        fake_extract,
    )
    first = engine.verify_payment_screenshot(
        b"identical-image",
        source_module="extract",
        entity_id="candidate-1",
    )
    second = engine.verify_payment_screenshot(
        b"identical-image",
        source_module="proof",
        entity_id="candidate-1",
    )
    assert first["payment_id"] == second["payment_id"]
    data = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert len(data["payments"]) == 1
    assert len(data["entries"]) == 1


def test_round_entitlement_is_single_use_and_retry_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    _install_extractor(monkeypatch, _extraction(utr_number="ROUND-ONE"))
    result = engine.verify_payment_screenshot(
        b"round",
        source_module="test",
        entity_id="candidate",
        candidate_id="candidate",
        payment_scope="ROUND",
    )
    first = engine.consume_entitlement(
        result["entitlement_id"], source_entity_id="slot-1"
    )
    retry = engine.consume_entitlement(
        result["entitlement_id"], source_entity_id="slot-1"
    )
    blocked = engine.consume_entitlement(
        result["entitlement_id"], source_entity_id="slot-2"
    )
    assert first["status"] == "CONSUMED"
    assert retry["usage_count"] == 1
    assert blocked is None


def test_ledger_save_failure_does_not_return_booking_eligibility(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    _install_extractor(monkeypatch, _extraction(utr_number="SAVE-FAIL"))
    monkeypatch.setattr(engine, "_save_ledger", lambda _data: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError, match="disk"):
        engine.verify_payment_screenshot(b"save-fail", source_module="test")


def test_concurrent_same_transaction_posts_once(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    _install_extractor(monkeypatch, _extraction(utr_number="CONCURRENT-ONE"))

    def run(_index):
        return engine.verify_payment_screenshot(
            b"same",
            source_module="test",
            entity_id="candidate",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))
    assert len({row["ledger_entry_id"] for row in results}) == 1
    assert len(engine.ledger_entries()) == 1


@pytest.mark.parametrize("amount", [1500, 5000, 10000, 15000, 30500, 31000])
def test_thrilok_exact_full_phone_is_amount_independent(
    monkeypatch, tmp_path, amount
):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps(
            [
                {
                    "id": "thrilok-phone",
                    "name": "SAMPLE REFERRER TWO",
                    "referrer_id": "referrer-thrilok",
                    "payment_phone_number": "+91 90000 00110",
                    "verification_status": "VERIFIED",
                }
            ]
        ),
    )
    # A referrer-sponsored payment verifies only when the receiver account
    # links to a canonical registered referrer, so register it here too.
    monkeypatch.setenv(
        "REFERRER_REGISTRY_FILE", _write_referrer_registry(tmp_path, ("referrer-thrilok", "SAMPLE REFERRER TWO")
        )
    )
    _install_extractor(
        monkeypatch,
        _extraction(
            amount=amount,
            direction="PAID_TO",
            receiver_name="SAMPLE REFERRER TWO",
            receiver_upi_id="",
            receiver_phone_number="9000000110",
            utr_number=f"THRILOK{amount}",
        ),
    )

    result = engine.verify_payment_screenshot(
        f"thrilok-{amount}".encode(),
        source_module="public_slot_payment_proof",
        expected_amount=amount,
        entity_id=f"candidate-{amount}",
        entity_name="Candidate",
        referrer_hint="Thrilok",
        payment_scope="ROUND",
    )

    assert result["verification_state"] == "VERIFIED_REFERRER_PAYMENT"
    assert result["matched_referrer_id"] == "referrer-thrilok"
    assert result["receiver_match"] == "phone"
    assert result["booking_eligible"] is True


@pytest.mark.parametrize("amount", [5000, 25000])
def test_venugopal_exact_full_phone_is_amount_independent(
    monkeypatch, tmp_path, amount
):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps(
            [
                {
                    "id": "venugopal-phone",
                    "name": "SAMPLE REFERRER ONE",
                    "referrer_id": "referrer-venugopal",
                    "payment_phone_number": "+919000000002",
                    "verification_status": "VERIFIED",
                }
            ]
        ),
    )
    # A referrer-sponsored payment verifies only when the receiver account
    # links to a canonical registered referrer, so register it here too.
    monkeypatch.setenv(
        "REFERRER_REGISTRY_FILE", _write_referrer_registry(tmp_path, ("referrer-venugopal", "SAMPLE REFERRER ONE")
        )
    )
    _install_extractor(
        monkeypatch,
        _extraction(
            amount=amount,
            direction="PAID_TO",
            receiver_name="SAMPLE REFERRER ONE",
            receiver_upi_id="",
            receiver_phone_number="+91 90000 00002",
            utr_number=f"VENUGOPAL{amount}",
        ),
    )

    result = engine.verify_payment_screenshot(
        f"venugopal-{amount}".encode(),
        source_module="public_slot_payment_proof",
        expected_amount=amount,
        entity_id=f"candidate-v-{amount}",
        entity_name="Candidate",
        referrer_hint="Venugopal",
        payment_scope="ROUND",
    )

    assert result["verification_state"] == "VERIFIED_REFERRER_PAYMENT"
    assert result["matched_referrer_id"] == "referrer-venugopal"


def test_masked_phone_is_incomplete_and_never_auto_approved(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps(
            [
                {
                    "id": "thrilok-phone",
                    "name": "SAMPLE REFERRER TWO",
                    "referrer_id": "referrer-thrilok",
                    "payment_phone_number": "+919000000003",
                    "verification_status": "VERIFIED",
                }
            ]
        ),
    )
    _install_extractor(
        monkeypatch,
        _extraction(
            direction="PAID_TO",
            receiver_name="SAMPLE REFERRER TWO",
            receiver_upi_id="",
            receiver_phone_number="******5810",
            utr_number="MASKED5810",
        ),
    )

    result = engine.verify_payment_screenshot(
        b"masked-thrilok",
        source_module="public_slot_payment_proof",
        expected_amount=5000,
        entity_id="candidate-masked",
        referrer_hint="Thrilok",
    )

    assert result["verification_state"] == "INCOMPLETE_PAYMENT_EVIDENCE"
    assert result["booking_eligible"] is False


def test_received_from_sender_maps_only_credited_to_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps(
            [
                {
                    "id": "thrilok-upi",
                    "name": "SAMPLE REFERRER TWO",
                    "referrer_id": "referrer-thrilok",
                    "upi_id": "thrilok@upi",
                    "verification_status": "VERIFIED",
                },
                {
                    "id": "ravinder-upi",
                    "name": "Sample Referrer Three",
                    "referrer_id": "referrer-ravinder",
                    "upi_id": "referrer3@upi",
                    "verification_status": "VERIFIED",
                },
            ]
        ),
    )
    _install_extractor(
        monkeypatch,
        _extraction(
            direction="RECEIVED_FROM",
            receiver_name="SAMPLE REFERRER TWO",
            receiver_upi_id="thrilok@upi",
            credited_to_identifier="referrer3@upi",
            utr_number="DIRECTION123",
        ),
    )

    result = engine.verify_payment_screenshot(
        b"received-from",
        source_module="public_slot_payment_proof",
        expected_amount=5000,
        entity_id="candidate-direction",
    )

    assert result["verification_state"] == "VERIFIED_REFERRER_PAYMENT"
    assert result["receiver_registry_name"] == "Sample Referrer Three"
    assert result["matched_referrer_id"] == "referrer-ravinder"
    assert result["receiver_registry_name"] != "SAMPLE REFERRER TWO"


def test_received_from_preserves_explicit_receiver_when_both_parties_extracted(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv(
        "PAYMENT_REFERRER_RECEIVERS_JSON",
        json.dumps(
            [
                {
                    "id": "pavan-upi",
                    "name": "SAMPLE REFERRER",
                    "referrer_id": "referrer-pavan-kalyan",
                    "upi_id": "referrer@upi",
                    "verification_status": "VERIFIED",
                }
            ]
        ),
    )
    # A referrer-sponsored payment verifies only when the receiver account
    # links to a canonical registered referrer, so register it here too.
    monkeypatch.setenv(
        "REFERRER_REGISTRY_FILE", _write_referrer_registry(tmp_path, ("referrer-pavan-kalyan", "SAMPLE REFERRER")
        )
    )
    _install_extractor(
        monkeypatch,
        _extraction(
            direction="RECEIVED_FROM",
            sender_name="SAMPLE SENDER",
            sender_upi_id="sender@upi",
            receiver_name="SAMPLE REFERRER",
            receiver_upi_id="referrer@upi",
            credited_to_identifier="State Bank of India 4485",
            transaction_id="484653160050",
            utr_number="",
            confidence_score=75,
        ),
    )

    result = engine.verify_payment_screenshot(
        b"received-from-with-both-parties",
        source_module="handler_expense_create",
        expected_amount=5000,
        entity_name="Referrer One",
        referrer_hint="Referrer One",
        purpose="expense_reimbursement",
    )

    assert result["receiver_registry_name"] == "SAMPLE REFERRER"
    assert result["receiver_match"] == "upi"
    assert result["verification_state"] == "VERIFIED_REFERRER_PAYMENT"
    assert result["deterministic_verified"] is True
    assert "LOW_EXTRACTION_CONFIDENCE" not in result["reason_codes"]


def test_reversal_preserves_original_and_appends_audit_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    _install_extractor(monkeypatch, _extraction(utr_number="REVERSE-ME"))
    result = engine.verify_payment_screenshot(b"reverse", source_module="test")
    reversal = engine.reverse_ledger_entry(
        result["ledger_entry_id"],
        actor="admin",
        reason="bank reversal",
    )
    rows = engine.ledger_entries()
    assert len(rows) == 2
    assert reversal["transaction_type"] == "REVERSAL"
    assert reversal["reversal_of_entry_id"] == result["ledger_entry_id"]
