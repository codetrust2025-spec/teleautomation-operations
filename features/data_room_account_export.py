"""Download every Data Room service account at once: Excel, CSV or plain text.

The ordinary export carries what an operator reads off the Accounts table --
account name, type, username and status -- and never a password. Passwords
leave only in a separate export: an Excel workbook encrypted with a password
the admin chooses for that file (Office "Encrypt with Password", AES-256), so
the file is unreadable without it, and only after the admin has signed in
again (see api/routers/data_room.py). Nothing here stores or logs a password.

The Excel writer is the standard library's: an .xlsx is a zip of a few XML
parts, and a one-sheet table needs nothing more.
"""
from __future__ import annotations

import csv
import io
import re
import threading
import time
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
COLUMNS = (("name", "Account name"), ("type", "Type"), ("username", "Username"), ("status", "Status"))
PASSWORD_COLUMN = ("password", "Password")
MIN_FILE_PASSWORD = 10


def account_status(row: dict) -> str:
    """The Accounts table's own rule: an account named deprecated or legacy is Old, any other is Active."""
    label = str(row.get("label") or "").lower()
    return "Old" if ("deprecated" in label or "legacy" in label) else "Active"


def account_rows(service_accounts: list[dict], *, with_passwords: bool = False) -> list[dict]:
    """One row per account, as the table shows it (no type is "Gmail", as there)."""
    rows = []
    for row in service_accounts or []:
        out = {
            "name": str(row.get("label") or row.get("id") or "").strip(),
            "type": str(row.get("service") or "").strip() or "Gmail",
            "username": str(row.get("username") or "").strip(),
            "status": account_status(row),
        }
        if with_passwords:
            out["password"] = str(row.get("password") or "")
        rows.append(out)
    return rows


def columns(with_passwords: bool = False) -> tuple[tuple[str, str], ...]:
    return COLUMNS + ((PASSWORD_COLUMN,) if with_passwords else ())


def _csv_safe(value: str) -> str:
    """A cell a spreadsheet would run as a formula is written as text (CSV injection)."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def to_csv(rows: list[dict], cols) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([title for _, title in cols])
    for row in rows:
        writer.writerow([_csv_safe(str(row.get(key) or "")) for key, _ in cols])
    # The byte-order mark makes Excel read the file as UTF-8.
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def to_txt(rows: list[dict], cols, *, exported_at: datetime) -> bytes:
    lines = [
        "TeleAutomation · Data Room accounts",
        f"Exported {exported_at.astimezone(IST):%d %b %Y, %I:%M %p} IST · {len(rows)} account{'s' if len(rows) != 1 else ''}",
        "",
    ]
    for index, row in enumerate(rows, 1):
        lines.append(f"{index}. {row.get('name') or '—'}")
        for key, title in cols[1:]:
            lines.append(f"   {title}: {row.get(key) or '—'}")
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


def to_xlsx(rows: list[dict], cols) -> bytes:
    """A one-sheet workbook: bold, frozen header row, every cell plain text."""
    widths = [max([len(title)] + [len(str(row.get(key) or "")) for row in rows]) for key, title in cols]
    sheet_rows = []
    for r, values in enumerate([[title for _, title in cols]] + [[row.get(key) or "" for key, _ in cols] for row in rows], 1):
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


# msoffcrypto-tool 5.4.2 truncates an encrypted package under 4 KiB (one that lands in the OLE mini stream): the file
# then opens nowhere. Measured 19 Oct: 2.3 KB and 3.3 KB workbooks came back as 1,472 unreadable bytes; 7 KB was exact.
MIN_SEALED_BYTES = 8192


def _padded(data: bytes, minimum: int = MIN_SEALED_BYTES) -> bytes:
    """The same workbook, at least `minimum` bytes: the zip's archive comment (which every reader ignores) takes up
    the difference. Nothing inside the workbook changes."""
    if len(data) >= minimum:
        return data
    source, out = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info, source.read(info.filename))
        target.comment = b" " * min(65535, minimum - len(data) + 64)
    return out.getvalue()


def encrypt_xlsx(data: bytes, password: str) -> bytes:
    """The workbook encrypted the way Excel's "Encrypt with Password" does (ECMA-376 agile, AES-256).

    The result is opened again with the same password before it is returned, and anything that does not come back
    byte for byte is refused: an export that cannot be opened is worse than none."""
    import msoffcrypto
    from msoffcrypto.format.ooxml import OOXMLFile

    plain = _padded(data)
    sealed = io.BytesIO()
    OOXMLFile(io.BytesIO(plain)).encrypt(password, sealed)
    check, opened = msoffcrypto.OfficeFile(io.BytesIO(sealed.getvalue())), io.BytesIO()
    check.load_key(password=password)
    check.decrypt(opened)
    if opened.getvalue() != plain:
        raise RuntimeError("The encrypted workbook did not open back to its contents")
    return sealed.getvalue()


def render(rows: list[dict], fmt: str, *, with_passwords: bool = False, exported_at: datetime | None = None) -> bytes:
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format: {fmt}")
    cols = columns(with_passwords)
    if fmt == "csv":
        return to_csv(rows, cols)
    if fmt == "txt":
        return to_txt(rows, cols, exported_at=exported_at or datetime.now(timezone.utc))
    return to_xlsx(rows, cols)


def filename(fmt: str, *, with_passwords: bool = False, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).astimezone(IST).strftime("%Y%m%d-%H%M")
    return f"teleautomation-accounts{'-with-passwords' if with_passwords else ''}-{stamp}.{fmt}"


def file_password_problem(file_password: str, admin_password: str) -> str:
    """Why this file password cannot be used, or ""."""
    if len(file_password) < MIN_FILE_PASSWORD:
        return f"Choose a file password of at least {MIN_FILE_PASSWORD} characters."
    if file_password == admin_password:
        return "Use a file password that is different from your sign-in password."
    return ""


class ReauthLimiter:
    """Wrong sign-in passwords for the password export: after `limit` within `window` seconds the export is refused
    for `window` seconds, whatever is typed. Per operator, in memory (one uvicorn worker serves the API)."""

    def __init__(self, limit: int = 5, window: float = 15 * 60, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        now = self.clock()
        kept = [t for t in self._failures.get(key, []) if now - t < self.window]
        self._failures[key] = kept
        return kept

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key)) >= self.limit

    def failed(self, key: str) -> None:
        with self._lock:
            self._recent(key).append(self.clock())

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


reauth_limiter = ReauthLimiter()
