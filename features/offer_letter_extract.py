"""Fast local extraction of Data Room offer-letter metadata."""

from __future__ import annotations

import base64
import re
from datetime import date
from pathlib import Path
from typing import Any

from features.ollama_resume_extract import (
    _extract_text_from_pdf,
    _ocr_text_from_image_base64,
    _pdf_first_page_to_image,
)


_SPACE_RE = re.compile(r"[ \t]+")
_LEGAL_COMPANY_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&.,'()\- ]{2,80}?"
    r"(?:Private Limited|Pvt\.?\s+Ltd\.?|Limited|Ltd\.?|LLP|Inc\.?|Corporation|"
    r"Corp\.?|Technologies|Technology|Solutions|Consulting|Services))\b"
)
_PERSON_VALUE_RE = re.compile(
    r"(?:candidate\s+name|employee\s+name|name)\s*[:\-]\s*"
    r"([A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){1,4})",
    re.I,
)
_DEAR_RE = re.compile(
    r"\bDear\s+(?:(?:Mr|Ms|Mrs)\.?\s+)?"
    r"([A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){0,4})\s*[,!]",
)
_COMPANY_CONTEXT_RE = re.compile(
    r"(?:employment|position|career|join(?:ing)?)\s+(?:with|at)\s+"
    r"([A-Z][A-Za-z0-9&.,'()\- ]{2,80}?)(?=[,.;\n]|\s+as\b)",
    re.I,
)
_OFFER_DATE_RE = re.compile(
    r"(?:offer\s+date|date\s+of\s+offer|date)\s*[:\-]\s*"
    r"([0-3]?\d[./\-][01]?\d[./\-](?:19|20)\d{2}|"
    r"(?:[0-3]?\d\s+)?[A-Za-z]{3,9}\s*,?\s*(?:19|20)\d{2})",
    re.I,
)
_JOINING_DATE_RE = re.compile(
    r"(?:date\s+of\s+joining|joining\s+date|commencement\s+date|start\s+date)"
    r"\s*[:\-]?\s*([0-3]?\d[./\-][01]?\d[./\-](?:19|20)\d{2}|"
    r"(?:[0-3]?\d\s+)?[A-Za-z]{3,9}\s*,?\s*(?:19|20)\d{2})",
    re.I,
)
_CTC_RE = re.compile(
    r"(?:annual\s+ctc|total\s+ctc|ctc|annual\s+compensation|annual\s+salary)"
    r"\s*[:\-]?\s*(?:INR|Rs\.?|₹)?\s*([0-9][0-9,]*(?:\.\d+)?)"
    r"\s*(lakhs?|lacs?|lpa)?",
    re.I,
)


def _clean(value: str, *, limit: int = 100) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip(" \t\r\n,.;:-")[:limit]


def _candidate_name(text: str) -> str:
    for pattern in (_PERSON_VALUE_RE, _DEAR_RE):
        match = pattern.search(text)
        if match:
            value = _clean(match.group(1), limit=80)
            if value.casefold() not in {"sir", "madam", "candidate", "applicant"}:
                return value
    return ""


def _company_name(text: str, filename: str) -> str:
    for pattern in (_COMPANY_CONTEXT_RE, _LEGAL_COMPANY_RE):
        match = pattern.search(text)
        if match:
            value = re.sub(
                r"^(?:the|our)\s+", "", _clean(match.group(1)), flags=re.I
            )
            if value and "offer letter" not in value.casefold():
                return value
    stem = Path(filename or "").stem.replace("_", " ").replace("-", " ")
    match = re.search(
        r"\b([A-Za-z][A-Za-z0-9&. ]{2,50})\s+(?:offer|appointment)\b",
        stem,
        re.I,
    )
    return _clean(match.group(1)) if match else ""


def _notes(text: str) -> str:
    facts: list[str] = []
    offer_date = _OFFER_DATE_RE.search(text)
    joining_date = _JOINING_DATE_RE.search(text)
    ctc = _CTC_RE.search(text)
    if offer_date:
        facts.append(f"Offer date: {_clean(offer_date.group(1), limit=40)}")
    if joining_date:
        facts.append(f"Joining date: {_clean(joining_date.group(1), limit=40)}")
    if ctc:
        amount = _clean(ctc.group(1), limit=30)
        unit = _clean(ctc.group(2) or "", limit=12)
        facts.append(f"CTC: ₹{amount}{f' {unit}' if unit else ''}")
    return " · ".join(facts)


def extract_offer_letter_fields(pdf_data: bytes, filename: str = "", *, image: bool = False) -> dict[str, Any]:
    """Return editable catalog fields; OCR is used for scanned first pages, and for a photo or scan of the letter
    (`image`), which has no text of its own."""
    if image:
        text = (_ocr_text_from_image_base64(base64.b64encode(pdf_data).decode("ascii")) or "").strip()
        method = "local_ocr" if text else "unreadable"
    else:
        text = (_extract_text_from_pdf(pdf_data) or "").strip()
        method = "embedded_text"
    if not image and len(text) < 40:
        image_base64 = _pdf_first_page_to_image(pdf_data)
        text = (_ocr_text_from_image_base64(image_base64) if image_base64 else "").strip()
        method = "local_ocr" if text else "unreadable"
    candidate = _candidate_name(text)
    company = _company_name(text, filename)
    populated = sum(bool(value) for value in (candidate, company))
    return {
        "filename": Path(filename or "offer-letter.pdf").name,
        "candidate": candidate,
        "company_name": company,
        "date_modified": date.today().isoformat(),
        "size_kb": max(1, (len(pdf_data) + 1023) // 1024),
        "drive_file_id": "",
        "notes": _notes(text),
        "analysis_method": method,
        "analysis_confidence": 40 + populated * 25 if text else 0,
        "text_extracted": bool(text),
    }
