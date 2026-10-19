"""Download every Data Room service account at once: Excel, CSV or plain text.

Each account comes with its complete details, as the Accounts table holds them:
account name, type, username, password, status, last used and notes. The file
is built in memory and handed to the browser; nothing is written to the
server's disk. Who may download it -- the admin, by role -- and the record of
every download are the route's (api/routers/data_room.py).

The Excel writer is the standard library's: an .xlsx is a zip of a few XML
parts, and a one-sheet table needs nothing more.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

IST = timezone(timedelta(hours=5, minutes=30))
FORMATS = ("xlsx", "csv", "txt")
MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
}
COLUMNS = (
    ("name", "Account name"), ("type", "Type"), ("username", "Username"), ("password", "Password"),
    ("status", "Status"), ("last_used", "Last used"), ("notes", "Notes"),
)


def account_status(row: dict) -> str:
    """The Accounts table's own rule: an account named deprecated or legacy is Old, any other is Active."""
    label = str(row.get("label") or "").lower()
    return "Old" if ("deprecated" in label or "legacy" in label) else "Active"


def account_rows(service_accounts: list[dict]) -> list[dict]:
    """One row per account, as the table shows it (no type is "Gmail", as there)."""
    return [
        {
            "name": str(row.get("label") or row.get("id") or "").strip(),
            "type": str(row.get("service") or "").strip() or "Gmail",
            "username": str(row.get("username") or "").strip(),
            "password": str(row.get("password") or ""),
            "status": account_status(row),
            "last_used": str(row.get("last_used") or "").strip(),
            "notes": str(row.get("notes") or "").strip(),
        }
        for row in service_accounts or []
    ]


def _csv_safe(value: str) -> str:
    """A cell a spreadsheet would run as a formula is written as text (CSV injection)."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def to_csv(rows: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([title for _, title in COLUMNS])
    for row in rows:
        writer.writerow([_csv_safe(str(row.get(key) or "")) for key, _ in COLUMNS])
    # The byte-order mark makes Excel read the file as UTF-8.
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def to_txt(rows: list[dict], *, exported_at: datetime) -> bytes:
    lines = [
        "TeleAutomation · Data Room accounts",
        f"Exported {exported_at.astimezone(IST):%d %b %Y, %I:%M %p} IST · {len(rows)} account{'s' if len(rows) != 1 else ''}",
        "",
    ]
    for index, row in enumerate(rows, 1):
        lines.append(f"{index}. {row.get('name') or '—'}")
        for key, title in COLUMNS[1:]:
            value = str(row.get(key) or "—").replace("\r\n", "\n").replace("\n", "\n" + " " * (len(title) + 5))
            lines.append(f"   {title}: {value}")
        lines.append("")
    return "\r\n".join(lines).encode("utf-8")


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
            '<sheets><sheet name="Accounts" sheetId="1" r:id="rId1"/></sheets></workbook>'
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


def render(rows: list[dict], fmt: str, *, exported_at: datetime | None = None) -> bytes:
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format: {fmt}")
    if fmt == "csv":
        return to_csv(rows)
    if fmt == "txt":
        return to_txt(rows, exported_at=exported_at or datetime.now(timezone.utc))
    return to_xlsx(rows)


def filename(fmt: str, *, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).astimezone(IST).strftime("%Y%m%d-%H%M")
    return f"teleautomation-accounts-{stamp}.{fmt}"
