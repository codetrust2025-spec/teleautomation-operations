"""Consent attestations are optional, explicit, identity-bound and never guessed."""
from __future__ import annotations

from features import data_room_credentials_store as store
from features import daybreak_credential_sync as sync


def test_no_implicit_consent():
    fields, error = store._service_account_fields(
        {"label": "Example", "service": "Naukri", "username": "example@test.com"},
        creating=True, consent_actor="operator")
    assert not error
    assert not fields.get("daybreak_consent_at")
    assert not sync.has_attested_daybreak_consent(fields)


def test_consent_attestation_bound_to_exact_email_and_authenticated_actor():
    fields, error = store._service_account_fields(
        {"service": "Naukri", "username": "Example@TEST.com ",
         "daybreak_consent_confirmed": True},
        creating=True, consent_actor="admin")
    assert not error
    assert fields["daybreak_consent_email"] == "example@test.com"
    assert fields["daybreak_consent_by"] == "admin"
    assert sync.has_attested_daybreak_consent(fields)
    assert not sync.has_attested_daybreak_consent({**fields, "username": "someoneelse@test.com"})
    assert not sync.has_attested_daybreak_consent({**fields, "daybreak_consent_by": ""})


def test_consent_revocation_and_invalid_attestations():
    revoked, error = store._service_account_fields(
        {"daybreak_consent_confirmed": False}, creating=False)
    assert not error
    assert revoked["daybreak_consent_at"] is None
    assert revoked["daybreak_consent_email"] is None

    for payload in (
        {"service": "Gmail", "username": "person@test.com", "daybreak_consent_confirmed": True},
        {"service": "Naukri", "username": "not-an-email", "daybreak_consent_confirmed": True},
        {"service": "Naukri", "username": "person@test.com", "daybreak_consent_confirmed": "true"},
    ):
        _, error = store._service_account_fields(payload, creating=True, consent_actor="admin")
        assert error

    _, error = store._service_account_fields(
        {"service": "Naukri", "username": "person@test.com",
         "daybreak_consent_confirmed": True},
        creating=True, consent_actor="")
    assert error
