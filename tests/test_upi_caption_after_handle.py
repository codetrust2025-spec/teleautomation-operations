"""A receiver handle followed by the app's caption is still checked (11 Oct).

A Rs 5,000 PhonePe receipt to the company was read as
"....1111@ybl on PhonePe". The engine joined every word into the handle, so the
provider became "yblonphonepe", the visible digits were never compared with
the registered "...1111@ybl", and the receipt sat at "incomplete evidence".
The provider is now the first word after the "@". Handles are invented.
"""
from __future__ import annotations

import pytest

from features import payment_verification_engine as engine
from tests.test_company_receiver_upi_registration import (  # noqa: F401 - shared harness
    _isolated_registry,
    _receipt,
    _register_company,
    _verify,
)

REGISTERED = "companyacct1111@ybl"


@pytest.mark.parametrize("read, expected", [
    ("....1111@ybl on PhonePe", "....1111@ybl"),
    ("XXXXXX1111@ybl", "xxxxxx1111@ybl"),
    ("companyacct1111@ybl", "companyacct1111@ybl"),
    ("company acct @ ybl", "companyacct@ybl"),
    ("1111@ybl.", "1111@ybl"),
    ("no handle here", "nohandlehere"),
    ("", ""),
])
def test_the_provider_is_the_first_word_after_the_at(read, expected):
    assert engine._norm_upi(read) == expected


def test_a_masked_handle_with_its_caption_matches_the_company_account(monkeypatch):
    _register_company(monkeypatch, REGISTERED)
    result = _verify(monkeypatch, _receipt(receiver_upi_id="....1111@ybl on PhonePe", amount=5000))
    assert result["receiver_type"] == "company"
    assert result["receiver_match"] == "masked_upi_alias"
    assert result["verification_state"] == "VERIFIED_COMPANY_PAYMENT"


def test_a_caption_does_not_make_other_digits_match(monkeypatch):
    _register_company(monkeypatch, REGISTERED)
    result = _verify(monkeypatch, _receipt(receiver_upi_id="....9999@ybl on PhonePe", amount=5000))
    assert result["verification_state"] != "VERIFIED_COMPANY_PAYMENT"
