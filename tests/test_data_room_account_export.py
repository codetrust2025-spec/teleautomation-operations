"""Download All Accounts (Data Room -> Accounts).

The ordinary export (Excel, CSV, text) never carries a password. Passwords leave only in an encrypted Excel workbook,
only for the admin by role, only after the admin's own password is entered again, never unrecorded, and wrong
passwords lock that export. Driven through the real routes with the stores and the session replaced.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import data_room
from features import data_room_account_export as export

ACCOUNTS = [
    {"id": "karthik", "label": "Karthik Gmail (current)", "service": "", "username": "karthik@example.com",
     "password": "Pa55-karthik-SECRET", "notes": "recovery phone 9000000101"},
    {"id": "naukri", "label": "Naukri legacy", "service": "Naukri", "username": "ops@example.com", "password": "naukri-SECRET-2"},
    {"id": "formula", "label": "=HYPERLINK(\"http://x\")", "service": "Gmail", "username": "+91 trick", "password": ""},
]
SECRETS = ("Pa55-karthik-SECRET", "naukri-SECRET-2")
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def sheet_rows(data: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return [[t.text or "" for t in row.iterfind(".//m:t", NS)] for row in root.iterfind(".//m:row", NS)]


class TestTheRows:
    def test_name_type_username_and_status_as_the_table_shows_them(self):
        rows = export.account_rows(ACCOUNTS)
        assert rows[0] == {"name": "Karthik Gmail (current)", "type": "Gmail", "username": "karthik@example.com", "status": "Active"}
        assert rows[1]["status"] == "Old" and rows[1]["type"] == "Naukri"     # "legacy" in the name, as on the table

    def test_no_password_unless_the_protected_export_asks(self):
        assert all("password" not in row for row in export.account_rows(ACCOUNTS))
        assert export.account_rows(ACCOUNTS, with_passwords=True)[0]["password"] == "Pa55-karthik-SECRET"


class TestTheFormats:
    def test_csv_opens_in_excel_as_utf8_and_runs_no_formula(self):
        data = export.render(export.account_rows(ACCOUNTS), "csv")
        assert data.startswith("\ufeff".encode("utf-8"))
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        assert rows[0] == ["Account name", "Type", "Username", "Status"]
        assert rows[3][0] == "'=HYPERLINK(\"http://x\")" and rows[3][2] == "'+91 trick"
        assert not any(s in data.decode("utf-8") for s in SECRETS)

    def test_text_lists_each_account(self):
        text = export.render(export.account_rows(ACCOUNTS), "txt", exported_at=datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)).decode("utf-8")
        assert "Exported 19 Oct 2026, 09:30 AM IST · 3 accounts" in text
        assert "1. Karthik Gmail (current)\r\n   Type: Gmail\r\n   Username: karthik@example.com\r\n   Status: Active" in text
        assert not any(s in text for s in SECRETS)

    def test_excel_is_a_real_workbook_with_a_bold_frozen_header(self):
        data = export.render(export.account_rows(ACCOUNTS), "xlsx")
        assert sheet_rows(data) == [
            ["Account name", "Type", "Username", "Status"],
            ["Karthik Gmail (current)", "Gmail", "karthik@example.com", "Active"],
            ["Naukri legacy", "Naukri", "ops@example.com", "Old"],
            ["=HYPERLINK(\"http://x\")", "Gmail", "+91 trick", "Active"],    # text cells: nothing is a formula
        ]
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/styles.xml"} <= set(archive.namelist())
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert 'state="frozen"' in sheet and '<c r="A1" t="inlineStr" s="1">' in sheet and "<f>" not in sheet
        assert not any(s.encode() in data for s in SECRETS)

    def test_excel_escapes_markup_and_drops_characters_xml_cannot_hold(self):
        data = export.to_xlsx([{"name": "A & B <x>\x07", "type": "", "username": "", "status": ""}], export.columns())
        assert sheet_rows(data)[1][0] == "A & B <x>"

    def test_an_unknown_format_is_refused(self):
        with pytest.raises(ValueError):
            export.render([], "pdf")


class TestTheEncryptedWorkbook:
    def test_it_opens_only_with_its_password_and_holds_the_passwords(self):
        import msoffcrypto

        plain = export.render(export.account_rows(ACCOUNTS, with_passwords=True), "xlsx", with_passwords=True)
        sealed = export.encrypt_xlsx(plain, "a-long-file-password")
        assert sealed[:8] == bytes.fromhex("D0CF11E0A1B11AE1")            # an encrypted Office file, not a zip
        assert not any(s.encode() in sealed for s in SECRETS)
        office = msoffcrypto.OfficeFile(io.BytesIO(sealed))
        office.load_key(password="a-long-file-password")
        opened = io.BytesIO()
        office.decrypt(opened)
        assert sheet_rows(opened.getvalue())[0] == ["Account name", "Type", "Username", "Status", "Password"]
        assert sheet_rows(opened.getvalue())[1][4] == "Pa55-karthik-SECRET"
        wrong = msoffcrypto.OfficeFile(io.BytesIO(sealed))
        wrong.load_key(password="not-the-password")
        with pytest.raises(Exception):
            wrong.decrypt(io.BytesIO())

    @pytest.mark.parametrize("rows", [0, 3, 40, 200])
    def test_every_size_opens_back_exactly(self, rows):
        """msoffcrypto truncates a package under 4 KiB; the workbook is padded so a short account list opens too."""
        import msoffcrypto

        data = export.to_xlsx([{"name": f"acct {i}", "type": "Gmail", "username": f"u{i}@example.com", "status": "Active",
                                "password": "p" * 12} for i in range(rows)], export.columns(True))
        office = msoffcrypto.OfficeFile(io.BytesIO(export.encrypt_xlsx(data, "a-long-file-password")))
        office.load_key(password="a-long-file-password")
        opened = io.BytesIO()
        office.decrypt(opened)
        assert len(sheet_rows(opened.getvalue())) == rows + 1
        with zipfile.ZipFile(io.BytesIO(opened.getvalue())) as archive:
            assert archive.testzip() is None

    def test_an_encryption_that_does_not_open_back_is_refused(self, monkeypatch):
        from msoffcrypto.format import ooxml

        monkeypatch.setattr(ooxml.OOXMLFile, "encrypt", lambda self, password, out: out.write(b"broken"))
        with pytest.raises(Exception):
            export.encrypt_xlsx(export.to_xlsx([], export.columns(True)), "a-long-file-password")

    @pytest.mark.parametrize("file_password, problem", [
        ("short", "at least 10"), ("admin-pass-123", "different from your sign-in"), ("long-enough-pw", ""),
    ])
    def test_the_file_password_rules(self, file_password, problem):
        assert problem in export.file_password_problem(file_password, "admin-pass-123")
        if not problem:
            assert export.file_password_problem(file_password, "admin-pass-123") == ""


class TestTheLock:
    def test_five_wrong_passwords_lock_it_for_the_window_and_a_success_clears_them(self):
        now = [0.0]
        lock = export.ReauthLimiter(limit=5, window=900, clock=lambda: now[0])
        for _ in range(4):
            lock.failed("admin")
        assert not lock.blocked("admin")
        lock.failed("admin")
        assert lock.blocked("admin") and not lock.blocked("someone-else")
        now[0] = 901
        assert not lock.blocked("admin")
        lock.failed("admin"); lock.succeeded("admin")
        assert "admin" not in lock._failures


# ---------------------------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------------------------

@pytest.fixture()
def api(monkeypatch):
    from core import dashboard_access, recruitment_mail_store
    from core import dashboard_auth_vps as auth
    from features import data_room_credentials_store

    state = {"profile": {"username": "admin", "role": "admin"}, "audits": [], "audit_fails": False}
    monkeypatch.setattr(dashboard_access, "operator_profile", lambda request: state["profile"])
    monkeypatch.setattr(data_room_credentials_store, "get_credentials", lambda: {"service_accounts": ACCOUNTS})
    monkeypatch.setattr(auth, "auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "admin_password_matches", lambda password, expected=None: password == "admin-pass-123")

    def audit(**kwargs):
        if state["audit_fails"]:
            raise RuntimeError("database unavailable")
        state["audits"].append(kwargs)

    monkeypatch.setattr(recruitment_mail_store, "audit", audit)
    export.reauth_limiter._failures.clear()
    app = FastAPI()
    app.include_router(data_room.router)
    return TestClient(app), state


def protected(client, admin="admin-pass-123", file="long-enough-pw"):
    return client.post("/data-room/credentials/service-accounts/export-with-passwords",
                       json={"admin_password": admin, "file_password": file})


class TestTheExportRoute:
    @pytest.mark.parametrize("fmt, media", [("xlsx", "spreadsheetml"), ("csv", "text/csv"), ("txt", "text/plain")])
    def test_the_admin_downloads_every_account_without_passwords(self, api, fmt, media):
        client, state = api
        res = client.get(f"/data-room/credentials/service-accounts/export?format={fmt}")
        assert res.status_code == 200 and media in res.headers["content-type"]
        assert res.headers["cache-control"] == "no-store"
        assert res.headers["content-disposition"].startswith('attachment; filename="teleautomation-accounts-')
        assert res.headers["content-disposition"].endswith(f'.{fmt}"')
        body = res.content if fmt != "xlsx" else zipfile.ZipFile(io.BytesIO(res.content)).read("xl/worksheets/sheet1.xml")
        assert b"karthik@example.com" in body and not any(s.encode() in body for s in SECRETS)
        assert state["audits"][-1]["new"] == {"format": fmt, "accounts": 3, "with_passwords": False}

    def test_a_handler_cannot_download_them(self, api):
        client, state = api
        state["profile"] = {"username": "ravi", "role": "handler"}
        assert client.get("/data-room/credentials/service-accounts/export?format=csv").status_code == 403
        assert protected(client).status_code == 403
        state["profile"] = {"username": None, "role": None}
        assert client.get("/data-room/credentials/service-accounts/export").status_code == 403

    def test_an_unknown_format_is_refused(self, api):
        client, _ = api
        assert client.get("/data-room/credentials/service-accounts/export?format=pdf").status_code == 400

    def test_a_record_that_cannot_be_written_does_not_stop_a_password_free_export(self, api):
        client, state = api
        state["audit_fails"] = True
        assert client.get("/data-room/credentials/service-accounts/export?format=txt").status_code == 200


class TestThePasswordExportRoute:
    def test_with_the_admin_password_it_returns_the_encrypted_workbook(self, api):
        import msoffcrypto

        client, state = api
        res = protected(client)
        assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
        assert 'with-passwords' in res.headers["content-disposition"] and res.content[:4] == bytes.fromhex("D0CF11E0")
        office = msoffcrypto.OfficeFile(io.BytesIO(res.content))
        office.load_key(password="long-enough-pw")
        opened = io.BytesIO()
        office.decrypt(opened)
        assert sheet_rows(opened.getvalue())[2][4] == "naukri-SECRET-2"
        # recorded, and the record holds no password of any kind
        assert state["audits"][-1]["action"] == "data_room_accounts_export_with_passwords"
        recorded = json.dumps(state["audits"])
        assert not any(s in recorded for s in SECRETS + ("admin-pass-123", "long-enough-pw"))

    def test_a_wrong_sign_in_password_is_refused_and_five_lock_the_export(self, api):
        client, _ = api
        for _ in range(5):
            res = protected(client, admin="guess")
            # 403: a 401 would make the dashboard sign the admin out over a mistyped password
            assert res.status_code == 403 and res.json()["detail"] == "Your sign-in password is not correct"
        res = protected(client)                       # even the right one, now
        assert res.status_code == 429 and "15 minutes" in res.json()["detail"]

    @pytest.mark.parametrize("file_password, words", [("short", "at least 10"), ("admin-pass-123", "different")])
    def test_a_weak_file_password_is_refused(self, api, file_password, words):
        client, _ = api
        res = protected(client, file=file_password)
        assert res.status_code == 400 and words in res.json()["detail"]

    def test_passwords_never_leave_unrecorded(self, api):
        client, state = api
        state["audit_fails"] = True
        res = protected(client)
        assert res.status_code == 503 and not res.content.startswith(bytes.fromhex("D0CF11E0"))

    def test_without_dashboard_sign_in_there_is_no_password_export(self, api, monkeypatch):
        from core import dashboard_auth_vps as auth

        client, _ = api
        monkeypatch.setattr(auth, "auth_enabled", lambda: False)
        assert protected(client).status_code == 403
