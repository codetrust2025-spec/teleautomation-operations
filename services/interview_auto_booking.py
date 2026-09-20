"""Validated Ollama interview outcomes applied through the existing slot store."""
from __future__ import annotations

import logging
import os
import re
import uuid
from html import unescape
from io import BytesIO
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable
from threading import Lock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core import recruitment_mail_store as mail_store
from features import candidate_store
from services import booking_block_reasons, interview_timezones
from services import interview_lifecycle

logger = logging.getLogger("teleautomation.interview_auto_booking")

ACTIONABLE = {"interview_confirmed", "interview_rescheduled", "interview_cancelled"}
TIME_RE = re.compile(r"^(0?[1-9]|1[0-2]):([0-5]\d)\s*([AP]M)$", re.I)
_BOOKING_LOCK = Lock()
# Historical booking-audit rows may carry this outcome.  It remains a
# recognized deterministic validation code for those records, but it is not
# emitted for external interview commitments: two different lifecycle events
# are allowed to occupy the same time.
_LEGACY_SLOT_CONFLICT_CODE = "SLOT_CONFLICT"


@dataclass
class BookingValidationError(ValueError):
    code: str
    message: str
    payment_status: str = "NOT_CHECKED"
    duplicate_status: str = "NOT_CHECKED"
    conflict_status: str = "NOT_CHECKED"

    def __str__(self) -> str:
        return self.message


def _threshold(name: str, default: float) -> float:
    raw = float(os.getenv(name, str(default)))
    return max(0.0, min(1.0, raw / 100 if raw > 1 else raw))


def _confidence(result: dict[str, Any]) -> float:
    value = float(result.get("confidence") or 0)
    return value / 100 if value > 1 else value


def normalize_booking_round(result: dict[str, Any]) -> str:
    """Discard model/calendar labels unless they are supported round values."""
    # Keep the source result and its automation projection in sync.  The
    # booking wrapper adds top-level decision fields, but callers may retain
    # the nested interview object for subsequent event/alert persistence.
    interview = result.get("interview")
    if not isinstance(interview, dict):
        interview = {}
        result["interview"] = interview
    round_name = candidate_store.normalise_interview_round(interview.get("round"))
    interview["round"] = round_name or None
    return round_name


def parse_interview_time(value: str) -> str:
    """Require the AI contract's 12-hour clock and return candidate-store HH:MM."""
    match = TIME_RE.fullmatch(str(value or "").strip())
    if not match:
        raise BookingValidationError("INVALID_TIME", "Interview time must use 12-hour HH:MM AM/PM format.")
    hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3).upper()
    if meridiem == "AM" and hour == 12:
        hour = 0
    elif meridiem == "PM" and hour != 12:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def validate_timezone(value: str):
    """The zone the sender wrote, so the schedule can be converted to IST.

    Resolution lives in `services.interview_timezones`, shared with the mail
    agent so both ends of the pipeline agree on what a zone name means. It
    accepts IANA names, the abbreviations recruiters actually write (UTC, GMT,
    IST, and the US set), and numeric offsets like +05:30. Anything it cannot
    resolve still raises, because a guessed zone books the wrong hour.
    """
    name = str(value or "").strip()
    if not name:
        raise BookingValidationError("MISSING_TIMEZONE", "Interview timezone is required for automatic booking.")
    zone = interview_timezones.resolve(name)
    if zone is None:
        raise BookingValidationError(
            "INVALID_TIMEZONE", "Interview timezone is not a recognisable timezone.",
        )
    return zone


# An invitation that reaches us minutes before the interview is still worth
# booking, and so is a meeting link sent once the call has started: the queue,
# not the sender, is what made it late. Two production invites were refused as
# past with nothing wrong with them -- a screening invite that arrived at
# 5:29 PM for a 5:30 PM interview and finished processing at 5:32, and a Teams
# link sent nine minutes into a 4:00 PM call -- and neither interview ever
# reached Daily Ops.
#
# The mail must have been sent while the interview was still ahead or barely
# underway, and the booking is only filled in for a few hours afterwards, so a
# backlog of old mail cannot write bookings across past weeks.
LATE_INVITE_GRACE = timedelta(minutes=30)
LATE_BOOKING_WINDOW = timedelta(hours=6)


def _still_bookable_after_it_started(
    start: datetime, current: datetime, arrived_at: datetime | None,
) -> bool:
    if arrived_at is None:
        return False
    return arrived_at <= start + LATE_INVITE_GRACE and current <= start + LATE_BOOKING_WINDOW


def normalized_schedule(
    result: dict[str, Any], *, now: datetime | None = None, arrived_at: datetime | None = None,
) -> dict[str, str]:
    interview = result.get("interview") or {}
    try:
        day = date.fromisoformat(str(interview.get("date") or ""))
    except ValueError as exc:
        raise BookingValidationError("INVALID_DATE", "Interview date is missing or is not ISO YYYY-MM-DD.") from exc
    source_time = parse_interview_time(str(interview.get("time") or ""))
    source_zone = validate_timezone(str(interview.get("timezone") or ""))
    source_dt = datetime.combine(day, datetime.strptime(source_time, "%H:%M").time(), source_zone)
    current = now or datetime.now(source_zone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=source_zone)
    if (source_dt <= current.astimezone(source_zone)
            and not _still_bookable_after_it_started(
                source_dt, current.astimezone(source_zone), arrived_at)):
        raise BookingValidationError("PAST_INTERVIEW", "Interview date and time must be in the future.")
    local = source_dt.astimezone(ZoneInfo("Asia/Kolkata"))
    # Preserve the schedule supplied by the invitation. Trusted RFC 5545
    # calendar invitations expose ``duration_minutes`` and model results may
    # expose an explicit ``end_time``. Previously both were discarded and a
    # fixed 30-minute slot was manufactured.
    raw_end_time = str(interview.get("end_time") or "").strip()
    raw_duration = interview.get("duration_minutes")
    if raw_end_time:
        end_clock = parse_interview_time(raw_end_time)
        source_end = datetime.combine(
            day, datetime.strptime(end_clock, "%H:%M").time(), source_zone,
        )
        if source_end <= source_dt:
            raise BookingValidationError(
                "INVALID_END_TIME",
                "Interview end time must be later than the start time.",
            )
    elif raw_duration not in (None, ""):
        try:
            duration_minutes = int(raw_duration)
        except (TypeError, ValueError) as exc:
            raise BookingValidationError(
                "INVALID_DURATION", "Interview duration must be a whole number of minutes.",
            ) from exc
        if not 5 <= duration_minutes <= 12 * 60:
            raise BookingValidationError(
                "INVALID_DURATION", "Interview duration must be between 5 and 720 minutes.",
            )
        source_end = source_dt + timedelta(minutes=duration_minutes)
    else:
        default_minutes = max(
            15, int(os.getenv("AI_INTERVIEW_DEFAULT_DURATION_MINUTES", "30")),
        )
        source_end = source_dt + timedelta(minutes=default_minutes)

    end = source_end.astimezone(ZoneInfo("Asia/Kolkata"))
    if end.date() != local.date():
        raise BookingValidationError(
            "CROSS_DAY_INTERVIEW",
            "Interview start and end must fall on the same India calendar date.",
        )
    return {
        "date": local.date().isoformat(), "time": local.strftime("%H:%M"),
        "time_end": end.strftime("%H:%M"),
        # Booking values are canonical IST; the zone the sender wrote is kept
        # beside them so an audit can still see what was converted from.
        "timezone": interview_timezones.OPERATIONS_TIMEZONE,
        "source_timezone": interview_timezones.label(source_zone),
    }


def historical_booking_disposition(
    result: dict[str, Any], classification: str, *, now: datetime | None = None,
) -> dict[str, str] | None:
    """Classify delayed historical recovery before live-booking validation.

    A historical rescan must never turn a past interview into a live booking
    failure. Incomplete historical schedules remain reviewable, while a
    complete future schedule continues through the normal safety gates.
    """
    if not result.get("_historical_reprocess") or classification not in {
        "interview_confirmed", "interview_rescheduled",
    }:
        return None
    interview = result.get("interview") or {}
    raw_day = str(interview.get("date") or "").strip()
    if not raw_day:
        return {
            "status": "Review Required",
            "validation_status": "REVIEW_REQUIRED",
            "failure_code": "HISTORICAL_SCHEDULE_INCOMPLETE",
            "message": "Historical interview email has no reliable date; review only and do not auto-book.",
            "display_status": "Historical Interview — Review Only",
            "priority": "review_required",
            "event_type": "historical_interview_review_required",
        }
    try:
        interview_day = date.fromisoformat(raw_day)
    except ValueError:
        return {
            "status": "Review Required",
            "validation_status": "REVIEW_REQUIRED",
            "failure_code": "HISTORICAL_SCHEDULE_INCOMPLETE",
            "message": "Historical interview email has an invalid date; review only and do not auto-book.",
            "display_status": "Historical Interview — Review Only",
            "priority": "review_required",
            "event_type": "historical_interview_review_required",
        }
    timezone_name = str(interview.get("timezone") or "").strip()
    try:
        zone = validate_timezone(timezone_name) if timezone_name else interview_timezones.operations_zone()
    except BookingValidationError:
        zone = ZoneInfo("Asia/Kolkata")
    current = now or datetime.now(zone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=zone)
    past = interview_day < current.astimezone(zone).date()
    if interview_day == current.astimezone(zone).date():
        raw_time = str(interview.get("time") or "").strip()
        if not raw_time or not timezone_name:
            return {
                "status": "Review Required",
                "validation_status": "REVIEW_REQUIRED",
                "failure_code": "HISTORICAL_SCHEDULE_INCOMPLETE",
                "message": "Today's historical interview email lacks a complete time or timezone; review only.",
                "display_status": "Historical Interview — Review Only",
                "priority": "review_required",
                "event_type": "historical_interview_review_required",
            }
        try:
            source_time = parse_interview_time(raw_time)
            source_zone = validate_timezone(timezone_name)
            scheduled = datetime.combine(
                interview_day, datetime.strptime(source_time, "%H:%M").time(), source_zone,
            )
            past = scheduled <= current.astimezone(source_zone)
        except BookingValidationError:
            return {
                "status": "Review Required",
                "validation_status": "REVIEW_REQUIRED",
                "failure_code": "HISTORICAL_SCHEDULE_INCOMPLETE",
                "message": "Historical interview email has an invalid time or timezone; review only.",
                "display_status": "Historical Interview — Review Only",
                "priority": "review_required",
                "event_type": "historical_interview_review_required",
            }
    if not past:
        return None
    return {
        "status": "Historical Skipped",
        "validation_status": "SKIPPED",
        "failure_code": "PAST_INTERVIEW",
        "message": "Historical interview date has already passed; no slot was created.",
        "display_status": "Historical Interview Skipped",
        "priority": "low",
        "event_type": "historical_interview_skipped",
    }


def should_suppress_historical_notification(
    result: dict[str, Any], classification: str, *, now: datetime | None = None,
) -> bool:
    """Return whether a historical interview has no remaining admin action.

    The booking audit is still recorded, but a past interview must not create
    an unread notification, browser sound, or push notification.
    """
    disposition = historical_booking_disposition(
        result, classification, now=now,
    )
    return bool(disposition and disposition.get("validation_status") == "SKIPPED")


def validate_ai_for_booking(result: dict[str, Any], classification: str) -> None:
    if os.getenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "false").lower() != "true":
        raise BookingValidationError("AUTO_BOOKING_DISABLED", "Automatic interview booking is disabled by configuration.")
    source = result.get("classification_source")
    ollama_valid = source == "OLLAMA" and result.get("ai_validation_status") == "VALIDATED"
    calendar_valid = (
        source == "ICALENDAR_VERIFIED"
        and result.get("calendar_validation_status") == "TRUSTED"
        and result.get("validation_status") == "VALIDATED"
    )
    structured_valid = (
        source == "STRUCTURED_EMAIL_VERIFIED"
        and result.get("structured_validation_status") == "TRUSTED"
        and result.get("validation_status") == "VALIDATED"
    )
    if not (ollama_valid or calendar_valid or structured_valid):
        raise BookingValidationError("AI_NOT_VALIDATED", "A validated Ollama result or trusted authenticated calendar invitation is required for automatic booking.")
    confidence = _confidence(result)
    review = _threshold("AI_INTERVIEW_REVIEW_THRESHOLD", 0.80)
    automatic = _threshold("AI_INTERVIEW_AUTO_BOOK_THRESHOLD", 0.90)
    if confidence < review:
        raise BookingValidationError("LOW_CONFIDENCE", "AI confidence is below the review threshold.")
    interview = result.get("interview") or {}
    required = all(str(interview.get(key) or "").strip() for key in ("date", "time", "timezone"))
    if confidence < automatic and not required:
        raise BookingValidationError("MEDIUM_CONFIDENCE_INCOMPLETE", "Medium-confidence booking requires explicit date, time, and timezone.")
    # The detection layer owns the final automation decision. The booking layer
    # consumes it rather than re-litigating classifier logic, while retaining
    # its own source, schedule, candidate, payment and lifecycle invariants.
    final_decision = str(
        result.get("automation_decision") or result.get("booking_decision") or ""
    ).strip().upper()
    if final_decision in {"AUTO_IGNORE", "AI_RETRY_PENDING"}:
        raise BookingValidationError(
            "NOT_ACTIONABLE", "The final automation decision does not authorize booking."
        )
    if final_decision not in {"", "AUTO_BOOK"}:
        raise BookingValidationError(
            "NOT_ACTIONABLE", "The final automation decision is not recognized for booking."
        )
    # The model's own request for review is not a veto, with or without an
    # explicit decision. It described the model's confidence in its reading
    # rather than the evidence: a Karat interview reminder carried it at
    # confidence 1.0 with a full schedule, and a Teams invite naming the
    # candidate as an attendee was refused on it too, on the day of the
    # interview.
    #
    # Everything that protects a booking by reading evidence is still here and
    # still ahead of this point -- a validated Ollama result or a trusted
    # authenticated invitation, confidence over the review threshold, an
    # explicit date, time and timezone for a medium-confidence booking, and an
    # actionable classification. Payment, duplicate, conflict and lifecycle all
    # run after it.
    if classification not in ACTIONABLE:
        raise BookingValidationError("NOT_ACTIONABLE", "This interview classification does not change a booking.")


def validate_manual_approval_for_booking(result: dict[str, Any], classification: str) -> None:
    """Allow an administrator decision to replace AI validation, not safety checks."""
    if classification not in ACTIONABLE:
        raise BookingValidationError("NOT_ACTIONABLE", "This interview classification does not change a booking.")
    evidence = result.get("evidence") or []
    if not evidence:
        raise BookingValidationError("MISSING_EVIDENCE", "Source-email evidence is required for manual booking approval.")
    if classification != "interview_cancelled":
        interview = result.get("interview") or {}
        if not all(str(interview.get(key) or "").strip() for key in ("date", "time", "timezone")):
            raise BookingValidationError(
                "INCOMPLETE_SCHEDULE",
                "Date, time, and timezone are required for an approved interview booking.",
            )


def _payment_check(candidate: dict[str, Any], schedule: dict[str, str] | None) -> None:
    preview = dict(candidate)
    preview["slots_group_posted"] = True
    if schedule:
        preview["date"] = schedule["date"]
    reason = candidate_store.slot_confirm_block_reason(preview)
    if reason:
        raise BookingValidationError("PAYMENT_VALIDATION_FAILED", reason, payment_status="BLOCKED")


def _candidate_slots(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Every booking row this person owns, not the one the list happens to show.

    `list_candidates` collapses a person's rows to a single row for display --
    newest by `updated_at` -- and this read that collapsed output. So the
    lifecycle saw one booking per candidate however many they held. Measured on
    production: 117 confirmed bookings were invisible to it, twenty of
    Gangadhar's and twenty of Yamini Akhil's among them, and for Pujitha the
    row it did see was an unconfirmed one while four confirmed bookings were
    hidden behind it.

    That is what let her Persistent Systems interview book twice. The duplicate
    check could not see the row the Google invitation had already written, so
    the reminder booked the same 02:30-04:00 slot again; and the cancellation
    could only release whichever row the collapse happened to show, so clearing
    one interview took two passes.

    Identity resolution is not involved and was never at fault: every one of
    those rows already resolved to the same identity set. What changes here is
    only which rows are handed to it.

    `_with_computed` is applied exactly as `list_candidates` applies it, so the
    rows are identical to what this function returned before -- the collapse
    and the display filters are what is dropped, and nothing else.
    """
    identity_ids = set(candidate_store.candidate_identity_ids(str(candidate["id"]), include_name_matches=False))
    stored = candidate_store._load().get("candidates") or []
    return [
        row for row in (candidate_store._with_computed(item) for item in stored)
        if str(row.get("id")) in identity_ids
    ]


def _confirmed_slots(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in _candidate_slots(candidate) if row.get("slot_confirmed") and row.get("date")]


def _same_text(left: Any, right: Any) -> bool:
    return bool(str(left or "").strip()) and str(left or "").strip().casefold() == str(right or "").strip().casefold()


_STABLE_INTERVIEW_ID_RE = re.compile(r"\b(?=[A-Z0-9]*\d)[A-Z][A-Z0-9]{7,}\b")


def _stable_interview_ids(*values: Any) -> set[str]:
    """Extract enterprise requisition/event IDs used across invite updates."""
    text = " ".join(str(value or "") for value in values).upper()
    return set(_STABLE_INTERVIEW_ID_RE.findall(text))


def _reference_schedule(result: dict[str, Any], classification: str) -> dict[str, str] | None:
    """Normalize an explicitly stated old/cancelled schedule without requiring it to be future."""
    interview = result.get("interview") or {}
    prefix = "original_" if classification == "interview_rescheduled" else ""
    day = str(interview.get(f"{prefix}date") or "").strip()
    raw_time = str(interview.get(f"{prefix}time") or "").strip()
    timezone_name = str(interview.get(f"{prefix}timezone") or "").strip()
    if not (day and raw_time and timezone_name):
        return None
    try:
        source_day = date.fromisoformat(day)
        source_time = parse_interview_time(raw_time)
        source_zone = validate_timezone(timezone_name)
    except (ValueError, BookingValidationError):
        return None
    source_dt = datetime.combine(source_day, datetime.strptime(source_time, "%H:%M").time(), source_zone)
    local = source_dt.astimezone(ZoneInfo("Asia/Kolkata"))
    return {"date": local.date().isoformat(), "time": local.strftime("%H:%M")}


# "Looks like you missed your interview ... we can help you get it rescheduled
# quickly." A mail like this may release the booking it is about -- a missed
# interview must not stay confirmed, which is why the source parser reads it as
# a cancellation at all -- but it is about an interview that has already
# happened. One was read on 11 Sep for an interview missed on 3 Sep, and every
# booking that candidate held by then was a later one, any of which a single
# match would have cancelled.
_MISSED_INTERVIEW = re.compile(
    r"missed (?:your|the|his|her|their|this|an?) interview"
    r"|missed interview"
    r"|did\s?n[o’']?t (?:attend|join|show up)"
    r"|no[\s-]show"
    r"|(?:were|was) not able to attend",
    re.IGNORECASE,
)


def _reads_as_missed_interview(message: dict[str, Any]) -> bool:
    text = " ".join(str(message.get(key) or "") for key in ("subject", "body"))
    return bool(_MISSED_INTERVIEW.search(text))


def _started_before(row: dict[str, Any], moment: datetime) -> bool:
    """Whether a booking had already started then. An unreadable time has not."""
    try:
        day = date.fromisoformat(str(row.get("date") or "")[:10])
        clock = datetime.strptime(str(row.get("time") or "")[:5], "%H:%M").time()
    except ValueError:
        return False
    return datetime.combine(day, clock, ZoneInfo("Asia/Kolkata")) <= moment


def _resolve_existing_slot(
    slots: list[dict[str, Any]], *, result: dict[str, Any], message: dict[str, Any], classification: str,
) -> dict[str, Any]:
    """Resolve one slot using stable interview identity; never pick an arbitrary recent row."""
    if slots and classification == "interview_cancelled" and _reads_as_missed_interview(message):
        arrived = interview_lifecycle._sent_at(message.get("sent_at")) or datetime.now(ZoneInfo("Asia/Kolkata"))
        already_started = [row for row in slots if _started_before(row, arrived)]
        if not already_started:
            raise BookingValidationError(
                "BOOKING_NOT_FOUND",
                "This mail reports a missed interview, and none of this candidate's "
                "bookings had started when it arrived.",
            )
        slots = already_started
    if not slots:
        raise BookingValidationError("BOOKING_NOT_FOUND", "No active interview booking was found.")
    calendar_uid = _calendar_uid(result)
    if len(slots) == 1:
        # A lone slot used to be accepted unconditionally, which let an
        # organiser's CANCEL for one event land on an unrelated booking whenever
        # the real target had already left the confirmed list. Two calendar UIDs
        # that disagree are different meetings by definition, so refuse rather
        # than mutate the only row that happens to be standing.
        only_uid = str(slots[0].get("interview_calendar_uid") or "").strip()
        if calendar_uid and only_uid and not _same_text(only_uid, calendar_uid):
            raise BookingValidationError(
                "BOOKING_NOT_FOUND",
                "The only active interview booking belongs to a different calendar event.",
            )
        _guard_existing_slot_order(slots[0], result=result, message=message)
        return slots[0]
    interview = result.get("interview") or {}
    company = (result.get("company") or {}).get("name")
    role = (result.get("job") or {}).get("title")
    round_name = interview.get("round")
    thread_id = message.get("provider_thread_id")
    reference = _reference_schedule(result, classification)
    message_ids = _stable_interview_ids(
        message.get("subject"),
        message.get("body"),
        (result.get("job") or {}).get("title"),
        *((result.get("interview") or {}).get("stable_ids") or []),
    )
    ranked: list[tuple[int, dict[str, Any]]] = []
    for row in slots:
        score = 0
        if calendar_uid and _same_text(row.get("interview_calendar_uid"), calendar_uid):
            # The organiser's own identifier for the event. Nothing else here
            # comes close: threads fork and subjects repeat, but a UID match is
            # the same meeting by definition.
            score += 500
        if thread_id and _same_text(row.get("interview_source_thread_id"), thread_id):
            score += 100
        row_ids = _stable_interview_ids(
            row.get("interview_role"),
            row.get("notes"),
            row.get("interview_source_message_id"),
        )
        if message_ids.intersection(row_ids):
            # Enterprise calendar systems often start a new Gmail thread for a
            # cancellation.  A shared requisition/event ID is more stable than
            # Gmail's thread id and safely identifies the original booking.
            score += 120
        if reference and str(row.get("date") or "")[:10] == reference["date"] and str(row.get("time") or "")[:5] == reference["time"]:
            score += 80
        if round_name and _same_text(candidate_store.normalise_interview_round(row.get("interview_round")), candidate_store.normalise_interview_round(round_name)):
            score += 30
        if company and _same_text(row.get("interview_company"), company):
            score += 20
        if role and _same_text(row.get("interview_role"), role):
            score += 15
        ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    # Nothing matching and two things matching equally are different failures,
    # and they were reported as the same one. Every stored "more than one
    # booking" alert was the first kind: the interview being cancelled had no
    # booking, so the alert sent a person looking for one among the others.
    if ranked[0][0] <= 0:
        raise BookingValidationError(
            "BOOKING_NOT_FOUND",
            "None of this candidate's active interview bookings matches this email.",
        )
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        raise BookingValidationError(
            "BOOKING_AMBIGUOUS",
            "Multiple active interview slots match this candidate; the source email does not identify one safely.",
        )
    _guard_existing_slot_order(ranked[0][1], result=result, message=message)
    return ranked[0][1]


def _guard_existing_slot_order(row, *, result, message):
    """A no-UID historical cancellation cannot target a newer booked source.

    The lifecycle key of a no-ICS mail differs from its calendar sibling. The
    target's persisted source is therefore an additional precedence barrier.
    Matching calendar revisions continue to use UID/SEQUENCE, not receipt time.
    """
    incoming_uid = _calendar_uid(result)
    stored_uid = str(row.get('interview_calendar_uid') or '')
    if incoming_uid and stored_uid:
        if not _same_text(incoming_uid, stored_uid):
            raise BookingValidationError('BOOKING_NOT_FOUND', 'Calendar event identity differs from the target booking.')
        return
    source = mail_store.booking_source_message(str(row.get('interview_source_message_id') or ''))
    before = interview_lifecycle._sent_at((source or {}).get('sent_at'))
    incoming = interview_lifecycle._sent_at(message.get('sent_at'))
    if before and (incoming is None or incoming < before):
        raise BookingValidationError('STALE_INTERVIEW_EVENT', 'This transition predates the source of the target booking.')


def _booking_metadata(result: dict[str, Any], message: dict[str, Any], schedule: dict[str, str] | None) -> dict[str, str]:
    return {
        "interview_company": str((result.get("company") or {}).get("name") or ""),
        "interview_role": str((result.get("job") or {}).get("title") or ""),
        "interview_source_thread_id": str(message.get("provider_thread_id") or ""),
        "interview_source_message_id": str(message.get("provider_message_id") or ""),
        "interview_source_timezone": str((schedule or {}).get("source_timezone") or ""),
        # The calendar event this slot came from. An organiser who moves the
        # meeting sends the same UID with a higher SEQUENCE, and without these
        # the revision looks like an unrelated second interview.
        "interview_calendar_uid": _calendar_uid(result),
        "interview_calendar_sequence": str(_calendar_sequence(result)),
    }


def _calendar_uid(result: dict[str, Any]) -> str:
    return str((result.get("calendar") or {}).get("uid") or "").strip()


def _calendar_sequence(result: dict[str, Any]) -> int:
    try:
        return max(0, int((result.get("calendar") or {}).get("sequence") or 0))
    except (TypeError, ValueError):
        return 0


def _calendar_revision_of(slots: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any] | None:
    """The booking this invite supersedes, if it is a revision of one.

    RFC 5545 identifies an event by UID; SEQUENCE increments each time the
    organiser changes it. A higher sequence for a UID we already booked is the
    same interview moved, so it must update that booking rather than compete
    with it for a slot.
    """
    uid = _calendar_uid(result)
    if not uid:
        return None
    incoming = _calendar_sequence(result)
    for row in slots:
        if not _same_text(row.get("interview_calendar_uid"), uid):
            continue
        try:
            booked = int(row.get("interview_calendar_sequence") or 0)
        except (TypeError, ValueError):
            booked = 0
        if incoming >= booked:
            return row
    return None


def _same_lifecycle_slot(
    row: dict[str, Any], *, result: dict[str, Any], message: dict[str, Any], schedule: dict[str, str],
) -> bool:
    """Whether a confirmed row is the same logical interview as this event.

    Interview commitments are deliberately allowed to overlap.  A date/time
    comparison alone therefore cannot be a duplicate guard: it would reject a
    second, genuinely different invite for the candidate.  Calendar UID is
    authoritative when both sources carry one. Otherwise require message or
    source-event identity, never just company, role, or equal times. Candidate
    identity is scoped by the caller; SEQUENCE precedence is handled by the
    lifecycle guard before this duplicate check.
    """
    uid = _calendar_uid(result)
    stored_uid = str(row.get("interview_calendar_uid") or "").strip()
    if uid and stored_uid:
        return _same_text(stored_uid, uid)
    source_id = str(message.get("provider_message_id") or "").strip()
    if source_id and _same_text(row.get("interview_source_message_id"), source_id):
        return True
    same_schedule = (
        str(row.get("date") or "")[:10] == schedule["date"]
        and str(row.get("time") or "")[:5] == schedule["time"]
        and str(row.get("time_end") or "")[:5] == schedule["time_end"]
    )
    thread_id = str(message.get("provider_thread_id") or "").strip()
    if thread_id and _same_text(row.get("interview_source_thread_id"), thread_id) and same_schedule:
        return True
    if same_schedule:
        incoming_meetings = _source_teams_meetings(message)
        if incoming_meetings:
            source = mail_store.booking_source_message(str(row.get("interview_source_message_id") or ""))
            if (source and _same_text(source.get("subject"), message.get("subject"))
                    and incoming_meetings.intersection(_source_teams_meetings(source))):
                return True
    # Identical times, titles, or public job links cannot establish that two
    # independent messages describe the same interview.
    return False


def _source_teams_meetings(message):
    """Exact Teams meeting identities in raw MIME alternatives, not AI fields."""
    from html import unescape
    from urllib.parse import unquote, urlsplit
    text = unescape(' '.join(str(message.get(k) or '') for k in (
        'body', 'html_body', 'body_text', 'html_body_text')))
    found = set()
    for url in re.findall(r'''https?://[^\s<>"']+''', text):
        parsed = urlsplit(unquote(url))
        if parsed.hostname not in {'teams.microsoft.com', 'teams.live.com'}:
            continue
        # Help links, Teams home pages, and reusable generic URLs prove nothing.
        path = parsed.path.rstrip('/')
        if re.fullmatch(r'/l/meetup-join/19:meeting_[^/]+/0', path):
            found.add((parsed.hostname, path))
    return found


def _recover_pending_lifecycle_slot(
    slots: list[dict[str, Any]], *, result: dict[str, Any], message: dict[str, Any], schedule: dict[str, str],
) -> dict[str, Any] | None:
    """Find the already-written slot after a crash before audit/alert write.

    This is deliberately stricter than ordinary duplicate detection: recovery
    requires the logical event identity plus the exact persisted schedule, so
    a retry cannot adopt another interview merely because it shares a time.
    """
    uid = _calendar_uid(result)
    source_id = str(message.get("provider_message_id") or "").strip()
    for row in slots:
        identity_matches = (
            bool(uid) and _same_text(row.get("interview_calendar_uid"), uid)
        ) or (
            not uid and bool(source_id) and _same_text(row.get("interview_source_message_id"), source_id)
        )
        if not identity_matches:
            continue
        if (
            str(row.get("date") or "")[:10] == schedule["date"]
            and str(row.get("time") or "")[:5] == schedule["time"]
            and str(row.get("time_end") or "")[:5] == schedule["time_end"]
        ):
            return row
    return None


def _evidence_font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    names = (
        ("DejaVuSans-Bold.ttf", "Arial Bold.ttf")
        if bold
        else ("DejaVuSans.ttf", "Arial.ttf")
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _evidence_lines(value: Any, *, width: int = 82, limit: int = 28) -> list[str]:
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ["Source email body was empty; booking fields came from validated structured evidence."]
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        extra = len(word) + (1 if current else 0)
        if current and current_len + extra > width:
            lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += extra
        if len(lines) >= limit:
            break
    if current and len(lines) < limit:
        lines.append(" ".join(current))
    if len(words) > sum(len(line.split()) for line in lines):
        lines[-1] = f"{lines[-1]} ..."
    return lines


def render_booking_evidence_png(
    *, candidate: dict[str, Any], message: dict[str, Any], result: dict[str, Any],
    booking: dict[str, Any], booking_status: str,
) -> bytes:
    """Render a safe, self-contained email evidence image for Daily Ops."""
    from PIL import Image, ImageDraw

    interview = result.get("interview") or {}
    company = (result.get("company") or {}).get("name") or booking.get("interview_company")
    role = (result.get("job") or {}).get("title") or booking.get("interview_role")
    body_lines = _evidence_lines(message.get("body") or message.get("html_body"))
    detail_rows = [
        ("Candidate", candidate.get("name") or result.get("candidate", {}).get("name") or "Unknown"),
        ("Interview", f"{booking.get('date') or interview.get('date') or 'Unknown date'}  {booking.get('time') or interview.get('time') or 'Unknown time'}  Asia/Kolkata"),
        ("Company / role", f"{company or 'Unknown company'} / {role or 'Unknown role'}"),
        ("Round", interview.get("round") or booking.get("interview_round") or "Not specified"),
        ("Email subject", message.get("subject") or "No subject"),
        ("From", f"{message.get('sender_name') or ''} <{message.get('sender_email') or 'Unknown'}>"),
        ("To", message.get("recipient_email") or "Unknown"),
        ("Email received", message.get("sent_at") or "Unknown"),
        ("Gmail message ID", message.get("provider_message_id") or "Unknown"),
        ("Booking ID", booking.get("id") or "Unknown"),
    ]
    width = 1200
    height = max(980, 315 + len(detail_rows) * 43 + len(body_lines) * 30)
    image = Image.new("RGB", (width, height), "#071523")
    draw = ImageDraw.Draw(image)
    title_font = _evidence_font(38, bold=True)
    heading_font = _evidence_font(24, bold=True)
    label_font = _evidence_font(19, bold=True)
    body_font = _evidence_font(19)
    small_font = _evidence_font(16)

    draw.rounded_rectangle((38, 34, width - 38, 190), radius=22, fill="#10243b", outline="#2f80ed", width=3)
    draw.text((70, 62), "AUTOMATIC INTERVIEW BOOKING EVIDENCE", font=title_font, fill="#f8fbff")
    draw.text((72, 117), booking_status, font=heading_font, fill="#34d399")
    draw.text((72, 154), "Generated from the validated source email by AI Mail Monitoring", font=small_font, fill="#91b7df")

    y = 230
    for label, value in detail_rows:
        draw.text((70, y), f"{label}:", font=label_font, fill="#60a5fa")
        draw.text((285, y), str(value or "Unknown")[:105], font=body_font, fill="#eef6ff")
        y += 43
    y += 12
    draw.line((70, y, width - 70, y), fill="#29415f", width=2)
    y += 28
    draw.text((70, y), "SOURCE EMAIL CONTENT", font=heading_font, fill="#f8fbff")
    y += 46
    for line in body_lines:
        draw.text((70, y), line, font=body_font, fill="#d7e6f7")
        y += 30
    draw.text((70, height - 54), "Audit evidence only - verify changes against the complete source email.", font=small_font, fill="#7890ad")

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _attach_automatic_booking_evidence(
    *, candidate: dict[str, Any], message: dict[str, Any], result: dict[str, Any],
    booking: dict[str, Any], booking_status: str,
) -> dict[str, Any] | None:
    """Attach evidence without allowing rendering/storage failure to undo a booking."""
    booking_id = str(booking.get("id") or "")
    if not booking_id:
        return None
    try:
        image = render_booking_evidence_png(
            candidate=candidate, message=message, result=result,
            booking=booking, booking_status=booking_status,
        )
        proof = candidate_store.attach_public_slot_screenshot(
            booking_id,
            data=image,
            original_name=f"auto-booking-evidence-{booking_id}.png",
            mime_type="image/png",
            source="AI Mail Monitoring",
        )
        if proof:
            mail_store.audit(
                actor="system", role="system", action="AUTO_BOOKING_EVIDENCE_ATTACHED",
                candidate_id=str(candidate.get("id") or ""), source_id=booking_id,
                new={"proof_id": proof.get("id"), "gmail_message_id": message.get("provider_message_id")},
            )
        return proof
    except Exception as exc:
        logger.warning(
            "Automatic booking evidence snapshot failed booking_id=%s code=%s",
            booking_id, type(exc).__name__,
        )
        try:
            mail_store.audit(
                actor="system", role="system", action="AUTO_BOOKING_EVIDENCE_FAILED",
                candidate_id=str(candidate.get("id") or ""), source_id=booking_id,
                new={"error_code": type(exc).__name__, "gmail_message_id": message.get("provider_message_id")},
            )
        except Exception:
            logger.debug("Unable to persist evidence failure audit", exc_info=True)
        return None


def execute_auto_booking(
    *, mailbox: dict[str, Any], message: dict[str, Any], event: dict[str, Any],
    result: dict[str, Any], correlation_id: str | None = None,
) -> dict[str, Any]:
    """Serialize validation plus mutation so concurrent mailbox jobs cannot race."""
    # This boundary consumes Claude's final automation decision.  It does not
    # infer relevance itself; booking only applies deterministic validation and
    # lifecycle-safe persistence to the decision it receives.
    from services import recruitment_automation

    # A mailbox can still carry a historical duplicate candidate row.  Booking
    # state must always be created under the canonical person identity; the
    # original mailbox/event ids remain the audit provenance.
    booking_mailbox = dict(mailbox)
    raw_candidate_id = str(mailbox.get("candidate_id") or "")
    try:
        canonical_candidate_id = mail_store.canonical_candidate_id(raw_candidate_id)
    except Exception as exc:
        raise BookingValidationError(
            "CANDIDATE_MAPPING_FAILED", "The mailbox candidate identity could not be resolved."
        ) from exc
    if not canonical_candidate_id:
        raise BookingValidationError(
            "CANDIDATE_MAPPING_FAILED", "The mailbox candidate identity could not be resolved."
        )
    booking_mailbox["candidate_id"] = canonical_candidate_id
    automated_result = recruitment_automation.apply_decision(result)
    decision = recruitment_automation.decision_for(automated_result)
    _record_automation_projection(
        event=event, state=decision.value, result=automated_result,
        reason="FINAL_AI_DECISION",
    )
    with _BOOKING_LOCK:
        with mail_store.candidate_booking_lock(canonical_candidate_id):
            outcome = _execute_auto_booking(
                mailbox=booking_mailbox, message=message, event=event, result=automated_result,
                correlation_id=correlation_id,
            )
    # A final non-booking decision is authoritative.  It can deliberately
    # enter the deterministic executor only to leave a normal booking audit;
    # its expected NOT_ACTIONABLE outcome must never turn an AI retry into an
    # ignore state.
    final_state = (
        decision
        if decision in {
            recruitment_automation.AutomationState.AUTO_IGNORE,
            recruitment_automation.AutomationState.AI_RETRY_PENDING,
        }
        else recruitment_automation.outcome_for(outcome)
    )
    _record_automation_projection(
        event=event, state=final_state.value, result=automated_result,
        reason=str(outcome.get("failure_code") or outcome.get("status") or "AUTOMATION_COMPLETE"),
        booking_id=str((outcome.get("booking") or {}).get("id") or "") or None,
        details={"event_type": outcome.get("event_type"), "booking_status": outcome.get("status")},
    )
    outcome["automation_state"] = final_state.value
    return outcome


def execute_manual_approved_booking(
    *, mailbox: dict[str, Any], message: dict[str, Any], event: dict[str, Any],
    result: dict[str, Any], reviewer: str, correlation_id: str | None = None,
) -> dict[str, Any]:
    """Legacy-only compatibility path; no public automation route calls this."""
    with _BOOKING_LOCK:
        with mail_store.candidate_booking_lock(str(mailbox.get("candidate_id") or "")):
            return _execute_auto_booking(
                mailbox=mailbox, message=message, event=event, result=result,
                correlation_id=correlation_id, manual_reviewer=reviewer,
            )


_PERSISTED_BOOKING_STATUSES = {"Auto Booked", "Approved & Booked", "Rescheduled"}


def _record_automation_projection(
    *, event: dict[str, Any], state: str, result: dict[str, Any], reason: str,
    booking_id: str | None = None, details: dict[str, Any] | None = None,
) -> None:
    """Keep mail/event state aligned with a booking attempt without masking it."""
    message_id = str(event.get("mailbox_message_id") or "")
    if not message_id:
        return
    try:
        mail_store.record_automation_state(
            mailbox_message_id=message_id, event_id=str(event.get("id") or "") or None,
            state=state, reason=reason, booking_id=booking_id,
            details={"automation_decision": result.get("automation_decision"), **(details or {})},
        )
    except Exception:
        # Booking audit/lifecycle persistence remains the source of truth if a
        # non-critical projection write is temporarily unavailable. The worker
        # reconciliation report will surface it rather than losing the mail.
        logger.exception("Unable to persist automation projection state=%s event=%s", state, event.get("id"))


def _booking_not_saved() -> BookingValidationError:
    return BookingValidationError(
        "BOOKING_NOT_PERSISTED",
        "The interview slot was not saved, so nothing has been booked.",
        payment_status="PASSED", duplicate_status="PASSED", conflict_status="PASSED",
    )


def _persisted(book: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run a store booking call, turning a write that did not stick into a block.

    The store certifies its own writes. When that certification fails the
    interview is simply not booked, and the outcome has to say so rather than
    reporting the in-memory return value as a successful booking.
    """
    try:
        return book()
    except candidate_store.SlotNotPersistedError as exc:
        logger.error("Booking write was not persisted detail=%s", exc)
        raise _booking_not_saved() from exc


def _confirm_slot_still_stored(
    booking: dict[str, Any], schedule: dict[str, str] | None,
) -> dict[str, Any]:
    """Re-read the booked row and refuse to report a slot storage does not hold.

    Runs after evidence attachment, which is the last thing to touch the row, so
    "Auto Booked" can only be recorded against a row that still carries the
    date, time, end time and confirmation that were booked. A booking that
    silently lost its slot fields used to be indistinguishable from a good one:
    the return value in memory looked right while the stored row was empty.
    """
    if not schedule:
        return booking
    booking_id = str(booking.get("id") or "")
    try:
        return candidate_store.assert_slot_persisted(
            booking_id,
            date=schedule["date"], time=schedule["time"], time_end=schedule["time_end"],
        )
    except candidate_store.SlotNotPersistedError as exc:
        logger.error("Booking did not survive storage booking_id=%s detail=%s", booking_id, exc)
        raise _booking_not_saved() from exc


def _execute_auto_booking(
    *, mailbox: dict[str, Any], message: dict[str, Any], event: dict[str, Any],
    result: dict[str, Any], correlation_id: str | None = None,
    manual_reviewer: str | None = None,
) -> dict[str, Any]:
    """Apply one actionable classification and durably describe every outcome."""
    correlation_id = correlation_id or str(uuid.uuid4())
    interview_round = normalize_booking_round(result)
    classification = mail_store.canonical_classification(result)
    notification = event.get("notification") or {}
    prior_audit = mail_store.booking_audit_for_message(message['provider_message_id'], classification)
    preserve_notification = bool(prior_audit and prior_audit.get('auto_booked'))
    lifecycle_claim = None
    source_snapshot = {
        'mailbox_message_id': event.get('mailbox_message_id'),
        'provider_message_id': message.get('provider_message_id'),
        'provider_thread_id': message.get('provider_thread_id'),
        'sent_at': message.get('sent_at'), 'result': result,
    }
    def audit_context():
        return {
            'source_event_id': event.get('id'), 'source_snapshot': source_snapshot,
            'lifecycle_transition_key': lifecycle_claim.incoming.idempotency_key if lifecycle_claim else None,
        }
    analysis = mail_store.record_interview_analysis(
        mailbox_message_id=event["mailbox_message_id"],
        email_analysis_id=notification.get("email_analysis_id"), mailbox_id=mailbox["id"],
        gmail_message_id=message["provider_message_id"],
        gmail_thread_id=message.get("provider_thread_id"), candidate_id=mailbox["candidate_id"],
        result=result,
        validation_status="MANUAL_APPROVED" if manual_reviewer else str(result.get("ai_validation_status") or "UNAVAILABLE"),
        processing_status="VALIDATING",
    )
    historical = historical_booking_disposition(result, classification)
    if historical:
        audit = mail_store.record_booking_audit(
            analysis_id=analysis["id"], candidate_id=str(mailbox.get("candidate_id") or ""),
            gmail_message_id=message["provider_message_id"], gmail_thread_id=message.get("provider_thread_id"),
            classification=classification, booking_id=None, auto_booked=False,
            validation_status=historical["validation_status"], payment_status="NOT_CHECKED",
            duplicate_status="NOT_CHECKED", conflict_status="NOT_CHECKED",
            booking_status=historical["status"], failure_code=historical["failure_code"],
            failure_message=historical["message"], correlation_id=correlation_id,
            **audit_context(),
        )
        updated_notification = mail_store.attach_booking_to_notification(
            notification.get("id"), audit_id=audit["id"], booking_id=None,
            booking_status=historical["status"], result=result, priority=historical["priority"],
            display_status=historical["display_status"], detail=historical["message"],
            block_reason=booking_block_reasons.describe(
                historical["failure_code"], interview=(result.get("interview") or {}),
                classification=classification, detail=historical["message"],
            ),
        ) if notification.get("id") and not preserve_notification else notification
        logger.info(
            "Historical interview disposition correlation_id=%s code=%s",
            correlation_id, historical["failure_code"],
        )
        return {
            "status": historical["status"], "event_type": historical["event_type"],
            "failure_code": historical["failure_code"], "message": historical["message"],
            "audit": audit, "notification": updated_notification,
        }
    booking: dict[str, Any] | None = None
    previous: dict[str, Any] | None = None
    schedule: dict[str, str] | None = None
    payment_status, duplicate_status, conflict_status = "NOT_CHECKED", "NOT_CHECKED", "NOT_CHECKED"
    try:
        if manual_reviewer:
            validate_manual_approval_for_booking(result, classification)
        else:
            validate_ai_for_booking(result, classification)
        candidate = candidate_store.get_candidate(str(mailbox.get("candidate_id") or ""))
        if not candidate:
            raise BookingValidationError("CANDIDATE_MAPPING_FAILED", "The connected mailbox candidate could not be found.")
        result_email = str((result.get("candidate") or {}).get("email") or "").strip().lower()
        mailbox_email = str(mailbox.get("email_address") or "").strip().lower()
        if result_email and mailbox_email and result_email != mailbox_email:
            raise BookingValidationError("CANDIDATE_MAPPING_FAILED", "The AI candidate email does not match the authorized mailbox.")
        schedule = None if classification == "interview_cancelled" else normalized_schedule(
            result, arrived_at=interview_lifecycle._sent_at(message.get("sent_at")),
        )
        if classification != "interview_cancelled":
            _payment_check(candidate, schedule); payment_status = "PASSED"
        slots = _confirmed_slots(candidate)
        # An organiser moving an interview re-sends the same calendar UID with a
        # higher SEQUENCE. Booking that as a fresh interview leaves the old time
        # standing and makes the new one fight the old for a slot, so a revision
        # of something already booked is handled as the reschedule it is.
        if classification == "interview_confirmed" and _calendar_revision_of(slots, result):
            classification = "interview_rescheduled"
        # The mail event is not the idempotency boundary: a resend, a
        # calendar revision, and an old replay all describe the same logical
        # interview.  Claim its lifecycle transition before mutating a slot.
        lifecycle_result = dict(result)
        lifecycle_result["classification"] = classification
        lifecycle_claim = interview_lifecycle.claim(str(candidate["id"]), lifecycle_result, message)
        if lifecycle_claim.decision in {
            interview_lifecycle.TransitionDecision.STOP_STALE,
            interview_lifecycle.TransitionDecision.STOP_CONFLICT,
            interview_lifecycle.TransitionDecision.STOP_RETRY_PENDING,
        }:
            raise BookingValidationError(
                "STALE_INTERVIEW_EVENT",
                "This mail is older than, or conflicts with, the current interview lifecycle state.",
                payment_status=payment_status, duplicate_status="PASSED", conflict_status="NOT_REQUIRED",
            )
        if lifecycle_claim.decision == interview_lifecycle.TransitionDecision.IDEMPOTENT and lifecycle_claim.booking_id:
            if classification != "interview_cancelled":
                booking = _confirm_slot_still_stored(lifecycle_claim.booking_id and {"id": lifecycle_claim.booking_id}, schedule)
            else:
                booking = {"id": lifecycle_claim.booking_id}
            existing_audit = mail_store.booking_audit_for_message(message["provider_message_id"], classification)
            if (existing_audit and existing_audit.get('auto_booked')
                    and str(existing_audit.get('booking_id') or '') == str(booking.get('id') or '')):
                # Retrying the idempotent outcome also heals a crash after the
                # audit write but before its notification projection.
                repaired_notification = mail_store.attach_booking_to_notification(
                    notification.get("id"), audit_id=existing_audit.get("id"), booking_id=str(booking.get("id") or ""),
                    booking_status=existing_audit.get("booking_status") or "Already Processed", result=result,
                    priority="high", schedule=schedule,
                    display_status=("Interview Cancelled" if classification == 'interview_cancelled'
                                    else "Interview Rescheduled" if classification == 'interview_rescheduled'
                                    else "Interview Automatically Booked"),
                ) if notification.get("id") else notification
                return {
                    "status": existing_audit.get("booking_status") or "Already Processed",
                    "event_type": "notification_created", "booking": booking,
                    "audit": existing_audit, "notification": repaired_notification, "duplicate": True,
                }
            booking_status, event_type = (
                ("Auto Booked", "slot_auto_booked") if classification == "interview_confirmed"
                else ("Rescheduled", "interview_rescheduled") if classification == "interview_rescheduled"
                else ("Cancelled", "interview_cancelled")
            )
        # Older audit records predate the lifecycle table.  They are only
        # trusted if the canonical candidate row still proves the exact slot.
        existing_audit = mail_store.booking_audit_for_message(message["provider_message_id"], classification)
        if existing_audit and existing_audit.get("auto_booked") and classification != "interview_cancelled":
            try:
                verified = _confirm_slot_still_stored({"id": existing_audit.get("booking_id")}, schedule)
            except BookingValidationError:
                logger.warning("Booking audit points at a missing slot; replay will use lifecycle recovery gmail_message_id=%s", message["provider_message_id"])
            else:
                interview_lifecycle.mark_applied(
                    lifecycle_claim, booking_id=str(verified.get("id") or ""),
                    state=(
                        interview_lifecycle.InterviewState.RESCHEDULED
                        if classification == "interview_rescheduled"
                        else interview_lifecycle.InterviewState.BOOKED
                    ),
                )
                return {"status": existing_audit.get("booking_status") or "Already Processed", "event_type": "notification_created",
                        "booking": verified, "audit": existing_audit, "notification": notification, "duplicate": True}
        # An intent with no booking id means a worker may have crashed after
        # candidate persistence.  Recover its exact row, then complete audit
        # and notification bookkeeping without assigning a second slot.
        if lifecycle_claim.decision == interview_lifecycle.TransitionDecision.IDEMPOTENT and not lifecycle_claim.booking_id and schedule:
            recovered = _recover_pending_lifecycle_slot(slots, result=result, message=message, schedule=schedule)
            if recovered:
                booking = _confirm_slot_still_stored(recovered, schedule)
                if classification == "interview_confirmed":
                    booking_status, event_type = "Auto Booked", "slot_auto_booked"
                else:
                    booking_status, event_type = "Rescheduled", "interview_rescheduled"
                payment_status, duplicate_status, conflict_status = "PASSED", "PASSED", "PASSED"
        if booking is None and classification == "interview_confirmed":
            duplicate = next((
                row for row in slots
                if _same_lifecycle_slot(row, result=result, message=message, schedule=schedule)
            ), None)
            if duplicate:
                raise BookingValidationError("DUPLICATE_BOOKING", "This candidate already has the same interview booking.", payment_status="PASSED", duplicate_status="DUPLICATE")
            duplicate_status = "PASSED"
            # Different interview identities are allowed to overlap.  The
            # system records external interview commitments, not an exclusive
            # interviewer resource calendar; lifecycle identity is the only
            # duplicate boundary here.
            conflict_status = "NOT_REQUIRED"
            booking = _persisted(lambda: candidate_store.assign_interview_slot(
                candidate_id=str(candidate["id"]), date=schedule["date"], time=schedule["time"],
                time_end=schedule["time_end"], interview_round=interview_round,
                notes=(
                    f"Manually approved from reviewed interview email by {manual_reviewer}."
                    if manual_reviewer else
                    "Automatically booked from validated interview email (AI Mail Monitoring)."
                ),
                interview_booking_source="candidate_booked" if manual_reviewer else "ai_auto_booked",
                **_booking_metadata(result, message, schedule),
            ))
            booking_status, event_type = (
                ("Approved & Booked", "slot_manually_booked")
                if manual_reviewer else ("Auto Booked", "slot_auto_booked")
            )
        elif booking is None and classification == "interview_rescheduled":
            target = _resolve_existing_slot(slots, result=result, message=message, classification=classification)
            previous = dict(target)
            booking = _persisted(lambda: candidate_store.update_interview_slot(
                candidate_id=str(target["id"]), date=schedule["date"], time=schedule["time"],
                time_end=schedule["time_end"], interview_round=interview_round,
                notes=(
                    f"Reschedule manually approved from reviewed email by {manual_reviewer}."
                    if manual_reviewer else
                    "Rescheduled from validated interview email (AI Mail Monitoring)."
                ),
                interview_booking_source="candidate_booked" if manual_reviewer else "ai_auto_booked",
                **_booking_metadata(result, message, schedule),
            ))
            duplicate_status, conflict_status = "PASSED", "NOT_REQUIRED"
            booking_status, event_type = "Rescheduled", "interview_rescheduled"
        elif booking is None:
            target = _resolve_existing_slot(slots, result=result, message=message, classification=classification)
            previous = dict(target)
            booking = candidate_store.cancel_interview_slot(candidate_id=str(target["id"]))
            payment_status, duplicate_status, conflict_status = "NOT_REQUIRED", "PASSED", "NOT_REQUIRED"
            booking_status, event_type = "Cancelled", "interview_cancelled"
        # Evidence is attached before the outcome is recorded, so the audit can
        # be gated on the row as it stands after the last write touches it.
        evidence_snapshot = None
        if not manual_reviewer and booking_status in {"Auto Booked", "Rescheduled"}:
            evidence_snapshot = _attach_automatic_booking_evidence(
                candidate=candidate, message=message, result=result,
                booking=booking, booking_status=booking_status,
            )
        if booking_status in _PERSISTED_BOOKING_STATUSES:
            booking = _confirm_slot_still_stored(booking, schedule)
        interview_lifecycle.mark_applied(
            lifecycle_claim,
            booking_id=str(booking.get("id") or ""),
            state=(
                interview_lifecycle.InterviewState.BOOKED if booking_status in {"Auto Booked", "Approved & Booked"}
                else interview_lifecycle.InterviewState.RESCHEDULED if booking_status == "Rescheduled"
                else interview_lifecycle.InterviewState.CANCELLED
            ),
        )
        audit = mail_store.record_booking_audit(
            analysis_id=analysis["id"], candidate_id=str(candidate["id"]),
            gmail_message_id=message["provider_message_id"], gmail_thread_id=message.get("provider_thread_id"),
            classification=classification, booking_id=str(booking.get("id") or ""), auto_booked=True,
            validation_status="MANUAL_APPROVED" if manual_reviewer else "PASSED",
            payment_status=payment_status, duplicate_status=duplicate_status,
            conflict_status=conflict_status, booking_status=booking_status,
            previous_booking=previous, new_booking=booking, correlation_id=correlation_id,
            **audit_context(),
        )
        updated_notification = mail_store.attach_booking_to_notification(
            notification.get("id"), audit_id=audit["id"], booking_id=str(booking.get("id") or ""),
            booking_status=booking_status, result=result, priority="high",
            schedule=schedule,
            display_status=(
                "Interview Manually Approved & Booked"
                if booking_status == "Approved & Booked" else
                "Interview Automatically Booked"
                if booking_status == "Auto Booked" else f"Interview {booking_status}"
            ),
        ) if notification.get("id") else {}
        logger.info("Interview booking applied correlation_id=%s classification=%s booking_id=%s", correlation_id, classification, booking.get("id"))
        return {
            "status": booking_status, "event_type": event_type,
            "booking": booking, "audit": audit, "notification": updated_notification,
            "evidence_snapshot": evidence_snapshot,
        }
    except BookingValidationError as exc:
        payment_status = exc.payment_status if exc.payment_status != "NOT_CHECKED" else payment_status
        duplicate_status = exc.duplicate_status if exc.duplicate_status != "NOT_CHECKED" else duplicate_status
        conflict_status = exc.conflict_status if exc.conflict_status != "NOT_CHECKED" else conflict_status
        duplicate_ignored = exc.code == "DUPLICATE_BOOKING"
        booking_status = "Duplicate Ignored" if duplicate_ignored else "Blocked"
        validation_status = "SKIPPED" if duplicate_ignored else "BLOCKED"
        display_status = "Already Booked — Duplicate Ignored" if duplicate_ignored else "Automatic Booking Blocked"
        priority = "low" if duplicate_ignored else "retry_pending"
        event_type = "duplicate_booking_ignored" if duplicate_ignored else "slot_booking_blocked"
        audit = mail_store.record_booking_audit(
            analysis_id=analysis["id"], candidate_id=str(mailbox.get("candidate_id") or ""),
            gmail_message_id=message["provider_message_id"], gmail_thread_id=message.get("provider_thread_id"),
            classification=classification, booking_id=None, auto_booked=False,
            validation_status=validation_status, payment_status=payment_status, duplicate_status=duplicate_status,
            conflict_status=conflict_status, booking_status=booking_status, failure_code=exc.code,
            failure_message=exc.message, correlation_id=correlation_id,
            **audit_context(),
        )
        # The operator-facing reason is decided here, beside the decision
        # itself, so the notification carries why the booking was blocked
        # instead of leaving the UI to infer it from a status string.
        block_reason = booking_block_reasons.describe(
            exc.code, schedule=schedule, interview=(result.get("interview") or {}),
            classification=classification, detail=exc.message,
        )
        updated_notification = mail_store.attach_booking_to_notification(
            notification.get("id"), audit_id=audit["id"], booking_id=None,
            booking_status=booking_status, result=result, priority=priority,
            schedule=schedule,
            display_status=display_status, detail=exc.message,
            block_reason=block_reason,
        ) if notification.get("id") and not preserve_notification else notification
        logger.info(
            "Interview booking blocked correlation_id=%s code=%s reason=%s",
            correlation_id, exc.code, block_reason["reason_code"],
        )
        return {
            "status": booking_status, "event_type": event_type, "failure_code": exc.code,
            "message": exc.message, "block_reason": block_reason,
            "audit": audit, "notification": updated_notification,
        }
    except Exception as exc:
        code = type(exc).__name__
        audit = mail_store.record_booking_audit(
            analysis_id=analysis["id"], candidate_id=str(mailbox.get("candidate_id") or ""),
            gmail_message_id=message["provider_message_id"], gmail_thread_id=message.get("provider_thread_id"),
            classification=classification, booking_id=None, auto_booked=False,
            validation_status="FAILED", payment_status=payment_status, duplicate_status=duplicate_status,
            conflict_status=conflict_status, booking_status="Processing Failed", failure_code=code,
            failure_message="Automatic booking could not be completed safely.", correlation_id=correlation_id,
            **audit_context(),
        )
        updated_notification = mail_store.attach_booking_to_notification(
            notification.get("id"), audit_id=audit["id"], booking_id=None,
            booking_status="Processing Failed", result=result, priority="retry_pending",
            display_status="Automatic Booking Processing Failed",
            detail="Review the mail analysis and retry after the underlying error is resolved.",
            # An unexpected error remains durable retry work; it never becomes
            # a manual-review action.
            block_reason=booking_block_reasons.describe(
                code, interview=(result.get("interview") or {}),
                classification=classification,
            ),
        ) if notification.get("id") and not preserve_notification else notification
        logger.exception("Interview booking processing failed correlation_id=%s code=%s", correlation_id, code)
        return {"status": "Processing Failed", "event_type": "slot_booking_blocked", "failure_code": code,
                "message": "Automatic booking could not be completed safely.", "audit": audit,
                "notification": updated_notification}
