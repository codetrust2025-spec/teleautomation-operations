"""Download All Accounts (Data Room -> Accounts).

Three columns -- Account Name, Username, Password -- for every account, each value exactly as stored, as Excel, CSV
or text; for the signed-in admin by role only; every download recorded (and none made unrecorded); built in memory
and never written to the server or cached. Driven through the real route with the stores and session replaced.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import data_room
from features import data_room_account_export as export

ACCOUNTS = [
    {"id": "karthik", "label": "Karthik Gmail (current)", "service": "", "username": "karthik@example.com",
     "password": "Pa55-karthik-SECRET", "last_used": "18 Oct", "notes": "recovery phone on file"},
    {"id": "naukri", "label": "Naukri legacy", "service": "Naukri", "username": "ops@example.com", "password": "naukri-SECRET-2"},
    # Passwords that a careless export would change: a leading formula sign, spaces at both ends, a comma and quotes.
    {"id": "tricky", "label": "Tricky", "username": "+91 trick", "password": " =SUM(1,2) \"x\" "},
    {"id": "no-name-id", "label": "", "username": "nameless@example.com", "password": "nameless-SECRET"},
    {"id": "no-password", "label": "No password yet", "username": "empty@example.com", "password": ""},
]
HEADER = ["Account Name", "Username", "Password"]
EXPECTED = [
    ["Karthik Gmail (current)", "karthik@example.com", "Pa55-karthik-SECRET"],
    ["Naukri legacy", "ops@example.com", "naukri-SECRET-2"],
    ["Tricky", "+91 trick", " =SUM(1,2) \"x\" "],
    ["no-name-id", "nameless@example.com", "nameless-SECRET"],
    ["No password yet", "empty@example.com", ""],
]
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
BOM = chr(0xFEFF)
CRLF = chr(13) + chr(10)


def sheet_rows(data: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return [[t.text or "" for t in row.iterfind(".//m:t", NS)] for row in root.iterfind(".//m:row", NS)]


class TestTheRows:
    def test_only_name_username_and_password_for_every_account(self):
        rows = export.account_rows(ACCOUNTS)
        assert [list(row) for row in rows] == [["name", "username", "password"]] * len(ACCOUNTS)
        assert [[row["name"], row["username"], row["password"]] for row in rows] == EXPECTED

    def test_an_account_with_no_name_is_named_by_its_id_rather_than_left_out(self):
        assert export.account_rows([{"id": "abc", "username": "u"}]) == [{"name": "abc", "username": "u", "password": ""}]


class TestTheFormats:
    def test_csv_is_three_columns_with_every_value_exactly_as_stored(self):
        data = export.render(export.account_rows(ACCOUNTS), "csv")
        assert data.startswith(BOM.encode("utf-8"))                      # Excel reads it as UTF-8
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        assert rows == [HEADER] + EXPECTED                               # no prefix, no trimming, nothing missing

    def test_text_is_a_header_and_one_aligned_line_per_account(self):
        text = export.render(export.account_rows(ACCOUNTS), "txt").decode("utf-8")
        lines = text.split(CRLF)
        assert lines[-1] == "" and len(lines) == len(ACCOUNTS) + 2         # header, the accounts, the final newline
        assert lines[0].split() == ["Account", "Name", "Username", "Password"]
        assert lines[1] == "Karthik Gmail (current)  karthik@example.com   Pa55-karthik-SECRET"
        # columns line up: every password starts where the Password heading does
        start = lines[0].index("Password")
        for line, (_, _, password) in zip(lines[1:], EXPECTED):
            assert line[start:] == password                              # exact, trailing spaces included
        assert lines[5] == "No password yet          empty@example.com"   # no padding after the last value
        for gone in ("Type", "Status", "Last used", "Notes", "Exported", "1. "):
            assert gone not in text

    def test_excel_is_three_text_columns_with_a_bold_frozen_header(self):
        data = export.render(export.account_rows(ACCOUNTS), "xlsx")
        assert sheet_rows(data) == [HEADER] + EXPECTED
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert 'state="frozen"' in sheet and '<c r="A1" t="inlineStr" s="1">' in sheet
        assert "<f>" not in sheet and '<c r="D' not in sheet              # text cells only, three columns

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
    def test_the_admin_downloads_every_account_exactly(self, api, fmt, media):
        client, _ = api
        res = client.get(f"{URL}?format={fmt}")
        assert res.status_code == 200 and media in res.headers["content-type"]
        assert res.headers["cache-control"] == "no-store" and res.headers["pragma"] == "no-cache"
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["content-disposition"].startswith('attachment; filename="teleautomation-accounts-')
        assert res.headers["content-disposition"].endswith(f'.{fmt}"')
        if fmt == "xlsx":
            assert sheet_rows(res.content) == [HEADER] + EXPECTED
        elif fmt == "csv":
            assert list(csv.reader(io.StringIO(res.content.decode("utf-8-sig")))) == [HEADER] + EXPECTED
        else:
            assert all(password in res.content.decode("utf-8") for _, _, password in EXPECTED)

    def test_every_download_is_recorded_without_its_contents(self, api):
        client, state = api
        client.get(f"{URL}?format=csv")
        assert len(state["audits"]) == 1
        record = state["audits"][0]
        assert record["action"] == "data_room_accounts_export" and record["actor"] == "admin"
        assert record["new"] == {"format": "csv", "accounts": len(ACCOUNTS), "with_passwords": True}
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
