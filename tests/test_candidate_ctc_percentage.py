import pytest

from features.candidate_store import (
    _normalise,
    slot_booking_payment_block_reason,
    validate_profile_ctc_percentage,
)


def test_profile_service_requires_ctc_percentage():
    with pytest.raises(ValueError, match="CTC is required"):
        validate_profile_ctc_percentage(
            {"service_type": "profile_service", "ctc_percentage": ""},
        )


def test_profile_service_ctc_is_validated_and_persisted():
    value = validate_profile_ctc_percentage(
        {"service_type": "profile_service", "ctc_percentage": "8.5"},
    )
    row = _normalise(
        {"name": "Anjali", "service_type": "profile_service", "ctc_percentage": value},
    )

    assert value == 8.5
    assert row["ctc_percentage"] == 8.5


def test_round_wise_does_not_require_or_store_ctc_percentage():
    record = {"name": "Candidate", "service_type": "round_wise", "ctc_percentage": ""}

    assert validate_profile_ctc_percentage(record) == ""
    assert _normalise(record)["ctc_percentage"] == ""


def test_dropped_profile_service_does_not_require_ctc_percentage():
    assert validate_profile_ctc_percentage(
        {"stage": "dropped", "service_type": "profile_service", "ctc_percentage": ""},
    ) == ""


def test_dropped_profile_service_preserves_valid_existing_ctc_percentage():
    assert validate_profile_ctc_percentage(
        {"stage": "dropped", "service_type": "profile_service", "ctc_percentage": ""},
        existing={"ctc_percentage": 12},
    ) == 12


@pytest.mark.parametrize("value", ["0", "-1", "100.1", "invalid"])
def test_invalid_profile_ctc_is_rejected(value):
    with pytest.raises(ValueError):
        validate_profile_ctc_percentage(
            {"service_type": "profile_service", "ctc_percentage": value},
        )


def test_round_wise_booking_requires_a_payment_proof_even_without_a_saved_balance(monkeypatch):
    monkeypatch.setattr("features.candidate_store.merged_balance_due_for_name", lambda _name: 0)

    assert slot_booking_payment_block_reason("New client") is None
    assert slot_booking_payment_block_reason(
        "New client",
        require_payment_proof=True,
    ) == "Upload a verified payment screenshot before booking this round."
