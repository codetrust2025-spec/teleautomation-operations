"""Download all credentials (Data Room -> Credentials, 20 Oct).

Every credential -- the dashboard's logins (the admin and the handlers) and every service account, 33 in production --
in four columns, Credential Name, Type, Username, Password, the same records in the same order in Excel, CSV and text.
Names and types as the table shows them (features/credential_names.py). A service account's password exactly as stored,
"Not stored" when there is none; a dashboard login's "Not available (hashed)", and never its hash. For the signed-in
admin by role only; every download recorded (and none made unrecorded); built in memory and never written to the
server or cached. Driven through the real route with the stores and session replaced.
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

# Invented, shaped like production on 20 Oct. The admin and handlers as get_credentials sends them: no password --
# except that each here carries a hash, to prove a hash never reaches a file even if one were handed in.
HASHES = ("$2b$12$abcdefghijklmnopqrstuv0123456789ABCDEFGHIJKLMNOPQRS", "pbkdf2_sha256$600000$saltsalt$hashhashhash=")
ADMIN = {"username": "operations_admin", "reference": "Full dashboard", "role": "admin", "password": HASHES[0]}
HANDLERS = [
    {"username": "asha", "reference": "asha rao", "role": "handler", "password": HASHES[1], "password_hash": HASHES[0]},
    {"username": "kiran", "reference": "Kiran", "role": "handler"},
    {"username": "meera", "reference": "Meera", "role": "handler"},
    {"username": "ravi", "reference": "", "role": "handler"},
    {"username": "sunil", "reference": "Sunil Kumar", "role": "handler"},
]
TRICKY = " =SUM(1,2) \"x\" "            # a leading formula sign, spaces at both ends, a comma and quotes
ACCOUNTS = [
    {"id": "zed_naukri", "label": "zed example naukri", "service": "Naukri", "username": "zed@example.com", "password": "Zed-1"},
    {"id": "amy_sn", "label": "Amy Example Servicenow  Naukri", "service": "Naukri", "username": "amy@example.com", "password": TRICKY},
    {"id": "tg_ops", "label": "Telegram login number", "service": "Telegram", "username": "+91 90000 00000", "password": ""},
    {"id": "no-name-id", "label": "", "service": "Naukri", "username": "nameless@example.com", "password": "nameless-SECRET"},
    {"id": "helper", "label": "interview helper", "service": "Tool", "username": "tools@example.com", "password": "tool-SECRET"},
    {"id": "loose", "label": "Unsorted login", "service": "", "username": "", "password": "loose-SECRET"},
] + [
    {"id": f"n{i}", "label": f"Candidate {i:02d} Naukri", "service": "Naukri", "username": f"c{i}@example.com", "password": f"pw-{i}"}
    for i in range(21)
]
HEADER = ["Credential Name", "Type", "Username", "Password"]
LOGINS = [
    ["Admin", "Dashboard / Tool Login", "operations_admin", "Not available (hashed)"],
    ["Asha Rao", "Dashboard / Tool Login", "asha", "Not available (hashed)"],
    ["Kiran", "Dashboard / Tool Login", "kiran", "Not available (hashed)"],
    ["Meera", "Dashboard / Tool Login", "meera", "Not available (hashed)"],
    ["ravi", "Dashboard / Tool Login", "ravi", "Not available (hashed)"],
    ["Sunil Kumar", "Dashboard / Tool Login", "sunil", "Not available (hashed)"],
]
EXPECTED = LOGINS + [
    ["Amy Example ServiceNow", "Naukri", "amy@example.com", TRICKY],
    *[[f"Candidate {i:02d}", "Naukri", f"c{i}@example.com", f"pw-{i}"] for i in range(21)],
    ["Interview Helper", "Dashboard / Tool Login", "tools@example.com", "tool-SECRET"],
    ["no-name-id", "Naukri", "nameless@example.com", "nameless-SECRET"],
    ["Telegram Login Number", "Telegram", "+91 90000 00000", "Not stored"],
    ["Unsorted Login", "Not Set", "", "loose-SECRET"],
    ["Zed Example", "Naukri", "zed@example.com", "Zed-1"],
]
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
BOM = chr(0xFEFF)
CRLF = chr(13) + chr(10)


def rows_of(admin=ADMIN, handlers=HANDLERS, accounts=ACCOUNTS):
    return export.credential_rows(admin, handlers, [dict(a) for a in accounts])


def sheet_rows(data: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return [[t.text or "" for t in row.iterfind(".//m:t", NS)] for row in root.iterfind(".//m:row", NS)]


def csv_rows(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def txt_rows(data: bytes) -> list[list[str]]:
    """Each block back into its four values, by label."""
    out = []
    for block in data.decode("utf-8").split(CRLF + CRLF):
        values = []
        for title, line in zip(HEADER, [l for l in block.split(CRLF) if l]):
            assert line == f"{title}:" or line.startswith(f"{title}: "), line
            values.append(line[len(title) + 2:] if line.startswith(f"{title}: ") else "")
        out.append(values)
    return out


class TestTheRecords:
    def test_every_credential_once_logins_first_then_the_accounts_by_name(self):
        rows = rows_of()
        assert len(rows) == 33 and [list(r) for r in rows] == [["name", "type", "username", "password"]] * 33
        assert [[r["name"], r["type"], r["username"], r["password"]] for r in rows] == EXPECTED

    def test_names_are_standard_and_the_type_is_its_own_column(self):
        names = [r["name"] for r in rows_of()]
        assert "Amy Example ServiceNow" in names and "Zed Example" in names       # "Naukri" dropped, capitals given
        assert not any(n.lower().endswith(" naukri") for n in names)
        assert "Telegram Login Number" in names                                    # a type word before the end stays

    def test_a_dashboard_login_never_carries_its_hash(self):
        dumped = json.dumps(rows_of())
        for digest in HASHES:
            assert digest not in dumped
        assert sum(1 for r in rows_of() if r["password"] == "Not available (hashed)") == 6

    def test_a_missing_password_says_not_stored_and_a_nameless_account_keeps_its_id(self):
        by_user = {r["username"]: r for r in rows_of()}
        assert by_user["+91 90000 00000"]["password"] == "Not stored"
        assert by_user["nameless@example.com"]["name"] == "no-name-id"

    def test_passwords_are_exactly_as_stored(self):
        stored = {a["username"]: a["password"] for a in ACCOUNTS if a["password"]}
        got = {r["username"]: r["password"] for r in rows_of() if r["username"] in stored}
        assert got == stored


class TestTheFormats:
    def test_the_three_formats_hold_the_same_records(self):
        rows = rows_of()
        assert sheet_rows(export.render(rows, "xlsx")) == [HEADER] + EXPECTED
        assert csv_rows(export.render(rows, "csv")) == [HEADER] + EXPECTED
        assert txt_rows(export.render(rows, "txt")) == EXPECTED

    def test_csv_has_a_bom_for_excel_and_writes_values_untouched(self):
        data = export.render(rows_of(), "csv")
        assert data.startswith(BOM.encode("utf-8"))
        assert [TRICKY in row for row in csv_rows(data)].count(True) == 1

    def test_text_is_four_labelled_lines_per_credential_with_a_blank_line_between(self):
        text = export.render(rows_of(), "txt").decode("utf-8")
        blocks = text.split(CRLF + CRLF)
        assert len(blocks) == 33
        assert blocks[0] == CRLF.join(["Credential Name: Admin", "Type: Dashboard / Tool Login",
                                       "Username: operations_admin", "Password: Not available (hashed)"])
        assert "Credential Name: Telegram Login Number" + CRLF + "Type: Telegram" in text
        assert "Password: Not stored" + CRLF in text
        assert "Username:" + CRLF + "Password: loose-SECRET" in text                # an empty value leaves its label alone
        assert text.endswith("Password: Zed-1" + CRLF) and not text.endswith(CRLF + CRLF)
        # nothing but labelled values and the blank lines between them; plain line endings only
        for line in text.split(CRLF):
            assert line == "" or line.split(":", 1)[0] in HEADER
        assert text.replace(CRLF, "").count(chr(10)) == 0 and text.replace(CRLF, "").count(chr(13)) == 0

    def test_excel_is_four_text_columns_on_a_credentials_sheet(self):
        data = export.render(rows_of(), "xlsx")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            workbook = archive.read("xl/workbook.xml").decode("utf-8")
        assert '<sheet name="Credentials"' in workbook
        assert 'state="frozen"' in sheet and '<c r="A1" t="inlineStr" s="1">' in sheet
        assert "<f>" not in sheet and '<c r="E' not in sheet                       # text cells only, four columns

    @pytest.mark.parametrize("fmt", export.FORMATS)
    def test_no_hash_in_any_format(self, fmt):
        data = export.render(rows_of(), fmt)
        raw = data.decode("utf-8", "ignore") if fmt != "xlsx" else "".join(
            zipfile.ZipFile(io.BytesIO(data)).read(n).decode("utf-8") for n in zipfile.ZipFile(io.BytesIO(data)).namelist())
        for digest in HASHES:
            assert digest not in raw

    def test_no_credentials_is_an_empty_file_and_an_unknown_format_is_refused(self):
        assert export.render([], "txt") == b""
        with pytest.raises(ValueError):
            export.render([], "pdf")

    def test_the_file_is_named_for_credentials(self):
        assert export.filename("csv").startswith("teleautomation-credentials-") and export.filename("csv").endswith(".csv")


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
    monkeypatch.setattr(data_room_credentials_store, "get_credentials",
                        lambda: {"admin": dict(ADMIN), "handlers": [dict(h) for h in HANDLERS], "service_accounts": []})
    monkeypatch.setattr(data_room_credentials_store, "service_accounts_with_passwords", lambda: [dict(a) for a in ACCOUNTS])

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
    def test_the_admin_downloads_all_33_credentials(self, api, fmt, media):
        client, _ = api
        res = client.get(f"{URL}?format={fmt}")
        assert res.status_code == 200 and media in res.headers["content-type"]
        assert res.headers["cache-control"] == "no-store" and res.headers["pragma"] == "no-cache"
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["content-disposition"].startswith('attachment; filename="teleautomation-credentials-')
        assert res.headers["content-disposition"].endswith(f'.{fmt}"')
        parsed = {"xlsx": lambda d: sheet_rows(d)[1:], "csv": lambda d: csv_rows(d)[1:], "txt": txt_rows}[fmt](res.content)
        assert parsed == EXPECTED
        for digest in HASHES:
            assert digest.encode() not in res.content

    def test_every_download_is_recorded_without_its_contents(self, api):
        client, state = api
        client.get(f"{URL}?format=csv")
        assert len(state["audits"]) == 1
        record = state["audits"][0]
        assert record["action"] == "data_room_accounts_export" and record["actor"] == "admin"
        assert record["new"] == {"format": "csv", "credentials": 33, "dashboard_logins": 6, "service_accounts": 27,
                                 "with_passwords": True}
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
