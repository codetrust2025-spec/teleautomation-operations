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
# The same cause also reads differently depending on what the email was trying
# to do: a mail cancelling an interview cannot fail to "create a slot". Each
# sentence therefore names its consequence through {result}, and each action
# its manual fallback through {manual}, both taken from the email's own
# classification. {when} names the interview time where knowing it changes
# what a person does.
#
# An action promises an automatic retry only where one happens: the outcomes
# that `recruitment_automation.outcome_for` makes final -- duplicate, stale,
# past, not actionable -- never say "try again", and the tests hold every code
# to that.

NO_ACTION = "No action needed."
_RETRY = "The system will try again automatically."
_RETRY_THEN_MANUAL = f"{_RETRY} If it's still not done, {{manual}}."

_OUTCOMES = {
    "create": {
        "title": "Booking not created",
        "result": "no slot was created",
        "manual": "book the slot by hand in Daily Ops",
        "verb": "book",
    },
    "change": {
        "title": "Booking not changed",
        "result": "the booking was not changed",
        "manual": "update the booking by hand in Daily Ops",
        "verb": "update",
    },
    "cancel": {
        "title": "Booking not cancelled",
        "result": "the booking was not cancelled",
        "manual": "cancel the booking by hand in Daily Ops",
        "verb": "cancel",
    },
    "assessment": {
        "title": "Assessment not booked",
        "result": "no slot was booked",
        "manual": "book the assessment by hand in Daily Ops",
        "verb": "book",
    },
}
ALREADY_BOOKED_TITLE = "Already booked"

_UNCLEAR_SCHEDULE = (
    "The email doesn't give a clear interview date and time, so {result}.",
    f"{_RETRY} If it's still not done, {{manual}} using the date and time in the email.",
)

# (reason, what to do), keyed on the validator's own code.
_BY_INTERNAL_CODE: dict[str, tuple[str, str]] = {
    # Final outcomes. Nothing retries these, so nothing promises to.
    "DUPLICATE_BOOKING": (
        "This interview{when} is already booked, so another slot was not created.",
        NO_ACTION,
    ),
    "PAST_INTERVIEW": (
        "This interview time{when} has already passed, so {result}.",
        NO_ACTION,
    ),
    "STALE_INTERVIEW_EVENT": (
        "A newer email about this interview has already been applied, so this "
        "older or conflicting update was not used.",
        "No action needed. If the booking looks wrong, check the latest email "
        "for this interview.",
    ),
    "NOT_ACTIONABLE": (
        "The system decided this email needs no booking change, so {result}.",
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
        _UNCLEAR_SCHEDULE[1],
    ),
    "INVALID_TIMEZONE": (
        "The email's time zone wasn't recognised, so {result}.",
        _UNCLEAR_SCHEDULE[1],
    ),
    "CROSS_DAY_INTERVIEW": (
        "The interview's start and end fall on different days, so {result}.",
        "Check the times in the email, then {manual}.",
    ),
    "HISTORICAL_SCHEDULE_INCOMPLETE": (
        "This older email doesn't give a clear interview date, so it was kept "
        "for reference only.",
        "No action needed, unless the interview is still coming up. If it is, "
        "book it by hand in Daily Ops.",
    ),
    # Who, and whether the system may act at all.
    "CANDIDATE_MAPPING_FAILED": (
        "This email couldn't be matched to a candidate, so {result}.",
        "Check that this Gmail account is linked to the right candidate, then {manual}.",
    ),
    "LOW_CONFIDENCE": (
        "The system wasn't sure enough about this email to act on it "
        "automatically, so {result}.",
        _RETRY_THEN_MANUAL,
    ),
    "AI_NOT_VALIDATED": (
        "The email's details couldn't be confirmed, so {result}.",
        _RETRY_THEN_MANUAL,
    ),
    "MISSING_EVIDENCE": (
        "The email doesn't contain enough detail to support this booking, so {result}.",
        "Check the email, and if it's a real interview, {manual}.",
    ),
    "AI_REQUIRES_REVIEW": (
        "This email was set aside for a person to check, so {result}.",
        "Check the email, and if it's a real interview, {manual}.",
    ),
    "AUTO_BOOKING_DISABLED": (
        "Automatic booking is switched off, so {result}.",
        "Ask an admin to switch automatic booking back on, or {manual}.",
    ),
    # Saving failed after every check passed.
    "BOOKING_NOT_PERSISTED": (
        "The booking couldn't be saved, so {result}.",
        f"Please report this problem. {_RETRY} If it's still not done, {{manual}}.",
    ),
    # Retired: overlapping interviews are allowed now, but alerts from before
    # that still carry the code.
    "SLOT_CONFLICT": (
        "This time{when} clashed with another booking for the candidate, so {result}.",
        "Check the candidate's bookings, and if this interview is still "
        "needed, {manual}.",
    ),
}

# An email that changes or cancels an interview has to be matched to the
# booking it is about. These are the two ways that fails.
_UPDATES_AN_EXISTING_BOOKING: dict[tuple[str, str], tuple[str, str]] = {
    ("BOOKING_NOT_FOUND", "cancel"): (
        "This email cancels an interview, but no active booking was found for "
        "it, so nothing was cancelled.",
        "No action needed if this interview was never booked. If it still "
        "appears in Daily Ops, cancel it there by hand.",
    ),
    ("BOOKING_NOT_FOUND", "change"): (
        "This email changes an interview, but no existing booking was found "
        "for it, so nothing was changed.",
        "If the interview is going ahead, book the new time by hand in Daily Ops.",
    ),
    ("BOOKING_AMBIGUOUS", "cancel"): (
        "This email cancels an interview, but the candidate has more than one "
        "booking it could refer to, so nothing was cancelled.",
        "Open the candidate in Daily Ops and cancel the right booking by hand.",
    ),
    ("BOOKING_AMBIGUOUS", "change"): (
        "This email changes an interview, but the candidate has more than one "
        "booking it could refer to, so nothing was changed.",
        "Open the candidate in Daily Ops and update the right booking by hand.",
    ),
}

# Assessment blocks store the scheduler's own code in the reason-code column.
# `services/assessment_schedule.py` is the source of these; a test holds the
# two together.
_ASSESSMENT: dict[str, tuple[str, str]] = {
    "ASSESSMENT_WITHOUT_DATE": (
        "The assessment email doesn't give a date, so no slot was booked.",
        "Check the email for the assessment date or deadline, then {manual}.",
    ),
    "ASSESSMENT_WINDOW_CLOSED": (
        "The time allowed for this assessment has already ended, so no slot was booked.",
        "No action needed, unless the deadline was extended. If it was, {manual}.",
    ),
    "ASSESSMENT_TIME_PASSED": (
        "The assessment time has already passed, so no slot was booked.",
        NO_ACTION,
    ),
    "ASSESSMENT_WINDOW_TOO_SHORT": (
        "The time allowed is shorter than the assessment itself, so no slot fits.",
        "Check the dates in the email, and if the assessment is still needed, {manual}.",
    ),
    "ASSESSMENT_SLOT_CONFLICT": (
        "The assessment time is already taken by another booking, so no slot was booked.",
        "Check the candidate's bookings, then {manual}.",
    ),
    "ASSESSMENT_NO_FREE_SLOT": (
        "There's no free time before the assessment deadline, so no slot was booked.",
        "Free up time in the candidate's bookings, or {manual}.",
    ),
}

_UNEXPECTED = (
    "Something went wrong while handling this email, so {result}.",
    _RETRY_THEN_MANUAL,
)

# Only reached when a notification carries no usable validator code.
_BY_REASON_CODE: dict[str, tuple[str, str]] = {
    DUPLICATE_BOOKING: _BY_INTERNAL_CODE["DUPLICATE_BOOKING"],
    MISSING_DATE_TIME: _UNCLEAR_SCHEDULE,
    PAST_INTERVIEW_DATE: _BY_INTERNAL_CODE["PAST_INTERVIEW"],
    NO_MATCHING_SLOT: _BY_INTERNAL_CODE["SLOT_CONFLICT"],
    CANDIDATE_NOT_FOUND: _BY_INTERNAL_CODE["CANDIDATE_MAPPING_FAILED"],
    ROUND_NOT_FOUND: (
        "This email couldn't be matched to a single existing booking, so {result}.",
        "Open the candidate in Daily Ops and {verb} the right booking by hand.",
    ),
    LOW_CONFIDENCE: _BY_INTERNAL_CODE["LOW_CONFIDENCE"],
    INCOMPLETE_EVIDENCE: _BY_INTERNAL_CODE["AI_NOT_VALIDATED"],
    DUPLICATE_INVITE: (
        "This invite was already received, so it wasn't booked again.",
        NO_ACTION,
    ),
    AI_RETRY_PENDING: _UNEXPECTED,
    BOOKING_NOT_SAVED: _BY_INTERNAL_CODE["BOOKING_NOT_PERSISTED"],
    STALE_INTERVIEW_EVENT: _BY_INTERNAL_CODE["STALE_INTERVIEW_EVENT"],
    MANUAL_REVIEW_REQUIRED: _BY_INTERNAL_CODE["AI_REQUIRES_REVIEW"],
}

# The payment check reports one of these instructions, written by
# `candidate_store.slot_confirm_block_reason`; each is already a plain
# instruction, so it is used as the action rather than paraphrased.
_OWNER_MISSING = "Assign an owner"
_PAYMENT_SHORT = "Record at least"
_REQUIREMENT_INSTRUCTIONS = (
    _OWNER_MISSING, _PAYMENT_SHORT, "Confirm the slot screenshot", "Set the interview date",
)


def _requirements(detail: Any) -> tuple[str, str]:
    """PAYMENT_VALIDATION_FAILED, which is not always about payment.

    The same check refuses a slot when nobody owns the candidate, and every
    such alert used to say "Payment is not cleared".
    """
    instruction = str(detail or "").strip()
    if instruction.startswith(_OWNER_MISSING):
        reason = "No owner is assigned to this candidate yet, so {result}."
    elif instruction.startswith(_PAYMENT_SHORT):
        reason = "The candidate's payment is below the amount needed for a booking, so {result}."
    else:
        reason = "The candidate's booking requirements aren't met yet, so {result}."
    if instruction.startswith(_REQUIREMENT_INSTRUCTIONS):
        # Braces in stored text must not be read as template fields.
        first = instruction.replace("{", "{{").replace("}", "}}")
    else:
        first = "Update the candidate's payment and owner details."
    return reason, f"{first} {_RETRY} If it's still not done, {{manual}}."


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

    if internal_key in {"BOOKING_NOT_FOUND", "BOOKING_AMBIGUOUS"}:
        parts = _UPDATES_AN_EXISTING_BOOKING[
            (internal_key, "cancel" if outcome == "cancel" else "change")
        ]
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
