"""A refusal has to leave something behind, and it must not be the receipt.

Diagnosing the ₹5,000 Ravinder refusal meant probing the engine with facts read
off a screenshot by eye: the upload was never stored (by policy), and the
detailed refusal line the code already wrote went to a module logger that
production's container log does not carry. These pin the record that replaces
that reconstruction, and the masking that keeps it safe to keep.
"""
from __future__ import annotations

import json

import pytest

from features import payment_refusal_log as refusals


@pytest.fixture(autouse=True)
def log_file(monkeypatch, tmp_path):
    path = tmp_path / "payment_refusals.jsonl"
    monkeypatch.setenv("PAYMENT_REFUSAL_LOG_FILE", str(path))
    return path


REFUSED = {
    "receiver_type": "company", "receiver_match": "name", "receiver_match_score": 90,
    "receiver_registry_id": "company", "receiver_name": "JOLLU RAVINDER",
    "receiver_upi_id": "XXXXXX4573@yescred", "receiver_phone_number": "+918639074573",
    "receiver_account": "123456789012", "receiver_identifier_masked": True,
    "amount": 5000, "status": "success", "utr_number": "269080108616",
    "transaction_id": "T2609161245508194570195", "confidence_score": 98,
    "is_payment_screenshot": True, "primary_model": "qwen3-vl:8b-instruct",
    "analysed_by": "RTX 4060",
}


def record(**patch):
    return refusals.record_refusal(
        source_module="public_slot_payment_proof",
        verification_state="INCOMPLETE_PAYMENT_EVIDENCE",
        reason_codes=["STABLE_RECEIVER_IDENTIFIER_REQUIRED"],
        result={**REFUSED, **patch}, elapsed_ms=21700, evidence_id="evidence-1")


class TestWhatItRecords:
    def test_the_decision_is_enough_to_explain_itself(self):
        entry = record()

        assert entry["verification_state"] == "INCOMPLETE_PAYMENT_EVIDENCE"
        assert entry["reason_codes"] == ["STABLE_RECEIVER_IDENTIFIER_REQUIRED"]
        assert entry["receiver"]["match"] == "name" and entry["receiver"]["score"] == 90
        assert entry["extraction"]["amount"] == 5000
        assert entry["extraction"]["status"] == "success"
        assert entry["extraction"]["has_utr"] is True
        assert entry["extraction"]["has_transaction_id"] is True
        assert entry["ai"]["node"] == "RTX 4060" and entry["ai"]["elapsed_ms"] == 21700
        assert entry["evidence_id"] == "evidence-1"

    def test_a_missing_reference_is_visible_as_such(self):
        entry = record(utr_number="", transaction_id="", reference_number="")

        assert entry["extraction"]["has_utr"] is False
        assert entry["extraction"]["has_transaction_id"] is False

    def test_it_is_readable_back(self):
        record()
        record(amount=15000)

        rows = refusals.recent_refusals(10)
        assert [row["extraction"]["amount"] for row in rows] == [5000, 15000]


class TestWhatItRefusesToKeep:
    def test_identifiers_are_masked_to_provider_and_tail(self):
        entry = record()

        assert entry["receiver"]["upi"] == "…4573@yescred"
        assert entry["receiver"]["phone"] == "…4573"
        assert entry["receiver"]["account"] == "…9012"

    def test_no_full_identifier_survives_anywhere_in_the_record(self, log_file):
        record()

        written = log_file.read_text(encoding="utf-8")
        for secret in ("+918639074573", "8639074573", "123456789012", "XXXXXX4573@yescred"):
            assert secret not in written

    def test_the_payee_name_is_reduced_to_whether_one_was_read(self):
        entry = record()

        assert entry["receiver"]["name_seen"] is True
        assert "RAVINDER" not in json.dumps(entry)

    def test_nothing_of_the_screenshot_is_kept(self, log_file):
        record()
        assert "image" not in log_file.read_text(encoding="utf-8").lower()

    @pytest.mark.parametrize("value,expected", [
        ("raviarvind1111@ybl", "…1111@ybl"),
        ("abc@ybl", "…@ybl"),
        ("+91 86390 74573", "…4573"),
        ("", ""),
        (None, ""),
    ])
    def test_masking_keeps_only_what_a_registry_check_needs(self, value, expected):
        assert refusals.mask_identifier(value) == expected


class TestItNeverCostsAPayment:
    def test_an_unwritable_path_does_not_raise(self, monkeypatch, tmp_path):
        """A file where a directory should be: the write fails, the payment
        decision does not."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        monkeypatch.setenv("PAYMENT_REFUSAL_LOG_FILE", str(blocker / "refusals.jsonl"))

        assert record() is None

    def test_the_file_never_grows_without_bound(self, log_file):
        for index in range(refusals.MAX_ENTRIES + 25):
            record(amount=index)

        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == refusals.MAX_ENTRIES
        assert json.loads(lines[-1])["extraction"]["amount"] == refusals.MAX_ENTRIES + 24
