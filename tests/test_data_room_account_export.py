"""Download All Accounts (Data Room -> Accounts).

Every account with its complete details -- name, type, username, password, status, last used, notes -- as Excel,
CSV or text; for the signed-in admin by role only; every download recorded (and none made unrecorded); built in
memory and never written to the server or cached. Driven through the real route with the stores and session replaced.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import data_room
from features import data_room_account_export as export

ACCOUNTS = [
    {"id": "karthik", "label": "Karthik Gmail (current)", "service": "", "username": "karthik@example.com",
     "password": "Pa55-karthik-SECRET", "last_used": "18 Oct", "notes": "recovery phone on file\nsecond line"},
    {"id": "naukri", "label": "Naukri legacy", "service": "Naukri", "username": "ops@example.com", "password": "naukri-SECRET-2"},
    {"id": "formula", "label": "=HYPERLINK(\"http://x\")", "service": "Gmail", "username": "+91 trick", "password": "-dash"},
]
HEADER = ["Account name", "Type", "Username", "Password", "Status", "Last used", "Notes"]
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
BOM = chr(0xFEFF)


def sheet_rows(data: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return [[t.text or "" for t in row.iterfind(".//m:t", NS)] for row in root.iterfind(".//m:row", NS)]


class TestTheRows:
    def test_complete_details_for_every_account(self):
        rows = export.account_rows(ACCOUNTS)
        assert rows[0] == {"name": "Karthik Gmail (current)", "type": "Gmail", "username": "karthik@example.com",
                           "password": "Pa55-karthik-SECRET", "status": "Active", "last_used": "18 Oct",
                           "notes": "recovery phone on file\nsecond line"}
        assert rows[1]["status"] == "Old" and rows[1]["type"] == "Naukri"     # "legacy" in the name, as on the table
        assert len(rows) == len(ACCOUNTS)


class TestTheFormats:
    def test_csv_opens_in_excel_as_utf8_holds_the_passwords_and_runs_no_formula(self):
        data = export.render(export.account_rows(ACCOUNTS), "csv")
        assert data.startswith(BOM.encode("utf-8"))
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        assert rows[0] == HEADER
        assert rows[1][:5] == ["Karthik Gmail (current)", "Gmail", "karthik@example.com", "Pa55-karthik-SECRET", "Active"]
        assert rows[1][6] == "recovery phone on file\nsecond line"
        assert rows[3][0] == "'=HYPERLINK(\"http://x\")" and rows[3][2] == "'+91 trick" and rows[3][3] == "'-dash"

    def test_text_lists_each_account_with_every_detail(self):
        text = export.render(export.account_rows(ACCOUNTS), "txt", exported_at=datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)).decode("utf-8")
        assert "Exported 19 Oct 2026, 09:30 AM IST · 3 accounts" in text
        assert ("1. Karthik Gmail (current)\r\n   Type: Gmail\r\n   Username: karthik@example.com\r\n"
                "   Password: Pa55-karthik-SECRET\r\n   Status: Active\r\n   Last used: 18 Oct\r\n") in text
        assert "   Notes: recovery phone on file\n          second line" in text      # a second line stays under its label

    def test_excel_is_a_real_workbook_with_a_bold_frozen_header(self):
        data = export.render(export.account_rows(ACCOUNTS), "xlsx")
        rows = sheet_rows(data)
        assert rows[0] == HEADER
        assert rows[1][:5] == ["Karthik Gmail (current)", "Gmail", "karthik@example.com", "Pa55-karthik-SECRET", "Active"]
        assert rows[3][0] == "=HYPERLINK(\"http://x\")"                      # text cells: nothing is a formula
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert 'state="frozen"' in sheet and '<c r="A1" t="inlineStr" s="1">' in sheet and "<f>" not in sheet

    def test_excel_escapes_markup_and_drops_characters_xml_cannot_hold(self):
        data = export.to_xlsx([{"name": "A & B <x>" + chr(7)}])
        assert sheet_rows(data)[1][0] == "A & B <x>"

    def test_an_unknown_format_is_refused(self):
        with pytest.raises(ValueError):
            export.render([], "pdf")


def test_nothing_is_written_to_the_server():
    """The file is built in memory and handed over; neither the builder nor the route opens a file to write."""
    builder = Path(export.__file__).read_text(encoding="utf-8")
    router = Path(data_room.__file__).read_text(encoding="utf-8")
    start = router.index("async def data_room_accounts_export")
    route = router[start:router.index("@router.", start)]
    for body in (builder, route):
        for forbidden in ("open(", "tempfile", "write_bytes", "write_text", "FileResponse(", "NamedTemporaryFile"):
            assert forbidden not in body, forbidden


# ---------------------------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------------------------

@pytest.fixture()
def api(monkeypatch):
    from core import dashboard_access, recruitment_mail_store
    from features import data_room_credentials_store

    state = {"profile": {"username": "admin", "role": "admin"}, "audits": [], "audit_fails": False}
    monkeypatch.setattr(dashboard_access, "operator_profile", lambda request: state["profile"])
    monkeypatch.setattr(data_room_credentials_store, "get_credentials", lambda: {"service_accounts": ACCOUNTS})

    def audit(**kwargs):
        if state["audit_fails"]:
            raise RuntimeError("database unavailable")
        state["audits"].append(kwargs)

    monkeypatch.setattr(recruitment_mail_store, "audit", audit)
    app = FastAPI()
    app.include_router(data_room.router)
    return TestClient(app), state


URL = "/data-room/credentials/service-accounts/export"


class TestTheRoute:
    @pytest.mark.parametrize("fmt, media", [("xlsx", "spreadsheetml"), ("csv", "text/csv"), ("txt", "text/plain")])
    def test_the_admin_downloads_every_account_with_its_password(self, api, fmt, media):
        client, _ = api
        res = client.get(f"{URL}?format={fmt}")
        assert res.status_code == 200 and media in res.headers["content-type"]
        assert res.headers["cache-control"] == "no-store" and res.headers["pragma"] == "no-cache"
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["content-disposition"].startswith('attachment; filename="teleautomation-accounts-')
        assert res.headers["content-disposition"].endswith(f'.{fmt}"')
        body = res.content if fmt != "xlsx" else zipfile.ZipFile(io.BytesIO(res.content)).read("xl/worksheets/sheet1.xml")
        for value in (b"karthik@example.com", b"Pa55-karthik-SECRET", b"naukri-SECRET-2"):
            assert value in body

    def test_every_download_is_recorded_without_its_contents(self, api):
        client, state = api
        client.get(f"{URL}?format=csv")
        assert len(state["audits"]) == 1
        record = state["audits"][0]
        assert record["action"] == "data_room_accounts_export" and record["actor"] == "admin"
        assert record["new"] == {"format": "csv", "accounts": 3, "with_passwords": True}
        assert "SECRET" not in json.dumps(record) and "example.com" not in json.dumps(record)

    def test_no_download_is_made_unrecorded(self, api):
        client, state = api
        state["audit_fails"] = True
        res = client.get(f"{URL}?format=xlsx")
        assert res.status_code == 503 and b"SECRET" not in res.content

    @pytest.mark.parametrize("profile", [{"username": "ravi", "role": "handler"}, {"username": None, "role": None}])
    def test_only_the_admin(self, api, profile):
        client, state = api
        state["profile"] = profile
        res = client.get(f"{URL}?format=csv")
        assert res.status_code == 403 and b"SECRET" not in res.content and state["audits"] == []

    def test_an_unknown_format_is_refused(self, api):
        client, _ = api
        assert client.get(f"{URL}?format=pdf").status_code == 400

    def test_there_is_no_separate_password_export_any_more(self, api):
        client, _ = api
        res = client.post("/data-room/credentials/service-accounts/export-with-passwords", json={})
        assert res.status_code in (404, 405)
