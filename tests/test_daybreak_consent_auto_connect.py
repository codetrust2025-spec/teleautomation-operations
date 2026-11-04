"""Only explicitly verified Naukri consent can unlock automatic connection."""
from __future__ import annotations

from features import data_room_credentials_store as creds
from features import daybreak_credential_sync as sync
from services import daybreak_credential_auto_sync as auto


def test_new_naukri_account_does_not_implicitly_consent():
    fields, error = creds._service_account_fields(
        {"service": "Naukri", "username": "person@example.test"}, creating=True,
        consent_actor="admin")
    assert error is None
    assert sync.has_attested_daybreak_consent(fields) is False


def test_verified_consent_is_bound_to_exact_email_and_admin():
    fields, error = creds._service_account_fields(
        {"service": "Naukri", "username": "Person@Example.test ",
         "daybreak_consent_confirmed": True},
        creating=True, consent_actor="admin")
    assert error is None
    assert fields["daybreak_consent_email"] == "person@example.test"
    assert fields["daybreak_consent_by"] == "admin"
    assert sync.has_attested_daybreak_consent(fields)
    assert not sync.has_attested_daybreak_consent({**fields, "username": "different@example.test"})
    assert not sync.has_attested_daybreak_consent({**fields, "daybreak_consent_by": ""})


def test_untrusted_or_non_naukri_consent_is_rejected():
    cases = [
        {"service": "Naukri", "username": "not-an-email", "daybreak_consent_confirmed": True},
        {"service": "Gmail", "username": "person@example.test", "daybreak_consent_confirmed": True},
        {"service": "Naukri", "username": "person@example.test", "daybreak_consent_confirmed": "true"},
    ]
    for row in cases:
        _, error = creds._service_account_fields(row, creating=True, consent_actor="admin")
        assert error
    _, error = creds._service_account_fields(
        {"service": "Naukri", "username": "person@example.test",
         "daybreak_consent_confirmed": True}, creating=True, consent_actor="")
    assert error


def test_consented_account_enters_delivery_after_daybreak_receives_attestation():
    view = {"workers": [{"enabled": True, "recipient": True, "approved": True,
                         "key_problem": None}],
            "accounts": [{"account_id": "new", "synced": True,
                          "profile": {"enabled": True}, "attention_codes": [],
                          "delivery": None}]}
    assert auto.plan(view) == (["new"], None)
    view["accounts"][0]["attention_codes"] = ["consent_missing"]
    assert auto.plan(view) == ([], None)


def test_revoke_consent_clears_source_attestation():
    row, error = creds._service_account_fields(
        {"service": "Naukri", "username": "person@example.test",
         "daybreak_consent_confirmed": False}, creating=True, consent_actor="admin")
    assert error is None
    assert not sync.has_attested_daybreak_consent(row)
    assert row["daybreak_consent_at"] is None


def test_five_minute_sync_delivers_newly_authorized_account_without_password_in_roster(monkeypatch, tmp_path):
    monkeypatch.setattr(auto, "DATA_DIR", str(tmp_path))
    source = {
        "id": "fresh", "label": "Fresh Candidate", "username": "fresh@example.test",
        "service": "Naukri", "password_enc": "secret-must-not-appear",
        "daybreak_consent_at": "2026-11-04T00:00:00Z",
        "daybreak_consent_email": "fresh@example.test", "daybreak_consent_by": "admin",
    }
    calls = []
    deliveries = []
    monkeypatch.setattr(sync, "authoritative_naukri_accounts", lambda: [source])
    monkeypatch.setattr(sync, "_daybreak", lambda action, payload: (
        calls.append((action, payload)) or
        {"status": "ok", "sourceCount": 1, "matchedCount": 1}))
    monkeypatch.setattr(sync, "refresh", lambda **kwargs: {})
    monkeypatch.setattr(sync, "status_view", lambda: {
        "workers": [{"enabled": True, "approved": True, "recipient": True, "key_problem": None}],
        "accounts": [{"account_id": "fresh", "synced": True,
                      "profile": {"enabled": True}, "attention_codes": [],
                      "delivery": None}],
    })
    monkeypatch.setattr(sync, "deliver", lambda ids, *, actor: (
        deliveries.append((ids, actor)) or {"delivered": ids, "refused": []}))
    assert auto.sync_once()["delivered"] == 1
    assert calls[0][1]["accounts"][0]["consentVerified"] is True
    assert "secret-must-not-appear" not in str(calls)
    assert deliveries == [(["fresh"], "automatic-data-room-sync")]
