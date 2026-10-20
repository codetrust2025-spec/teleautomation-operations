"""Data Room credential names (20 Oct): one rule, shared with the dashboard.

tests/fixtures/credential_names.json holds the cases; dashboard/src/utils/credentialNames.test.js runs the same cases
against the browser's twin of this rule, so the table and every export always name a credential alike.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from features.credential_names import LOGINS_TYPE, credential_type, standard_credential_name

CASES = json.loads((Path(__file__).parent / "fixtures" / "credential_names.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("stored, service, shown", CASES)
def test_every_case(stored, service, shown):
    assert standard_credential_name(stored, service) == shown


@pytest.mark.parametrize("stored, service, shown", CASES)
def test_naming_twice_changes_nothing(stored, service, shown):
    assert standard_credential_name(shown, service) == shown


def test_the_type_word_can_be_kept():
    assert standard_credential_name("asha rao naukri", "Naukri", drop_type=False) == "Asha Rao Naukri"


@pytest.mark.parametrize("service, login, shown", [
    ("Naukri", False, "Naukri"), ("Gmail", False, "Gmail"), ("Telegram", False, "Telegram"),
    ("Tool", False, LOGINS_TYPE), ("", False, "Not Set"), ("LinkedIn", False, "Not Set"), ("", True, LOGINS_TYPE),
])
def test_types(service, login, shown):
    assert credential_type(service, login=login) == shown
