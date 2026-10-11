"""The admin password is no longer kept as typed anywhere the app writes (11 Oct audit).

Handler passwords were already scrypt hashes everywhere. The admin's was not:
the Data Room copy (data_room/credentials.json, on the data volume and in its
backups) held it as typed, the Data Room API sent it to the page, and a
password changed from the dashboard was saved as typed in the override file.

    - the Data Room copy is hashed on first read or at startup, once, and the
      API returns no admin password at all;
    - a change from the dashboard stores only a hash, logins and the admin
      re-check verify against it, and a file written before keeps working;
    - nothing is rotated: the same password still signs in.

Passwords here are invented.
"""
from __future__ import annotations

import json

import pytest

from core import dashboard_auth_vps as auth
from core.password_hashing import is_hashed, verify_password
from features import data_room_credentials_store as dr


@pytest.fixture
def vault(monkeypatch, tmp_path):
    path = tmp_path / "data_room" / "credentials.json"
    path.parent.mkdir()
    path.write_text(json.dumps({
        "site_url": "https://ops.example.test",
        "admin": {"username": "operations_admin", "password": "typed-admin-secret", "role": "admin",
                  "reference": "Full dashboard"},
        "handlers": [{"username": "handler1", "password": "scrypt$16384$8$1$c2FsdA$a2V5", "role": "handler"}],
        "interview_data": [{"kept": "untouched"}],
    }), encoding="utf-8")
    monkeypatch.setattr(dr, "_FILE", str(path))
    return path


def stored(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_data_room_copy_is_hashed_and_never_returned(vault):
    payload = dr.get_credentials()
    assert "password" not in payload["admin"]
    assert payload["admin"]["username"] == "operations_admin"
    data = stored(vault)
    assert is_hashed(data["admin"]["password"])
    assert verify_password("typed-admin-secret", data["admin"]["password"])
    assert "typed-admin-secret" not in vault.read_text(encoding="utf-8")
    # Everything else is left as it was.
    assert data["handlers"][0]["password"] == "scrypt$16384$8$1$c2FsdA$a2V5"
    assert data["interview_data"] == [{"kept": "untouched"}]


def test_startup_hashes_it_once_and_then_leaves_the_file_alone(vault):
    assert dr.hash_stored_admin_password() is True
    first = vault.read_text(encoding="utf-8")
    assert dr.hash_stored_admin_password() is False
    assert vault.read_text(encoding="utf-8") == first


def test_a_new_admin_password_reaches_the_copy_as_a_hash(vault):
    dr.sync_admin_login_copy({"username": "operations_admin", "password": "next-admin-secret"})
    saved = stored(vault)["admin"]["password"]
    assert is_hashed(saved) and verify_password("next-admin-secret", saved)


@pytest.fixture
def admin(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_USERNAME", "operations_admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "deployment-password")
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", "independent-session-secret")
    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    override = tmp_path / "auth" / "dashboard_admin.json"
    monkeypatch.setattr(auth, "_ADMIN_CREDENTIAL_OVERRIDE_FILE", str(override))
    monkeypatch.setattr(dr, "sync_admin_login_copy", lambda updates: ({}, None))
    return override


def test_a_dashboard_password_change_is_stored_as_a_hash_and_still_signs_in(admin):
    assert auth.change_operator_password("operations_admin", "deployment-password", "changed-password") is None
    text = admin.read_text(encoding="utf-8")
    assert "changed-password" not in text
    assert is_hashed(json.loads(text)["password_hash"])
    assert auth.verify_credentials("operations_admin", "changed-password") is True
    assert auth.verify_credentials("operations_admin", "deployment-password") is False
    assert auth.admin_password_matches("changed-password") is True
    assert auth.admin_password_matches("wrong") is False
    # And it can be changed again from the hashed state.
    assert auth.change_operator_password("operations_admin", "changed-password", "third-password") is None
    assert auth.verify_credentials("operations_admin", "third-password") is True


def test_an_override_file_written_before_hashing_still_signs_in(admin):
    admin.parent.mkdir(parents=True)
    admin.write_text(json.dumps({
        "username": "operations_admin", "password": "older-typed-password",
        "environment_password_sha256": auth._environment_password_fingerprint("deployment-password"),
    }), encoding="utf-8")
    assert auth.verify_credentials("operations_admin", "older-typed-password") is True
    assert auth.verify_credentials("operations_admin", "deployment-password") is False


def test_the_deployment_password_still_signs_in_with_no_override(admin):
    assert auth.verify_credentials("operations_admin", "deployment-password") is True
    assert auth.admin_password_matches("deployment-password") is True
    assert auth.admin_password_matches("") is False
