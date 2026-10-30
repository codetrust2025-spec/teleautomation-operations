from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape


def clean_xml_string(val: Any) -> str:
    if val is None:
        return ""
    s = str(val)
    # Strip XML 1.0 invalid control characters except \t, \n, \r
    return re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", s)


def col_letter(n: int) -> str:
    name = ""
    n += 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        name = chr(65 + r) + name
    return name


def build_xlsx_bytes(
    headers: list[str],
    rows: list[list[Any]],
    sheet_name: str = "Mail Alerts",
) -> bytes:
    """Build a compliant ECMA-376 OpenXML spreadsheet (.xlsx) without third-party dependencies."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. [Content_Types].xml
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '  <Default Extension="xml" ContentType="application/xml"/>\n'
            '  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\n'
            '  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
            '  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>\n'
            '</Types>',
        )

        # 2. _rels/.rels
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>\n'
            '</Relationships>',
        )

        # 3. xl/_rels/workbook.xml.rels
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>\n'
            '  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>\n'
            '</Relationships>',
        )

        # 4. xl/workbook.xml
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
            '  <sheets>\n'
            f'    <sheet name="{escape(clean_xml_string(sheet_name))}" sheetId="1" r:id="rId1"/>\n'
            '  </sheets>\n'
            '</workbook>',
        )

        # 5. xl/styles.xml (style 1 = bold header)
        zf.writestr(
            "xl/styles.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n'
            '  <fonts count="2">\n'
            '    <font><name val="Calibri"/><sz val="11"/></font>\n'
            '    <font><b/><name val="Calibri"/><sz val="11"/></font>\n'
            '  </fonts>\n'
            '  <fills count="2">\n'
            '    <fill><patternFill patternType="none"/></fill>\n'
            '    <fill><patternFill patternType="gray125"/></fill>\n'
            '  </fills>\n'
            '  <borders count="1">\n'
            '    <border><left/><right/><top/><bottom/></border>\n'
            '  </borders>\n'
            '  <cellStyleXfs count="1">\n'
            '    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>\n'
            '  </cellStyleXfs>\n'
            '  <cellXfs count="2">\n'
            '    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>\n'
            '    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>\n'
            '  </cellXfs>\n'
            '</styleSheet>',
        )

        # 6. xl/worksheets/sheet1.xml
        parts = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n',
            '  <sheetData>\n',
        ]

        # Header row (row 1, style s="1")
        parts.append('    <row r="1">\n')
        for col_idx, h in enumerate(headers):
            ref = f"{col_letter(col_idx)}1"
            txt = escape(clean_xml_string(h))
            parts.append(f'      <c r="{ref}" t="inlineStr" s="1"><is><t>{txt}</t></is></c>\n')
        parts.append('    </row>\n')

        # Data rows (row 2..N)
        for row_idx, row in enumerate(rows, start=2):
            parts.append(f'    <row r="{row_idx}">\n')
            for col_idx, val in enumerate(row):
                if val is None or val == "":
                    continue
                ref = f"{col_letter(col_idx)}{row_idx}"
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    parts.append(f'      <c r="{ref}"><v>{val}</v></c>\n')
                elif isinstance(val, bool):
                    parts.append(f'      <c r="{ref}" t="b"><v>{1 if val else 0}</v></c>\n')
                else:
                    txt = escape(clean_xml_string(val))
                    parts.append(f'      <c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{txt}</t></is></c>\n')
            parts.append('    </row>\n')

        parts.append('  </sheetData>\n</worksheet>')
        zf.writestr("xl/worksheets/sheet1.xml", "".join(parts))

    buf.seek(0)
    return buf.getvalue()


EXCEL_EXPORT_COLUMNS = [
    ("Notification ID", lambda r: r.get("notification_id") or ""),
    ("Message ID", lambda r: r.get("gmail_message_id") or r.get("rfc_message_id") or ""),
    ("Subject", lambda r: r.get("email_subject") or ""),
    ("Sender Name", lambda r: r.get("sender_name") or ""),
    ("Sender Email", lambda r: r.get("sender_email") or ""),
    ("Recipient Email", lambda r: r.get("recipient_email") or ""),
    ("Candidate Name", lambda r: r.get("candidate_name") or ""),
    ("Candidate Email", lambda r: r.get("candidate_email") or ""),
    ("Candidate ID", lambda r: r.get("candidate_id") or ""),
    ("Company", lambda r: r.get("company_name") or ""),
    ("Job Role", lambda r: r.get("job_role") or ""),
    ("Classification", lambda r: r.get("classification") or ""),
    ("Detected Status", lambda r: r.get("candidate_status") or ""),
    ("Confidence", lambda r: f"{round(float(r.get('ai_confidence') or 0) * 100)}%"),
    ("Email Received At", lambda r: str(r.get("email_received_at") or r.get("message_sent_at") or "")),
    ("Detected At", lambda r: str(r.get("detected_at") or "")),
    ("Booking Result", lambda r: r.get("booking_status") or r.get("candidate_status") or ""),
    ("Booking Block Reason", lambda r: r.get("booking_block_reason") or ""),
    ("Meeting Link", lambda r: r.get("meeting_link") or ""),
    ("Priority", lambda r: r.get("priority") or ""),
    ("Is Read", lambda r: "Yes" if r.get("is_read") else "No"),
    (
        "Attachments",
        lambda r: (
            "; ".join(
                f"{a['filename']} ({round(a['size_bytes'] / 1024, 1)} KB)"
                if a.get("size_bytes")
                else a.get("filename", "")
                for a in r.get("attachments", [])
            )
            if r.get("attachments")
            else "None"
        ),
    ),
    ("Email Body", lambda r: r.get("email_body") or ""),
]


def records_to_xlsx(records: list[dict[str, Any]]) -> bytes:
    headers = [col[0] for col in EXCEL_EXPORT_COLUMNS]
    rows = []
    for r in records:
        rows.append([col[1](r) for col in EXCEL_EXPORT_COLUMNS])
    return build_xlsx_bytes(headers, rows)


def records_to_json_payload(
    records: list[dict[str, Any]], filters: dict[str, Any]
) -> dict[str, Any]:
    clean_filters = {
        k: v for k, v in filters.items() if v is not None and v != "" and k not in {"sort"}
    }
    return {
        "export_version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "total_records": len(records),
        "filters_applied": clean_filters,
        "records": [
            {
                "notification_id": r.get("notification_id"),
                "message_id": r.get("gmail_message_id"),
                "rfc_message_id": r.get("rfc_message_id"),
                "thread_id": r.get("gmail_thread_id"),
                "subject": r.get("email_subject") or "",
                "sender": {
                    "name": r.get("sender_name") or "",
                    "email": r.get("sender_email") or "",
                },
                "recipient_email": r.get("recipient_email") or "",
                "candidate": {
                    "id": r.get("candidate_id") or "",
                    "name": r.get("candidate_name") or "",
                    "email": r.get("candidate_email") or "",
                },
                "company": r.get("company_name") or "",
                "job_role": r.get("job_role") or "",
                "classification": r.get("classification") or "",
                "detected_status": r.get("candidate_status") or "",
                "confidence": float(r.get("ai_confidence") or 0),
                "email_received_at": (
                    r.get("email_received_at").isoformat()
                    if hasattr(r.get("email_received_at"), "isoformat")
                    else str(r.get("email_received_at") or "")
                ),
                "detected_at": (
                    r.get("detected_at").isoformat()
                    if hasattr(r.get("detected_at"), "isoformat")
                    else str(r.get("detected_at") or "")
                ),
                "booking_result": r.get("booking_status") or "",
                "booking_block_reason": r.get("booking_block_reason"),
                "booking_id": r.get("booking_id"),
                "meeting_link": r.get("meeting_link"),
                "priority": r.get("priority") or "informational",
                "is_read": bool(r.get("is_read")),
                "attachments": r.get("attachments") or [],
                "email_body": r.get("email_body") or "",
            }
            for r in records
        ],
    }
