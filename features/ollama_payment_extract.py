"""Ollama vision model integration for payment proof screenshot extraction.

Reads UPI/bank transfer screenshots and extracts structured payment data:
amount, sender, receiver, date, UTR/reference number, payment app, status.

Auto-verifies against the candidate's due amount (₹10k+ threshold for slot confirmation).

Architecture (same as ollama_invite_extract.py):
  - Ollama runs on developer laptop (64GB RAM)
  - Tunneled to VPS via SSH reverse tunnel (localhost:11434)
  - Hybrid flow: OCR fast path → text model → vision model fallback

Primary model: qwen2.5vl:7b (reliable structured extraction)
Backup model: qwen2.5vl:7b
Falls back to existing OCR regex if both AI models fail.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import logging
from datetime import date, datetime
from typing import Any

from core.ai_gateway import AIGatewayError, chat, health
from core.ai_model_routing import model_for
from core.ocr_policy import ocr_enabled

logger = logging.getLogger(__name__)

# ── Configuration (shared with ollama_invite_extract) ───────────────────────
OLLAMA_VISION_MODEL = model_for("payment_screenshot_vision")
# The backup runs when the primary vision call fails, so it has to be capable of
# the same job. Deliberately its own variable: OLLAMA_BACKUP_VISION_MODEL is
# shared with invite extraction and is set to moondream in production, which
# cannot read a 22-digit transaction ID at all - it returns empty strings, so a
# failed fallback looked identical to "no identifiers in the image".
OLLAMA_BACKUP_VISION_MODEL = os.environ.get(
    "OLLAMA_PAYMENT_BACKUP_VISION_MODEL", "qwen2.5vl:7b"
)
OLLAMA_REASONING_MODEL = model_for("reasoning_text")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "900"))
OLLAMA_TEXT_TIMEOUT = int(os.environ.get("OLLAMA_TEXT_TIMEOUT", "60"))


# ── Prompt ──────────────────────────────────────────────────────────────────
PAYMENT_EXTRACTION_PROMPT = """You are a payment screenshot extraction assistant for Indian UPI/bank transfers.
Read the uploaded screenshot carefully. It may be from PhonePe, GPay, Paytm, CRED, BHIM, or any bank app.

Return ONLY valid JSON. Do not explain. Do not use markdown. Do not wrap JSON inside code blocks.

IMPORTANT: Today's date is {today}.

Extract these fields from the payment screenshot:

Schema:
{{"payment_status": "SUCCESS|FAILED|PENDING|UNKNOWN", "direction": "PAID_TO|RECEIVED_FROM|TRANSFERRED_TO|UNKNOWN", "amount_text": "", "amount": 0, "visible_amounts": [], "currency": "INR", "sender_name": "", "sender_upi_id": "", "sender_phone_number": "", "sender_account_identifier": "", "receiver_name": "", "receiver_upi_id": "", "receiver_phone_number": "", "receiver_account_identifier": "", "credited_to_identifier": "", "debited_from_identifier": "", "transaction_id": "", "utr": "", "transaction_date": "YYYY-MM-DD", "transaction_time": "hh:mm AM/PM", "provider": "", "confidence": {{"payment_status": 0.0, "direction": 0.0, "amount": 0.0, "receiver_name": 0.0, "receiver_upi_id": 0.0, "receiver_phone_number": 0.0, "transaction_id": 0.0, "utr": 0.0}}, "missing_fields": [], "warnings": [], "is_payment_screenshot": true}}

Rules:
- "amount_text" is the primary amount COPIED EXACTLY as printed, including the
  currency symbol and every comma. Example: "₹30,000". Copy it character for
  character. Do NOT reformat it, round it, or convert it to another unit.
- "amount" is the same figure as integer rupees. Example: ₹30,000 is 30000.
  Preserve every digit. Never convert to paise and never do arithmetic on it.
- "visible_amounts" lists every payment-like rupee amount visible in the receipt.
- "transaction_id" and "utr" are the two identifiers the backend authorises on.
  Copy each one DIGIT FOR DIGIT exactly as printed. These are long - a UTR is
  typically 12 or 16 digits and a PhonePe transaction ID is a "T" followed by
  ~21 digits. Reproduce every character, keep any leading letter, and never
  shorten, truncate, group, space out, re-order or summarise them. If you
  cannot read every character with certainty, leave the field empty and list it
  in "missing_fields" rather than guessing a shorter value.
- "transaction_id" and "utr" are DIFFERENT fields even when both appear. Never
  copy one into the other, and never merge them.
- "payment_status" is SUCCESS, PENDING, FAILED, or UNKNOWN.
- "direction" is critical. A person under "Received from" is the sender, not the receiver.
- For RECEIVED_FROM, put the account after "Credited to" in "credited_to_identifier".
- Never copy sender details into receiver fields.
- "receiver_phone_number" is the complete phone shown for the paid-to recipient.
- "receiver_account_identifier" is the receiving bank account identifier, if visible.
- Confidence values are 0.0-1.0. The backend makes the final authorization decision.
- If the image is not a payment receipt, set "is_payment_screenshot": false.
- If a field is not visible, leave it empty and include its name in "missing_fields".
"""


# ── Empty result template ───────────────────────────────────────────────────
def _empty_extraction() -> dict[str, Any]:
    return {
        "amount": 0,
        "amount_minor": 0,
        "currency": "INR",
        "direction": "UNKNOWN",
        "sender_name": "",
        "sender_upi_id": "",
        "sender_phone_number": "",
        "sender_account_identifier": "",
        "receiver_name": "",
        "receiver_upi_id": "",
        "receiver_phone": "",
        "receiver_phone_number": "",
        "receiver_account": "",
        "receiver_account_identifier": "",
        "credited_to_identifier": "",
        "debited_from_identifier": "",
        "utr_number": "",
        "reference_number": "",
        "transaction_id": "",
        "payment_app": "",
        "bank_name": "",
        "payment_date": "",
        "payment_time": "",
        "status": "unknown",
        "payment_method": "unknown",
        "receiver_type": "unknown",
        "confidence_score": 0,
        "is_payment_screenshot": False,
        "warnings": [],
        "missing_fields": [],
        "confidence": {},
        "raw_detected_text": "",
        "extraction_source": "",
        "extraction_method": "",
        "primary_model": "",
        "backup_model": "",
        "detected_by": "",
        # Verification fields (populated by verify_payment_against_due)
        "verified": False,
        "verification_result": "",
        "amount_due": 0,
        "amount_sufficient": False,
    }


# ── Ollama helpers (reuse pattern from ollama_invite_extract) ───────────────
def _is_ollama_available() -> bool:
    """Check if Ollama is running and accessible."""
    status = health(model=OLLAMA_REASONING_MODEL, timeout=5)
    return bool(status.get("endpoint_reachable") and status.get("model_available"))


def _call_vision_model(
    model_name: str,
    image_base64: str,
    prompt: str,
    *,
    timeout: int = OLLAMA_TIMEOUT,
) -> str | None:
    """Call Ollama vision model with an image and prompt."""
    try:
        result = chat(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            images=[image_base64],
            timeout=timeout,
            temperature=0.1,
            num_predict=2048,
            workload="payment_screenshot_vision",
        )
        return result.content or None
    except AIGatewayError as exc:
        logger.warning("Payment vision failed model=%s code=%s", model_name, exc.code)
        return None


def _call_text_model(prompt: str, *, timeout: int = OLLAMA_TEXT_TIMEOUT) -> str | None:
    """Call Ollama text model (qwen2.5:7b) for fast OCR text cleanup."""
    try:
        result = chat(
            model=OLLAMA_REASONING_MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
            temperature=0.1,
            num_predict=2048,
            workload="payment_screenshot_text",
        )
        return result.content or None
    except AIGatewayError as exc:
        logger.warning("Payment text failed model=%s code=%s", OLLAMA_REASONING_MODEL, exc.code)
        return None


def _reference_missing(parsed: dict[str, Any] | None) -> bool:
    """A recognised receipt that carries no reference of any kind.

    UPI receipts show a UTR, a transaction id, or both; a bank transfer shows a
    reference number. A reading with none of the three is the model having
    skipped the transfer-details block, not a receipt that lacks one -- so it is
    worth reading again rather than refusing the payment on it.
    """
    if not parsed or not parsed.get("is_payment_screenshot"):
        return False
    return not any(
        str(parsed.get(field) or "").strip()
        for field in ("utr", "utr_number", "transaction_id", "reference_number")
    )


# ── JSON parsing ────────────────────────────────────────────────────────────
def _parse_json_response(raw: str) -> dict[str, Any] | None:
    """Parse JSON from model response, handling markdown code blocks."""
    if not raw:
        return None
    text = raw.strip()
    # Strip markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    # Try to find JSON object in the text
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    return None


# ── OCR fallback (Tesseract) ───────────────────────────────────────────────
def _run_tesseract_ocr(image_data: bytes) -> str | None:
    """Extract text from image using Tesseract."""
    try:
        import io
        from PIL import Image
        import pytesseract

        img = Image.open(io.BytesIO(image_data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        default_text = pytesseract.image_to_string(img, lang="eng")
        sparse_text = pytesseract.image_to_string(img, lang="eng", config="--psm 11")
        return "\n".join(part for part in (default_text, sparse_text) if part)
    except Exception as exc:
        logger.warning("Tesseract OCR failed: %s", exc)
        return None


def _extract_amount_from_text(text: str) -> float:
    """Extract payment amount from OCR text using regex patterns."""
    amounts = _extract_amount_candidates_from_text(text)
    if not amounts:
        return 0
    # Return the most common amount, or the largest reasonable one
    from collections import Counter
    counts = Counter(int(a) for a in amounts if 500 <= a <= 1_000_000)
    if counts:
        most_common = counts.most_common(1)[0]
        if most_common[1] >= 2:
            return float(most_common[0])
        return float(max(counts.keys()))
    return 0


def _normalize_amount_number(value: Any) -> int:
    """Parse a rupee amount without dropping comma-grouped digits."""
    if value is None or isinstance(value, bool):
        return 0
    cleaned = re.sub(r"(?i)(?:inr|rs\.?)", "", str(value))
    cleaned = cleaned.replace("₹", "").replace(",", "").strip()
    match = re.search(r"\d+(?:\.\d{1,2})?", cleaned)
    if not match:
        return 0
    try:
        return int(float(match.group(0)))
    except (TypeError, ValueError):
        return 0


def _extract_amount_candidates_from_text(text: str) -> list[int]:
    """Return every visible payment-like rupee amount found by OCR."""
    candidates: list[int] = []
    # Amounts carrying an explicit currency marker are trustworthy; a bare
    # comma-grouped number is not, because OCR reads the rupee sign as a 7.
    marked: set[int] = set()
    patterns = (
        (r"\u20b9\s*([\d,]+(?:\.\d{1,2})?)", True),
        (r"(?i)\b(?:INR|Rs\.?)\s*([\d,]+(?:\.\d{1,2})?)", True),
        (r"%\s*([\d,]+(?:\.\d{1,2})?)", True),
        (r"(?<![\d,])(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?)(?![\d,])", False),
    )
    for pattern, has_marker in patterns:
        for match in re.finditer(pattern, text or ""):
            amount = _normalize_amount_number(match.group(1))
            if 500 <= amount <= 1_000_000:
                candidates.append(amount)
                if has_marker:
                    marked.add(amount)
    status_amount = re.search(
        r"(?i)(?:payment|transaction)\s+successful\s*\n+\s*([2%]?\d{3,7})\b",
        text or "",
    )
    if status_amount:
        raw_amount = status_amount.group(1)
        if raw_amount.startswith(("2", "%")) and len(raw_amount) >= 5:
            raw_amount = raw_amount[1:]
        amount = _normalize_amount_number(raw_amount)
        if 500 <= amount <= 1_000_000:
            candidates.append(amount)
    return _drop_rupee_glyph_misreads(candidates, marked)


def _drop_rupee_glyph_misreads(candidates: list[int], marked: set[int]) -> list[int]:
    """Drop amounts that are only a misread rupee sign away from a real one.

    OCR regularly reads the sign in a value like the rupee-prefixed 42,000 as
    the digit 7, producing a phantom 742,000 beside the genuine figure. Both
    then count as visible amounts, so the cross-check reports a conflict on a
    receipt that only ever showed one number and blocks a valid expense.

    A candidate is discarded only when it carried no currency marker of its own
    and the same digits without the leading 7 were seen with one. A receipt
    that genuinely shows a rupee-marked 742,000 keeps its marker, so a real
    two-amount conflict is still reported.
    """
    return [
        amount
        for amount in candidates
        if not (
            amount not in marked
            and str(amount).startswith("7")
            and int(str(amount)[1:] or 0) in marked
        )
    ]


def _amount_from_model(extracted: dict[str, Any]) -> int:
    """Normalize model rupee and legacy paise fields while preserving digits."""
    direct_amount = _normalize_amount_number(extracted.get("amount"))
    if direct_amount:
        return direct_amount
    minor_amount = _normalize_amount_number(extracted.get("amount_minor"))
    return minor_amount // 100 if minor_amount else 0


def _cross_check_visible_amounts(
    result: dict[str, Any], ocr_text: str | None
) -> dict[str, Any]:
    """Reconcile vision output with all payment amounts visible to OCR."""
    checked = dict(result)
    vision_amount = int(checked.get("amount") or 0)
    model_visible_amounts = [
        amount
        for amount in (
            _normalize_amount_number(value)
            for value in (checked.get("visible_amounts") or [])
        )
        if 500 <= amount <= 1_000_000
    ]
    unique_amounts = sorted(
        set([*_extract_amount_candidates_from_text(ocr_text or ""), *model_visible_amounts])
    )
    ocr_amount = int(_extract_amount_from_text(ocr_text or ""))
    visible_amount = ocr_amount or (max(unique_amounts) if unique_amounts else 0)
    checked["vision_amount"] = vision_amount
    checked["ocr_amount"] = ocr_amount
    checked["ocr_amount_candidates"] = unique_amounts

    mismatch_reason = ""
    if vision_amount and visible_amount and vision_amount != visible_amount:
        source_label = "OCR amount" if ocr_amount else "Visible amount"
        mismatch_reason = (
            f"{source_label} INR {visible_amount:,} disagrees with vision amount "
            f"INR {vision_amount:,}."
        )
        checked["amount"] = visible_amount
    elif len(unique_amounts) > 1:
        mismatch_reason = "OCR found conflicting visible amounts: " + ", ".join(
            f"INR {amount:,}" for amount in unique_amounts
        ) + "."

    checked["amount_mismatch_reason"] = mismatch_reason
    checked["amount_crosscheck"] = (
        "mismatch" if mismatch_reason else "matched" if vision_amount and visible_amount else "single_source"
    )
    if mismatch_reason:
        checked["verified"] = False
        warnings = list(checked.get("warnings") or [])
        if mismatch_reason not in warnings:
            warnings.append(mismatch_reason)
        checked["warnings"] = warnings
        logger.warning("Payment amount cross-check failed: %s", mismatch_reason)
    return checked


def _extract_utr_from_text(text: str) -> str:
    """Extract UTR/reference number from OCR text."""
    # UTR pattern (12-digit number)
    m = re.search(r'(?:UTR|utr|Utr)[:\s]*([A-Za-z0-9]{12,22})', text)
    if m:
        return m.group(1)
    m = re.search(
        r"(?:UPI\s*)?(?:Reference|Ref)\s*(?:ID|No|Number)?[:\s]*([A-Za-z0-9]{8,22})",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # Reference number pattern
    m = re.search(r'(?:Ref|ref|REF)\s*(?:No|no|NO)?[:\s]*([A-Za-z0-9]{8,22})', text)
    if m:
        return m.group(1)
    # Transaction ID (labeled)
    m = re.search(r'(?:Txn|txn|TXN|Transaction)\s*(?:ID|Id|id)?[:\s]*([A-Za-z0-9]{8,30})', text)
    if m:
        return m.group(1)
    # Standalone 12-16 digit number (UTR from various banks)
    m = re.search(r'\b(\d{12,16})\b', text)
    if m:
        return m.group(1)
    return ""


def _extract_transaction_id_from_text(text: str) -> str:
    """Extract Transaction ID from OCR text (distinct from UTR).

    Handles PhonePe T-prefix IDs (e.g. T2605191149403948648792) and other
    labeled transaction identifiers that _extract_utr_from_text may miss
    when the label and value are on separate lines.
    """
    # PhonePe-style T-prefix transaction ID (T followed by 15-30 digits)
    m = re.search(r'\b(T\d{15,30})\b', text)
    if m:
        return m.group(1)
    # Labeled: "Transaction ID" possibly on a separate line from the value
    m = re.search(
        r'(?:Transaction|Txn)\s*(?:ID|Id|id)\s*[:\s]*\n?\s*([A-Za-z0-9]{10,30})',
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # Google Pay style "UPI transaction ID" or just transaction reference
    m = re.search(
        r'(?:UPI\s+)?[Tt]ransaction\s*(?:ID|Id|id|ref|reference)\s*[:\s]*([A-Za-z0-9]{10,30})',
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    return ""


def _detect_payment_app(text: str) -> str:
    """Detect payment app from OCR text."""
    text_lower = text.lower()
    apps = [
        ("phonepe", "PhonePe"),
        ("phone pe", "PhonePe"),
        ("gpay", "GPay"),
        ("google pay", "GPay"),
        ("paytm", "Paytm"),
        ("cred", "CRED"),
        ("bhim", "BHIM"),
        ("hdfc", "HDFC"),
        ("icici", "ICICI"),
        ("sbi", "SBI"),
        ("axis", "Axis"),
        ("kotak", "Kotak"),
        ("idfc", "IDFC"),
        ("yes bank", "Yes Bank"),
    ]
    for keyword, name in apps:
        if keyword in text_lower:
            return name
    return ""


def _detect_status(text: str) -> str:
    """Detect payment status from OCR text."""
    text_lower = text.lower()
    if any(w in text_lower for w in ("success", "successful", "completed", "paid", "done")):
        return "success"
    if any(w in text_lower for w in ("pending", "processing", "initiated")):
        return "pending"
    if any(w in text_lower for w in ("failed", "failure", "declined", "rejected")):
        return "failed"
    return "unknown"


def _extract_date_from_text(text: str) -> str:
    """Try to extract payment date from OCR text."""
    # DD Mon YYYY or DD-Mon-YYYY
    m = re.search(
        r'(\d{1,2})\s*[-/]?\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s*[-/]?\s*(\d{2,4})',
        text, re.IGNORECASE
    )
    if m:
        day = int(m.group(1))
        mon_str = m.group(2)[:3].capitalize()
        year = int(m.group(3))
        if year < 100:
            year += 2000
        months = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                  "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
        mon = months.get(mon_str, 0)
        if mon and 1 <= day <= 31:
            return f"{year}-{mon:02d}-{day:02d}"
    # DD/MM/YYYY or DD-MM-YYYY
    m = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})', text)
    if m:
        day, mon, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        if 1 <= mon <= 12 and 1 <= day <= 31:
            return f"{year}-{mon:02d}-{day:02d}"
    return ""


def _drop_sender_values_from_receiver_fields(result: dict) -> None:
    """Clear receiver identifiers that merely echo the sender's own.

    Compared on digits and normalised handles, so "State Bank of India 4485"
    and "4485" are recognised as the same value however each was written. Only
    the receiver side is cleared: the sender fields are left exactly as read, so
    nothing is lost and the receipt can still be audited in full.
    """
    def digits(value: object) -> str:
        return "".join(ch for ch in str(value or "") if ch.isdigit())

    def handle(value: object) -> str:
        return str(value or "").strip().lower()

    sender_accounts = {
        digits(result.get("sender_account_identifier")),
        digits(result.get("sender_account")),
        digits(result.get("debited_from_identifier")),
    } - {""}
    sender_handles = {
        handle(result.get("sender_upi_id")),
        handle(result.get("debited_from_identifier")),
    } - {""}
    sender_phones = {
        digits(result.get("sender_phone_number")),
        digits(result.get("sender_phone")),
    } - {""}

    if digits(result.get("receiver_account")) and digits(result.get("receiver_account")) in sender_accounts:
        result["receiver_account"] = ""
        result["receiver_account_identifier"] = ""
    if handle(result.get("receiver_upi_id")) and handle(result.get("receiver_upi_id")) in sender_handles:
        result["receiver_upi_id"] = ""
    if digits(result.get("receiver_phone")) and digits(result.get("receiver_phone")) in sender_phones:
        result["receiver_phone"] = ""
        result["receiver_phone_number"] = ""

def _extract_receiver_upi_from_text(text: str) -> str:
    """Extract the visible paid-to UPI identifier from OCR text."""
    match = re.search(r"\b[A-Za-z0-9._-]{2,}@[A-Za-z][A-Za-z0-9.-]{1,}\b", text)
    return match.group(0).lower() if match else ""


def _extract_receiver_phone_from_text(text: str) -> str:
    """Extract a visible Indian recipient phone number from OCR text."""
    match = re.search(r"(?:\+?91[\s-]*)?[6-9](?:[\s-]*\d){9}\b", text)
    return match.group(0).strip() if match else ""


# ── OCR-based fast extraction (no AI needed) ───────────────────────────────
def _ocr_regex_extraction(ocr_text: str) -> dict[str, Any] | None:
    """Try to extract payment details from OCR text using regex only.

    Returns a filled dict if we can get amount + (UTR or transaction ID or status=success).
    """
    amount = _extract_amount_from_text(ocr_text)
    if amount < 500:
        return None  # Not a meaningful payment amount

    utr = _extract_utr_from_text(ocr_text)
    transaction_id = _extract_transaction_id_from_text(ocr_text)
    status = _detect_status(ocr_text)
    app = _detect_payment_app(ocr_text)
    pay_date = _extract_date_from_text(ocr_text)
    receiver_upi = _extract_receiver_upi_from_text(ocr_text)
    receiver_phone = _extract_receiver_phone_from_text(ocr_text)

    # Need at least amount + one of (UTR, transaction ID, success status) to trust regex
    if not utr and not transaction_id and status != "success":
        return None

    result = _empty_extraction()
    result["amount"] = int(amount)
    result["utr_number"] = utr
    result["transaction_id"] = transaction_id
    result["status"] = status
    result["payment_app"] = app
    result["payment_date"] = pay_date
    result["receiver_upi_id"] = receiver_upi
    result["receiver_phone"] = receiver_phone
    result["is_payment_screenshot"] = True
    result["confidence_score"] = min(85, 40 + (20 if utr else 0) + (15 if transaction_id else 0) + (15 if status == "success" else 0) + (10 if app else 0))
    result["extraction_source"] = "ocr_regex"
    result["extraction_method"] = "regex_fast"
    result["primary_model"] = "tesseract+regex"
    result["detected_by"] = "OCR + regex"
    return result


# ── Text model cleanup (OCR text → structured JSON) ────────────────────────
def _try_text_model_cleanup(ocr_text: str) -> dict[str, Any] | None:
    """Send OCR text to qwen2.5:7b for structured extraction."""
    today = date.today()
    prompt = f"""You are a payment screenshot text parser.
The following text was extracted via OCR from a UPI/bank payment screenshot.
Extract the payment details into JSON.

Return ONLY valid JSON. No explanation. No markdown.

Today's date: {today.isoformat()}

OCR Text:
---
{ocr_text[:3000]}
---

Schema:
{{"amount": 0, "sender_name": "", "sender_upi_id": "", "receiver_name": "", "receiver_upi_id": "", "receiver_phone": "", "utr_number": "", "reference_number": "", "transaction_id": "", "payment_app": "", "bank_name": "", "payment_date": "YYYY-MM-DD", "payment_time": "hh:mm AM/PM", "status": "", "payment_method": "", "confidence_score": 0, "is_payment_screenshot": true, "warnings": []}}

Rules:
- "amount" must be a number (no ₹, no commas). Example: 10000
- "status": "success", "pending", "failed", or "unknown"
- "payment_method": "upi", "neft", "imps", "rtgs", "cash", or "unknown"
- If the text is NOT from a payment screenshot, set "is_payment_screenshot": false
"""
    response = _call_text_model(prompt)
    if not response:
        return None
    parsed = _parse_json_response(response)
    if not parsed:
        return None
    if not parsed.get("is_payment_screenshot"):
        return None
    # Ensure amount is a number
    try:
        parsed["amount"] = int(float(parsed.get("amount", 0)))
    except (ValueError, TypeError):
        parsed["amount"] = 0
    if parsed["amount"] < 500:
        return None
    return parsed


# ── Prompt builder ──────────────────────────────────────────────────────────
def _get_payment_prompt() -> str:
    """Build the payment extraction prompt with today's date."""
    today = date.today()
    return PAYMENT_EXTRACTION_PROMPT.format(today=today.isoformat())


# ── Main extraction function ────────────────────────────────────────────────
def extract_payment_with_ollama(
    image_data: bytes,
    mime_type: str = "image/jpeg",
    *,
    allow_slow_ai: bool = True,
    use_ocr: bool | None = None,
    crosscheck_ocr: bool = False,
) -> dict[str, Any]:
    """Extract payment details from a screenshot using hybrid OCR + AI approach.

    Hybrid flow (optimized for speed):
      1. OCR image → raw text (Tesseract, instant)
      2. If OCR finds amount + UTR/status, return immediately (regex fast path)
      3. If OCR has text but regex incomplete, send to qwen2.5:7b text model (~10-30s)
      4. Only if OCR fails, call qwen2.5vl:7b vision model (~5 min)
      5. If vision fails, try the backup vision model
      6. If all AI fails, return whatever regex found

    Ollama runs on the developer's laptop, tunneled to VPS via SSH.
    """
    # Check if Ollama is reachable
    ollama_available = _is_ollama_available()
    if not ollama_available:
        logger.info("Ollama not reachable (SSH tunnel may be down), using OCR only")

    # ── Step 1: OCR + regex (instant) ───────────────────────────────────────
    run_ocr = (
        ocr_enabled()
        if use_ocr is None
        else bool((use_ocr or crosscheck_ocr) and ocr_enabled())
    )
    ocr_text = _run_tesseract_ocr(image_data) if run_ocr else None
    if not run_ocr:
        logger.info("Global OCR is disabled; sending payment proof directly to Ollama vision")

    if ocr_text and len(ocr_text) > 10:
        logger.info("OCR extracted %d chars for payment", len(ocr_text))

        # OCR is an independent cross-check. A full verification request still
        # sends the original image bytes to the vision model.
        regex_result = _ocr_regex_extraction(ocr_text)
        if regex_result and regex_result["amount"] >= 500:
            logger.info(
                "Regex found amount=₹%d, UTR=%s, status=%s — using fast path",
                regex_result["amount"],
                regex_result.get("utr_number", ""),
                regex_result.get("status", ""),
            )
            regex_result["raw_detected_text"] = ocr_text[:1000]
            if not allow_slow_ai or not ollama_available:
                return regex_result

        # ── Step 2: Text model cleanup (~10-30s) ────────────────────────────
        if not allow_slow_ai:
            fallback = _empty_extraction()
            fallback["amount"] = int(_extract_amount_from_text(ocr_text))
            fallback["utr_number"] = _extract_utr_from_text(ocr_text)
            fallback["status"] = _detect_status(ocr_text)
            fallback["payment_app"] = _detect_payment_app(ocr_text)
            fallback["payment_date"] = _extract_date_from_text(ocr_text)
            fallback["receiver_upi_id"] = _extract_receiver_upi_from_text(ocr_text)
            fallback["receiver_phone"] = _extract_receiver_phone_from_text(ocr_text)
            fallback["is_payment_screenshot"] = fallback["amount"] >= 500
            fallback["raw_detected_text"] = ocr_text[:1000]
            fallback["extraction_source"] = "ocr_interactive"
            fallback["extraction_method"] = "ocr_bounded"
            fallback["detected_by"] = "OCR (interactive upload)"
            fallback["confidence_score"] = 35 if fallback["amount"] > 0 else 0
            fallback["warnings"] = ["AI enrichment skipped to keep the upload responsive"]
            return fallback

        if ollama_available and not regex_result:
            logger.info("Regex incomplete, trying text model for payment extraction")
            text_result = _try_text_model_cleanup(ocr_text)
            if text_result and text_result.get("amount", 0) >= 500:
                # Merge into our template
                result = _empty_extraction()
                result.update(text_result)
                result["extraction_source"] = "ocr_ai_cleanup"
                result["extraction_method"] = "hybrid_fast"
                result["primary_model"] = OLLAMA_REASONING_MODEL
                result["detected_by"] = f"OCR + {OLLAMA_REASONING_MODEL}"
                result["raw_detected_text"] = ocr_text[:1000]
                result["is_payment_screenshot"] = True
                logger.info(
                    "Text model extracted: amount=₹%d, UTR=%s",
                    result["amount"],
                    result.get("utr_number", ""),
                )
                if not allow_slow_ai:
                    return result
    else:
        logger.info("OCR text too short (%d chars), going to vision model", len(ocr_text or ""))

    if not allow_slow_ai:
        fallback = _empty_extraction()
        fallback["extraction_source"] = "interactive_no_ocr"
        fallback["extraction_method"] = "ocr_bounded"
        fallback["detected_by"] = "OCR (interactive upload)"
        fallback["warnings"] = ["Payment details need manual review; AI enrichment was skipped"]
        return fallback

    # ── Step 3: Vision model (slow path, ~5 min) ────────────────────────────
    if not ollama_available:
        # Return whatever OCR found (even if incomplete)
        fallback = _empty_extraction()
        if ocr_text:
            fallback["amount"] = int(_extract_amount_from_text(ocr_text))
            fallback["utr_number"] = _extract_utr_from_text(ocr_text)
            fallback["status"] = _detect_status(ocr_text)
            fallback["payment_app"] = _detect_payment_app(ocr_text)
            fallback["payment_date"] = _extract_date_from_text(ocr_text)
            fallback["receiver_upi_id"] = _extract_receiver_upi_from_text(ocr_text)
            fallback["receiver_phone"] = _extract_receiver_phone_from_text(ocr_text)
            fallback["is_payment_screenshot"] = fallback["amount"] >= 500
            fallback["raw_detected_text"] = ocr_text[:1000]
        fallback["extraction_source"] = "ocr_only"
        fallback["extraction_method"] = "ocr_fallback"
        fallback["detected_by"] = "OCR (Ollama unavailable)"
        fallback["confidence_score"] = 30 if fallback["amount"] > 0 else 0
        fallback["warnings"] = ["Ollama unavailable — OCR-only extraction, may be incomplete"]
        return fallback

    img_b64 = base64.b64encode(image_data).decode("utf-8")
    prompt = _get_payment_prompt()

    logger.info("Calling vision model: %s for payment extraction", OLLAMA_VISION_MODEL)
    start = time.time()
    response = _call_vision_model(OLLAMA_VISION_MODEL, img_b64, prompt, timeout=OLLAMA_TIMEOUT)
    elapsed = time.time() - start
    logger.info("Vision model responded in %.1fs", elapsed)

    extracted = None
    used_model = OLLAMA_VISION_MODEL
    used_raw_response = response or ""

    if response:
        extracted = _parse_json_response(response)

    # ── Step 4: Backup vision model ─────────────────────────────────────────
    # Also re-read when the primary recognised the receipt but came back without
    # any reference at all. On 10 Nov a candidate's PhonePe receipt was refused
    # ten times in ten minutes -- is_payment_screenshot true, amount read, UTR
    # and transaction id both empty, so the refusal said the evidence was
    # incomplete -- and the eleventh upload of the same file was accepted and
    # verified. The screenshot carries both identifiers plainly; the model had
    # skipped its Transfer Details block. Without this the reading was final,
    # because the backup only ran when the receipt was not recognised at all.
    if (
        (not extracted
         or not extracted.get("is_payment_screenshot")
         or _reference_missing(extracted))
        and OLLAMA_BACKUP_VISION_MODEL != OLLAMA_VISION_MODEL
    ):
        logger.warning(
            "Primary vision %s for payment, trying backup: %s",
            "read no reference" if extracted else "failed",
            OLLAMA_BACKUP_VISION_MODEL,
        )
        backup_response = _call_vision_model(
            OLLAMA_BACKUP_VISION_MODEL, img_b64, prompt, timeout=OLLAMA_TIMEOUT
        )
        if backup_response:
            backup_parsed = _parse_json_response(backup_response)
            if backup_parsed and backup_parsed.get("is_payment_screenshot"):
                # Never trade a reading that has a reference for one that does
                # not: the backup is here to recover what the primary missed.
                recovered = not _reference_missing(backup_parsed)
                if recovered or not extracted or not extracted.get("is_payment_screenshot"):
                    extracted = backup_parsed
                    used_model = OLLAMA_BACKUP_VISION_MODEL
                    used_raw_response = backup_response

    # ── Build final result ──────────────────────────────────────────────────
    if extracted and extracted.get("is_payment_screenshot"):
        result = _empty_extraction()
        result.update(extracted)
        # Normalize the V2 extraction contract into the legacy-compatible
        # fields consumed by the central verification service.
        result["status"] = str(
            result.get("payment_status") or result.get("status") or "unknown"
        ).lower()
        result["utr_number"] = result.get("utr") or result.get("utr_number") or ""
        result["payment_date"] = (
            result.get("transaction_date") or result.get("payment_date") or ""
        )
        result["payment_time"] = (
            result.get("transaction_time") or result.get("payment_time") or ""
        )
        result["payment_app"] = result.get("provider") or result.get("payment_app") or ""
        result["receiver_phone"] = (
            result.get("receiver_phone_number")
            or result.get("receiver_phone")
            or ""
        )
        result["receiver_account"] = (
            result.get("receiver_account_identifier")
            or result.get("receiver_account")
            or ""
        )
        # A receiver identifier that is really the sender's is worse than none.
        #
        # A Google Pay receipt shows the payee as "To: J RAVINDER, PhonePe
        # ••••1111@ybl" and the payer's own funding account below it as "State
        # Bank of India 4485". The model put 4485 in receiver_account_identifier,
        # and from there it was treated as the receiver's account: it matched no
        # registered receiver, and because a stable identifier was now present
        # alongside a matching receiver NAME, the payment was reported as a
        # receiver-identity conflict -- "the receiver name resembles a
        # registered account, but the visible payment identifier does not match
        # it" -- for a payment to an account that is registered.
        #
        # The schema has always had somewhere else for those values to go, so
        # anything that merely repeats the sender's side is dropped rather than
        # carried into receiver matching.
        _drop_sender_values_from_receiver_fields(result)
        result["amount"] = _amount_from_model(result)
        if not result.get("confidence_score") and isinstance(
            result.get("confidence"), dict
        ):
            confidence_dict = result["confidence"]
            relevant_values = []
            for field, value in confidence_dict.items():
                if not isinstance(value, (int, float)):
                    continue
                if value == 0.0 and field in {
                    "receiver_phone_number",
                    "sender_phone_number",
                    "transaction_id",
                    "utr",
                    "receiver_account_identifier",
                    "receiver_upi_id",
                }:
                    if not result.get(field):
                        continue
                relevant_values.append(float(value))
            if not relevant_values:
                relevant_values = [
                    float(v) for v in confidence_dict.values() if isinstance(v, (int, float))
                ]
            if relevant_values:
                average = sum(relevant_values) / len(relevant_values)
                result["confidence_score"] = round(
                    average if average > 1 else average * 100
                )
        result["extraction_source"] = "vision_model"
        result["extraction_method"] = "vision"
        result["primary_model"] = used_model
        # The central payment engine persists this for audit and removes it
        # from API responses before returning to the browser.
        result["_raw_model_response"] = used_raw_response
        result["detected_by"] = f"Vision ({used_model})"
        result["is_payment_screenshot"] = True
        if ocr_text:
            result["raw_detected_text"] = ocr_text[:1000]
            result["utr_number"] = result.get("utr_number") or _extract_utr_from_text(ocr_text)
            result["transaction_id"] = (
                result.get("transaction_id") or _extract_transaction_id_from_text(ocr_text)
            )
            result["receiver_upi_id"] = (
                result.get("receiver_upi_id") or _extract_receiver_upi_from_text(ocr_text)
            )
            result["receiver_phone"] = (
                result.get("receiver_phone") or _extract_receiver_phone_from_text(ocr_text)
            )
        result = _cross_check_visible_amounts(result, ocr_text)
        logger.info("Vision extracted: amount=₹%d, UTR=%s", result["amount"], result.get("utr_number", ""))
        return result

    # ── Step 5: Final fallback — return OCR data if any ─────────────────────
    fallback = _empty_extraction()
    if ocr_text:
        fallback["amount"] = int(_extract_amount_from_text(ocr_text))
        fallback["utr_number"] = _extract_utr_from_text(ocr_text)
        fallback["transaction_id"] = _extract_transaction_id_from_text(ocr_text)
        fallback["status"] = _detect_status(ocr_text)
        fallback["payment_app"] = _detect_payment_app(ocr_text)
        fallback["payment_date"] = _extract_date_from_text(ocr_text)
        fallback["receiver_upi_id"] = _extract_receiver_upi_from_text(ocr_text)
        fallback["receiver_phone"] = _extract_receiver_phone_from_text(ocr_text)
        fallback["is_payment_screenshot"] = fallback["amount"] >= 500
        fallback["raw_detected_text"] = ocr_text[:1000]
    fallback["extraction_source"] = "ocr_fallback" if ocr_text else "vision_failed"
    fallback["extraction_method"] = "ocr_fallback" if ocr_text else "vision"
    fallback["detected_by"] = "OCR (AI models failed)" if ocr_text else "Ollama Vision failed"
    fallback["confidence_score"] = 25 if fallback["amount"] > 0 else 0
    fallback["warnings"] = (
        ["All AI models failed — OCR-only extraction"]
        if ocr_text
        else ["All configured Ollama Vision models failed"]
    )
    fallback["_raw_model_response"] = used_raw_response
    return fallback


# ── Verification against candidate due amount ───────────────────────────────
def verify_payment_against_due(
    extraction: dict[str, Any],
    amount_due: int,
    *,
    tolerance_pct: float = 0.0,
) -> dict[str, Any]:
    """Verify extracted payment amount against what's due.

    Adds verification fields to the extraction dict:
      - verified: bool — True if amount meets threshold
      - verification_result: str — human-readable verdict
      - amount_due: int — what was expected
      - amount_sufficient: bool — True if amount >= due (within tolerance)

    tolerance_pct: allow 5% under for OCR misreads (e.g., ₹9,500 read as ₹9500
    when ₹10,000 is due — that's rejected. But ₹9,800 → accept as close enough).
    """
    result = dict(extraction)
    detected = int(result.get("amount", 0))
    result["amount_due"] = amount_due

    if not result.get("is_payment_screenshot"):
        result["verified"] = False
        result["verification_result"] = "Not a payment screenshot"
        result["amount_sufficient"] = False
        return result

    if detected <= 0:
        result["verified"] = False
        result["verification_result"] = "Could not detect payment amount"
        result["amount_sufficient"] = False
        return result

    if amount_due <= 0:
        # No specific amount required — just verify it's a payment
        result["verified"] = True
        result["verification_result"] = f"₹{detected:,} payment detected (no minimum required)"
        result["amount_sufficient"] = True
        return result

    min_acceptable = amount_due * (1 - max(0.0, tolerance_pct))

    if detected >= min_acceptable:
        result["verified"] = True
        result["amount_sufficient"] = True
        if detected >= amount_due:
            result["verification_result"] = (
                f"✓ ₹{detected:,} payment verified (₹{amount_due:,} was due)"
            )
        else:
            result["verification_result"] = (
                f"✓ ₹{detected:,} payment accepted (₹{amount_due:,} due, within tolerance)"
            )
    else:
        result["verified"] = False
        result["amount_sufficient"] = False
        result["verification_result"] = (
            f"✗ ₹{detected:,} detected but ₹{amount_due:,} is due. "
            f"Short by ₹{amount_due - detected:,}."
        )

    return result


# ── Confidence narrative (human-readable summary for payout modal) ──────────

def _rule_based_narrative(
    extraction: dict[str, Any],
    candidate_name: str,
    expected_amount: int,
    received_amount: int,
) -> str:
    """Deterministic narrative when Ollama is unavailable.

    Produces a sentence like:
      "Amount ₹10,000 matches expected ₹10,000 · UTR 123456789012 · PhonePe ·
       date 2025-06-01 · status success — looks valid."
    """
    parts: list[str] = []
    detected = int(extraction.get("amount") or 0)
    due = max(0, expected_amount - received_amount)

    # Amount check
    if detected > 0:
        if due > 0:
            tolerance = due * 0.05
            if detected >= due - tolerance:
                parts.append(f"Amount ₹{detected:,} matches due ₹{due:,}")
            else:
                parts.append(f"Amount ₹{detected:,} detected but ₹{due:,} was due")
        else:
            parts.append(f"Amount ₹{detected:,} detected (candidate is fully paid)")
    else:
        parts.append("Amount not detected")

    # Receiver name check
    receiver = (extraction.get("receiver_name") or "").strip()
    if receiver and candidate_name:
        canon = " ".join(candidate_name.strip().lower().split())
        recv_key = " ".join(receiver.lower().split())
        # Check for partial name overlap
        name_words = [w for w in canon.split() if len(w) > 2]
        if any(w in recv_key for w in name_words):
            parts.append(f"receiver name '{receiver}' matches candidate")
        else:
            parts.append(f"receiver '{receiver}' (verify against candidate name '{candidate_name}')")

    # UTR
    utr = (extraction.get("utr_number") or extraction.get("reference_number") or "").strip()
    if utr:
        parts.append(f"UTR {utr}")

    # App
    app = (extraction.get("payment_app") or "").strip()
    if app:
        parts.append(app)

    # Date
    pay_date = (extraction.get("payment_date") or "").strip()
    if pay_date:
        today_str = date.today().isoformat()
        if pay_date <= today_str:
            parts.append(f"date {pay_date}")
        else:
            parts.append(f"date {pay_date} (future date — verify)")

    # Status
    status = (extraction.get("status") or "unknown").lower()
    if status == "success":
        parts.append("status success")
    elif status == "pending":
        parts.append("status pending — payment not yet settled")
    elif status == "failed":
        parts.append("status failed — do not accept")

    if not parts:
        return "Could not extract payment details — review screenshot manually."

    summary = " · ".join(parts)
    verified = extraction.get("verified", False)
    suffix = " — looks valid." if verified else " — review manually."
    return summary + suffix


_NARRATIVE_PROMPT_TEMPLATE = """You are a concise payment verification assistant for a recruiting operations tool.

A payment screenshot was uploaded for candidate: {candidate_name}
Expected payment: ₹{expected_amount}
Already received: ₹{received_amount}
Amount still due: ₹{due_amount}

Extracted payment details from screenshot:
- Detected amount: ₹{detected_amount}
- Receiver name: {receiver_name}
- UTR / Reference: {utr}
- Payment app: {payment_app}
- Payment date: {payment_date}
- Status: {status}
- Confidence score: {confidence}/100

Write ONE plain-English sentence (max 35 words) summarising whether this payment screenshot looks valid. Mention:
1. Whether the amount matches what's due
2. Whether the receiver name matches the candidate (if available)
3. Whether the date is plausible
4. A brief verdict: "looks valid", "needs review", or "reject"

Do not use bullet points. Do not use markdown. Return only the sentence.
"""


def generate_payment_narrative(
    extraction: dict[str, Any],
    *,
    candidate_name: str = "",
    expected_amount: int = 0,
    received_amount: int = 0,
) -> str:
    """Generate a plain-English confidence summary for the payout modal.

    Uses qwen2.5:7b (fast text model) if Ollama is available, otherwise
    falls back to a deterministic rule-based sentence.

    Example outputs:
      "Amount ₹10,000 matches due · UTR 320022345678 · PhonePe · date 2025-06-01 — looks valid."
      "Amount ₹5,000 detected but ₹10,000 was due — short by ₹5,000, needs review."
    """
    due = max(0, expected_amount - received_amount)
    detected = int(extraction.get("amount") or 0)
    utr = (extraction.get("utr_number") or extraction.get("reference_number") or "").strip() or "—"
    receiver = (extraction.get("receiver_name") or "").strip() or "—"
    app = (extraction.get("payment_app") or "").strip() or "—"
    pay_date = (extraction.get("payment_date") or "").strip() or "—"
    status = (extraction.get("status") or "unknown").strip()
    confidence = int(extraction.get("confidence_score") or 0)

    # Try Ollama text model first (fast, ~10-30s)
    if _is_ollama_available():
        prompt = _NARRATIVE_PROMPT_TEMPLATE.format(
            candidate_name=candidate_name or "Unknown",
            expected_amount=f"{expected_amount:,}" if expected_amount else "0",
            received_amount=f"{received_amount:,}" if received_amount else "0",
            due_amount=f"{due:,}" if due else "0 (fully paid)",
            detected_amount=f"{detected:,}" if detected else "not detected",
            receiver_name=receiver,
            utr=utr,
            payment_app=app,
            payment_date=pay_date,
            status=status,
            confidence=confidence,
        )
        try:
            response = _call_text_model(prompt, timeout=45)
            if response:
                # Strip any stray quotes or markdown the model adds
                narrative = response.strip().strip('"\'`').strip()
                # Sanity: must be a non-empty sentence under 300 chars
                if 10 < len(narrative) < 300:
                    logger.info("Narrative generated by text model (%d chars)", len(narrative))
                    return narrative
        except Exception as exc:
            logger.warning("Narrative generation failed: %s", exc)

    # Fallback: deterministic rule-based narrative
    logger.info("Using rule-based narrative (Ollama unavailable or model failed)")
    return _rule_based_narrative(extraction, candidate_name, expected_amount, received_amount)
