"""Why an automatic booking was blocked, in words anyone can act on.

The booking validator raises precise internal codes — INVALID_TIMEZONE,
CROSS_DAY_INTERVIEW, BOOKING_AMBIGUOUS — which are the right level of detail
for a log and the wrong level for a person reading Mail Alerts. Someone
looking at a blocked booking wants three things: what happened, why, and
whether to do anything about it.

So each internal code maps to two things, kept apart:

* a stable reason code, for anything that has to branch on the outcome and for
  the Technical details a person can open when something needs debugging; and
* an explanation in plain words -- a title, a reason and what to do -- which is
  all a user sees by default.

The internal code is never discarded — it is stored alongside, because it is
what identifies the exact validator branch when something needs debugging.

The mapping lives here rather than in the frontend so that the reason shown to
a user is the reason the backend actually decided, not a guess reconstructed
from a status string. Mail Alerts builds the explanation from the stored codes
each time a notification is read (`explain_notification`), so rewording it here
corrects every alert already written, not only new ones.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

# Reason codes. Deliberately coarser than the validator's internal codes: these
# describe what an operator has to do about it.
DUPLICATE_BOOKING = "DUPLICATE_BOOKING"
MISSING_DATE_TIME = "MISSING_DATE_TIME"
PAST_INTERVIEW_DATE = "PAST_INTERVIEW_DATE"
NO_MATCHING_SLOT = "NO_MATCHING_SLOT"
CANDIDATE_NOT_FOUND = "CANDIDATE_NOT_FOUND"
ROUND_NOT_FOUND = "ROUND_NOT_FOUND"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"
DUPLICATE_INVITE = "DUPLICATE_INVITE"
PAYMENT_NOT_CLEARED = "PAYMENT_NOT_CLEARED"
AI_RETRY_PENDING = "AI_RETRY_PENDING"
BOOKING_NOT_SAVED = "BOOKING_NOT_SAVED"
STALE_INTERVIEW_EVENT = "STALE_INTERVIEW_EVENT"
# Retired from the mapping below, but still stored on alerts written before it
# was, so it still needs an explanation.
MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"

# Validator code -> reason code. Anything absent is retried automatically;
# no unclassified booking block is silently converted into a human queue.
_INTERNAL_TO_REASON = {
    "DUPLICATE_BOOKING": DUPLICATE_BOOKING,
    "SLOT_CONFLICT": NO_MATCHING_SLOT,
    # Every way the schedule can fail to parse reads the same to an operator:
    # the invite did not yield a usable date and time.
    "INVALID_DATE": MISSING_DATE_TIME,
    "INVALID_TIME": MISSING_DATE_TIME,
    "INVALID_END_TIME": MISSING_DATE_TIME,
    "INVALID_DURATION": MISSING_DATE_TIME,
    "MISSING_TIMEZONE": MISSING_DATE_TIME,
    "INVALID_TIMEZONE": MISSING_DATE_TIME,
    "CROSS_DAY_INTERVIEW": MISSING_DATE_TIME,
    "MEDIUM_CONFIDENCE_INCOMPLETE": MISSING_DATE_TIME,
    "INCOMPLETE_SCHEDULE": MISSING_DATE_TIME,
    "HISTORICAL_SCHEDULE_INCOMPLETE": MISSING_DATE_TIME,
    "PAST_INTERVIEW": PAST_INTERVIEW_DATE,
    "CANDIDATE_MAPPING_FAILED": CANDIDATE_NOT_FOUND,
    "BOOKING_NOT_FOUND": ROUND_NOT_FOUND,
    "BOOKING_AMBIGUOUS": ROUND_NOT_FOUND,
    "LOW_CONFIDENCE": LOW_CONFIDENCE,
    "MISSING_EVIDENCE": INCOMPLETE_EVIDENCE,
    "AI_NOT_VALIDATED": INCOMPLETE_EVIDENCE,
    "PAYMENT_VALIDATION_FAILED": PAYMENT_NOT_CLEARED,
    "AI_REQUIRES_REVIEW": AI_RETRY_PENDING,
    "AUTO_BOOKING_DISABLED": AI_RETRY_PENDING,
    "NOT_ACTIONABLE": AI_RETRY_PENDING,
    # The store accepted the write and the row does not hold the slot. This is
    # never the invite's fault, so it must not read as a parsing or duplicate
    # problem — it is a storage failure an operator has to act on.
    "BOOKING_NOT_PERSISTED": BOOKING_NOT_SAVED,
    "STALE_INTERVIEW_EVENT": STALE_INTERVIEW_EVENT,
}

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def reason_code_for(internal_code: Any) -> str:
    return _INTERNAL_TO_REASON.get(str(internal_code or "").strip().upper(), AI_RETRY_PENDING)


def format_schedule(date: Any, time: Any = None) -> str:
    """'2026-08-03' + '16:30' -> '3 Aug 2026, 4:30 PM'.

    Returns "" when there is nothing usable, so callers can leave the reason
    unqualified rather than printing a half-formed date.
    """
    text = str(date or "").strip()[:10]
    parts = text.split("-")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return ""
    year, month, day = (int(part) for part in parts)
    if not 1 <= month <= 12:
        return ""
    stamp = f"{day} {_MONTHS[month - 1]} {year}"

    # A normalized schedule stores 24-hour "16:30"; the raw AI extraction uses
    # the contract's 12-hour "4:30 PM". Either can reach here depending on how
    # far validation got before it failed.
    clock = str(time or "").strip().upper()
    suffix = ""
    for meridiem in ("AM", "PM"):
        if clock.endswith(meridiem):
            suffix, clock = meridiem, clock[: -len(meridiem)].strip()
            break
    hhmm = clock.split(":")
    if len(hhmm) != 2 or not all(part.strip().isdigit() for part in hhmm):
        return stamp
    hour, minute = int(hhmm[0]), int(hhmm[1])
    if suffix:
        if not 1 <= hour <= 12 or not 0 <= minute <= 59:
            return stamp
        return f"{stamp}, {hour}:{minute:02d} {suffix}"
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return stamp
    return f"{stamp}, {hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


# ── What a person reads ─────────────────────────────────────────────────────
#
# No code above is ever the reason a user reads. Each explanation is keyed on
# the most precise code a notification holds -- the validator's own where it
# has one -- because the reason codes deliberately merge causes that read
# differently. BOOKING_NOT_FOUND and BOOKING_AMBIGUOUS are both ROUND_NOT_FOUND,
# and every stored alert carrying that code said "Interview round could not be
# identified" about an email that matched no booking, or several; none of them
# was about a round.
#
# Every reason and every action is one short sentence of everyday English.
# People read these between other work, and the earlier wording -- "applied",
# "conflicting update", "set aside for a person to check" -- was accurate and
# still had to be read twice. The tests hold every sentence to one sentence,
# a word limit, and a list of words nobody says out loud.
#
# The same cause also reads differently depending on what the email was trying
# to do: a mail cancelling an interview cannot fail to "create a booking". Each
# sentence therefore names its consequence through {result}, and each action
# its manual fallback through {manual}, both taken from the email's own
# classification. {when} names the interview time only where the time is the
# point: a time that has passed, a time that clashed.
#
# An action promises an automatic retry only where one happens: the outcomes
# that `recruitment_automation.outcome_for` makes final -- duplicate, stale,
# past, not actionable -- never say "try again", and the tests hold every code
# to that.

NO_ACTION = "No action needed."
_RETRY_THEN_MANUAL = "We'll try again automatically; if it still fails, {manual}."
# After the person fixes what the booking was waiting for.
_THEN_RETRY = "then we'll try again automatically."

_OUTCOMES = {
    "create": {
        "title": "Booking not created",
        "result": "no booking was created",
        "manual": "book it in Daily Ops",
    },
    "change": {
        "title": "Booking not updated",
        "result": "the booking was not updated",
        "manual": "update the booking in Daily Ops",
    },
    "cancel": {
        "title": "Cancellation not applied",
        "result": "nothing was cancelled",
        "manual": "cancel the booking in Daily Ops",
    },
    "assessment": {
        "title": "Assessment not booked",
        "result": "the assessment was not booked",
        "manual": "book the assessment in Daily Ops",
    },
}
ALREADY_BOOKED_TITLE = "Already booked"

_UNCLEAR_SCHEDULE = (
    "The email doesn't give a clear interview date and time, so {result}.",
    _RETRY_THEN_MANUAL,
)

# (reason, what to do), keyed on the validator's own code.
_BY_INTERNAL_CODE: dict[str, tuple[str, str]] = {
    # Final outcomes. Nothing retries these, so nothing promises to.
    #
    # No time here: the duplicate check matches on the calendar event, and a
    # booking that moved keeps its event, so the email's time can be one the
    # booking no longer has.
    "DUPLICATE_BOOKING": (
        "This interview is already booked, so it was not booked again.",
        NO_ACTION,
    ),
    # Both past-interview alerts production held on 19 Sep 2026 were real
    # interviews missing from Daily Ops: one invite arrived a minute before the
    # start and was processed two minutes after it, the other arrived nine
    # minutes after the start. "No action needed" told nobody to look.
    "PAST_INTERVIEW": (
        "This interview time{when} has already passed, so {result}.",
        "If the interview took place, make sure it's in Daily Ops.",
    ),
    "STALE_INTERVIEW_EVENT": (
        "A newer interview update was already processed, so this older email was ignored.",
        NO_ACTION,
    ),
    "NOT_ACTIONABLE": (
        "This email doesn't need a booking change.",
        NO_ACTION,
    ),
    # The email did not give a schedule that can be booked.
    "INVALID_DATE": _UNCLEAR_SCHEDULE,
    "INVALID_TIME": _UNCLEAR_SCHEDULE,
    "INVALID_END_TIME": _UNCLEAR_SCHEDULE,
    "INVALID_DURATION": _UNCLEAR_SCHEDULE,
    "INCOMPLETE_SCHEDULE": _UNCLEAR_SCHEDULE,
    "MEDIUM_CONFIDENCE_INCOMPLETE": _UNCLEAR_SCHEDULE,
    "MISSING_TIMEZONE": (
        "The email doesn't say which time zone the interview is in, so {result}.",
        _RETRY_THEN_MANUAL,
    ),
    "INVALID_TIMEZONE": (
        "We didn't recognise the time zone in the email, so {result}.",
        _RETRY_THEN_MANUAL,
    ),
    "CROSS_DAY_INTERVIEW": (
        "The interview starts and ends on different days, so {result}.",
        "Check the times in the email, then {manual}.",
    ),
    "HISTORICAL_SCHEDULE_INCOMPLETE": (
        "This is an old email without a clear interview date, so {result}.",
        "If the interview is still coming up, {manual}.",
    ),
    # Who, and whether the system may act at all.
    "CANDIDATE_MAPPING_FAILED": (
        "We couldn't tell which candidate this email is for, so {result}.",
        "Check that this Gmail account is linked to the right candidate, then {manual}.",
    ),
    "LOW_CONFIDENCE": (
        "We weren't sure enough about this email to act on it, so {result}.",
        _RETRY_THEN_MANUAL,
    ),
    "AI_NOT_VALIDATED": (
        "We could not confirm the interview details automatically, so {result}.",
        _RETRY_THEN_MANUAL,
    ),
    "MISSING_EVIDENCE": (
        "The email doesn't have enough interview details, so {result}.",
        "Review the email, and if the interview is real, {manual}.",
    ),
    # Retired, but still stored on older alerts. The one production held had
    # been booked by hand since, so the action must not ask for a second
    # booking.
    "AI_REQUIRES_REVIEW": (
        "We could not confirm the interview details automatically.",
        "Please review the email and update Daily Ops if needed.",
    ),
    "AUTO_BOOKING_DISABLED": (
        "Automatic booking is turned off, so {result}.",
        "Ask an admin to turn it back on, or {manual}.",
    ),
    # Saving failed after every check passed.
    "BOOKING_NOT_PERSISTED": (
        "The booking couldn't be saved because of a system error.",
        "We'll try again automatically; if it still fails, {manual} and report the problem.",
    ),
    # Retired: overlapping interviews are allowed now, but alerts from before
    # that still carry the code.
    "SLOT_CONFLICT": (
        "This time{when} clashed with another booking for this candidate, so {result}.",
        "Check the candidate's bookings, and {manual} if it's missing.",
    ),
}

# An email that changes or cancels an interview has to be matched to the
# booking it is about. These are the two ways that fails.
#
# The validator raises BOOKING_AMBIGUOUS both when two bookings match equally
# and when none of several matches at all. In all three alerts production held
# on 19 Sep 2026 the interview being cancelled had no booking, while others
# stood beside it. So no action here assumes the right booking exists.
_UPDATES_AN_EXISTING_BOOKING: dict[tuple[str, str], tuple[str, str]] = {
    ("BOOKING_NOT_FOUND", "cancel"): (
        "We could not find an active booking that matches this cancellation.",
        "If this interview is still in Daily Ops, cancel it there.",
    ),
    ("BOOKING_NOT_FOUND", "change"): (
        "We could not find an active booking that matches this update.",
        "If the interview is going ahead, book the new time in Daily Ops.",
    ),
    ("BOOKING_AMBIGUOUS", "cancel"): (
        "We found more than one booking for this candidate, so we could not "
        "tell which interview to cancel.",
        "If this interview is in Daily Ops, cancel it there.",
    ),
    ("BOOKING_AMBIGUOUS", "change"): (
        "We found more than one booking for this candidate, so we could not "
        "tell which interview to update.",
        "Update this interview in Daily Ops, or book it there if it's missing.",
    ),
}

# The same failure when an alert kept only the coarse ROUND_NOT_FOUND.
_UNMATCHED: dict[str, tuple[str, str]] = {
    "cancel": (
        "We couldn't match this email to one of the candidate's bookings, so {result}.",
        "If this interview is in Daily Ops, cancel it there.",
    ),
    "change": (
        "We couldn't match this email to one of the candidate's bookings, so {result}.",
        "Update this interview in Daily Ops, or book it there if it's missing.",
    ),
}

# Assessment blocks store the scheduler's own code in the reason-code column.
# `services/assessment_schedule.py` is the source of these; a test holds the
# two together.
_ASSESSMENT: dict[str, tuple[str, str]] = {
    "ASSESSMENT_WITHOUT_DATE": (
        "The assessment email doesn't give a date, so it was not booked.",
        "Check the email for the date or deadline, then {manual}.",
    ),
    "ASSESSMENT_WINDOW_CLOSED": (
        "The assessment deadline has already passed, so it was not booked.",
        "If the deadline was extended, {manual}.",
    ),
    "ASSESSMENT_TIME_PASSED": (
        "The assessment time has already passed, so it was not booked.",
        NO_ACTION,
    ),
    "ASSESSMENT_WINDOW_TOO_SHORT": (
        "There isn't enough time before the deadline to fit the assessment.",
        "Check the dates in the email, and {manual} if it's still needed.",
    ),
    "ASSESSMENT_SLOT_CONFLICT": (
        "The assessment time clashes with another booking, so it was not booked.",
        "Check the candidate's bookings, then {manual}.",
    ),
    "ASSESSMENT_NO_FREE_SLOT": (
        "There's no free time left before the assessment deadline, so it was not booked.",
        "Free up time in the candidate's bookings, or {manual}.",
    ),
}

_UNEXPECTED = (
    "Something went wrong while processing this email, so {result}.",
    _RETRY_THEN_MANUAL,
)

# Only reached when a notification carries no usable validator code.
_BY_REASON_CODE: dict[str, tuple[str, str]] = {
    DUPLICATE_BOOKING: _BY_INTERNAL_CODE["DUPLICATE_BOOKING"],
    MISSING_DATE_TIME: _UNCLEAR_SCHEDULE,
    PAST_INTERVIEW_DATE: _BY_INTERNAL_CODE["PAST_INTERVIEW"],
    NO_MATCHING_SLOT: _BY_INTERNAL_CODE["SLOT_CONFLICT"],
    CANDIDATE_NOT_FOUND: _BY_INTERNAL_CODE["CANDIDATE_MAPPING_FAILED"],
    LOW_CONFIDENCE: _BY_INTERNAL_CODE["LOW_CONFIDENCE"],
    INCOMPLETE_EVIDENCE: _BY_INTERNAL_CODE["AI_NOT_VALIDATED"],
    DUPLICATE_INVITE: (
        "We already received this invite, so it was not booked again.",
        NO_ACTION,
    ),
    AI_RETRY_PENDING: _UNEXPECTED,
    BOOKING_NOT_SAVED: _BY_INTERNAL_CODE["BOOKING_NOT_PERSISTED"],
    STALE_INTERVIEW_EVENT: _BY_INTERNAL_CODE["STALE_INTERVIEW_EVENT"],
    MANUAL_REVIEW_REQUIRED: _BY_INTERNAL_CODE["AI_REQUIRES_REVIEW"],
}

# The payment check reports one of these instructions, written by
# `candidate_store.slot_confirm_block_reason` for Daily Ops. They are shown as
# they are under Technical details; the action says the same thing more
# plainly, keeping the amount, which is the part a person needs.
_OWNER_MISSING = "Assign an owner"
_PAYMENT_SHORT = "Record at least"
_SCREENSHOT_MISSING = "Confirm the slot screenshot"
_DATE_MISSING = "Set the interview date"
_AMOUNT = re.compile(r"₹[\d,]+")


def _requirements(detail: Any) -> tuple[str, str]:
    """PAYMENT_VALIDATION_FAILED, which is not always about payment.

    The same check refuses a booking when nobody owns the candidate, and every
    such alert used to say "Payment is not cleared".
    """
    instruction = str(detail or "").strip()
    if instruction.startswith(_OWNER_MISSING):
        return (
            "This candidate doesn't have an owner yet, so {result}.",
            f"Assign an owner to the candidate, {_THEN_RETRY}",
        )
    if instruction.startswith(_PAYMENT_SHORT):
        amount = _AMOUNT.search(instruction)
        record = f"Record at least {amount.group(0)} as received" if amount else "Record the candidate's payment"
        return (
            "The candidate hasn't paid enough yet for a booking, so {result}.",
            f"{record}, {_THEN_RETRY}",
        )
    if instruction.startswith(_SCREENSHOT_MISSING):
        return (
            "The slot screenshot isn't marked as posted in the WhatsApp group yet, so {result}.",
            f"Confirm the slot screenshot was posted in the Interview slots WhatsApp group, {_THEN_RETRY}",
        )
    if instruction.startswith(_DATE_MISSING):
        return (
            "The candidate's interview date isn't set yet, so {result}.",
            f"Set the interview date, {_THEN_RETRY}",
        )
    return (
        "Some booking details for this candidate are missing, so {result}.",
        f"Update the candidate's payment and owner details, {_THEN_RETRY}",
    )


def _code(value: Any) -> str:
    return str(value or "").strip().upper()


def _outcome(reason_code: str, internal_code: str, classification: Any, candidate_status: Any) -> str:
    """What the email was trying to do to the booking."""
    if reason_code.startswith("ASSESSMENT_") or "assessment" in str(candidate_status or "").lower():
        return "assessment"
    kind = str(classification or "").strip().lower()
    if kind == "interview_cancelled":
        return "cancel"
    # Matching an email to an existing booking only happens when the email
    # changes one, including a confirmation that turned out to be a revision.
    if (kind == "interview_rescheduled"
            or internal_code in {"BOOKING_NOT_FOUND", "BOOKING_AMBIGUOUS"}
            or (not internal_code and reason_code == ROUND_NOT_FOUND)):
        return "change"
    return "create"


def explain(
    reason_code: Any = None,
    internal_code: Any = None,
    *,
    classification: Any = None,
    detail: Any = None,
    stored_reason: Any = None,
    booking_status: Any = None,
    candidate_status: Any = None,
    date: Any = None,
    time: Any = None,
) -> dict[str, Any]:
    """A blocked booking as a person should read it.

    Returns a title, a reason and what to do, all in plain words, plus the
    codes under `technical` for anyone debugging. `detail` is the validator's
    own message where the block came from interview booking; `stored_reason`
    is the sentence stored on the alert, which is where assessment blocks keep
    their specifics.
    """
    reason_key = _code(reason_code)
    internal_key = _code(internal_code)
    outcome = _outcome(reason_key, internal_key, classification, candidate_status)
    matching = "cancel" if outcome == "cancel" else "change"

    if internal_key in {"BOOKING_NOT_FOUND", "BOOKING_AMBIGUOUS"}:
        parts = _UPDATES_AN_EXISTING_BOOKING[(internal_key, matching)]
    elif internal_key == "PAYMENT_VALIDATION_FAILED" or (
        not internal_key and reason_key == PAYMENT_NOT_CLEARED
    ):
        parts = _requirements(detail)
    elif internal_key in _BY_INTERNAL_CODE:
        parts = _BY_INTERNAL_CODE[internal_key]
    elif reason_key in _ASSESSMENT:
        parts = _ASSESSMENT[reason_key]
    elif reason_key in _BY_INTERNAL_CODE:
        # Assessment blocks store validator-style codes in the reason column.
        parts = _BY_INTERNAL_CODE[reason_key]
    elif reason_key == ROUND_NOT_FOUND:
        parts = _UNMATCHED[matching]
    elif reason_key in _BY_REASON_CODE:
        parts = _BY_REASON_CODE[reason_key]
    else:
        parts = _UNEXPECTED

    when = format_schedule(date, time)
    fields = {**_OUTCOMES[outcome], "when": f" ({when})" if when else ""}
    reason, action = (part.format(**fields) for part in parts)
    duplicate = DUPLICATE_BOOKING in {internal_key, reason_key}
    message = detail if internal_key else stored_reason
    return {
        "title": ALREADY_BOOKED_TITLE if duplicate else fields["title"],
        "reason": reason,
        "action": action,
        "needs_action": not action.startswith(NO_ACTION),
        "technical": {
            "reason_code": reason_key or None,
            "internal_code": internal_key if internal_key and internal_key != reason_key else None,
            "booking_status": str(booking_status or "").strip() or None,
            "message": str(message or "").strip() or None,
        },
    }


def explain_notification(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The explanation Mail Alerts shows for one stored alert, or None.

    None means the booking was not blocked. The block columns are written on
    every booking attempt and cleared by a later success, so their presence is
    the test.
    """
    if not (row.get("booking_block_reason_code") or row.get("booking_block_reason")):
        return None
    return explain(
        row.get("booking_block_reason_code"),
        row.get("booking_failure_code"),
        classification=row.get("classification"),
        detail=row.get("recommended_action"),
        stored_reason=row.get("booking_block_reason"),
        booking_status=row.get("booking_status"),
        candidate_status=row.get("candidate_status"),
        date=row.get("interview_date"),
        time=row.get("interview_time"),
    )


def describe(
    internal_code: Any,
    *,
    schedule: dict[str, Any] | None = None,
    interview: dict[str, Any] | None = None,
    classification: Any = None,
    detail: Any = None,
) -> dict[str, str]:
    """The blocking reason as the notification should carry it.

    `schedule` is the normalized booking schedule when the validator got far
    enough to build one; `interview` is the raw AI extraction, used when it did
    not. The stored sentence is the same one `explain` gives, so an alert reads
    the same whether it is shown from storage or rebuilt from its codes.
    """
    code = _code(internal_code)
    reason_code = reason_code_for(code)
    source = schedule or interview or {}
    explanation = explain(
        reason_code, code, classification=classification, detail=detail,
        date=source.get("date"), time=source.get("time"),
    )
    return {
        "reason_code": reason_code,
        "reason": explanation["reason"],
        # The exact validator branch, kept for debugging and for anything that
        # needs to distinguish causes this mapping deliberately merges.
        "internal_code": code,
    }
