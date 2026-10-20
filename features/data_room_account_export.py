"""Download every Data Room credential at once: Excel, CSV or plain text.

Every credential (20 Oct): the dashboard's own logins -- the admin and the
handlers -- and every service account, 33 in production. Four columns in every
format, in the order the Credentials table lists them: Credential Name, Type,
Username, Password. Names and types follow features/credential_names.py, as the
table does.

A service account's password is written exactly as stored -- never trimmed,
escaped or prefixed -- and one with none stored says "Not stored". A dashboard
login's password is kept only as a hash, so it says "Not available (hashed)":
its row is built from the login's name, role and username alone, so no hash can
reach a file. The file is built in memory and handed to the browser; nothing is
written to the server's disk. Who may download it -- the admin, by role -- and
the record of every download are the route's (api/routers/data_room.py).

The Excel writer is the standard library's: an .xlsx is a zip of a few XML
parts, and a one-sheet table needs nothing more. Its cells are text cells, so a
value Excel would read as a formula is shown as typed.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from features.credential_names import LOGINS_TYPE, credential_type, standard_credential_name

IST = timezone(timedelta(hours=5, minutes=30))
FORMATS = ("xlsx", "csv", "txt")
MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
}
COLUMNS = (("name", "Credential Name"), ("type", "Type"), ("username", "Username"), ("password", "Password"))
HASHED = "Not available (hashed)"
NOT_STORED = "Not stored"


def account_name(row: dict) -> str:
    """A service account's name as the table shows it: standardised, or its id when it has none."""
    return standard_credential_name(row.get("label"), row.get("service")) or str(row.get("id") or "")


def login_name(login: dict, *, admin: bool = False) -> str:
    """A dashboard login's name as the table shows it: the admin by its role, a handler by its reference."""
    if admin:
        return "Admin"
    return standard_credential_name(login.get("reference"), drop_type=False) or str(login.get("username") or "")


def credential_rows(admin: dict | None, handlers: list[dict] | None, service_accounts: list[dict] | None) -> list[dict]:
    """One row per credential: the admin, the handlers, then the service accounts by name.

    A login's row is made from its name and username only -- never from any password field, so a hash cannot be
    copied into it even if one were handed in."""
    rows = []
    if isinstance(admin, dict):
        rows.append({"name": login_name(admin, admin=True), "type": LOGINS_TYPE,
                     "username": str(admin.get("username") or ""), "password": HASHED})
    for login in handlers or []:
        if isinstance(login, dict):
            rows.append({"name": login_name(login), "type": LOGINS_TYPE,
                         "username": str(login.get("username") or ""), "password": HASHED})
    accounts = sorted((r for r in service_accounts or [] if isinstance(r, dict)), key=lambda r: account_name(r).casefold())
    for row in accounts:
        rows.append({"name": account_name(row), "type": credential_type(row.get("service")),
                     "username": str(row.get("username") or ""),
                     "password": str(row.get("password") or "") or NOT_STORED})
    return rows


def to_csv(rows: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([title for _, title in COLUMNS])
    for row in rows:
        writer.writerow([row.get(key) or "" for key, _ in COLUMNS])
    # The byte-order mark makes Excel read the file as UTF-8.
    return (chr(0xFEFF) + buffer.getvalue()).encode("utf-8")


def to_txt(rows: list[dict]) -> bytes:
    """One block per credential, a blank line between blocks:

        Credential Name: ...
        Type: ...
        Username: ...
        Password: ...

    No columns to line up, so it reads the same in any font on a phone or a desktop. Each value follows its label
    exactly as stored; an empty one leaves the label alone ("Password:"). Windows line endings, which every phone
    and desktop viewer reads."""
    newline = chr(13) + chr(10)

    def line(title: str, value: str) -> str:
        return f"{title}: {value}" if value else f"{title}:"

    blocks = [newline.join(line(title, row.get(key) or "") for key, title in COLUMNS) for row in rows]
    return (newline.join([block + newline for block in blocks])).encode("utf-8") if blocks else b""


_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _cell_text(value: str) -> str:
    return escape(_XML_ILLEGAL.sub("", str(value or "")))


def _column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def to_xlsx(rows: list[dict]) -> bytes:
    """A one-sheet workbook: bold, frozen header row, every cell plain text."""
    widths = [max([len(title)] + [len(str(row.get(key) or "")) for row in rows]) for key, title in COLUMNS]
    sheet_rows = []
    for r, values in enumerate([[title for _, title in COLUMNS]] + [[row.get(key) or "" for key, _ in COLUMNS] for row in rows], 1):
        style = ' s="1"' if r == 1 else ""
        cells = "".join(
            f'<c r="{_column_letter(c)}{r}" t="inlineStr"{style}><is><t xml:space="preserve">{_cell_text(v)}</t></is></c>'
            for c, v in enumerate(values)
        )
        sheet_rows.append(f'<row r="{r}">{cells}</row>')
    col_defs = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{min(max(w + 2, 10), 60)}" customWidth="1"/>' for i, w in enumerate(widths)
    )
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Credentials" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"
        ),
        "xl/styles.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            "</styleSheet>"
        ),
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            f"<cols>{col_defs}</cols><sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def render(rows: list[dict], fmt: str) -> bytes:
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format: {fmt}")
    if fmt == "csv":
        return to_csv(rows)
    if fmt == "txt":
        return to_txt(rows)
    return to_xlsx(rows)


def filename(fmt: str, *, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).astimezone(IST).strftime("%Y%m%d-%H%M")
    return f"teleautomation-credentials-{stamp}.{fmt}"
