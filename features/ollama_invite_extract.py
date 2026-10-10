"""Ollama vision model integration for interview invite screenshot extraction.

Enhances the existing slot_screenshot_parse.py with AI-powered extraction
using Ollama models running on the developer's laptop (tunneled via SSH).

Primary model: qwen2.5vl:7b (reliable structured extraction)
Backup model: qwen2.5vl:7b
Falls back to existing OCR only if both AI models fail.

Hybrid flow (fast path):
  1. OCR image → raw text (Tesseract, instant)
  2. If OCR finds date+time, send raw text to qwen2.5:7b text model for JSON cleanup (fast, ~10s)
  3. Only if OCR fails, call qwen2.5vl:7b vision model (slow, ~5 min)
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import logging
from typing import Any

from core.ai_gateway import AIGatewayError, chat, health
from core.ai_model_routing import model_for
from core.ocr_policy import ocr_enabled, processing_mode

logger = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────────────────
OLLAMA_VISION_MODEL = model_for("interview_screenshot_vision")
# The backup has to be able to do the same job as the primary. A small vision
# model was the previous default and could not read long identifiers at all,
# which made a failed fallback indistinguishable from an unreadable image.
OLLAMA_BACKUP_VISION_MODEL = os.environ.get("OLLAMA_BACKUP_VISION_MODEL", "qwen2.5vl:7b")
OLLAMA_REASONING_MODEL = model_for("reasoning_text")
def _invite_model_timeout() -> int:
    """Per-call model timeout, never longer than the endpoint's own budget.

    The public invite endpoint gives up after INVITE_EXTRACTION_TIMEOUT and
    returns a manual-entry fallback, but it waits via asyncio.to_thread and
    thread-pool workers cannot be cancelled. Without this ceiling an abandoned
    call would keep a pool thread and an Ollama slot busy for the full
    OLLAMA_TIMEOUT (900s by default) — and a candidate retrying would stack
    more of them until the shared executor starved.
    """
    configured = int(os.environ.get("OLLAMA_TIMEOUT", "900"))
    try:
        budget = int(str(os.environ.get("INVITE_EXTRACTION_TIMEOUT", "")).strip())
    except (TypeError, ValueError):
        budget = 0
    if budget <= 0:
        # Mirrors INVITE_EXTRACTION_TIMEOUT_DEFAULT; both must stay under the
        # 300s proxy read timeout so the app answers before Nginx does.
        budget = 240
    # Mirror the endpoint's own ceiling so an over-large override cannot push
    # the model call back past the proxy read timeout.
    budget = min(budget, 240)
    return max(30, min(configured, budget))


OLLAMA_TIMEOUT = _invite_model_timeout()
OLLAMA_TEXT_TIMEOUT = min(int(os.environ.get("OLLAMA_TEXT_TIMEOUT", "60")), OLLAMA_TIMEOUT)


class InferenceResponse(str):
    """Model text plus the node that actually produced it.

    Keeping this string-compatible preserves the parser and its test seams while
    carrying the gateway metadata needed by the public invite UI.
    """

    def __new__(
        cls,
        content: str,
        *,
        node_id: str = "",
        node_label: str = "",
    ):
        value = super().__new__(cls, content)
        value.node_id = node_id
        value.node_label = node_label
        return value


def _attach_inference_node(
    extracted: dict[str, Any] | None,
    response: str | None,
) -> dict[str, Any] | None:
    if not extracted or response is None:
        return extracted
    node_id = str(getattr(response, "node_id", "") or "").strip()
    node_label = str(getattr(response, "node_label", "") or "").strip()
    if node_id:
        extracted["inference_node_id"] = node_id
    if node_label:
        extracted["inference_node_label"] = node_label
    return extracted


def _ollama_only_test_mode() -> bool:
    """Return True only for the explicit diagnostic mode."""
    return os.environ.get("INVITE_EXTRACTION_MODE", "").strip().lower() == "ollama_only"

# ── Prompt ──────────────────────────────────────────────────────────────────
INVITE_EXTRACTION_PROMPT = """You are an interview invite screenshot extraction assistant.
Read the uploaded screenshot carefully. It may be from Gmail, Teams, Zoom, Google Calendar, Outlook, WhatsApp, Telegram, or any interview scheduling message.

Return ONLY valid JSON. Do not explain. Do not use markdown. Do not wrap JSON inside code blocks.

IMPORTANT: Today's date is {today}. Use this to resolve relative dates:
- "Tomorrow" means {tomorrow}
- "Today" means {today}
- If only month and day are visible (e.g. "JUL 9"), use the current year {year} unless it would be in the past, then use {year} + 1.
- An explicit interview date in the invitation body or a labelled field such as
  "Meeting Date and Time", "Interview Date", or "Date" is authoritative.
- Gmail/Calendar labels such as "Today" and "Tomorrow" may be stale because the
  screenshot can be uploaded days later. Ignore those relative labels whenever
  an explicit interview date is visible anywhere in the invitation.
- If only a relative date is visible and its screenshot/capture date cannot be
  established from visible information, leave interview_date empty. Do not
  resolve it from the server date or guess.

Schema:
{{"candidate_name": "", "candidate_phone": "", "client_name": "", "technology": "", "company": "", "service_type": "", "interview_round": "", "interview_date": "YYYY-MM-DD", "start_time": "hh:mm AM/PM", "end_time": "hh:mm AM/PM", "timezone": "Asia/Kolkata", "timezone_written": "", "meeting_platform": "", "screenshot_source": "", "meeting_link": "", "attendee_name": "", "confidence_score": 0, "missing_fields": [], "warnings": [], "raw_detected_text": "", "is_payment_screenshot": false, "looks_like_interview_invite": true}}

Rules:
- Extract only visible information.
- Do not guess.
- If a field is not visible, keep it empty.
- Add missing required fields to missing_fields.
- Required booking fields are interview_date, start_time, and interview_round.
- Convert all dates to YYYY-MM-DD.
- Convert all times to 12-hour hh:mm AM/PM format only.
- Never return 24-hour time.
- If screenshot shows 14:30, return 02:30 PM.
- If screenshot shows 19:45, return 07:45 PM.
- If screenshot shows 09:00, return 09:00 AM.
- If screenshot shows 11 AM, return 11:00 AM.
- If screenshot shows 7 PM, return 07:00 PM.
- IMPORTANT: If a time RANGE is visible (e.g. "12:30 – 1:00 pm", "2:00 PM - 3:00 PM"), extract BOTH start_time AND end_time.
- For "12:30 – 1:00 pm": start_time = "12:30 PM", end_time = "01:00 PM"
- For "2:00 PM – 2:30 PM": start_time = "02:00 PM", end_time = "02:30 PM"
- If end time is not visible and duration is not visible, keep end_time empty. Do not guess end_time.
- If date/time is ambiguous, keep confidence_score below 80.
- screenshot_source is the app the screenshot was taken FROM (WhatsApp, Gmail, Teams, Telegram, etc.)
- meeting_platform is the ACTUAL interview platform (FloCareer, HirePro, Zoom, Teams, Google Meet, BarRaiser, etc.)
- Do NOT confuse screenshot_source with meeting_platform. They are different fields.
- technology should be the job role or tech stack (Java, React JS, Data Engineer, Sr Data Reliability Engineer, etc.)
- Do NOT put meeting platform names in technology field.
- timezone_written is the time zone exactly as the invite writes it (for example IST, GMT+5:30, EST). If no time zone is written, keep it empty. Never assume one.
- meeting_link is the full meeting URL only if it is completely visible. If it is cut off or shortened with ..., keep it empty.
- company is the organisation the interview is for (the hiring company or end client, for example Capgemini, Infosys or Wipro), written as the invite names it.
  Look for a Company, Client, Organisation or Customer label, a subject such as "Interview with X" or "X | Java | L1", or the sender's signature.
  Return only the name, never a sentence.
- Do NOT put the meeting platform, the app the screenshot came from, a job role, a person's name or an email address in company.
  Do not infer it from an email domain or a logo. If the invite does not name the company, keep company empty.
- If the screenshot is a payment receipt, UPI screenshot, bank transfer screenshot, transaction proof, or payment confirmation, set is_payment_screenshot=true.
- If it is not an interview invite, set looks_like_interview_invite=false.
- confidence_score must be between 0 and 100."""

def _get_invite_prompt() -> str:
    """Get the invite extraction prompt with today's date filled in."""
    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    year = datetime.now().year
    return INVITE_EXTRACTION_PROMPT.format(today=today, tomorrow=tomorrow, year=year)


RETRY_PROMPT = "Your previous response was not valid JSON. Return only valid JSON matching the schema. No markdown. No explanation."

# ── Text cleanup prompt (for qwen2.5:7b text model after OCR) ───────────────
TEXT_CLEANUP_PROMPT = """You are an interview invite text extraction assistant.
The following raw OCR text was extracted from an interview invite screenshot.
Parse it and return ONLY valid JSON. Do not explain. Do not use markdown.

IMPORTANT RULES:
- screenshot_source is the app the screenshot was taken from (WhatsApp, Gmail, Teams, Telegram, etc.)
- meeting_platform is the actual interview platform (FloCareer, HirePro, Zoom, Teams, Google Meet, BarRaiser, etc.)
- Do NOT confuse screenshot_source with meeting_platform.
- technology should be the job role/tech stack (Java, React JS, Data Engineer, Sr Data Reliability Engineer, etc.)
- Do NOT put meeting platform names in technology field.
- timezone_written is the time zone exactly as the text writes it (for example IST, GMT+5:30, EST). If none is written, keep it empty.
- company is the organisation the interview is for (the hiring company or end client, for example Capgemini, Infosys or Wipro), written as the text names it.
  Return only the name. Do NOT put the meeting platform, a job role, a person's name or an email address in company.
  Do not infer it from an email domain. If the text does not name the company, keep company empty.
- If only start_time is visible and no end_time or duration is mentioned, leave end_time empty.
- Convert all times to 12-hour hh:mm AM/PM format.
- Convert all dates to YYYY-MM-DD.
- confidence_score must be between 0 and 100.

Schema:
{"candidate_name": "", "candidate_phone": "", "client_name": "", "technology": "", "company": "", "service_type": "", "interview_round": "", "interview_date": "YYYY-MM-DD", "start_time": "hh:mm AM/PM", "end_time": "hh:mm AM/PM", "timezone": "Asia/Kolkata", "timezone_written": "", "meeting_platform": "", "screenshot_source": "", "meeting_link": "", "attendee_name": "", "confidence_score": 0, "missing_fields": [], "warnings": [], "raw_detected_text": "", "is_payment_screenshot": false, "looks_like_interview_invite": true}

OCR TEXT:
"""


# ── Time normalization ──────────────────────────────────────────────────────
def normalize_time_to_12h(time_value: str) -> str:
    """Convert any time format to 12-hour hh:mm AM/PM format.

    Examples:
        00:00 -> 12:00 AM
        09:00 -> 09:00 AM
        11:30 -> 11:30 AM
        12:00 -> 12:00 PM
        14:30 -> 02:30 PM
        19:45 -> 07:45 PM
        23:15 -> 11:15 PM
        7 PM -> 07:00 PM
        7:30 pm -> 07:30 PM
        11:00 AM -> 11:00 AM
    """
    if not time_value or not time_value.strip():
        return ""

    val = time_value.strip()

    # Already in 12h format? (e.g., "11:00 AM", "02:30 PM")
    match_12h = re.match(r'^(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)$', val)
    if match_12h:
        h, m, ap = int(match_12h.group(1)), int(match_12h.group(2)), match_12h.group(3).upper()
        # Vision models occasionally combine a 24-hour hour with an AM/PM
        # suffix (for example, "14:30 PM"). Normalize that malformed hybrid
        # instead of allowing it into a confirmed booking.
        if h > 12:
            ap = "PM"
            h -= 12
        if not 1 <= h <= 12 or not 0 <= m <= 59:
            return ""
        return f"{h:02d}:{m:02d} {ap}"

    # Short 12h format (e.g., "7 PM", "11 AM")
    match_short = re.match(r'^(\d{1,2})\s*(AM|PM|am|pm)$', val)
    if match_short:
        h, ap = int(match_short.group(1)), match_short.group(2).upper()
        return f"{h:02d}:00 {ap}"

    # 24-hour format (e.g., "14:30", "09:00", "7:30")
    match_24h = re.match(r'^(\d{1,2}):(\d{2})$', val)
    if match_24h:
        h, m = int(match_24h.group(1)), int(match_24h.group(2))
        if h == 0:
            return f"12:{m:02d} AM"
        elif h < 12:
            return f"{h:02d}:{m:02d} AM"
        elif h == 12:
            return f"12:{m:02d} PM"
        else:
            return f"{h - 12:02d}:{m:02d} PM"

    # HH:MM format with AM/PM stuck together (e.g., "2:30PM")
    match_stuck = re.match(r'^(\d{1,2}):(\d{2})(AM|PM|am|pm)$', val)
    if match_stuck:
        h, m, ap = int(match_stuck.group(1)), int(match_stuck.group(2)), match_stuck.group(3).upper()
        return f"{h:02d}:{m:02d} {ap}"

    return val  # Return as-is if can't parse


def validate_12h_time_format(time_value: str) -> bool:
    """Check if a time string is valid 12-hour format."""
    if not time_value:
        return False
    return bool(re.match(r'^(0[1-9]|1[0-2]):[0-5]\d\s(AM|PM)$', time_value))


def normalize_model_time_to_12h(time_value: str) -> str:
    """Normalize a vision-model time without inventing a missing meridiem.

    A bare hour from 01 through 12 is ambiguous: ``04:00`` may mean either
    4 AM or 4 PM. Twenty-four-hour values outside that range remain
    unambiguous, and explicit AM/PM values keep their existing behavior.
    """
    raw = str(time_value or "").strip()
    bare = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if bare:
        hour, minute = int(bare.group(1)), int(bare.group(2))
        if 1 <= hour <= 12 and 0 <= minute <= 59:
            return ""
    normalized = normalize_time_to_12h(raw)
    return normalized if validate_12h_time_format(normalized) else ""


def normalize_time_to_24h(time_value: str) -> str:
    normalized = normalize_time_to_12h(time_value)
    match = re.match(r"^(\d{2}):(\d{2})\s+(AM|PM)$", normalized)
    if not match:
        return ""
    hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3)
    if meridiem == "AM" and hour == 12:
        hour = 0
    elif meridiem == "PM" and hour != 12:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def _date_time_agree(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Require exact agreement on the fields that can create a booking."""
    first_date = str(first.get("interview_date") or "").strip()
    second_date = str(second.get("interview_date") or "").strip()
    first_time = normalize_time_to_12h(str(first.get("start_time") or ""))
    second_time = normalize_time_to_12h(str(second.get("start_time") or ""))
    return bool(
        first_date
        and first_time
        and first_date == second_date
        and first_time == second_time
    )


def _normalize_supported_timezone(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip()).lower()
    if normalized in {
        "asia/kolkata",
        "ist",
        "india standard time",
        "gmt+05:30",
        "gmt +05:30",
        "utc+05:30",
        "utc +05:30",
    }:
        return "Asia/Kolkata"
    return ""


def _timezone_agrees(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_timezone = _normalize_supported_timezone(str(first.get("timezone") or ""))
    second_timezone = _normalize_supported_timezone(str(second.get("timezone") or ""))
    return bool(first_timezone and first_timezone == second_timezone)


# ── Ollama API calls ────────────────────────────────────────────────────────
def _is_ollama_available() -> bool:
    """Check if Ollama is running and accessible."""
    status = health(model=OLLAMA_REASONING_MODEL, timeout=5)
    return bool(status.get("endpoint_reachable") and status.get("model_available"))


def call_ollama_vision_model(
    model_name: str,
    image_base64: str,
    prompt: str,
    *,
    timeout: int = OLLAMA_TIMEOUT,
) -> str | None:
    """Call Ollama vision model with an image and prompt.

    Returns the raw text response or None on failure.
    """
    try:
        result = chat(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            images=[image_base64],
            timeout=timeout,
            temperature=0.1,
            num_predict=2048,
            # Qwen3-VL can consume the complete output budget in its hidden
            # reasoning field and leave message.content empty. Screenshot
            # extraction needs only the final structured answer.
            think=False,
            workload="interview_screenshot_vision",
        )
        if not result.content:
            return None
        return InferenceResponse(
            result.content,
            node_id=result.node_id,
            node_label=result.node_label,
        )
    except AIGatewayError as exc:
        logger.warning("Ollama interview vision failed model=%s code=%s", model_name, exc.code)
        return None


def parse_strict_json_response(response_text: str) -> dict[str, Any] | None:
    """Parse JSON from model response, handling common issues."""
    if not response_text:
        return None

    text = response_text.strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```)
        lines = lines[1:]
        # Remove last line if it's ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Remove thinking tags if present (qwen2.5 sometimes adds these)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def call_ollama_text_model(
    model_name: str,
    prompt: str,
    *,
    timeout: int = OLLAMA_TEXT_TIMEOUT,
) -> str | None:
    """Call Ollama text model (no image) for OCR text cleanup.

    Returns the raw text response or None on failure.
    """
    try:
        result = chat(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
            temperature=0.1,
            num_predict=2048,
            workload="interview_screenshot_text",
        )
        return result.content or None
    except AIGatewayError as exc:
        logger.warning("Ollama interview text failed model=%s code=%s", model_name, exc.code)
        return None


def retry_invalid_json_once(
    model_name: str,
    image_base64: str,
    original_response: str,
) -> dict[str, Any] | None:
    """Retry with a correction prompt if first response was invalid JSON."""
    response = call_ollama_vision_model(
        model_name,
        image_base64,
        RETRY_PROMPT,
    )
    if response:
        return _attach_inference_node(parse_strict_json_response(response), response)
    return None


# What a model puts in `company` when it has nothing to say, and the names of
# the places an invite is held or received. None of these is the organisation
# the interview is for, so none may fill the field the candidate would then
# confirm without reading.
_NO_COMPANY = frozenset({
    "", "na", "n a", "none", "null", "nil", "unknown", "unspecified", "tbd", "not visible",
    "not available", "not mentioned", "not specified", "not found", "not provided",
    "not applicable", "company", "company name", "client", "client name",
    "organisation", "organization", "customer",
})
_NOT_A_COMPANY = frozenset({
    "hirepro", "flocareer", "zoom", "zoom meeting", "teams", "microsoft teams", "google meet", "meet",
    "barraiser", "skype", "webex", "cisco webex", "gmail", "google calendar", "calendar", "outlook",
    "whatsapp", "telegram", "linkedin", "naukri", "codility", "hackerrank", "hackerearth",
    "mettl", "mercer mettl", "amcat", "cocubes", "jobma", "hirevue", "interview", "interviewer",
})
# A model that has nothing to put in `company` often writes a phrase instead of leaving it empty
# ("Unknown company", "Not specified in the invite", "No company mentioned"). The exact words above
# cannot list every phrasing, so a value is also refused when it opens with one of these words, when it
# says the name is missing, or when it is made only of generic words ("Hiring company", "Employer").
_NO_COMPANY_FIRST_WORDS = frozenset({
    "unknown", "unspecified", "undefined", "unnamed", "unnamed", "not", "no", "none", "null", "tbd", "tba", "na", "n",
})
_NAME_IS_MISSING = (
    "not visible", "not mentioned", "not specified", "not provided", "not available", "not found", "not named",
    "not stated", "not given", "not shown", "not clear", "not disclosed", "name unknown",
)
_GENERIC_WORDS = frozenset({
    "company", "organisation", "organization", "client", "customer", "employer", "name", "the", "a", "hiring",
    "end", "interviewing", "firm", "business", "unknown", "your", "our", "this", "that",
})
_COMPANY_MAX_LENGTH = 120
_COMPANY_MAX_WORDS = 10


def _company_key(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def clean_company_name(value: Any, *, people: tuple[str, ...] = ()) -> str:
    """The company an invite names, or "" when what the model returned cannot be one.

    A model may answer with a string, an object with a `name`, a placeholder
    ("N/A", "Not visible"), the platform the invite came through, the
    candidate's own name, an address or a whole sentence. Only a plausible
    organisation name is kept; anything else is "", which is what sends the
    candidate to type it. A wrong name saved without anyone noticing is worse
    than an empty field that is asked for.
    """
    if isinstance(value, dict):
        value = value.get("name") or value.get("company") or ""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip(" \t\"'`,;:-|")
    if not text or len(text) > _COMPANY_MAX_LENGTH:
        return ""
    if "@" in text or "://" in text or text.lower().startswith("www."):
        return ""
    if not re.search(r"[A-Za-z]", text) or len(text.split()) > _COMPANY_MAX_WORDS:
        return ""
    key = _company_key(text)
    if key in _NO_COMPANY or key in _NOT_A_COMPANY:
        return ""
    words = key.split()
    if words and (words[0] in _NO_COMPANY_FIRST_WORDS or all(w in _GENERIC_WORDS for w in words)):
        return ""
    if any(phrase in key for phrase in _NAME_IS_MISSING):
        return ""
    if key and key in {_company_key(person) for person in people if person}:
        return ""
    return text


# The booking form fills platform, meeting link and time zone only with values that pass these checks; anything else is
# left empty for the candidate to type. A known platform, a complete link on a known meeting service, a time zone the
# invite actually writes: nothing is assumed.
_PLATFORMS = (
    (("microsoft teams", "ms teams", "teams meeting", "teams"), "Microsoft Teams"),
    (("google meet", "meet.google", "gmeet"), "Google Meet"),
    (("zoom",), "Zoom"),
    (("webex",), "Webex"),
    (("hirepro",), "HirePro"),
    (("flocareer",), "FloCareer"),
    (("barraiser",), "BarRaiser"),
    (("skype",), "Skype"),
    (("amazon chime", "chime"), "Amazon Chime"),
    (("gotomeeting", "goto meeting"), "GoTo Meeting"),
    (("jitsi",), "Jitsi Meet"),
    (("telephonic", "phone call", "phone interview", "voice call"), "Phone call"),
    (("in person", "in-person", "face to face", "face-to-face", "f2f", "walk-in", "walk in", "office visit"), "In person"),
)
_MEETING_HOSTS = (
    "teams.microsoft.com", "teams.live.com", "zoom.us", "meet.google.com", "webex.com", "hirepro.in", "flocareer.com",
    "barraiser.com", "skype.com", "chime.aws", "gotomeeting.com", "goto.com", "meet.jit.si",
)
_TIMEZONES = {
    "ist": "IST", "india standard time": "IST", "asia/kolkata": "IST", "asia/calcutta": "IST", "gmt+5:30": "IST", "gmt+05:30": "IST",
    "utc+5:30": "IST", "utc+05:30": "IST", "gmt +5:30": "IST", "gmt +05:30": "IST", "utc +05:30": "IST", "+05:30": "IST",
    "est": "EST", "edt": "EDT", "et": "ET", "cst": "CST", "cdt": "CDT", "ct": "CT", "mst": "MST", "mdt": "MDT", "pst": "PST", "pdt": "PDT",
    "pt": "PT", "gmt": "GMT", "utc": "UTC", "bst": "BST", "cet": "CET", "cest": "CEST", "eet": "EET", "sgt": "SGT", "aest": "AEST",
    "aedt": "AEDT", "jst": "JST", "gst": "GST", "pkt": "PKT", "npt": "NPT", "bdt": "BDT",
}


def clean_meeting_platform(value: Any) -> str:
    """A known meeting platform's own name, or "" when the reading names none we recognise."""
    text = " ".join(str(value or "").lower().split())
    if not text:
        return ""
    for spellings, name in _PLATFORMS:
        if any(spelling in text for spelling in spellings):
            return name
    return ""


def clean_meeting_link(value: Any) -> str:
    """A complete http(s) link on a known meeting service, or "". A link cut off with ... is never complete."""
    from urllib.parse import urlsplit

    text = str(value or "").strip().strip("<>()[]\"'")
    if not text or len(text) > 600 or any(ch.isspace() for ch in text) or "..." in text or "\u2026" in text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("https", "http") or not host or not parts.path.strip("/"):
        return ""
    if not any(host == known or host.endswith("." + known) for known in _MEETING_HOSTS):
        return ""
    return text


def clean_written_timezone(value: Any) -> str:
    """The time zone the invite writes, as a short label (IST, EST, GMT...), or "" when none is written or it is unclear."""
    text = " ".join(str(value or "").lower().replace("(", " ").replace(")", " ").split())
    if not text:
        return ""
    if text in _TIMEZONES:
        return _TIMEZONES[text]
    for word in text.replace(",", " ").split():
        if word in _TIMEZONES:
            return _TIMEZONES[word]
    return ""


def validate_invite_extraction(extracted: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the extracted data."""
    if not extracted:
        return _empty_extraction()

    # Normalize times to 12h format
    if extracted.get("start_time"):
        extracted["start_time"] = normalize_time_to_12h(extracted["start_time"])
    if extracted.get("end_time"):
        extracted["end_time"] = normalize_time_to_12h(extracted["end_time"])
    normalized_timezone = _normalize_supported_timezone(
        str(extracted.get("timezone") or "")
    )
    if normalized_timezone:
        extracted["timezone"] = normalized_timezone

    # ── Fix wrong year: if extracted date is in the past, correct year ──────
    if extracted.get("interview_date"):
        extracted["interview_date"] = _fix_past_year(extracted["interview_date"])

    # The company is read like any other field, but a placeholder, a platform
    # or a person is not one, so it is checked before anything shows it.
    extracted["company"] = clean_company_name(
        extracted.get("company"),
        people=tuple(
            str(extracted.get(field) or "")
            for field in ("candidate_name", "client_name", "attendee_name")
        ),
    )

    # What the booking form may fill without asking: only values that pass the checks above.
    extracted["confirmed_platform"] = clean_meeting_platform(extracted.get("meeting_platform"))
    extracted["confirmed_meeting_link"] = clean_meeting_link(extracted.get("meeting_link"))
    extracted["confirmed_timezone"] = clean_written_timezone(extracted.get("timezone_written"))

    # Ensure confidence_score is an integer 0-100
    score = extracted.get("confidence_score", 0)
    try:
        score = max(0, min(100, int(score)))
    except (ValueError, TypeError):
        score = 0
    extracted["confidence_score"] = score

    # Check required fields
    missing = []
    if not extracted.get("interview_date"):
        missing.append("interview_date")
    if not extracted.get("start_time"):
        missing.append("start_time")
    if not extracted.get("interview_round"):
        missing.append("interview_round")
    extracted["missing_fields"] = missing

    # Determine if manual fields are needed
    extracted["manual_fields_required"] = bool(
        not extracted.get("interview_date")
        or not extracted.get("start_time")
        or score < 70
    )

    # Ensure boolean fields
    extracted.setdefault("is_payment_screenshot", False)
    extracted.setdefault("looks_like_interview_invite", True)
    extracted.setdefault("warnings", [])
    extracted.setdefault("screenshot_source", "")
    extracted["date"] = extracted.get("interview_date") or ""
    extracted["time"] = normalize_time_to_24h(str(extracted.get("start_time") or ""))
    extracted["time_end"] = normalize_time_to_24h(str(extracted.get("end_time") or ""))

    return extracted


def _fix_past_year(date_str: str) -> str:
    """If the extracted date is in the past, fix the year to current/next year.

    Vision models sometimes return wrong years (e.g., 2023 instead of 2026)
    because their training data is from that era.
    """
    if not date_str:
        return date_str
    try:
        from datetime import datetime, date
        parts = date_str.split("-")
        if len(parts) != 3:
            return date_str
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        extracted_date = date(y, m, d)
        today = date.today()

        # If date is more than 7 days in the past, the year is likely wrong
        if (today - extracted_date).days > 7:
            # Try current year first
            try:
                corrected = date(today.year, m, d)
                if (today - corrected).days <= 7:
                    # Current year, within a week — accept it
                    return corrected.isoformat()
                elif corrected > today:
                    # Current year, in the future — accept it
                    return corrected.isoformat()
                else:
                    # Still in the past with current year, try next year
                    corrected = date(today.year + 1, m, d)
                    return corrected.isoformat()
            except ValueError:
                return date_str
        return date_str
    except (ValueError, TypeError):
        return date_str


def detect_payment_screenshot_from_ai(extracted: dict[str, Any]) -> bool:
    """Check if the AI detected this as a payment screenshot."""
    return bool(extracted.get("is_payment_screenshot"))


def compare_primary_backup_extractions(
    primary: dict[str, Any],
    backup: dict[str, Any],
) -> dict[str, Any]:
    """Compare primary and backup model outputs. Return merged result with warnings."""
    if not backup:
        return primary

    warnings = list(primary.get("warnings", []))
    conflicts = []

    # Compare key fields
    for field in ("interview_date", "start_time", "end_time", "interview_round", "meeting_platform"):
        p_val = (primary.get(field) or "").strip()
        b_val = (backup.get(field) or "").strip()
        if p_val and b_val and p_val.lower() != b_val.lower():
            conflicts.append(field)

    if conflicts:
        warnings.append("AI found conflicting invite details. Please verify manually.")
        # Lower confidence when models disagree
        primary["confidence_score"] = min(primary.get("confidence_score", 0), 75)
        primary["manual_fields_required"] = True

    primary["warnings"] = warnings
    return primary


def _empty_extraction() -> dict[str, Any]:
    """Return empty extraction result."""
    return {
        "candidate_name": "",
        "candidate_phone": "",
        "client_name": "",
        "technology": "",
        "company": "",
        "service_type": "",
        "interview_round": "",
        "interview_date": "",
        "start_time": "",
        "end_time": "",
        "timezone": "Asia/Kolkata",
        "meeting_platform": "",
        "screenshot_source": "",
        "meeting_link": "",
        "timezone_written": "",
        "confirmed_platform": "",
        "confirmed_meeting_link": "",
        "confirmed_timezone": "",
        "attendee_name": "",
        "confidence_score": 0,
        "missing_fields": ["interview_date", "start_time", "interview_round"],
        "warnings": [],
        "raw_detected_text": "",
        "is_payment_screenshot": False,
        "looks_like_interview_invite": False,
        "manual_fields_required": True,
        "auto_booking_safe": False,
        "failure_stage": "",
        "failure_reason": "",
        "date": "",
        "time": "",
        "time_end": "",
    }


def _labeled_ocr_failure_reason(candidate: dict[str, Any] | None) -> str:
    if not candidate:
        return "OCR did not produce a structured invite candidate."
    raw_text = str(candidate.get("raw_detected_text") or "")
    if not re.search(r"\b(?:interview|meeting|assessment)\b", raw_text, re.IGNORECASE):
        return "OCR text did not contain explicit interview context."
    if not candidate.get("_explicit_date") or not candidate.get("interview_date"):
        return "OCR did not contain an explicit supported interview date with a year."
    if not candidate.get("_explicit_start") or not candidate.get("start_time"):
        return "OCR did not contain an explicit supported interview start time."
    if not candidate.get("_timezone_explicit") or candidate.get("timezone") != "Asia/Kolkata":
        return "OCR timezone was missing, ambiguous, or unsupported."
    return ""


def _labeled_ocr_is_authoritative(candidate: dict[str, Any] | None) -> bool:
    return not _labeled_ocr_failure_reason(candidate)


# ── Main extraction function ────────────────────────────────────────────────
def extract_interview_invite_with_ollama(
    image_data: bytes,
    mime_type: str = "image/jpeg",
) -> dict[str, Any]:
    """Read an interview invite, in whichever mode the admin switch selects.

    The mode is snapshotted once here so a switch flipped mid-request cannot
    leave one run half in each mode, and every result reports the mode it was
    produced under.
    """
    mode = processing_mode()
    if mode == "ai":
        # AI-only means exactly that: Tesseract is never invoked, and the
        # dual-source cross-check that would demand its corroboration is not
        # applied. Requiring OCR to confirm what OCR is forbidden to read
        # blocked every booking outright.
        return _extract_with_ai_only(
            image_data, mode=mode, ollama_only=_ollama_only_test_mode()
        )

    result = _extract_with_ocr_and_ai(image_data, mime_type, mode=mode)
    result.setdefault("processing_mode", mode)
    result.setdefault("ocr_used", True)
    return result


def _extract_with_ocr_and_ai(
    image_data: bytes,
    mime_type: str,
    *,
    mode: str,
) -> dict[str, Any]:
    """Extract interview invite details using hybrid OCR + AI approach.

    Hybrid flow (optimized for speed):
      1. OCR image → raw text (Tesseract, instant)
      2. If OCR gets enough text, send to qwen2.5:7b text model for JSON cleanup (~10-30s)
      3. Only if OCR fails or text cleanup fails, call qwen2.5vl:7b vision model (~5 min)
      4. If vision fails, try the backup vision model
      5. If all AI fails, fall back to regex OCR parsing

    Ollama runs on the developer's laptop, tunneled to VPS via SSH.
    """
    ocr_candidate: dict[str, Any] | None = None
    ollama_only = _ollama_only_test_mode()

    # Check if Ollama is reachable (tunnel must be active)
    if not _is_ollama_available():
        if ollama_only:
            result = _empty_extraction()
            result["extraction_source"] = "ollama"
            result["extraction_method"] = "ollama_only_test"
            result["ollama_only_test"] = True
            result["auto_booking_safe"] = False
            result["warnings"] = [
                "Ollama is unavailable. OCR is disabled in test mode; enter the fields manually."
            ]
            return result
        logger.info("Ollama not reachable; OCR cannot authorize automatic booking")
        return _fallback_to_existing_ocr(image_data, mime_type)

    # ── Step 1: Try OCR + text model (fast path) ────────────────────────────
    ocr_text = "" if ollama_only or not ocr_enabled() else _run_tesseract_ocr(image_data)

    if ocr_text and len(ocr_text) > 10:
        logger.info("OCR extracted %d chars, trying fast extraction", len(ocr_text))

        # Try regex first (instant) — works great for structured invites
        regex_result = None
        try:
            from features.slot_screenshot_parse import parse_invite_text
            regex_result = parse_invite_text(ocr_text)
        except Exception as e:
            logger.warning("Regex parse failed: %s", e)

        if regex_result and (regex_result.get("date") or regex_result.get("time")):
            # Preserve partial labelled evidence so failure diagnostics can say
            # exactly which booking field was absent.
            logger.info(
                "Regex proposed date=%s time=%s; requesting vision verification",
                regex_result.get("date", ""),
                regex_result.get("time", ""),
            )

            # Build result from regex (instant)
            result = _empty_extraction()
            result["extraction_source"] = "ocr_ai_cleanup"
            result["primary_model"] = "tesseract+regex"
            result["backup_model"] = OLLAMA_REASONING_MODEL
            result["detected_by"] = "OCR + regex"
            result["extraction_method"] = "hybrid_fast"
            result["interview_date"] = regex_result.get("date", "")
            result["start_time"] = normalize_time_to_12h(regex_result.get("time", ""))
            result["end_time"] = normalize_time_to_12h(regex_result.get("time_end", ""))
            result["interview_round"] = regex_result.get("interview_round", "")
            result["meeting_platform"] = regex_result.get("platform", "")
            result["technology"] = regex_result.get("technology", "")
            result["timezone"] = regex_result.get("timezone", "")
            result["raw_detected_text"] = ocr_text[:3000]
            result["_explicit_date"] = bool(regex_result.get("_explicit_date"))
            result["_explicit_start"] = bool(regex_result.get("_explicit_start"))
            result["_timezone_explicit"] = bool(regex_result.get("_timezone_explicit"))
            result["_labeled"] = bool(regex_result.get("_labeled"))
            result["looks_like_interview_invite"] = True
            fields_found = sum(1 for f in ["interview_date", "start_time", "interview_round", "meeting_platform"] if result.get(f))
            result["confidence_score"] = 0
            result["missing_fields"] = [f for f in ["interview_date", "start_time", "interview_round"] if not result.get(f)]
            result["manual_fields_required"] = True
            result["auto_booking_safe"] = False
            result = validate_invite_extraction(result)
            ocr_candidate = result

        # Regex didn't find date+time — try text model (slower but more capable)
        if ocr_candidate is None:
            logger.info("Regex didn't find date/time, trying text model")
            extracted = _try_text_model_cleanup(ocr_text)
            if extracted:
                extracted = validate_invite_extraction(extracted)
                if extracted.get("interview_date") and extracted.get("start_time"):
                    extracted["extraction_source"] = "ocr_ai_cleanup"
                    extracted["primary_model"] = OLLAMA_REASONING_MODEL
                    extracted["backup_model"] = ""
                    extracted["detected_by"] = f"OCR + {OLLAMA_REASONING_MODEL}"
                    extracted["extraction_method"] = "hybrid_fast"
                    extracted["confidence_score"] = 0
                    extracted["manual_fields_required"] = True
                    extracted["auto_booking_safe"] = False
                    ocr_candidate = extracted
    else:
        logger.info("OCR text too short (%d chars), going to vision model", len(ocr_text or ""))

    if _labeled_ocr_is_authoritative(ocr_candidate):
        ocr_candidate["confidence_score"] = 95
        ocr_candidate["manual_fields_required"] = False
        ocr_candidate["auto_booking_safe"] = True
        ocr_candidate["detected_by"] = "OCR labelled date/time"
        ocr_candidate["extraction_method"] = "ocr_labelled_verified"
        ocr_candidate["failure_stage"] = ""
        ocr_candidate["failure_reason"] = ""
        ocr_candidate["diagnostics"] = {
            "input_bytes": len(image_data),
            "input_transport": "original_upload",
            "image_compressed": False,
            "vision_verification": "not_required_for_explicit_labelled_date_time",
        }
        ocr_candidate = validate_invite_extraction(ocr_candidate)
        logger.info(
            "Invite extraction accepted labelled OCR date=%s start=%s end=%s timezone=%s bytes=%d",
            ocr_candidate.get("interview_date"),
            ocr_candidate.get("time"),
            ocr_candidate.get("time_end"),
            ocr_candidate.get("timezone"),
            len(image_data),
        )
        return ocr_candidate

    # ── Step 2: Try vision model (slow path) ────────────────────────────────
    img_b64 = base64.b64encode(image_data).decode("utf-8")

    logger.info("Calling vision model: %s (timeout=%ds)", OLLAMA_VISION_MODEL, OLLAMA_TIMEOUT)
    start = time.time()
    response = call_ollama_vision_model(
        OLLAMA_VISION_MODEL, img_b64, _get_invite_prompt(), timeout=OLLAMA_TIMEOUT
    )
    elapsed = time.time() - start
    logger.info("Vision model responded in %.1fs", elapsed)

    extracted = None
    used_model = OLLAMA_VISION_MODEL

    if response:
        extracted = _attach_inference_node(
            parse_strict_json_response(response), response
        )
        if not extracted:
            logger.info("Invalid JSON from vision, retrying...")
            extracted = retry_invalid_json_once(OLLAMA_VISION_MODEL, img_b64, response)

    # ── Step 3: If vision failed, try the backup vision model ───────────────
    if (
        not extracted
        and not ollama_only
        and OLLAMA_BACKUP_VISION_MODEL
        and OLLAMA_BACKUP_VISION_MODEL != OLLAMA_VISION_MODEL
    ):
        logger.warning("Vision model (%s) failed, trying backup: %s", OLLAMA_VISION_MODEL, OLLAMA_BACKUP_VISION_MODEL)
        backup_response = call_ollama_vision_model(
            OLLAMA_BACKUP_VISION_MODEL, img_b64, _get_invite_prompt(), timeout=OLLAMA_TIMEOUT
        )
        if backup_response:
            extracted = _attach_inference_node(
                parse_strict_json_response(backup_response), backup_response
            )
            if extracted:
                used_model = OLLAMA_BACKUP_VISION_MODEL

    # ── Step 4: If all AI failed, fall back to regex OCR ────────────────────
    if not extracted:
        ocr_failure_reason = _labeled_ocr_failure_reason(ocr_candidate)
        logger.warning(
            "Invite extraction vision_failed: primary=%s backup=%s ocr_reason=%s bytes=%d",
            OLLAMA_VISION_MODEL,
            OLLAMA_BACKUP_VISION_MODEL,
            ocr_failure_reason,
            len(image_data),
        )
        if ollama_only:
            result = _empty_extraction()
            result["extraction_source"] = "ollama"
            result["extraction_method"] = "ollama_only_test"
            result["ollama_only_test"] = True
            result["auto_booking_safe"] = False
            result["warnings"] = [
                "Ollama could not read this screenshot. OCR is disabled in test mode; enter the fields manually."
            ]
            result["failure_stage"] = "vision"
            result["failure_reason"] = "Vision model returned no parseable JSON."
            return result
        result = _fallback_to_existing_ocr(image_data, mime_type)
        result["failure_stage"] = "vision"
        result["failure_reason"] = ocr_failure_reason or "Vision model returned no parseable JSON."
        return result

    # Validate and normalize
    extracted = validate_invite_extraction(extracted)

    # Add metadata
    extracted["extraction_source"] = "ollama"
    extracted["primary_model"] = used_model
    extracted["backup_model"] = OLLAMA_BACKUP_VISION_MODEL
    extracted["detected_by"] = used_model
    extracted["extraction_method"] = "vision"

    if ollama_only:
        extracted["extraction_method"] = "ollama_only_test"
        extracted["ollama_only_test"] = True
        extracted["auto_booking_safe"] = False
        extracted["manual_fields_required"] = True
        extracted["backup_model"] = ""
        extracted["detected_by"] = f"{used_model} · Ollama-only test"
        return extracted

    # Fail closed: a single parser or model cannot authorize an automatic
    # booking. OCR/text and vision must independently agree on date + start.
    extracted["auto_booking_safe"] = False
    date_time_agree = bool(
        ocr_candidate and _date_time_agree(ocr_candidate, extracted)
    )
    timezone_agrees = bool(
        ocr_candidate and _timezone_agrees(ocr_candidate, extracted)
    )
    if date_time_agree and timezone_agrees:
        extracted["auto_booking_safe"] = True
        extracted["manual_fields_required"] = False
        extracted["confidence_score"] = 95
        extracted["detected_by"] = f"OCR + {used_model} verified"
        extracted["extraction_method"] = "dual_source_verified"

        # End time is optional and is accepted only with exact agreement.
        ocr_end = normalize_time_to_12h(str(ocr_candidate.get("end_time") or ""))
        vision_end = normalize_time_to_12h(str(extracted.get("end_time") or ""))
        if ocr_end and vision_end and ocr_end == vision_end:
            extracted["end_time"] = vision_end
        elif ocr_end or vision_end:
            extracted["end_time"] = ""
            extracted.setdefault("warnings", []).append(
                "End time was not independently confirmed; verify it manually."
            )
    else:
        extracted["confidence_score"] = 0
        extracted["manual_fields_required"] = True
        extracted["failure_stage"] = "cross_source_verification"
        if ocr_candidate and not date_time_agree:
            extracted["verification_conflict"] = {
                "ocr": {
                    "interview_date": ocr_candidate.get("interview_date", ""),
                    "start_time": normalize_time_to_12h(
                        str(ocr_candidate.get("start_time") or "")
                    ),
                },
                "vision": {
                    "interview_date": extracted.get("interview_date", ""),
                    "start_time": normalize_time_to_12h(
                        str(extracted.get("start_time") or "")
                    ),
                },
            }
            extracted.setdefault("warnings", []).append(
                "OCR and AI did not independently agree on the booking date/time. "
                "Automatic booking is blocked; enter the fields manually."
            )
            extracted["failure_reason"] = (
                "OCR and vision returned different date or start-time values."
            )
        elif ocr_candidate:
            # Date/start agreement is not a conflict. Keep the fail-closed
            # manual path, but report the actual missing safety evidence so
            # the UI does not display two identical values as a mismatch.
            extracted.pop("verification_conflict", None)
            extracted.setdefault("warnings", []).append(
                "OCR and AI did not independently agree on a supported timezone. "
                "Automatic booking is blocked; enter the fields manually."
            )
            extracted["failure_reason"] = (
                "OCR and vision agreed on the date and start time, but did not "
                "independently agree on a supported timezone."
            )
        else:
            extracted.setdefault("warnings", []).append(
                "OCR did not independently confirm a supported date and start time. "
                "Automatic booking is blocked; enter the fields manually."
            )
            extracted["failure_reason"] = (
                "OCR did not independently extract a supported date and start time."
            )

    verified = validate_invite_extraction(extracted)
    verified["processing_mode"] = mode
    verified["ocr_used"] = True
    return verified


def _run_tesseract_ocr(image_data: bytes) -> str:
    """Run Tesseract OCR on image and return raw text."""
    try:
        from features.slot_screenshot_parse import _local_ocr_text
        return _local_ocr_text(image_data)
    except Exception as e:
        logger.warning("Tesseract OCR failed: %s", e)
        return ""


def _try_text_model_cleanup(ocr_text: str) -> dict[str, Any] | None:
    """Send OCR text to qwen2.5:7b text model for structured JSON extraction."""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + __import__('datetime').timedelta(days=1)).strftime("%Y-%m-%d")

    date_context = f"\n\nIMPORTANT: Today's date is {today}. If the text says 'Tomorrow', use {tomorrow}. If it says 'Today', use {today}. Resolve all relative dates to absolute YYYY-MM-DD format.\n\nOCR TEXT:\n"
    prompt = TEXT_CLEANUP_PROMPT.rsplit("OCR TEXT:\n", 1)[0] + date_context + ocr_text[:3000]

    response = call_ollama_text_model(OLLAMA_REASONING_MODEL, prompt, timeout=OLLAMA_TEXT_TIMEOUT)
    if not response:
        return None

    extracted = parse_strict_json_response(response)
    return extracted


def _ai_only_vision_extraction(image_data: bytes) -> tuple[dict[str, Any] | None, str]:
    """Run only the vision models. Returns (extraction, model_used)."""
    img_b64 = base64.b64encode(image_data).decode("utf-8")
    used_model = OLLAMA_VISION_MODEL
    response = call_ollama_vision_model(
        OLLAMA_VISION_MODEL, img_b64, _get_invite_prompt(), timeout=OLLAMA_TIMEOUT
    )
    extracted = (
        _attach_inference_node(parse_strict_json_response(response), response)
        if response
        else None
    )
    if response and not extracted:
        extracted = retry_invalid_json_once(OLLAMA_VISION_MODEL, img_b64, response)
    if (
        not extracted
        and OLLAMA_BACKUP_VISION_MODEL
        and OLLAMA_BACKUP_VISION_MODEL != OLLAMA_VISION_MODEL
    ):
        backup = call_ollama_vision_model(
            OLLAMA_BACKUP_VISION_MODEL, img_b64, _get_invite_prompt(), timeout=OLLAMA_TIMEOUT
        )
        if backup:
            extracted = _attach_inference_node(
                parse_strict_json_response(backup), backup
            )
            if extracted:
                used_model = OLLAMA_BACKUP_VISION_MODEL
    if extracted:
        # Preserve exactly what the vision model supplied before normalization.
        # The public API removes these private fields from its response after
        # writing the correlated, non-image diagnostic trace.
        extracted.setdefault(
            "_model_raw_interview_date", str(extracted.get("interview_date") or "")
        )
        extracted.setdefault(
            "_model_raw_start_time", str(extracted.get("start_time") or "")
        )
        extracted.setdefault(
            "_model_raw_end_time", str(extracted.get("end_time") or "")
        )
    return extracted, used_model


def _extract_with_ai_only(
    image_data: bytes,
    *,
    mode: str,
    ollama_only: bool = False,
) -> dict[str, Any]:
    """Invite extraction with OCR globally disabled.

    The admin switch says Tesseract must not run anywhere, so there is no
    second source to cross-check against and no OCR failure worth reporting.
    The vision model's own date and start time are the evidence; when it cannot
    supply them the operator confirms the booking by hand.
    """
    def _finish(result: dict[str, Any]) -> dict[str, Any]:
        result["processing_mode"] = mode
        result["ocr_used"] = False
        # Nothing here consulted OCR, so no OCR wording may reach the operator.
        result["warnings"] = [
            w for w in (result.get("warnings") or []) if "OCR" not in str(w)
        ]
        return result

    if not _is_ollama_available():
        result = _empty_extraction()
        result["extraction_source"] = "ollama"
        result["extraction_method"] = "ai_only"
        result["auto_booking_safe"] = False
        result["manual_fields_required"] = True
        result["failure_stage"] = "ollama_unavailable"
        result["failure_reason"] = "The AI model is unavailable."
        result["warnings"] = [
            "The AI could not be reached, so the invite was not read. "
            "Enter the interview date and start time to continue."
        ]
        return _finish(result)

    extracted, used_model = _ai_only_vision_extraction(image_data)

    if not extracted:
        result = _empty_extraction()
        result["extraction_source"] = "ollama"
        result["extraction_method"] = "ai_only"
        result["primary_model"] = used_model
        result["auto_booking_safe"] = False
        result["manual_fields_required"] = True
        result["failure_stage"] = "vision"
        result["failure_reason"] = "The AI could not read this screenshot."
        result["warnings"] = [
            "The AI could not read this screenshot. "
            "Enter the interview date and start time to continue."
        ]
        return _finish(result)

    raw_start = str(
        extracted.get("_model_raw_start_time", extracted.get("start_time")) or ""
    )
    raw_end = str(
        extracted.get("_model_raw_end_time", extracted.get("end_time")) or ""
    )
    normalized_start = normalize_model_time_to_12h(raw_start)
    normalized_end = normalize_model_time_to_12h(raw_end) if raw_end else ""
    ambiguous_start = bool(raw_start and not normalized_start)
    extracted["start_time"] = normalized_start
    extracted["end_time"] = normalized_end
    extracted = validate_invite_extraction(extracted)
    if ambiguous_start:
        extracted.setdefault("warnings", []).append(
            "The AI returned a start time without a reliable AM or PM. "
            "Choose AM or PM manually before confirming."
        )
    extracted["extraction_source"] = "ollama"
    extracted["primary_model"] = used_model
    extracted["backup_model"] = "" if ollama_only else OLLAMA_BACKUP_VISION_MODEL
    extracted["detected_by"] = f"{used_model} (AI only)"
    extracted["extraction_method"] = "ai_only"
    if ollama_only:
        extracted["ollama_only_test"] = True

    has_date = bool(str(extracted.get("interview_date") or "").strip())
    has_start = validate_12h_time_format(str(extracted.get("start_time") or ""))
    if has_date and has_start and not ollama_only:
        extracted["auto_booking_safe"] = True
        extracted["manual_fields_required"] = False
        extracted["failure_stage"] = ""
        extracted["failure_reason"] = ""
        extracted["confidence_score"] = max(
            int(extracted.get("confidence_score") or 0), 85
        )
    else:
        extracted["auto_booking_safe"] = False
        extracted["manual_fields_required"] = True
        extracted["failure_stage"] = "ai_incomplete"
        missing = [
            label
            for label, present in (("date", has_date), ("start time", has_start))
            if not present
        ]
        extracted["failure_reason"] = (
            "The AI did not return a complete " + " and ".join(missing) + "."
            if missing
            else "Automatic booking is disabled for this run."
        )
        if missing:
            extracted.setdefault("warnings", []).append(
                "The AI could not read the interview "
                + " and ".join(missing)
                + ". Enter it to continue."
            )
    return _finish(extracted)


def _fallback_to_existing_ocr(image_data: bytes, mime_type: str) -> dict[str, Any]:
    """Fall back to existing OCR parsing when Ollama is unavailable."""
    try:
        from features.slot_screenshot_parse import parse_invite_screenshot

        parsed = parse_invite_screenshot(image_data, mime_type)

        # Convert existing OCR output to our format
        result = _empty_extraction()
        result["extraction_source"] = "ocr_fallback"
        result["primary_model"] = "tesseract"
        result["backup_model"] = ""
        result["interview_date"] = parsed.get("date", "")
        result["start_time"] = normalize_time_to_12h(parsed.get("time", ""))
        result["end_time"] = normalize_time_to_12h(parsed.get("time_end", ""))
        result["interview_round"] = parsed.get("interview_round", "")
        result["meeting_platform"] = parsed.get("platform", "")
        result["technology"] = parsed.get("technology", "")
        result["looks_like_interview_invite"] = True
        result["warnings"] = ["AI extraction unavailable. Using standard OCR/manual entry."]

        result["confidence_score"] = 0
        result["missing_fields"] = [f for f in ["interview_date", "start_time", "interview_round"] if not result.get(f)]
        result["manual_fields_required"] = True
        result["auto_booking_safe"] = False
        result["failure_stage"] = "fallback_ocr"
        result["failure_reason"] = "AI was unavailable; fallback OCR cannot authorize booking."

        return result
    except Exception as e:
        logger.warning("Fallback OCR also failed: %s", e)
        result = _empty_extraction()
        result["extraction_source"] = "failed"
        result["warnings"] = ["AI extraction unavailable. Using standard OCR/manual entry."]
        result["failure_stage"] = "fallback_ocr"
        result["failure_reason"] = f"Fallback OCR failed: {e}"
        return result
