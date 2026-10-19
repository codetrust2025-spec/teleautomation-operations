"""Data Room service accounts: their service, and their passwords.

Every account is Naukri, Gmail or Telegram, decided only by what the account itself says -- never by its username's
email domain -- and an account that says nothing is left for a person. Passwords are encrypted at rest, never in a
list response, read one at a time on purpose, and still exported exactly. Driven against a real store in a temporary
directory and through the real routes.
"""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import data_room
from features import data_room_credentials_store as creds

# The shapes production holds (names, addresses and secrets invented; production's Naukri logins are on Gmail
# addresses, which the fixture guard keeps out of test data): Naukri logins with no service recorded, one Telegram
# account, one with nothing to go on, and one Gmail account named as such.
LEGACY = [
    {"id": "asha_rao_naukri", "label": "Asha Rao Naukri", "service": "", "username": "asha.rao@example.com",
     "password": "Asha-Pass-1 ", "notes": "resume refreshed weekly", "has_image": True, "image_ext": ".png"},
    {"id": "ravi_kumar_naukri", "label": "ravi kumar naukri", "service": "", "username": "ravi@example.com", "password": "Ravi=2"},
    {"id": "telegram_login_number", "label": "Telegram login number", "service": "Telegram", "username": "+91 90000 00000",
     "password": "tg-3", "import_batch": "whatsapp-import-2026-10-05", "source_verified": "supplied dataset"},
    {"id": "someone_gmail_com", "label": "", "service": "", "username": "someone@example.com", "password": "nothing-to-go-on"},
    {"id": "ops_gmail", "label": "Ops mailbox", "service": "gmail", "username": "ops@example.com", "password": "ops-5",
     "notes": ""},
]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "data_room" / "credentials.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"service_accounts": [dict(row) for row in LEGACY]}), encoding="utf-8")
    monkeypatch.setattr(creds, "_FILE", str(path))
    monkeypatch.setenv("MAILBOX_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return path


def on_disk(path) -> dict:
    return {row["id"]: row for row in json.loads(path.read_text(encoding="utf-8"))["service_accounts"]}


class TestClassification:
    @pytest.mark.parametrize("row, service", [
        ({"label": "Bhavana Naukri", "username": "bhavana@example.com"}, "Naukri"),
        ({"label": "kashaboina rajashekar naukri"}, "Naukri"),
        ({"label": "Telegram login number"}, "Telegram"),
        ({"label": "Ops", "notes": "the Gmail inbox for alerts"}, "Gmail"),
        ({"label": "Asha", "service": "TELEGRAM"}, "Telegram"),                    # a recorded service wins, tidied
        ({"label": "Naukri profile", "service": "Gmail"}, "Gmail"),               # ... even over the name
    ])
    def test_from_what_the_account_says(self, row, service):
        assert creds.classify_service(row) == service

    @pytest.mark.parametrize("row", [
        {"label": "", "username": "gmail.user@example.com"},                     # the username is never read
        {"label": "Ops", "username": "naukri.ops@example.com"},                  # not even a service named in it
        {"label": "Naukri via Gmail"},                                            # two services named: not decided
        {"label": "Naukrigmail"},                                                 # whole words only
    ])
    def test_never_from_the_username_and_never_a_guess(self, row):
        assert creds.classify_service(row) == ""


class TestTheMigration:
    def test_classifies_encrypts_and_keeps_everything_else(self, store):
        before = on_disk(store)
        report = creds.migrate_service_accounts()
        after = on_disk(store)
        assert report["accounts"] == 5 and report["still_plain"] == 0 and report["encrypted_now"] == 5
        assert report["by_service"] == {"Naukri": 2, "Gmail": 1, "Telegram": 1, "not_set": 1}
        assert set(after) == set(before)                                          # same ids, no duplicate, none lost
        assert {rid: row["service"] for rid, row in after.items()} == {
            "asha_rao_naukri": "Naukri", "ravi_kumar_naukri": "Naukri", "telegram_login_number": "Telegram",
            "someone_gmail_com": "", "ops_gmail": "Gmail"}
        text = store.read_text(encoding="utf-8")
        for row in LEGACY:
            assert row["password"] not in text                                    # no password left in the clear
            assert creds.service_account_password(row["id"]) == row["password"]   # and each decrypts exactly
        for rid, row in before.items():                                           # notes, images, provenance kept
            kept = {k: v for k, v in row.items() if k not in ("password", "service")}
            assert {k: after[rid][k] for k in kept} == kept

    def test_running_it_again_changes_nothing(self, store):
        creds.migrate_service_accounts()
        first = store.read_text(encoding="utf-8")
        report = creds.migrate_service_accounts()
        assert report["classified"] == 0 and report["encrypted_now"] == 0
        assert {r["id"]: r for r in json.loads(first)["service_accounts"]} == on_disk(store)

    def test_without_a_key_nothing_is_lost_and_nothing_is_half_done(self, store, monkeypatch):
        monkeypatch.delenv("MAILBOX_CREDENTIAL_ENCRYPTION_KEY")
        report = creds.migrate_service_accounts()
        assert report["still_plain"] == 5 and on_disk(store)["asha_rao_naukri"]["password"] == "Asha-Pass-1 "


class TestTheList:
    def test_carries_no_password_only_whether_there_is_one(self, store):
        creds.migrate_service_accounts()
        rows = {r["id"]: r for r in creds.get_credentials()["service_accounts"]}
        dumped = json.dumps(rows)
        assert "password\"" not in dumped.replace("has_password\"", "") and "password_enc" not in dumped
        assert all(row["has_password"] for row in rows.values())
        assert rows["asha_rao_naukri"]["service"] == "Naukri" and rows["asha_rao_naukri"]["has_image"] is True
        assert rows["someone_gmail_com"]["service"] == ""

    def test_withholds_passwords_even_before_the_migration_has_run(self, store):
        assert all("password" not in r for r in creds.get_credentials()["service_accounts"])


class TestAddingAndEditing:
    def test_a_new_account_is_naukri_unless_another_service_is_chosen(self, store):
        creds.create_service_account({"id": "new_one", "label": "New One", "username": "n@example.com", "password": "pw-new"})
        creds.create_service_account({"id": "tg_two", "label": "Second", "username": "+91", "password": "pw", "service": "Telegram"})
        rows = on_disk(store)
        assert rows["new_one"]["service"] == "Naukri" and rows["tg_two"]["service"] == "Telegram"
        assert "password" not in rows["new_one"] and creds.service_account_password("new_one") == "pw-new"

    def test_only_the_three_services(self, store):
        _, error = creds.create_service_account({"id": "x", "label": "X", "service": "LinkedIn"})
        assert "Naukri, Gmail, Telegram" in error and "x" not in on_disk(store)
        _, error = creds.update_service_account("ravi_kumar_naukri", {"service": ""})
        assert error

    def test_an_edit_changes_the_service_and_keeps_the_password_unless_a_new_one_is_typed(self, store):
        creds.migrate_service_accounts()
        creds.update_service_account("someone_gmail_com", {"service": "Gmail", "label": "Someone"})
        creds.update_service_account("asha_rao_naukri", {"password": "", "notes": "updated"})
        rows = on_disk(store)
        assert rows["someone_gmail_com"]["service"] == "Gmail" and rows["someone_gmail_com"]["label"] == "Someone"
        assert creds.service_account_password("asha_rao_naukri") == "Asha-Pass-1 " and rows["asha_rao_naukri"]["notes"] == "updated"
        creds.update_service_account("asha_rao_naukri", {"password": "Brand-New-9"})
        assert creds.service_account_password("asha_rao_naukri") == "Brand-New-9"
        assert "Brand-New-9" not in store.read_text(encoding="utf-8")

    def test_any_other_write_path_still_never_stores_a_password_in_the_clear(self, store):
        creds.migrate_service_accounts()
        creds.update_vault_item("service_accounts", "ravi_kumar_naukri", {"password": "via-generic", "password_enc": None})
        assert "via-generic" not in store.read_text(encoding="utf-8")
        assert creds.service_account_password("ravi_kumar_naukri") == "via-generic"


# ---------------------------------------------------------------------------------------------
# Through the routes
# ---------------------------------------------------------------------------------------------

@pytest.fixture()
def api(store, monkeypatch):
    from core import dashboard_access, recruitment_mail_store

    state = {"profile": {"username": "admin", "role": "admin"}, "audits": []}
    monkeypatch.setattr(dashboard_access, "operator_profile", lambda request: state["profile"])
    monkeypatch.setattr(data_room, "_data_room_admin_only", lambda request: bool(state["profile"].get("username")))
    monkeypatch.setattr(dashboard_access, "require_fleet_admin", lambda request: state["profile"])
    monkeypatch.setattr(recruitment_mail_store, "audit", lambda **kw: state["audits"].append(kw))
    creds.migrate_service_accounts()
    app = FastAPI()
    app.include_router(data_room.router)
    return TestClient(app), state


class TestTheRoutes:
    def test_the_list_route_carries_no_password(self, api):
        client, _ = api
        body = client.get("/data-room/credentials").json()
        assert all("password" not in row and "password_enc" not in row for row in body["credentials"]["service_accounts"])
        assert "Asha-Pass-1" not in json.dumps(body)

    def test_one_password_is_read_on_purpose_exactly_and_recorded(self, api):
        client, state = api
        res = client.get("/data-room/credentials/service-accounts/asha_rao_naukri/password")
        assert res.status_code == 200 and res.json()["password"] == "Asha-Pass-1 " and res.headers["cache-control"] == "no-store"
        assert state["audits"][-1]["action"] == "data_room_password_viewed" and state["audits"][-1]["source_id"] == "asha_rao_naukri"
        assert "Asha-Pass-1" not in json.dumps(state["audits"])
        assert client.get("/data-room/credentials/service-accounts/nobody/password").status_code == 404
        state["profile"] = {"username": None, "role": None}
        assert client.get("/data-room/credentials/service-accounts/asha_rao_naukri/password").status_code == 403

    def test_adding_and_editing_through_the_form_routes(self, api, store):
        client, _ = api
        res = client.post("/data-room/credentials/vault/service_accounts",
                          json={"id": "form_new", "label": "Form New", "username": "f@example.com", "password": "form-pw"})
        assert res.json()["status"] == "ok"
        added = next(r for r in res.json()["credentials"]["service_accounts"] if r["id"] == "form_new")
        assert added["service"] == "Naukri" and "password" not in added
        res = client.patch("/data-room/credentials/vault/service_accounts/form_new", json={"service": "Gmail"})
        assert res.json()["status"] == "ok" and on_disk(store)["form_new"]["service"] == "Gmail"
        assert creds.service_account_password("form_new") == "form-pw"
        bad = client.patch("/data-room/credentials/vault/service_accounts/form_new", json={"service": "Facebook"})
        assert bad.json()["status"] == "error"
        assert client.patch("/data-room/credentials/vault/service_accounts/nobody", json={"label": "x"}).json()["status"] == "error"

    def test_the_admin_export_still_carries_every_password_exactly(self, api):
        client, _ = api
        text = client.get("/data-room/credentials/service-accounts/export?format=txt").content.decode("utf-8")
        assert text.count("Account Name: ") == len(LEGACY)
        for row in LEGACY:
            assert f"Password: {row['password']}" in text
