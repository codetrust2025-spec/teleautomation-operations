"""Extract interview date/time from invite screenshots (vision + regex)."""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from core.ocr_policy import ocr_enabled

logger = logging.getLogger(__name__)

_MONTH_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_WEEKDAY_PATTERN = r"(?:mon|tue|wed|thu|fri|sat|sun)"


_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _month_num(token: str) -> int:
    t = (token or "").strip().lower()
    if not t:
        return 0
    if len(t) > 3:
        return _MONTHS.get(t, _MONTHS.get(t[:3], 0))
    return _MONTHS.get(t[:3], 0)


def _infer_year(month: int, day: int, ref: datetime | None = None) -> int:
    ref = ref or datetime.now()
    y = ref.year
    try:
        candidate = datetime(y, month, day).date()
    except ValueError:
        return y
    if (ref.date() - candidate).days > 7:
        return y + 1
    return y


def _parse_labeled_interview_block(blob: str) -> tuple[str, str, str]:
    """Bullet-list invites: Date: 20-06-2026, Saturday / Time: 5:00 pm IST."""
    text = (blob or "").replace("\n", " ")
    date = ""
    time_start = ""
    time_end = ""

    # Combined labels are common in email invites:
    # "Interview Date & Time: 12.07.2026 (Sunday) at 1: 00 PM".
    # OCR often inserts a line break/space around the time colon. Parse this
    # before the generic "Time:" rule so the date's leading 12 is never
    # mistaken for 12:00 PM.
    combined = re.search(
        r"\bdate\s*(?:&|and)\s*time\s*:\s*"
        r"(\d{1,2})[/.-](\d{1,2})[/.-](20\d{2})"
        r"(?:\s*\([^)]{0,24}\))?\s*(?:at\s*)?"
        r"(\d{1,2})\s*:\s*(\d{2})\s*(am|pm)?\s*(?:IST|UTC|GMT)?\b"
        r"(?:\s*(?:-|to|\u2013|\u2014|~)\s*"
        r"(\d{1,2})\s*:\s*(\d{2})\s*(am|pm)?\s*(?:IST|UTC|GMT)?)?",
        text,
        re.IGNORECASE,
    )
    if combined:
        d, m, y = int(combined.group(1)), int(combined.group(2)), int(combined.group(3))
        if m > 12 and d <= 12:
            d, m = m, d
        date = f"{y:04d}-{_pad2(m)}-{_pad2(d)}"
        start_hour, start_minute = int(combined.group(4)), int(combined.group(5))
        start_ampm = combined.group(6) or ""
        if 0 <= start_minute <= 59 and (
            (start_ampm and 1 <= start_hour <= 12)
            or (not start_ampm and 0 <= start_hour <= 23)
        ):
            sh, sm = _to_24h(start_hour, start_minute, start_ampm)
            time_start = _fmt_hhmm(sh, sm)
            if combined.group(7):
                end_hour, end_minute = int(combined.group(7)), int(combined.group(8))
                end_ampm = combined.group(9) or start_ampm
                if 0 <= end_minute <= 59 and (
                    (end_ampm and 1 <= end_hour <= 12)
                    or (not end_ampm and 0 <= end_hour <= 23)
                ):
                    eh, em = _to_24h(end_hour, end_minute, end_ampm)
                    time_end = _fmt_hhmm(eh, em)
            if not time_end:
                end_total = sh * 60 + sm + 30
                time_end = _fmt_hhmm(end_total // 60, end_total % 60)

    dm = re.search(
        r"\bdate\s*:\s*(\d{1,2})[/.-](\d{1,2})[/.-](20\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if dm:
        d, m, y = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
        if m > 12 and d <= 12:
            d, m = m, d
        date = f"{y:04d}-{_pad2(m)}-{_pad2(d)}"

    # Match the "Time:" label with 12h (5:00 pm) OR 24h (13:00:00) start,
    # optional seconds, and an optional end time after -, to, until, till.
    # Reliable label — must win over the phone status-bar clock elsewhere.
    tm = None if time_start else re.search(
        r"\btime\s*:\s*"
        r"(?!\d{1,2}[/.-]\d{1,2}[/.-]20\d{2}\b)"
        r"(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(am|pm)?"
        r"(?:\s*(?:-|to|\u2013|\u2014|until|untill|till)\s*[^\w\d]?\s*"
        r"(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(am|pm)?)?",
        text,
        re.IGNORECASE,
    )
    if tm:
        s_ap = (tm.group(3) or "").lower()
        e_ap = (tm.group(6) or "").lower()
        # If only the end time carries am/pm, apply it to the start too.
        if not s_ap and e_ap:
            s_ap = e_ap
        if s_ap:
            sh, sm = _to_24h(int(tm.group(1)), int(tm.group(2) or 0), s_ap)
        else:
            sh, sm = int(tm.group(1)) % 24, int(tm.group(2) or 0) % 60
        time_start = _fmt_hhmm(sh, sm)
        if tm.group(4):
            if e_ap:
                eh, em = _to_24h(int(tm.group(4)), int(tm.group(5) or 0), e_ap)
            else:
                eh, em = int(tm.group(4)) % 24, int(tm.group(5) or 0) % 60
            time_end = _fmt_hhmm(eh, em)
        else:
            end_total = sh * 60 + sm + 30
            time_end = _fmt_hhmm(end_total // 60, end_total % 60)

    return date, time_start, time_end


def _parse_gmail_calendar_line(blob: str) -> tuple[str, str, str]:
    """Gmail / Google Calendar card: Sat, Jun 20, 2:00 PM"""
    text = (blob or "").replace("\n", " ")
    pat = re.compile(
        rf"\b{_WEEKDAY_PATTERN},?\s+({_MONTH_PATTERN})\s+(\d{{1,2}}),?\s+"
        rf"(\d{{1,2}})(?::(\d{{2}}))?\s*(am|pm)\b",
        re.IGNORECASE,
    )
    m = pat.search(text)
    if not m:
        return "", "", ""
    mon = _month_num(m.group(1))
    day = int(m.group(2))
    if not mon:
        return "", "", ""
    y = _infer_year(mon, day)
    date = f"{y:04d}-{_pad2(mon)}-{_pad2(day)}"
    sh, sm = _to_24h(int(m.group(3)), int(m.group(4) or 0), m.group(5))
    start = _fmt_hhmm(sh, sm)
    end_total = sh * 60 + sm + 30
    end = _fmt_hhmm(end_total // 60, end_total % 60)
    return date, start, end


def _parse_month_day_24h_line(blob: str) -> tuple[str, str, str]:
    """Gmail AI Overview / bullet format: 'June 25, 10:30–11:10 IST' (no year, no am/pm).
    Also handles: '10:00AM Friday, 26 June 2026' (time before date).
    Handles OCR errors: '10.00AM' or '10.008M' etc."""
    text = (blob or "").replace("\n", " ").replace("–", "-").replace("—", "-")
    # Fix common OCR errors: dots instead of colons, 8M→AM, 0M→OM→AM
    text = re.sub(r'(\d{1,2})\.(\d{2})\s*(AM|PM|am|pm|8M|8m|0M)', lambda m: f"{m.group(1)}:{m.group(2)} {'AM' if '8' in m.group(3) or 'A' in m.group(3).upper() or '0' in m.group(3) else 'PM'}", text)
    # Fix dot-comma: "Friday. 26" → "Friday, 26"
    text = re.sub(r'(\w)\.\s+(\d)', r'\1, \2', text)

    # Format: "10:00AM Friday, 26 June 2026" or "10:00 AM Friday, 26 June 2026"
    time_before_date = re.compile(
        rf"(\d{{1,2}}):(\d{{2}})\s*(am|pm)\s*"
        rf"{_WEEKDAY_PATTERN}?,?\s*"
        rf"(\d{{1,2}})\s+({_MONTH_PATTERN})\s+(20\d{{2}})\b",
        re.IGNORECASE,
    )
    m_tbd = time_before_date.search(text)
    if m_tbd:
        sh, sm = _to_24h(int(m_tbd.group(1)), int(m_tbd.group(2)), m_tbd.group(3))
        day = int(m_tbd.group(4))
        mon = _month_num(m_tbd.group(5))
        y = int(m_tbd.group(6))
        if mon:
            date = f"{y:04d}-{_pad2(mon)}-{_pad2(day)}"
            end_total = sh * 60 + sm + 30
            return date, _fmt_hhmm(sh, sm), _fmt_hhmm(end_total // 60, end_total % 60)

    pat = re.compile(
        rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}}),?\s+"
        rf"(\d{{1,2}}):(\d{{2}})\s*(?:IST|UTC|GMT)?\s*"
        r"(?:-|to)\s*"
        rf"(\d{{1,2}}):(\d{{2}})\b",
        re.IGNORECASE,
    )
    m = pat.search(text)
    if m:
        mon = _month_num(m.group(1))
        day = int(m.group(2))
        if not mon:
            return "", "", ""
        y = _infer_year(mon, day)
        date = f"{y:04d}-{_pad2(mon)}-{_pad2(day)}"
        sh, sm = int(m.group(3)), int(m.group(4))
        eh, em = int(m.group(5)), int(m.group(6))
        return date, _fmt_hhmm(sh, sm), _fmt_hhmm(eh, em)
    # Single time variant: June 25, 10:30 IST
    pat2 = re.compile(
        rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}}),?\s+"
        rf"(\d{{1,2}}):(\d{{2}})\s*(?:IST|UTC|GMT)?\b",
        re.IGNORECASE,
    )
    m2 = pat2.search(text)
    if m2:
        mon = _month_num(m2.group(1))
        day = int(m2.group(2))
        if not mon:
            return "", "", ""
        y = _infer_year(mon, day)
        date = f"{y:04d}-{_pad2(mon)}-{_pad2(day)}"
        sh, sm = int(m2.group(3)), int(m2.group(4))
        end_total = sh * 60 + sm + 30
        return date, _fmt_hhmm(sh, sm), _fmt_hhmm(end_total // 60, end_total % 60)
    return "", "", ""


def _parse_relative_calendar_line(blob: str, ref: datetime | None = None) -> tuple[str, str, str]:
    """Gmail cards commonly say: ``Tomorrow · 12:00 PM – 12:30 PM``."""
    text = (blob or "").replace("\n", " ").replace("~", "-")
    match = re.search(
        r"\b(today|tomorrow)\b[^\d]{0,32}"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:-|to|–|—)\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return "", "", ""
    now = ref or datetime.now()
    day = now.date() + timedelta(days=1 if match.group(1).lower() == "tomorrow" else 0)
    sh, sm = _to_24h(int(match.group(2)), int(match.group(3) or 0), match.group(4))
    eh, em = _to_24h(int(match.group(5)), int(match.group(6) or 0), match.group(7))
    return day.isoformat(), _fmt_hhmm(sh, sm), _fmt_hhmm(eh, em)


def _parse_longform_invite_line(blob: str) -> tuple[str, str, str]:
    """Email body: Monday, 22 June, 2026, 3:30 PM to 4:00 PM (IST)
    Also handles Teams-style multiline: '25 June 2026  10:30 - 11:10 (IST)'
    """
    text = (blob or "").replace("\n", " ")
    range_pat = re.compile(
        rf"(?:{_WEEKDAY_PATTERN},?\s+)?(\d{{1,2}})\s+({_MONTH_PATTERN})\s*,?\s*"
        rf"(20\d{{2}}),?\s+"
        rf"(\d{{1,2}})(?::(\d{{2}}))?\s*(am|pm)\s*(?:-|to|\u2013|\u2014)\s*"
        rf"(\d{{1,2}})(?::(\d{{2}}))?\s*(am|pm)\b",
        re.IGNORECASE,
    )
    m = range_pat.search(text)
    if m:
        mon = _month_num(m.group(2))
        day = int(m.group(1))
        y = int(m.group(3))
        if mon:
            date = f"{y:04d}-{_pad2(mon)}-{_pad2(day)}"
            sh, sm = _to_24h(int(m.group(4)), int(m.group(5) or 0), m.group(6))
            eh, em = _to_24h(int(m.group(7)), int(m.group(8) or 0), m.group(9))
            return date, _fmt_hhmm(sh, sm), _fmt_hhmm(eh, em)

    single_pat = re.compile(
        rf"(?:{_WEEKDAY_PATTERN},?\s+)?(\d{{1,2}})\s+({_MONTH_PATTERN})\s*,?\s*"
        rf"(20\d{{2}}),?\s+"
        rf"(\d{{1,2}})(?::(\d{{2}}))?\s*(am|pm)\b",
        re.IGNORECASE,
    )
    m = single_pat.search(text)
    if m:
        mon = _month_num(m.group(2))
        day = int(m.group(1))
        y = int(m.group(3))
        if mon:
            date = f"{y:04d}-{_pad2(mon)}-{_pad2(day)}"
            sh, sm = _to_24h(int(m.group(4)), int(m.group(5) or 0), m.group(6))
            start = _fmt_hhmm(sh, sm)
            end_total = sh * 60 + sm + 30
            end = _fmt_hhmm(end_total // 60, end_total % 60)
            return date, start, end

    # Teams-style: "25 June 2026" then "10:30 - 11:10 (IST)" — date-only line, time on next.
    date_only = re.compile(
        rf"(?:{_WEEKDAY_PATTERN},?\s+)?(\d{{1,2}})\s+({_MONTH_PATTERN})\s*,?\s*(20\d{{2}})\b",
        re.IGNORECASE,
    )
    dm = date_only.search(text)
    if dm:
        mon = _month_num(dm.group(2))
        day = int(dm.group(1))
        y = int(dm.group(3))
        if mon:
            date = f"{y:04d}-{_pad2(mon)}-{_pad2(day)}"
            after = text[dm.end():]
            ts, te = _parse_times_from_blob(after)
            if ts:
                return date, ts, te

    return "", "", ""


def _env_api_key() -> str:
    return (os.getenv("AI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()


def _env_api_base() -> str:
    return (
        os.getenv("AI_API_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    ).rstrip("/")


def _local_ocr_text(data: bytes) -> str:
    """Tesseract OCR with image preprocessing for better accuracy."""
    if not ocr_enabled():
        logger.info("Global OCR is disabled; skipping local slot screenshot OCR")
        return ""
    try:
        from PIL import Image, ImageEnhance, ImageOps
        import pytesseract
    except ImportError as e:
        logger.warning("slot screenshot OCR import failed: %s", e)
        return ""
    try:
        # Configure Tesseract path for Windows and Linux
        if os.name == 'nt':
            tesseract_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            if os.path.exists(tesseract_path):
                pytesseract.pytesseract.tesseract_cmd = tesseract_path
        else:
            # Linux/Unix (production VPS) - usually in /usr/bin/tesseract
            tesseract_path = "/usr/bin/tesseract"
            if os.path.exists(tesseract_path):
                pytesseract.pytesseract.tesseract_cmd = tesseract_path
            else:
                logger.warning("Tesseract not found at %s", tesseract_path)
                return ""
        logger.info("slot screenshot OCR using tesseract at: %s", tesseract_path)
        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Convert to grayscale
        gray = img.convert("L")

        # Upscale for better OCR
        w, h = gray.size
        if w < 1500:
            scale = max(2, 1500 // w)
            gray = gray.resize((w * scale, h * scale), Image.LANCZOS)

        focused_text = ""
        if h >= 600:
            focused = img.crop((0, int(h * 0.43), w, int(h * 0.72))).convert("L")
            focused = ImageOps.autocontrast(focused)
            focused = focused.resize(
                (focused.width * 2, focused.height * 2),
                Image.LANCZOS,
            )
            focused = ImageEnhance.Contrast(focused).enhance(1.8)
            focused_text = pytesseract.image_to_string(
                focused,
                config="--psm 11 --oem 3",
            ) or ""
            focused_text = str(focused_text).strip()

        # Try OCR without binarization first (preserves more detail)
        text = pytesseract.image_to_string(gray, config='--psm 3 --oem 3') or ""
        text = str(text).strip()

        # If too short, try with binarization
        if len(text) < 20:
            threshold = 140
            binary = gray.point(lambda p: 255 if p > threshold else 0)
            text2 = pytesseract.image_to_string(binary, config='--psm 6 --oem 3') or ""
            text2 = str(text2).strip()
            if len(text2) > len(text):
                text = text2

        return "\n".join(part for part in (focused_text, text) if part)
    except Exception as exc:
        logger.warning("slot screenshot local OCR failed: %s", exc)
        return ""


def _vision_chat_completion(payload: dict[str, Any], *, label: str) -> dict[str, Any] | None:
    api_key = _env_api_key()
    if not api_key:
        return None
    body_bytes = json.dumps(payload).encode("utf-8")
    retries = max(1, int(os.getenv("SLOT_PARSE_VISION_RETRIES", "3")))
    for attempt in range(retries):
        req = urllib.request.Request(
            f"{_env_api_base()}/chat/completions",
            data=body_bytes,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries - 1:
                delay = 1.5 * (attempt + 1)
                logger.warning("slot screenshot %s rate-limited; retry in %.1fs", label, delay)
                time.sleep(delay)
                continue
            logger.warning("slot screenshot %s failed: HTTP %s", label, exc.code)
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            logger.warning("slot screenshot %s failed: %s", label, exc)
            return None
    return None


def _pad2(n: int) -> str:
    return f"{int(n):02d}"


def _to_24h(hour: int, minute: int, ampm: str) -> tuple[int, int]:
    h = int(hour)
    m = int(minute)
    ap = (ampm or "").strip().lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return h % 24, m % 60


def _fmt_hhmm(h: int, m: int) -> str:
    return f"{_pad2(h)}:{_pad2(m)}"


def _parse_date_token(text: str) -> str:
    """Return YYYY-MM-DD from common invite formats."""
    blob = (text or "").replace("\n", " ")
    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", blob)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
    slash = re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d{2})\b", blob)
    if slash:
        d, m, y = int(slash.group(1)), int(slash.group(2)), int(slash.group(3))
        if m > 12 and d <= 12:
            d, m = m, d
        return f"{y:04d}-{_pad2(m)}-{_pad2(d)}"
    named = re.search(
        r"\b(\d{1,2})\s*[/\-.]?\s*"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s*[/\-.]?\s*(20\d{2})\b",
        blob,
        re.IGNORECASE,
    )
    if named:
        d = int(named.group(1))
        mon = _MONTHS.get(named.group(2).lower()[:3], 0)
        if len(named.group(2)) > 3:
            mon = _MONTHS.get(named.group(2).lower(), mon)
        y = int(named.group(3))
        if mon:
            return f"{y:04d}-{_pad2(mon)}-{_pad2(d)}"
    named2 = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+(\d{1,2}),?\s+(20\d{2})\b",
        blob,
        re.IGNORECASE,
    )
    if named2:
        mon = _MONTHS.get(named2.group(1).lower()[:3], 0)
        if len(named2.group(1)) > 3:
            mon = _MONTHS.get(named2.group(1).lower(), mon)
        d = int(named2.group(2))
        y = int(named2.group(3))
        if mon:
            return f"{y:04d}-{_pad2(mon)}-{_pad2(d)}"
    # Google Calendar cards may use a yearless day-month format:
    # "Mon, 27 Jul • 14:30 - 15:00". Requiring the weekday avoids
    # accidentally treating an unrelated day-month phrase as the event date.
    weekday_day_month = re.search(
        rf"\b{_WEEKDAY_PATTERN},?\s+(\d{{1,2}})(?:st|nd|rd|th)?\s+"
        rf"({_MONTH_PATTERN})\b",
        blob,
        re.IGNORECASE,
    )
    if weekday_day_month:
        d = int(weekday_day_month.group(1))
        mon = _month_num(weekday_day_month.group(2))
        if mon:
            y = _infer_year(mon, d)
            return f"{y:04d}-{_pad2(mon)}-{_pad2(d)}"
    named3 = re.search(
        rf"(?:{_WEEKDAY_PATTERN},?\s+)?({_MONTH_PATTERN})\s+(\d{{1,2}})\b",
        blob,
        re.IGNORECASE,
    )
    if named3:
        mon = _month_num(named3.group(1))
        d = int(named3.group(2))
        if mon:
            y = _infer_year(mon, d)
            return f"{y:04d}-{_pad2(mon)}-{_pad2(d)}"
    return ""


def _parse_time_token(match: re.Match) -> tuple[int, int]:
    h = int(match.group(1))
    m = int(match.group(2)) if match.lastindex and match.group(2) and match.group(2).isdigit() else 0
    ampm = ""
    for g in match.groups():
        if g and str(g).lower() in {"am", "pm"}:
            ampm = str(g).lower()
            break
    return _to_24h(h, m, ampm)


def _parse_times_from_blob(blob: str) -> tuple[str, str]:
    text = (blob or "").lower().replace("–", "-").replace("—", "-")
    # Fix common OCR errors: 10.00am → 10:00am, 8m→am
    text = re.sub(r'(\d{1,2})\.(\d{2})\s*(am|pm|8m|0m)', lambda m: f"{m.group(1)}:{m.group(2)}{'am' if '8' in m.group(3) or 'a' in m.group(3) or '0' in m.group(3) else 'pm'}", text)
    text = re.sub(r'8m\b', 'am', text)
    text = re.sub(r'0m\b', 'am', text)

    # Full range with AM/PM on the end: 10:30 - 11:10 (IST), 2:00 PM - 3:00 PM, etc.
    range_pat = re.compile(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*"
        r"(?:-|to|–|—)\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
        re.IGNORECASE,
    )
    m = range_pat.search(text)
    if m:
        sh, sm = _to_24h(int(m.group(1)), int(m.group(2) or 0), m.group(3) or m.group(6))
        eh, em = _to_24h(int(m.group(4)), int(m.group(5) or 0), m.group(6))
        if (eh, em) <= (sh, sm) and (m.group(3) or "").lower() == "pm" and not (m.group(6) or "").lower():
            eh += 12 if eh < 12 else 0
        return _fmt_hhmm(sh, sm), _fmt_hhmm(eh, em)

    # 24h bare range without AM/PM: "10:30 - 11:10" or "10:30 - 11:10 (IST)"
    range_24h = re.compile(
        r"\b(\d{1,2}):(\d{2})\s*(?:-|to|–|—)\s*(\d{1,2}):(\d{2})\b"
    )
    m24 = range_24h.search(text)
    if m24:
        sh, sm = int(m24.group(1)), int(m24.group(2))
        eh, em = int(m24.group(3)), int(m24.group(4))
        if 0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59:
            return _fmt_hhmm(sh, sm), _fmt_hhmm(eh, em)

    colon = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)\b", text, re.IGNORECASE)
    if colon:
        sh, sm = _to_24h(int(colon.group(1)), int(colon.group(2)), colon.group(3))
        end_total = sh * 60 + sm + 30
        return _fmt_hhmm(sh, sm), _fmt_hhmm(end_total // 60, end_total % 60)

    # Single 24h colon time with no AM/PM: "10:30 (IST)"
    colon_24h = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if colon_24h:
        sh, sm = int(colon_24h.group(1)), int(colon_24h.group(2))
        if 0 <= sh <= 23 and 0 <= sm <= 59:
            end_total = sh * 60 + sm + 30
            return _fmt_hhmm(sh, sm), _fmt_hhmm(end_total // 60, end_total % 60)

    plain = re.search(r"(?<![:\d])\b(\d{1,2})\s*(am|pm)\b", text, re.IGNORECASE)
    if plain:
        sh, sm = _to_24h(int(plain.group(1)), 0, plain.group(2))
        end_total = sh * 60 + sm + 30
        return _fmt_hhmm(sh, sm), _fmt_hhmm(end_total // 60, end_total % 60)
    return "", ""


def _parse_round_from_blob(blob: str) -> str:
    """The round the screenshot's text states outright, or "".

    "Technical" alone used to be read as L1 -- a guess (23 Nov). The shared
    evidence reader takes only a named round ("Round 2", "Level 1", "L1
    Interview", "HR round") and returns nothing when two rounds are named.
    """
    from features.interview_round_evidence import resolve_round

    return resolve_round([("screenshot.text", blob)])["round"]


def _parse_platform_from_blob(blob: str) -> str:
    low = (blob or "").lower()
    if "microsoft teams" in low or "teams meeting" in low or "ms teams" in low or " teams" in low:
        return "teams"
    if "zoom" in low:
        return "zoom"
    if "google calendar" in low or "calendar invite" in low:
        return "google_calendar"
    if "gmail" in low or "google meet" in low:
        return "gmail"
    if "barraiser" in low:
        return "barraiser"
    return ""


def _parse_timezone_from_blob(blob: str) -> str:
    text = blob or ""
    if re.search(r"\bAsia/Kolkata\b", text, re.IGNORECASE):
        return "Asia/Kolkata"
    if re.search(r"\bIndia\s+Standard\s+Time\b", text, re.IGNORECASE):
        return "Asia/Kolkata"
    if re.search(r"\bIST\b", text, re.IGNORECASE):
        return "Asia/Kolkata"
    if re.search(r"\b(?:GMT|UTC)\s*\+\s*0?5\s*:\s*30\b", text, re.IGNORECASE):
        return "Asia/Kolkata"
    return ""


def _has_explicit_date_with_year(blob: str) -> bool:
    text = (blob or "").replace("\n", " ")
    patterns = (
        r"\b20\d{2}-\d{2}-\d{2}\b",
        r"\b\d{1,2}[/.-]\d{1,2}[/.-]20\d{2}\b",
        rf"\b\d{{1,2}}\s*[/.-]?\s*(?:{_MONTH_PATTERN})\s*[,/.-]?\s*20\d{{2}}\b",
        rf"\b(?:{_MONTH_PATTERN})\s+\d{{1,2}},?\s+20\d{{2}}\b",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _parse_technology_from_blob(blob: str) -> str:
    """Extract technology from invite titles like 'Technical Screening_Frontend Angular Developer_...'"""
    low = (blob or "").lower()
    # Order matters — check more specific phrases first
    checks = [
        ("angular", "Angular"),
        ("react js", "React JS"),
        ("react", "React JS"),
        ("java ", "Java"),
        ("java_", "Java"),
        (".net", ".NET"),
        ("dotnet", ".NET"),
        ("python", "Python"),
        ("node", "Node"),
        ("power bi", "Power BI"),
        ("powerbi", "Power BI"),
        ("sql", "SQL"),
        ("etl", "ETL"),
        ("microservice", "Microservices"),
        ("devops", "DevOps"),
        ("aws", "AWS"),
        ("azure", "Azure"),
        ("flutter", "Flutter"),
        ("android", "Android"),
        ("ios", "iOS"),
        ("data engineer", "Data Engineering"),
        ("data science", "Data Science"),
        ("machine learning", "Machine Learning"),
        ("frontend", "Frontend"),
        ("front end", "Frontend"),
        ("backend", "Backend"),
        ("back end", "Backend"),
        ("full stack", "Full Stack"),
        ("fullstack", "Full Stack"),
    ]
    for needle, label in checks:
        if needle in low:
            return label
    return ""


def parse_invite_text(blob: str) -> dict[str, Any]:
    """Regex extraction from OCR or vision text."""
    labeled_date, labeled_start, labeled_end = _parse_labeled_interview_block(blob)
    relative_date, relative_start, relative_end = _parse_relative_calendar_line(blob)
    g_date, g_start, g_end = _parse_gmail_calendar_line(blob)
    lf_date, lf_start, lf_end = _parse_longform_invite_line(blob)
    md24_date, md24_start, md24_end = _parse_month_day_24h_line(blob)
    date = labeled_date or g_date or lf_date or md24_date or _parse_date_token(blob) or relative_date
    if labeled_start:
        start = labeled_start
        # The body often provides only the start while Gmail's calendar card
        # contains the exact end. Use it only when both starts agree.
        end = relative_end if relative_start == labeled_start and relative_end else labeled_end
    elif relative_start:
        start, end = relative_start, relative_end
    elif g_start:
        start, end = g_start, g_end
    elif lf_start:
        start, end = lf_start, lf_end
    elif md24_start:
        start, end = md24_start, md24_end
    else:
        start, end = _parse_times_from_blob(blob)
    out = {
        "date": date,
        "time": start,
        "time_end": end,
        "interview_round": _parse_round_from_blob(blob),
        "technology": _parse_technology_from_blob(blob),
        "platform": _parse_platform_from_blob(blob),
        "timezone": _parse_timezone_from_blob(blob),
        "raw_text": (blob or "")[:2000],
        "_explicit_date": bool(date and _has_explicit_date_with_year(blob)),
        "_explicit_start": bool(start and (labeled_start or lf_start or md24_start)),
    }
    if labeled_start:
        out["_labeled"] = True
    out["_timezone_explicit"] = bool(out["timezone"])
    return out


def _vision_extract_json(data: bytes, mime: str) -> dict[str, Any]:
    api_key = _env_api_key()
    if not api_key:
        return {}
    b64 = base64.b64encode(data).decode("ascii")
    safe_mime = mime if mime.startswith("image/") else "image/jpeg"
    prompt = (
        "Read this interview invite / calendar / Teams / Zoom / Gmail screenshot. "
        "Gmail often shows 'Sat, Jun 20, 2:00 PM' without a year — infer the correct year. "
        "Gmail AI Overview bullets may show 'June 25, 10:30–11:10 IST' — these are 24-hour IST times with no am/pm. "
        "For bullet lists like 'Date: 20-06-2026' and 'Time: 5:00 pm IST', use those lines — "
        "ignore the email received time in the Gmail header (e.g. '3:09 pm' next to the sender). "
        "Return JSON only with keys: date (YYYY-MM-DD), time (HH:MM 24h), "
        "time_end (HH:MM 24h), interview_round (L1|L2|HR|Final|Screening or empty), "
        "technology (short stack or empty), platform (teams|zoom|gmail|google_calendar|barraiser|other). "
        "Use India timezone if shown. If only one time, set time_end 30 minutes after time."
    )
    payload = {
        "model": os.getenv("SLOT_PARSE_VISION_MODEL", "gpt-4o-mini"),
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{safe_mime};base64,{b64}"}},
            ],
        }],
        "max_tokens": 280,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    body = _vision_chat_completion(payload, label="vision-json")
    if not body:
        return {}
    try:
        content = (body.get("choices") or [{}])[0].get("message", {}).get("content") or "{}"
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError, IndexError):
        return {}


def _vision_extract_raw_text(data: bytes, mime: str) -> str:
    """OCR-style fallback: return all visible text from the screenshot."""
    api_key = _env_api_key()
    if not api_key:
        return ""
    b64 = base64.b64encode(data).decode("ascii")
    safe_mime = mime if mime.startswith("image/") else "image/jpeg"
    prompt = (
        "Transcribe ALL visible text from this interview invite screenshot exactly as shown. "
        "Include calendar lines like 'Sat, Jun 20, 2:00 PM'. Return plain text only."
    )
    payload = {
        "model": os.getenv("SLOT_PARSE_VISION_MODEL", "gpt-4o-mini"),
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{safe_mime};base64,{b64}"}},
            ],
        }],
        "max_tokens": 500,
        "temperature": 0,
    }
    body = _vision_chat_completion(payload, label="vision-ocr")
    if not body:
        return ""
    try:
        content = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return str(content).strip()
    except (TypeError, IndexError):
        return ""


def _apply_text_to_merged(merged: dict[str, Any], raw_text: str, *, method: str) -> dict[str, Any]:
    if not raw_text:
        return merged
    text_parsed = parse_invite_text(raw_text)
    merged = _merge_parsed(text_parsed, merged)
    if not merged.get("raw_text"):
        merged["raw_text"] = raw_text[:2000]
    if method:
        merged["method"] = method
    return merged


def _norm_time_field(val: str) -> str:
    raw = (val or "").strip()
    if not raw:
        return ""
    if re.match(r"^\d{2}:\d{2}$", raw):
        return raw
    start, _ = _parse_times_from_blob(raw)
    return start or raw


def _merge_parsed(vision: dict[str, Any], regex: dict[str, Any]) -> dict[str, Any]:
    out = {
        "date": "",
        "time": "",
        "time_end": "",
        "interview_round": "",
        "technology": "",
        "platform": "",
        "raw_text": regex.get("raw_text") or "",
    }
    labeled = bool(regex.get("_labeled"))
    for key in out:
        if key == "raw_text":
            continue
        regex_val = str(regex.get(key) or "").strip()
        vision_val = str(vision.get(key) or "").strip()
        if labeled and key in ("date", "time", "time_end") and regex_val:
            val = regex_val
        else:
            val = vision_val or regex_val
        out[key] = val
    out["date"] = _parse_date_token(out["date"]) or out["date"]
    out["time"] = _norm_time_field(out["time"])
    out["time_end"] = _norm_time_field(out["time_end"])
    if out["time"] and not out["time_end"]:
        try:
            sh, sm = map(int, out["time"].split(":")[:2])
            total = sh * 60 + sm + 30
            out["time_end"] = _fmt_hhmm(total // 60, total % 60)
        except ValueError:
            pass
    return out


def parse_invite_screenshot(data: bytes, mime: str = "image/jpeg") -> dict[str, Any]:
    """
    Parse invite screenshot → slot fields.
    Raises ValueError when date/time cannot be determined.
    Uses Tesseract OCR only (no external AI API).
    """
    if not data:
        raise ValueError("Screenshot file is empty")
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("Screenshot must be under 8 MB")

    # Primary: Tesseract OCR
    ocr_text = _local_ocr_text(data)
    merged: dict[str, Any] = {}
    method = ""

    logger.info("slot screenshot OCR output (%d chars): %s", len(ocr_text), ocr_text[:300].replace("\n", " | "))

    if ocr_text:
        merged = parse_invite_text(ocr_text)
        method = "ocr"

    # Fallback: Vision API only if OCR failed AND API key is available
    if not merged.get("date") or not merged.get("time"):
        vision = _vision_extract_json(data, mime)
        if vision:
            regex_blob = " ".join(
                str(v) for v in vision.values() if isinstance(v, str) and v.strip()
            )
            regex = parse_invite_text(regex_blob)
            merged = _merge_parsed(vision, regex if not merged.get("date") else merged)
            method = method or "vision"

    if not merged.get("date") or not merged.get("time"):
        raw_text = _vision_extract_raw_text(data, mime)
        if raw_text:
            merged = _apply_text_to_merged(merged, raw_text, method="vision-ocr")
            method = method or "vision-ocr"

    from features.candidate_store import canonical_technology, normalise_interview_round

    merged["interview_round"] = normalise_interview_round(merged.get("interview_round"))
    tech = canonical_technology(merged.get("technology") or "")
    if tech and tech not in {"", "Unspecified"}:
        merged["technology"] = tech
    else:
        merged["technology"] = ""

    if not merged.get("date") or not merged.get("time"):
        raise ValueError(
            "Could not read date and time from the screenshot — "
            "enter date & time manually, or upload a clearer invite image."
        )

    merged["parsed"] = True
    merged["method"] = method or "ocr"
    return merged
