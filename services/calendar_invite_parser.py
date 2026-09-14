"""Deterministic, authenticated RFC 5545 interview invitation parsing.

Calendar data is only promoted to an automatic-booking result when the Gmail
envelope, sender authentication, organizer and attendee all agree.  Anything
less remains on the semantic AI/manual-review path.
"""
from __future__ import annotations

import re
import os
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_INTERVIEW_RE = re.compile(r"\b(interview|technical round|managerial round|hr round)\b", re.I)

# Consumer mail providers. An invitation from one of these is somebody's own
# diary — a friend, a family event, the candidate inviting themselves — not an
# employer scheduling a round.
_CONSUMER_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.co.uk",
    "outlook.com", "hotmail.com", "live.com", "msn.com", "icloud.com", "me.com",
    "proton.me", "protonmail.com", "pm.me", "aol.com", "rediffmail.com",
    "zoho.com", "mail.com", "gmx.com", "yandex.com",
})

# An interview is a small meeting. A mass invitation — a webinar, a careers
# open day, a newsletter event — is not one, however well authenticated.
_MAX_INTERVIEW_ATTENDEES = 5
_CANCELLED_SUBJECT_RE = re.compile(
    r"^\s*(?:(?:re|fw|fwd)\s*:\s*)*(?:cancelled|canceled)\s*:",
    re.I,
)
_STABLE_INTERVIEW_ID_RE = re.compile(r"\b(?=[A-Z0-9]*\d)[A-Z][A-Z0-9]{7,}\b")
_URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
_WINDOWS_TZ = {
    "India Standard Time": "Asia/Kolkata", "Singapore Standard Time": "Asia/Singapore",
    "UTC": "UTC", "GMT Standard Time": "Europe/London",
    # Exchange sometimes serializes UTC as `TZID=Z` rather than using a
    # trailing `Z` datetime. It is still an explicit, non-floating timezone.
    "Z": "UTC",
    "Eastern Standard Time": "America/New_York", "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver", "Pacific Standard Time": "America/Los_Angeles",
}
_MONTHS = {name.lower(): index for index, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1
)}
_MONTHS.update({name[:3].lower(): value for name, value in list(_MONTHS.items())})


def _explicit_interview_round(*values: str) -> str | None:
    """Return only a round label that is explicitly present in source text."""
    text = " ".join(str(value or "") for value in values)
    level = re.search(r"\bL\s*([12])\b", text, re.I)
    if level:
        return f"L{level.group(1)}"
    for pattern, label in (
        (r"\bHR\s+(?:interview|round)\b|\b(?:interview|round)\s+HR\b", "HR"),
        (r"\bfinal\s+(?:interview|round)\b|\b(?:interview|round)\s+final\b", "Final"),
        (r"\bscreening\s+(?:interview|round)\b|\b(?:interview|round)\s+screening\b", "Screening"),
    ):
        if re.search(pattern, text, re.I):
            return label
    return None


def _unfold(text: str) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in normalized.split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _decode(value: str) -> str:
    return (value.replace("\\n", "\n").replace("\\N", "\n")
            .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def _property(line: str) -> tuple[str, dict[str, str], str] | None:
    if ":" not in line:
        return None
    left, value = line.split(":", 1)
    pieces = left.split(";")
    params: dict[str, str] = {}
    for item in pieces[1:]:
        if "=" in item:
            key, param = item.split("=", 1)
            params[key.upper()] = param.strip('"')
    return pieces[0].upper(), params, _decode(value.strip())


def _email(value: str) -> str:
    raw = unquote(str(value or "").strip())
    if raw.lower().startswith("mailto:"):
        raw = raw[7:]
    return parseaddr(raw)[1].strip().lower()


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def _parse_datetime(value: str, params: dict[str, str]) -> tuple[datetime | None, str | None]:
    raw = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", raw):
        return None, None
    tzid = params.get("TZID")
    try:
        if raw.endswith("Z"):
            parsed = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            return parsed, "UTC"
        if not tzid:
            return None, None
        zone = ZoneInfo(_WINDOWS_TZ.get(tzid, tzid))
        parsed = datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=zone)
        return parsed, zone.key
    except (ValueError, ZoneInfoNotFoundError):
        return None, None


def parse_calendar(text: str) -> dict[str, Any] | None:
    """Return the first VEVENT with explicit timezone-bearing DTSTART."""
    method = ""
    event: dict[str, list[tuple[dict[str, str], str]]] = {}
    in_event = False
    for line in _unfold(text):
        parsed = _property(line)
        if not parsed:
            continue
        name, params, value = parsed
        if name == "METHOD" and not in_event:
            method = value.upper()
        elif name == "BEGIN" and value.upper() == "VEVENT":
            in_event = True
        elif name == "END" and value.upper() == "VEVENT":
            break
        elif in_event:
            event.setdefault(name, []).append((params, value))
    if not event:
        return None

    def first(name: str) -> tuple[dict[str, str], str]:
        return (event.get(name) or [({}, "")])[0]

    start, timezone_name = _parse_datetime(first("DTSTART")[1], first("DTSTART")[0])
    end, _ = _parse_datetime(first("DTEND")[1], first("DTEND")[0])
    organizer = _email(first("ORGANIZER")[1])
    attendees = [_email(value) for _params, value in event.get("ATTENDEE", [])]
    description = first("DESCRIPTION")[1]
    location = first("LOCATION")[1]
    urls = _URL_RE.findall(" ".join((description, location, first("URL")[1])))
    sequence_raw = first("SEQUENCE")[1] or "0"
    try:
        sequence = max(0, int(sequence_raw))
    except ValueError:
        sequence = 0
    return {
        "method": method or "REQUEST", "uid": first("UID")[1], "sequence": sequence,
        "status": first("STATUS")[1].upper(), "summary": first("SUMMARY")[1],
        "description": description, "location": location, "organizer": organizer,
        "attendees": [address for address in attendees if address], "start": start,
        "end": end, "timezone": timezone_name, "meeting_link": urls[0].rstrip(".,);>") if urls else None,
    }


def _sender_authenticated(decoded: dict[str, Any], sender: str) -> bool:
    auth = str(decoded.get("authentication_results") or "").lower()
    spf = str(decoded.get("received_spf") or "").lower()
    sender_domain = _domain(sender)
    dmarc_ok = "dmarc=pass" in auth and sender_domain and f"header.from={sender_domain}" in auth
    dkim_ok = "dkim=pass" in auth and sender_domain and (
        f"header.d={sender_domain}" in auth or f"header.i=@{sender_domain}" in auth
    )
    spf_ok = ("spf=pass" in auth or spf.startswith("pass")) and sender_domain and sender_domain in (auth + " " + spf)
    return bool(dmarc_ok or dkim_ok or spf_ok)


def _plain_text_interview_cancellation_result(
    decoded: dict[str, Any],
) -> dict[str, Any] | None:
    """Recognize an authenticated calendar-style cancellation without trusting AI.

    Some Exchange/Google calendar cancellation messages do not expose their
    RFC 5545 payload as a downloadable ``.ics`` attachment.  Gmail still
    renders them as cancelled events, but the semantic model only sees the
    subject/body and can mistake the quoted original invitation for a new
    shortlist.  Accept this narrow fallback only when:

    * the message is inbound and sender-authenticated;
    * the subject explicitly begins with Canceled/Cancelled; and
    * the source names an interview, or contains both an explicit interview
      round and a stable enterprise requisition/event identifier.

    The booking service still has to match this event to an existing confirmed
    slot before any mutation occurs.
    """
    if str(decoded.get("message_direction") or "INBOUND").upper() != "INBOUND":
        return None
    sender = _email(str(decoded.get("sender_email") or ""))
    recipient = _email(str(decoded.get("recipient_email") or ""))
    subject = str(decoded.get("subject") or "")
    body = str(decoded.get("body") or "")
    if not sender or not recipient or not _sender_authenticated(decoded, sender):
        return None
    if not _CANCELLED_SUBJECT_RE.search(subject):
        return None

    explicit_round = _explicit_interview_round(subject, body)
    stable_ids = sorted(set(_STABLE_INTERVIEW_ID_RE.findall(subject.upper())))
    if not _INTERVIEW_RE.search(f"{subject}\n{body}") and not (
        explicit_round and stable_ids
    ):
        return None

    evidence_text = f"{subject} | authenticated sender {_domain(sender)}"
    role = _CANCELLED_SUBJECT_RE.sub("", subject, count=1).strip() or None
    return {
        "schema_version": "selection_offer_event_v1",
        "is_recruitment_related": True,
        "is_selection_or_offer_related": True,
        "should_create_review_record": True,
        "status": "INTERVIEW_CANCELLED",
        "primary_status": "INTERVIEW_CANCELLED",
        "classification": "interview_cancelled",
        "candidate_status": "Interview Cancelled",
        "confidence": 0.99,
        "ignore_reason": None,
        "reason": (
            "Authenticated inbound calendar-style cancellation with an "
            "explicit cancelled subject."
        ),
        "candidate": {"name": None, "email": recipient},
        "company": {"name": None, "domain": _domain(sender)},
        "job": {"title": role, "employment_type": None, "location": None},
        "recruiter": {
            "name": decoded.get("sender_name"),
            "email": sender,
        },
        "interview": {
            "date": None,
            "time": None,
            "timezone": None,
            "mode": None,
            "round": explicit_round,
            "location": None,
            "meeting_link": None,
            "stable_ids": stable_ids,
        },
        "offer": {
            "offer_detected": False,
            "offer_letter_detected": False,
            "appointment_letter_detected": False,
            "offer_date": None,
            "offered_ctc": None,
            "currency": None,
            "joining_date": None,
            "offer_expiry_date": None,
        },
        "attachments": [],
        "evidence": [{
            "source": "EMAIL_SUBJECT",
            "meaning": "INTERVIEW_CANCELLED",
            "text": evidence_text[:500],
        }],
        "risk_flags": [],
        "requires_manual_review": False,
        "summary": f"Trusted interview cancellation: {subject}",
        "recommended_action": (
            "Match the cancellation to an existing confirmed interview slot."
        ),
        "classification_source": "STRUCTURED_EMAIL_VERIFIED",
        "structured_validation_status": "TRUSTED",
        "ai_validation_status": "NOT_REQUIRED",
        "ai_status": "NOT_REQUIRED",
        "validation_status": "VALIDATED",
        "email_intent": "INTERVIEW_CANCELLATION",
        "document_type": "INTERVIEW_INVITATION_DOCUMENT",
        "is_candidate_specific": True,
        "is_job_outcome": False,
        "is_current_event": True,
        "is_questionnaire": False,
        "is_promotional_or_job_ad": False,
        "is_historical_information": False,
        "historical_employment_evidence": False,
        "lifecycle_event": "NONE",
        "interview_event": "INTERVIEW_CANCELLED",
        "business_domain": "INTERVIEW_TRACKING",
        "evidence_summary": evidence_text[:1000],
        "schedule_state": "UNKNOWN",
    }


# Month spellings accepted in plain-text invitations, in either order.
_MONTH_WORD = (
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?"
    r"|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
# The year is optional in both orders, and TCS writes it with no separator at
# all ("15th September26"), so none is required. It must not swallow the hour
# of a following clock time: "Sep 3 10:30 AM" is the 3rd at half past ten, not
# the year 2010.
_YEAR = r"(?:[\s,]*(\d{2,4})\b(?!\s*:))?"
# A month name must not be the start of an ordinary word - "3 Marching orders"
# names no date - but "September26" must still end it.
_MONTH_END = r"(?![a-z])"
# "3 September 2026" / "3rd Sep, 26" / "15th September26" / "3rd September"
_DAY_FIRST = re.compile(
    r"\b([0-3]?\d)(?:st|nd|rd|th)?\b[\s,]+" + _MONTH_WORD + _MONTH_END + _YEAR, re.I,
)
# "September 3, 2026" / "Sep 3"
_MONTH_FIRST = re.compile(
    r"\b" + _MONTH_WORD + _MONTH_END + r"[\s,]+([0-3]?\d)(?:st|nd|rd|th)?\b" + _YEAR, re.I,
)


def _nearest_occurrence(reference: datetime, month: int, day: int) -> datetime | None:
    """The instance of month/day closest to the message's own send date.

    An invitation that omits the year means the next one coming up, which is
    also how a December mail about a January interview has to read.
    """
    base = reference.date() if isinstance(reference, datetime) else reference
    best: tuple[int, datetime] | None = None
    for year in (base.year - 1, base.year, base.year + 1):
        try:
            candidate = datetime(year, month, day)
        except ValueError:
            continue  # 29 February outside a leap year
        delta = abs((candidate.date() - base).days)
        if best is None or delta < best[0]:
            best = (delta, candidate)
    # Nothing within a year of the message is not a reading of that message.
    if best is None or best[0] > 366:
        return None
    return best[1]


def _plain_date(text: str, reference: datetime) -> datetime | None:
    """The interview day from plain text, in either month order.

    Recruiters write the day both ways - "3rd September 2026" and
    "September 3, 2026" - and routinely leave the year out altogether
    ("Interview scheduled on 3rd Sep at 5:00 PM IST"). Only the day-first
    spelling carrying an explicit year was read here, so every other wording
    yielded no date at all.

    That is not a harmless miss. When routing does not send a message to the
    AI, this parser is the only thing that can classify it, so an unread date
    drops an authenticated invitation as not recruitment-related and the slot
    is never booked.

    A missing year is resolved against `reference`, the message's own send
    time. Nothing is guessed between two competing readings: a spelling that
    states its year still wins, an all-numeric date keeps its existing
    day-first-only handling, and a date that resolves into the past is still
    refused by the booking validator downstream.
    """
    found: list[tuple[int, int, int, str | None]] = []
    for pattern, order in ((_DAY_FIRST, "dmy"), (_MONTH_FIRST, "mdy")):
        for hit in pattern.finditer(text):
            first, second, year_text = hit.group(1), hit.group(2), hit.group(3)
            day_text, month_name = (first, second) if order == "dmy" else (second, first)
            month = _MONTHS.get(month_name[:3].lower())
            if month:
                found.append((hit.start(), int(day_text), month, year_text))
    if found:
        # A spelling that states its year is preferred over one that does not,
        # so text which already parsed still parses to exactly the same day.
        dated = [row for row in found if row[3]]
        _, day, month, year_text = min(dated or found, key=lambda row: row[0])
        if not year_text:
            return _nearest_occurrence(reference, month, day)
        year = int(year_text)
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day)
        except ValueError:
            return None

    # Enterprise interview emails often use the unambiguous Indian date form
    # (DD-MM-YYYY), for example "25-07-2026 10:30 IST".  Treat it as a date,
    # not as an AI-only signal, so a valid invitation is tracked immediately.
    numeric = re.search(r"\b([0-3]?\d)[/-]([01]?\d)[/-](\d{2,4})\b", text)
    if not numeric:
        return None
    day, month, year_text = numeric.groups()
    year = int(year_text)
    if year < 100:
        year += 2000
    try:
        return datetime(year, int(month), int(day))
    except ValueError:
        return None


def _clock(hour: str, minute: str | None, meridiem: str) -> tuple[int, int]:
    value = int(hour) % 12
    if meridiem.upper() == "PM":
        value += 12
    return value, int(minute or 0)


def _timezone_policy(sender_domain: str, text: str) -> tuple[str | None, str | None]:
    explicit = re.search(r"\b(IST|UTC|GMT)\b", text, re.I)
    if explicit:
        label = explicit.group(1).upper()
        return ({"IST": "Asia/Kolkata", "UTC": "UTC", "GMT": "UTC"}[label], "EXPLICIT")
    default = str(os.getenv("AI_INTERVIEW_DEFAULT_TIMEZONE") or "").strip()
    domains = {item.strip().lower() for item in str(os.getenv("AI_INTERVIEW_DEFAULT_TIMEZONE_DOMAINS") or "").split(",") if item.strip()}
    aligned = any(sender_domain == item or sender_domain.endswith("." + item) for item in domains)
    if default and aligned:
        try:
            return ZoneInfo(default).key, "DOMAIN_POLICY"
        except ZoneInfoNotFoundError:
            return None, None
    return None, None


def _plain_text_interview_result(decoded: dict[str, Any]) -> dict[str, Any] | None:
    if str(decoded.get("message_direction") or "INBOUND").upper() != "INBOUND":
        return None
    sender = _email(str(decoded.get("sender_email") or ""))
    recipient = _email(str(decoded.get("recipient_email") or ""))
    subject, body = str(decoded.get("subject") or ""), str(decoded.get("body") or "")
    text = f"{subject}\n{body}"
    # Gmail can deliver calendar invitations through an alias, forwarding rule,
    # or distribution address.  The connected mailbox is the authoritative
    # recipient; a mismatch in the visible To header must not discard an
    # authenticated invitation that actually arrived in that mailbox.
    if not sender or not recipient:
        return None
    if not _sender_authenticated(decoded, sender) or not _INTERVIEW_RE.search(text):
        return None
    invitation = re.search(r"\b(invite|invitation|please\s+join|interview\s+details|interview\s+will\s+be)\b", text, re.I)
    meeting_links = [url.rstrip(".,);>") for url in _URL_RE.findall(text) if any(
        host in url.lower() for host in ("teams.microsoft.com", "meet.google.com", "zoom.us")
    )]
    reference = decoded.get("sent_at") if isinstance(decoded.get("sent_at"), datetime) else datetime.now(timezone.utc)
    day = _plain_date(text, reference)
    time_match = re.search(r"\b(0?[1-9]|1[0-2])(?::([0-5]\d))?\s*(AM|PM)\b", text, re.I)
    time_24_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\s*(?:IST|UTC|GMT)\b", text, re.I)
    timezone_name, timezone_source = _timezone_policy(_domain(sender), text)
    if not (invitation and meeting_links and day and (time_match or time_24_match) and timezone_name):
        return None
    if time_match:
        hour, minute = _clock(*time_match.groups())
    else:
        hour, minute = (int(time_24_match.group(1)), int(time_24_match.group(2)))
    zone = ZoneInfo(timezone_name)
    start = day.replace(hour=hour, minute=minute, tzinfo=zone)
    schedule_state = "PAST" if start <= datetime.now(zone) else "FUTURE"
    company_name = "Tata Consultancy Services" if _domain(sender) == "tcs.com" else None
    evidence_text = f"{subject} | {start.strftime('%Y-%m-%d %I:%M %p')} {timezone_name}"
    return {
        "schema_version": "selection_offer_event_v1", "is_recruitment_related": True,
        "is_selection_or_offer_related": True, "should_create_review_record": True,
        "status": "INTERVIEW_CONFIRMED", "primary_status": "INTERVIEW_CONFIRMED",
        "classification": "interview_confirmed", "candidate_status": "Interview Confirmed",
        "confidence": 0.98, "ignore_reason": None,
        "reason": "Authenticated inbound interview invitation with explicit date, time and meeting link.",
        "candidate": {"name": None, "email": recipient},
        "company": {"name": company_name, "domain": _domain(sender)},
        "job": {"title": None, "employment_type": None, "location": None},
        "recruiter": {"name": decoded.get("sender_name"), "email": sender},
        "interview": {"date": start.date().isoformat(), "time": start.strftime("%I:%M %p"),
                      "timezone": timezone_name, "mode": "Online",
                      "round": _explicit_interview_round(subject, body),
                      "location": "Microsoft Teams" if "teams.microsoft.com" in meeting_links[0] else None,
                      "meeting_link": meeting_links[0]},
        "offer": {"offer_detected": False, "offer_letter_detected": False,
                  "appointment_letter_detected": False, "offer_date": None, "offered_ctc": None,
                  "currency": None, "joining_date": None, "offer_expiry_date": None},
        "attachments": [],
        "evidence": [{"source": "EMAIL_BODY", "meaning": "INTERVIEW_CONFIRMED", "text": evidence_text[:500]}],
        "risk_flags": [], "requires_manual_review": False,
        "summary": f"Trusted interview invitation from {_domain(sender)} for {evidence_text}.",
        "recommended_action": "Apply automatic interview booking safety checks.",
        "classification_source": "STRUCTURED_EMAIL_VERIFIED",
        "structured_validation_status": "TRUSTED", "timezone_source": timezone_source,
        "ai_validation_status": "NOT_REQUIRED", "ai_status": "NOT_REQUIRED",
        "validation_status": "VALIDATED", "email_intent": "INTERVIEW_INVITATION",
        "document_type": "NONE", "is_candidate_specific": True, "is_job_outcome": False,
        "is_current_event": schedule_state == "FUTURE", "is_questionnaire": False, "is_promotional_or_job_ad": False,
        "is_historical_information": False, "historical_employment_evidence": False,
        "lifecycle_event": "NONE", "interview_event": "INTERVIEW_CONFIRMED",
        "business_domain": "INTERVIEW_TRACKING", "evidence_summary": evidence_text[:1000],
        "schedule_state": schedule_state,
    }


def _is_employer_invitation(sender: str, recipient: str, invite: dict[str, Any]) -> bool:
    """An outside organisation inviting this candidate to a small meeting.

    This is what separates a recruiting invitation from everything else in a
    jobseeker's calendar once authentication has already been proved. Three
    things have to hold, and each excludes a specific false positive:

    * the sender is on a different domain from the candidate — an invitation
      from the candidate's own address is their own diary, not a round;
    * that domain is not a consumer mail provider — a friend's Gmail invite is
      not an employer;
    * the meeting is small — a webinar or careers open day is authenticated and
      external and still is not an interview.
    """
    sender_domain = _domain(sender)
    recipient_domain = _domain(recipient)
    if not sender_domain or sender_domain == recipient_domain:
        return False
    if sender_domain in _CONSUMER_MAIL_DOMAINS:
        return False
    attendees = invite.get("attendees") or []
    return 0 < len(attendees) <= _MAX_INTERVIEW_ATTENDEES


def _accepts_as_interview(
    decoded: dict[str, Any],
    invite: dict[str, Any],
    sender: str,
    recipient: str,
    subject: str,
) -> bool:
    """Whether a trusted calendar invitation counts as an interview.

    The keyword used to be mandatory, and that veto discarded a real booking:
    Sourcebae titled the event "Fullstack Ai || Pujitha", which is how
    recruiters normally title them — by the role, not by the ceremony. Every
    other signal had already passed, so the invitation was cryptographically
    authenticated, organizer-aligned, addressed to the monitored candidate and
    carried a valid start, and it was dropped because the title lacked a word.

    The keyword is still *sufficient* on its own, so nothing that worked before
    stops working. It is simply no longer *necessary* when the invitation is
    plainly an employer inviting this candidate.
    """
    if _INTERVIEW_RE.search(" ".join((subject, invite["summary"], invite["description"][:500]))):
        return True
    return _is_employer_invitation(sender, recipient, invite)


def trusted_interview_result(decoded: dict[str, Any], attachments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build a validated classifier result only for a trusted calendar invite."""
    cancellation = _plain_text_interview_cancellation_result(decoded)
    if cancellation:
        return cancellation
    calendars = [item for item in attachments if str(item.get("filename") or "").lower().endswith(".ics")]
    if not calendars:
        return _plain_text_interview_result(decoded)
    # Google Calendar attaches the invitation twice: once inline as
    # text/calendar and once as a named invite.ics. Two copies of one event are
    # not the ambiguous multi-event mail this guard exists for, so identical
    # invitations are collapsed before counting. A mail carrying genuinely
    # different events is still refused.
    distinct: dict[Any, dict[str, Any]] = {}
    for item in calendars:
        text = str(item.get("text") or "")
        parsed = parse_calendar(text)
        key = (
            (parsed["uid"], parsed["sequence"], parsed["method"])
            if parsed and parsed.get("uid")
            else ("unparsed", " ".join(text.split()))
        )
        distinct.setdefault(key, item)
    if len(distinct) != 1:
        return None
    invite = parse_calendar(str(next(iter(distinct.values())).get("text") or ""))
    if not invite or not invite["uid"] or invite["method"] not in {"REQUEST", "CANCEL"}:
        return None
    sender = _email(str(decoded.get("sender_email") or ""))
    recipient = _email(str(decoded.get("recipient_email") or ""))
    organizer = invite["organizer"]
    organizer_aligned = (
        organizer == sender
        or bool(organizer and _domain(organizer) == _domain(sender))
    )
    # Enterprise recruiting systems commonly send an invite through a shared
    # talent-acquisition mailbox while naming the individual recruiter as the
    # organizer. Exact-address equality is therefore too strict; authenticated
    # same-domain alignment remains required and cross-domain organizers fail.
    if not sender or not recipient or not organizer_aligned or recipient not in invite["attendees"]:
        return None
    if not _sender_authenticated(decoded, sender):
        return None
    subject = str(decoded.get("subject") or "")
    if not _accepts_as_interview(decoded, invite, sender, recipient, subject):
        return None

    cancelled = invite["method"] == "CANCEL" or invite["status"] == "CANCELLED"
    if not cancelled and (not invite["start"] or not invite["timezone"]):
        return None
    start = invite["start"]
    schedule_state = "PAST" if start and start <= datetime.now(start.tzinfo) else "FUTURE"
    status = "INTERVIEW_CANCELLED" if cancelled else "INTERVIEW_CONFIRMED"
    classification = "interview_cancelled" if cancelled else "interview_confirmed"
    candidate_status = "Interview Cancelled" if cancelled else "Interview Confirmed"
    local_start = start if start else None
    duration = (invite["end"] - start) if start and invite["end"] and invite["end"] > start else timedelta(minutes=30)
    mode = "Online" if invite["meeting_link"] else "In Person"
    evidence_text = f"{invite['summary']} — {local_start.isoformat() if local_start else 'cancelled'}"
    return {
        "schema_version": "selection_offer_event_v1", "is_recruitment_related": True,
        "is_selection_or_offer_related": True, "should_create_review_record": True,
        "status": status, "primary_status": status, "classification": classification,
        "candidate_status": candidate_status, "confidence": 0.99, "ignore_reason": None,
        "reason": "Authenticated RFC 5545 calendar invitation with matching organizer and candidate attendee.",
        # The calendar's own identity for this meeting. A recruiter sends the
        # covering note and the invitation as two separate mails describing one
        # interview, and Google attaches the invitation twice; the UID is what
        # ties all of those to a single event, and SEQUENCE is what tells a
        # reschedule apart from a resend.
        "calendar_uid": invite["uid"],
        "calendar_sequence": invite["sequence"],
        "candidate": {"name": None, "email": recipient},
        "company": {"name": None, "domain": _domain(sender)},
        "job": {"title": invite["summary"], "employment_type": None, "location": invite["location"] or None},
        "recruiter": {"name": decoded.get("sender_name"), "email": sender},
        "interview": {
            "date": local_start.date().isoformat() if local_start else None,
            "time": local_start.strftime("%I:%M %p") if local_start else None,
            "timezone": invite["timezone"], "mode": mode,
            "round": _explicit_interview_round(subject, invite["summary"], invite["description"]),
            "location": invite["location"] or None, "meeting_link": invite["meeting_link"],
            "duration_minutes": int(duration.total_seconds() // 60),
        },
        "offer": {"offer_detected": False, "offer_letter_detected": False,
                  "appointment_letter_detected": False, "offer_date": None,
                  "offered_ctc": None, "currency": None, "joining_date": None,
                  "offer_expiry_date": None},
        "attachments": [{"type": "INTERVIEW_INVITATION", "filename": calendars[0].get("filename") or "invite.ics", "confidence": 1.0}],
        "evidence": [{"source": "ATTACHMENT", "meaning": status, "text": evidence_text[:500]}],
        "risk_flags": [], "requires_manual_review": False,
        "summary": f"Trusted calendar invite: {invite['summary']}",
        "recommended_action": "Apply automatic interview booking safety checks.",
        "classification_source": "ICALENDAR_VERIFIED", "calendar_validation_status": "TRUSTED",
        "ai_validation_status": "NOT_REQUIRED", "ai_status": "NOT_REQUIRED",
        "validation_status": "VALIDATED",
        "email_intent": "INTERVIEW_CANCELLATION" if cancelled else "INTERVIEW_INVITATION",
        "document_type": "INTERVIEW_INVITATION_DOCUMENT", "is_candidate_specific": True,
        "is_job_outcome": False, "is_current_event": schedule_state == "FUTURE", "is_questionnaire": False,
        "is_promotional_or_job_ad": False, "is_historical_information": False,
        "historical_employment_evidence": False, "lifecycle_event": "NONE",
        "interview_event": status, "business_domain": "INTERVIEW_TRACKING",
        "evidence_summary": evidence_text[:1000], "schedule_state": schedule_state,
        "calendar": {
            "uid": invite["uid"], "sequence": invite["sequence"],
            "method": invite["method"], "summary": invite["summary"],
            "organizer": invite["organizer"],
            "attendee_count": len(invite["attendees"]),
            "has_dtend": bool(invite["end"]),
        },
    }
