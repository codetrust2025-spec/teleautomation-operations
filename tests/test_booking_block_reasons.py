"""A blocked booking says what happened, why, and what to do, in plain words.

Reason codes and validator codes are for branching and for debugging. A person
reading Mail Alerts sees a title, a reason and what to do; the codes are one
click away under Technical details and nowhere else.

These tests hold every code the system can store to that, including the ones
production alerts actually carry. They hold the words to everyday English --
one short sentence each, none of the words people had to stop and decode --
and they hold the words to the decision behind them. Every stored
ROUND_NOT_FOUND alert said "Interview round could not be identified" about a
cancellation that matched no booking, or several, and NOT_ACTIONABLE promised
"a safe retry" for an outcome nothing retries. An explanation that reads well
and says the wrong thing is the failure this file exists to catch.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

from core import recruitment_mail_store as mail_store
from features import candidate_store
from services import booking_block_reasons as reasons
from services.recruitment_automation import AutomationState, outcome_for

ROOT = pathlib.Path(__file__).resolve().parents[1]

# ROUND_NOT_FOUND, BOOKING_AMBIGUOUS, PAST_INTERVIEW_DATE ...
RAW_CODE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
# Words that were accurate and still made people read a sentence twice.
UNPLAIN = re.compile(
    r"\b(conflicting|applied|ambiguous|actionable|lifecycle|persisted|evidence|"
    r"mapping|validator|stale)\b",
    re.IGNORECASE,
)
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
MAX_WORDS = 20

CLASSIFICATIONS = ["interview_confirmed", "interview_rescheduled", "interview_cancelled"]
TITLES = {
    "Booking not created", "Booking not updated", "Cancellation not applied",
    "Already booked", "Assessment not booked",
}
RETRY_PROMISE = "try again automatically"
GENERIC_REASON = reasons.explain("NEVER_SEEN_BEFORE")["reason"]


def _validator_codes() -> list[str]:
    """Every code interview booking can raise, read from the source itself."""
    tree = ast.parse((ROOT / "services" / "interview_auto_booking.py").read_text(encoding="utf-8"))
    codes = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", "")) == "BookingValidationError"
                and node.args and isinstance(node.args[0], ast.Constant)):
            codes.add(node.args[0].value)
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == "failure_code"
                        and isinstance(value, ast.Constant)):
                    codes.add(value.value)
    return sorted(codes)


def _assessment_codes() -> list[str]:
    """Every code the assessment scheduler can store, read from its source."""
    tree = ast.parse((ROOT / "services" / "assessment_schedule.py").read_text(encoding="utf-8"))
    return sorted({
        keyword.value.value
        for node in ast.walk(tree) if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "reason_code" and isinstance(keyword.value, ast.Constant) and keyword.value.value
    })


def _candidate(**overrides):
    row = {
        "name": "Synthetic Candidate", "slots_group_posted": True, "reference": "Owner One",
        "payment": 0, "expected_payment": 20000, "service_type": "profile_service", "date": "2099-01-01",
    }
    row.update(overrides)
    return row


def _requirement_instructions() -> dict[str, str]:
    """What the payment check says for each thing it can be waiting for."""
    paid = {"payment": 20000}
    return {
        "screenshot": candidate_store.slot_confirm_block_reason(_candidate(slots_group_posted=False)),
        "owner": candidate_store.slot_confirm_block_reason(_candidate(reference="")),
        "payment": candidate_store.slot_confirm_block_reason(_candidate()),
        "date": candidate_store.slot_confirm_block_reason(_candidate(date="", **paid)),
        "other": "Something the check says in future.",
    }


def _every_explanation():
    """Every explanation the module can give, labelled by what produced it."""
    for classification in CLASSIFICATIONS:
        for code in sorted(reasons._INTERNAL_TO_REASON):
            yield (code, classification), reasons.explain(
                reasons.reason_code_for(code), code, classification=classification,
                date="2026-08-03", time="16:30",
            )
        for code in sorted(set(reasons._INTERNAL_TO_REASON.values())
                           | {reasons.DUPLICATE_INVITE, reasons.MANUAL_REVIEW_REQUIRED}):
            yield (code, classification), reasons.explain(code, classification=classification)
        for kind, instruction in _requirement_instructions().items():
            yield ("requirement", kind, classification), reasons.explain(
                "PAYMENT_NOT_CLEARED", "PAYMENT_VALIDATION_FAILED",
                classification=classification, detail=instruction,
            )
    for code in _assessment_codes() + ["CANDIDATE_MAPPING_FAILED"]:
        yield (code, "assessment"), reasons.explain(code, candidate_status="Assessment Needs a Slot")
    for value in (None, "", "KEYERROR", "SOME_NEW_VALIDATOR_BRANCH"):
        yield ("unexpected", value), reasons.explain(reasons.reason_code_for(value), value)


def _words(text: str) -> int:
    # The interview time in brackets is a detail, not wording to read.
    return len(re.sub(r"\s*\([^)]*\)", "", text).split())


def _plain(explanation: dict) -> None:
    for part in ("title", "reason", "action"):
        text = explanation[part]
        assert text and text.strip() == text, (part, text)
        assert not RAW_CODE.search(text), f"{part} shows a code: {text!r}"
        assert "{" not in text and "}" not in text, f"unfilled template in {part}: {text!r}"
    assert explanation["title"] in TITLES
    for part in ("reason", "action"):
        text = explanation[part]
        assert not UNPLAIN.search(text), f"{part} uses {UNPLAIN.search(text).group(0)!r}: {text!r}"
        assert text.endswith("."), (part, text)
        assert len(SENTENCE_END.findall(text)) == 1, f"{part} is more than one sentence: {text!r}"
        assert _words(text) <= MAX_WORDS, f"{part} is {_words(text)} words: {text!r}"


# ── The decision is untouched ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "internal_code,expected_code",
    [
        ("DUPLICATE_BOOKING", "DUPLICATE_BOOKING"),
        ("SLOT_CONFLICT", "NO_MATCHING_SLOT"),
        ("INVALID_DATE", "MISSING_DATE_TIME"),
        ("INVALID_TIME", "MISSING_DATE_TIME"),
        ("MISSING_TIMEZONE", "MISSING_DATE_TIME"),
        ("INVALID_TIMEZONE", "MISSING_DATE_TIME"),
        ("INVALID_END_TIME", "MISSING_DATE_TIME"),
        ("INVALID_DURATION", "MISSING_DATE_TIME"),
        ("CROSS_DAY_INTERVIEW", "MISSING_DATE_TIME"),
        ("MEDIUM_CONFIDENCE_INCOMPLETE", "MISSING_DATE_TIME"),
        ("HISTORICAL_SCHEDULE_INCOMPLETE", "MISSING_DATE_TIME"),
        ("INCOMPLETE_SCHEDULE", "MISSING_DATE_TIME"),
        ("PAST_INTERVIEW", "PAST_INTERVIEW_DATE"),
        ("CANDIDATE_MAPPING_FAILED", "CANDIDATE_NOT_FOUND"),
        ("BOOKING_NOT_FOUND", "ROUND_NOT_FOUND"),
        ("BOOKING_AMBIGUOUS", "ROUND_NOT_FOUND"),
        ("LOW_CONFIDENCE", "LOW_CONFIDENCE"),
        ("MISSING_EVIDENCE", "INCOMPLETE_EVIDENCE"),
        ("AI_NOT_VALIDATED", "INCOMPLETE_EVIDENCE"),
        ("PAYMENT_VALIDATION_FAILED", "PAYMENT_NOT_CLEARED"),
        ("AI_REQUIRES_REVIEW", "AI_RETRY_PENDING"),
        ("AUTO_BOOKING_DISABLED", "AI_RETRY_PENDING"),
        ("NOT_ACTIONABLE", "AI_RETRY_PENDING"),
        ("BOOKING_NOT_PERSISTED", "BOOKING_NOT_SAVED"),
        ("STALE_INTERVIEW_EVENT", "STALE_INTERVIEW_EVENT"),
    ],
)
def test_the_reason_codes_are_the_ones_they_were(internal_code, expected_code):
    """Only the words changed. Anything branching on a code sees the same code."""
    described = reasons.describe(internal_code)
    assert described["reason_code"] == expected_code
    # The exact validator branch survives the translation.
    assert described["internal_code"] == internal_code


def test_an_unmapped_code_still_falls_back_to_automatic_retry():
    # An unknown deterministic failure remains visible and is retried; it is
    # never silently discarded or routed to a human approval queue.
    described = reasons.describe("SOME_NEW_VALIDATOR_BRANCH")
    assert described["reason_code"] == "AI_RETRY_PENDING"
    assert described["internal_code"] == "SOME_NEW_VALIDATOR_BRANCH"
    assert RETRY_PROMISE in reasons.explain("AI_RETRY_PENDING", "SOME_NEW_VALIDATOR_BRANCH")["action"]


def test_no_validator_code_is_left_without_a_mapping():
    """A new block would otherwise silently read as the generic reason. This
    caught INCOMPLETE_SCHEDULE only after it had already reached Production."""
    unmapped = sorted(set(_validator_codes()) - set(reasons._INTERNAL_TO_REASON))
    assert not unmapped, f"these blocking codes have no reason code: {unmapped}"


# ── Every code reads plainly ────────────────────────────────────────────────

def test_every_explanation_is_plain_short_english():
    """Every title, reason and action the module can produce: no code, none
    of the words that had to be decoded, one sentence, at most twenty words."""
    seen = 0
    for label, explanation in _every_explanation():
        try:
            _plain(explanation)
        except AssertionError as failure:
            raise AssertionError(f"{label}: {failure}") from None
        seen += 1
    # Every code, in every classification, plus each requirement and fallback.
    assert seen > 100


@pytest.mark.parametrize("code", _validator_codes())
def test_every_validator_code_has_its_own_explanation(code):
    """Not the catch-all: each block a validator raises says what it means."""
    explanation = reasons.explain(reasons.reason_code_for(code), code, classification="interview_confirmed")
    assert explanation["reason"] != GENERIC_REASON


def test_the_assessment_codes_are_the_scheduler_s_own():
    assert _assessment_codes() == sorted(reasons._ASSESSMENT)


@pytest.mark.parametrize("code", _assessment_codes() + ["CANDIDATE_MAPPING_FAILED"])
def test_every_assessment_block_reads_as_an_assessment(code):
    explanation = reasons.explain(code, candidate_status="Assessment Needs a Slot")
    assert explanation["title"] == "Assessment not booked"
    assert explanation["reason"] != GENERIC_REASON


@pytest.mark.parametrize("value", [None, "", "   ", "KEYERROR", "OPERATIONALERROR"])
def test_an_unexpected_failure_still_reads_plainly(value):
    """An exception's class name is stored as the code; nobody should read it."""
    explanation = reasons.explain(reasons.reason_code_for(value), value)
    _plain(explanation)
    assert explanation["reason"] == "Something went wrong while processing this email, so no booking was created."
    assert RETRY_PROMISE in explanation["action"]


# ── The words asked for ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "reason_code,internal_code,classification,reason",
    [
        ("STALE_INTERVIEW_EVENT", "STALE_INTERVIEW_EVENT", "interview_cancelled",
         "A newer interview update was already processed, so this older email was ignored."),
        ("ROUND_NOT_FOUND", "BOOKING_AMBIGUOUS", "interview_cancelled",
         "We found more than one booking for this candidate, so we could not tell which "
         "interview to cancel."),
        ("PAST_INTERVIEW_DATE", "PAST_INTERVIEW", "interview_confirmed",
         "This interview time has already passed, so no booking was created."),
        ("MANUAL_REVIEW_REQUIRED", "AI_REQUIRES_REVIEW", "interview_confirmed",
         "We could not confirm the interview details automatically."),
        ("ROUND_NOT_FOUND", "BOOKING_NOT_FOUND", "interview_cancelled",
         "We could not find an active booking that matches this cancellation."),
    ],
)
def test_the_requested_sentences_are_the_ones_shown(reason_code, internal_code, classification, reason):
    assert reasons.explain(reason_code, internal_code, classification=classification)["reason"] == reason


def test_a_person_is_asked_to_review_a_mail_nothing_could_confirm():
    explanation = reasons.explain("MANUAL_REVIEW_REQUIRED", "AI_REQUIRES_REVIEW",
                                  classification="interview_confirmed")
    assert explanation["action"].startswith("Please review the email")


# ── The words match the decision ────────────────────────────────────────────

@pytest.mark.parametrize("code", sorted(reasons._INTERNAL_TO_REASON))
def test_only_outcomes_that_retry_promise_a_retry(code):
    """NOT_ACTIONABLE said "queued for a safe retry" while outcome_for made it
    final. Whatever outcome_for decides, the action must agree."""
    final = outcome_for({"status": "Blocked", "failure_code": code}) == AutomationState.AUTO_IGNORE
    for classification in CLASSIFICATIONS:
        action = reasons.explain(reasons.reason_code_for(code), code, classification=classification)["action"]
        if final:
            assert RETRY_PROMISE not in action, (code, action)


@pytest.mark.parametrize("code", sorted(reasons._INTERNAL_TO_REASON))
def test_a_cancellation_never_reads_as_a_booking_that_was_not_created(code):
    explanation = reasons.explain(reasons.reason_code_for(code), code, classification="interview_cancelled")
    assert "no booking was created" not in explanation["reason"]
    assert "book it in Daily Ops" not in explanation["action"]
    assert explanation["title"] in {"Cancellation not applied", "Already booked"}


@pytest.mark.parametrize("code", ["BOOKING_NOT_FOUND", "BOOKING_AMBIGUOUS"])
def test_matching_an_existing_booking_is_never_called_a_round_problem(code):
    for classification in CLASSIFICATIONS:
        explanation = reasons.explain("ROUND_NOT_FOUND", code, classification=classification)
        assert "round" not in (explanation["reason"] + explanation["action"]).lower()


@pytest.mark.parametrize("code", ["BOOKING_NOT_FOUND", "BOOKING_AMBIGUOUS"])
def test_a_cancellation_that_matched_nothing_never_points_at_a_booking(code):
    """In all three alerts production held on 19 Sep 2026 the interview being
    cancelled had no booking, while the candidate had others. "Cancel the right
    booking by hand" sent a person looking for one that did not exist."""
    action = reasons.explain("ROUND_NOT_FOUND", code, classification="interview_cancelled")["action"]
    assert action.startswith("If this interview is")
    assert "cancel it there" in action


def test_a_confirmation_that_was_really_a_revision_reads_as_a_change():
    # A re-sent calendar UID turns a confirmation into a reschedule inside the
    # booking service, which is the only way a confirmation can fail to match
    # an existing booking.
    explanation = reasons.explain("ROUND_NOT_FOUND", "BOOKING_AMBIGUOUS", classification="interview_confirmed")
    assert explanation["title"] == "Booking not updated"
    assert "which interview to update" in explanation["reason"]


def test_a_past_interview_is_not_waved_through():
    """Both past-interview alerts production held on 19 Sep 2026 were real
    interviews that never reached Daily Ops -- one invite arrived a minute
    before the start -- and both said "No action needed"."""
    explanation = reasons.explain("PAST_INTERVIEW_DATE", "PAST_INTERVIEW", classification="interview_confirmed")
    assert explanation["action"] == "If the interview took place, make sure it's in Daily Ops."
    assert explanation["needs_action"] is True


def test_an_old_review_alert_does_not_ask_for_a_second_booking():
    """The one production held had been booked by hand since it was written."""
    action = reasons.explain("MANUAL_REVIEW_REQUIRED", "AI_REQUIRES_REVIEW",
                             classification="interview_confirmed")["action"]
    assert "if needed" in action
    assert "book it" not in action


def test_already_booked_never_names_the_email_s_time():
    """The duplicate check matches on the calendar event or the source mail,
    and a booking that moved keeps both, so the email's time can be one the
    booking no longer has."""
    explanation = reasons.explain("DUPLICATE_BOOKING", "DUPLICATE_BOOKING",
                                  classification="interview_confirmed", date="2026-09-03", time="14:30")
    assert explanation["reason"] == "This interview is already booked, so it was not booked again."
    assert explanation["title"] == "Already booked"


def test_nothing_to_do_is_marked_as_nothing_to_do():
    assert reasons.explain("DUPLICATE_BOOKING", "DUPLICATE_BOOKING")["needs_action"] is False
    assert reasons.explain("STALE_INTERVIEW_EVENT", "STALE_INTERVIEW_EVENT",
                           classification="interview_cancelled")["needs_action"] is False
    assert reasons.explain("ROUND_NOT_FOUND", "BOOKING_AMBIGUOUS",
                           classification="interview_cancelled")["needs_action"] is True


# ── The alerts production holds ─────────────────────────────────────────────

# Every combination stored on a production alert on 19 Sep 2026: the reason
# code, the validator code and the email's classification.
PRODUCTION = [
    ("ROUND_NOT_FOUND", "BOOKING_NOT_FOUND", "interview_cancelled", "Cancellation not applied",
     "We could not find an active booking that matches this cancellation.",
     "If this interview is still in Daily Ops, cancel it there."),
    ("ROUND_NOT_FOUND", "BOOKING_AMBIGUOUS", "interview_cancelled", "Cancellation not applied",
     "We found more than one booking for this candidate, so we could not tell which interview to cancel.",
     "If this interview is in Daily Ops, cancel it there."),
    ("DUPLICATE_BOOKING", "DUPLICATE_BOOKING", "interview_confirmed", "Already booked",
     "This interview is already booked, so it was not booked again.",
     "No action needed."),
    ("PAST_INTERVIEW_DATE", "PAST_INTERVIEW", "interview_confirmed", "Booking not created",
     "This interview time has already passed, so no booking was created.",
     "If the interview took place, make sure it's in Daily Ops."),
    ("STALE_INTERVIEW_EVENT", "STALE_INTERVIEW_EVENT", "interview_cancelled", "Cancellation not applied",
     "A newer interview update was already processed, so this older email was ignored.",
     "No action needed."),
    ("NO_MATCHING_SLOT", "SLOT_CONFLICT", "interview_confirmed", "Booking not created",
     "This time clashed with another booking for this candidate, so no booking was created.",
     "Check the candidate's bookings, and book it in Daily Ops if it's missing."),
    ("MANUAL_REVIEW_REQUIRED", "AI_REQUIRES_REVIEW", "interview_confirmed", "Booking not created",
     "We could not confirm the interview details automatically.",
     "Please review the email and update Daily Ops if needed."),
]


@pytest.mark.parametrize("reason_code,internal_code,classification,title,reason,action", PRODUCTION)
def test_every_alert_production_holds_reads_as_what_happened(
    reason_code, internal_code, classification, title, reason, action,
):
    explanation = reasons.explain(reason_code, internal_code, classification=classification)
    _plain(explanation)
    assert (explanation["title"], explanation["reason"], explanation["action"]) == (title, reason, action)


def test_the_codes_stay_available_for_debugging():
    explanation = reasons.explain(
        "ROUND_NOT_FOUND", "BOOKING_AMBIGUOUS", classification="interview_cancelled",
        detail="Multiple active interview slots match this candidate.", booking_status="Blocked",
    )
    assert explanation["technical"] == {
        "reason_code": "ROUND_NOT_FOUND",
        "internal_code": "BOOKING_AMBIGUOUS",
        "booking_status": "Blocked",
        "message": "Multiple active interview slots match this candidate.",
    }


def test_the_dashboard_fixture_is_what_the_backend_says():
    """MailMonitoringNotifications.test.jsx renders this fixture, so the screen's
    tests fail here first if the backend's wording moves on without it."""
    fixture = json.loads(
        (ROOT / "dashboard" / "src" / "components" / "__fixtures__" / "blockedBookingNotification.json")
        .read_text(encoding="utf-8")
    )
    assert reasons.explain_notification(fixture["row"]) == fixture["booking_block"]


# ── Payment is not always the requirement ───────────────────────────────────

def test_a_missing_owner_is_not_called_a_payment_problem():
    instruction = _requirement_instructions()["owner"]
    explanation = reasons.explain("PAYMENT_NOT_CLEARED", "PAYMENT_VALIDATION_FAILED", detail=instruction)
    assert "owner" in explanation["reason"]
    assert "paid" not in explanation["reason"]
    assert explanation["action"] == "Assign an owner to the candidate, then we'll try again automatically."
    # The check's own words stay available.
    assert explanation["technical"]["message"] == instruction


def test_a_short_payment_says_how_much_is_needed():
    instruction = _requirement_instructions()["payment"]
    explanation = reasons.explain("PAYMENT_NOT_CLEARED", "PAYMENT_VALIDATION_FAILED", detail=instruction)
    assert "paid" in explanation["reason"]
    amount = re.search(r"₹[\d,]+", instruction).group(0)
    assert explanation["action"] == f"Record at least {amount} as received, then we'll try again automatically."
    assert RETRY_PROMISE in explanation["action"]


@pytest.mark.parametrize("kind,words", [("screenshot", "screenshot"), ("date", "interview date")])
def test_each_requirement_names_itself(kind, words):
    instruction = _requirement_instructions()[kind]
    explanation = reasons.explain("PAYMENT_NOT_CLEARED", "PAYMENT_VALIDATION_FAILED", detail=instruction)
    assert words in explanation["reason"] and words in explanation["action"]


def test_stored_text_cannot_break_the_template():
    explanation = reasons.explain("PAYMENT_NOT_CLEARED", "PAYMENT_VALIDATION_FAILED",
                                  detail="Record at least {amount} received.")
    _plain(explanation)
    assert explanation["action"] == "Record the candidate's payment, then we'll try again automatically."


# ── The stored sentence and the shown one agree ─────────────────────────────

@pytest.mark.parametrize("code", sorted(reasons._INTERNAL_TO_REASON))
def test_the_stored_sentence_is_the_one_shown(code):
    schedule = {"date": "2026-08-03", "time": "16:30"}
    stored = reasons.describe(code, schedule=schedule, classification="interview_confirmed")
    shown = reasons.explain(stored["reason_code"], stored["internal_code"],
                            classification="interview_confirmed", date="2026-08-03", time="16:30")
    assert stored["reason"] == shown["reason"]


def test_the_reason_names_the_time_where_it_matters():
    described = reasons.describe("SLOT_CONFLICT", schedule={"date": "2026-08-03", "time": "16:30"})
    assert described["reason"] == (
        "This time (3 Aug 2026, 4:30 PM) clashed with another booking for this candidate, "
        "so no booking was created."
    )


def test_the_raw_ai_schedule_is_used_when_validation_never_normalized_one():
    # A schedule that failed to parse leaves only the model's 12-hour reading.
    described = reasons.describe(
        "PAST_INTERVIEW", interview={"date": "2026-08-03", "time": "4:30 PM"},
    )
    assert "(3 Aug 2026, 4:30 PM)" in described["reason"]


def test_a_normalized_schedule_wins_over_the_raw_extraction():
    described = reasons.describe(
        "PAST_INTERVIEW",
        schedule={"date": "2026-08-03", "time": "16:30"},
        interview={"date": "2026-09-09", "time": "9:00 AM"},
    )
    assert "3 Aug 2026, 4:30 PM" in described["reason"]


def test_reasons_that_a_time_would_not_clarify_stay_plain():
    described = reasons.describe(
        "LOW_CONFIDENCE", schedule={"date": "2026-08-03", "time": "16:30"},
    )
    assert "2026" not in described["reason"]


def test_an_unusable_schedule_leaves_the_reason_unqualified():
    for schedule in ({}, {"date": "not-a-date"}, {"date": "2026-13-40"}):
        described = reasons.describe("SLOT_CONFLICT", schedule=schedule)
        assert described["reason"] == (
            "This time clashed with another booking for this candidate, so no booking was created."
        )


def test_a_date_without_a_usable_time_still_names_the_day():
    assert reasons.format_schedule("2026-08-03", None) == "3 Aug 2026"
    assert reasons.format_schedule("2026-08-03", "half past four") == "3 Aug 2026"
    assert reasons.format_schedule("2026-08-03", "99:99") == "3 Aug 2026"


@pytest.mark.parametrize(
    "time,expected",
    [
        ("00:05", "3 Aug 2026, 12:05 AM"),
        ("12:00", "3 Aug 2026, 12:00 PM"),
        ("23:59", "3 Aug 2026, 11:59 PM"),
        ("9:00 AM", "3 Aug 2026, 9:00 AM"),
        ("12:00 AM", "3 Aug 2026, 12:00 AM"),
        ("4:30 pm", "3 Aug 2026, 4:30 PM"),
    ],
)
def test_both_clock_formats_read_the_same_to_a_user(time, expected):
    assert reasons.format_schedule("2026-08-03", time) == expected


# ── Mail Alerts carries the explanation ─────────────────────────────────────

def test_every_alert_read_carries_its_explanation():
    """Rebuilt on every read, so alerts written with the old sentences read the
    new way without a data migration."""
    fixture = json.loads(
        (ROOT / "dashboard" / "src" / "components" / "__fixtures__" / "blockedBookingNotification.json")
        .read_text(encoding="utf-8")
    )
    blocked = dict(fixture["row"])
    delivered = {"classification": "interview_confirmed", "booking_status": None, "booking_id": None}
    rows = mail_store.reconcile_booking_claims([blocked, delivered])
    assert rows[0]["booking_block"] == fixture["booking_block"]
    # The stored codes stay on the row, untouched.
    assert rows[0]["booking_block_reason_code"] == "ROUND_NOT_FOUND"
    # A row with nothing blocked comes back as it went in.
    assert "booking_block" not in rows[1]
