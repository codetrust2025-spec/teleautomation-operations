"""Booking-side automation contract for recruitment mail.

Claude decides *what a mail means*.  This module deliberately does not score
mail, infer relevance, or inspect model prompts.  It consumes that final
decision and gives the mailbox worker one durable, non-human state machine.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping


class AutomationState(StrEnum):
    AUTO_BOOK = "AUTO_BOOK"
    AUTO_IGNORE = "AUTO_IGNORE"
    AI_RETRY_PENDING = "AI_RETRY_PENDING"
    AUTO_BOOKED = "AUTO_BOOKED"
    AUTO_CANCELLED = "AUTO_CANCELLED"
    AUTO_RESCHEDULED = "AUTO_RESCHEDULED"


DECISION_STATES = frozenset({
    AutomationState.AUTO_BOOK,
    AutomationState.AUTO_IGNORE,
    AutomationState.AI_RETRY_PENDING,
})
OUTCOME_STATES = frozenset({
    AutomationState.AUTO_BOOKED,
    AutomationState.AUTO_CANCELLED,
    AutomationState.AUTO_RESCHEDULED,
})
ALL_STATES = DECISION_STATES | OUTCOME_STATES
ACTIONABLE_CLASSIFICATIONS = frozenset({
    "interview_confirmed", "interview_rescheduled", "interview_cancelled",
})
LEGACY_UNCERTAIN_STATUSES = frozenset({
    "MANUAL_REVIEW_REQUIRED", "NEEDS_REVIEW", "REVIEW_REQUIRED",
    "VALIDATION_FAILED", "AI_FAILED_TERMINAL", "IGNORED_LOW_CONFIDENCE",
    "OFFER_NEEDS_REVIEW", "JOINING_NEEDS_REVIEW", "SELECTION_NEEDS_REVIEW",
})


def normalize_analysis(value: dict[str, Any]) -> dict[str, Any]:
    """Close every legacy validator exit without approving its evidence.

    Raw model payloads/audit history are retained by the caller. This changes
    the operational destination only: an unresolved reading must be retried,
    never approved by an operator and never silently treated as marketing.
    """
    status = _text(value.get('status') or value.get('primary_status'))
    pending = (
        status in LEGACY_UNCERTAIN_STATUSES | {'AI_RETRY_PENDING'}
        or _text(value.get('validation_status')) in LEGACY_UNCERTAIN_STATUSES
        or classification_of(value) in {'needs_review', 'ai_retry_pending'}
        or bool(value.get('requires_manual_review') or value.get('manual_review_required'))
    )
    if pending:
        reason = (value.get('ignore_reason') or value.get('backend_validation_reason')
                  or next(iter(value.get('risk_flags') or []), None) or 'UNRESOLVED_AI_DECISION')
        value.update(status='AI_RETRY_PENDING', primary_status='AI_RETRY_PENDING',
                     classification='ai_retry_pending', candidate_status='AI Retry Pending',
                     automation_decision='AI_RETRY_PENDING', automation_state='AI_RETRY_PENDING',
                     validation_status='RETRY_PENDING', ai_status='AI_RETRY_PENDING',
                     requires_manual_review=False, manual_review_required=False,
                     should_create_review_record=False, is_selection_or_offer_related=False,
                     backend_transition_validated=False, lifecycle_event='NONE', interview_event='NONE',
                     ignore_reason=reason,
                     recommended_action='Automatic evidence validation will retry; no operator action is required.')
    else:
        value['automation_decision'] = decision_for(value).value
        value['requires_manual_review'] = False
    return value


def _text(value: Any) -> str:
    return str(value or "").strip().upper()


def classification_of(result: Mapping[str, Any]) -> str:
    return str(
        result.get("classification") or result.get("primary_status") or result.get("status") or ""
    ).strip().lower().replace("-", "_").replace(" ", "_")


def decision_for(result: Mapping[str, Any]) -> AutomationState:
    """Return the only permitted automated decision for a stored result.

    A supplied decision is Claude's authority.  Compatibility handling never
    upgrades uncertain legacy rows into a booking: they become retryable until
    Claude emits a final decision.  Established, actionable legacy results are
    accepted only when they contain source evidence; booking performs the
    stricter deterministic source/payment/candidate/lifecycle checks later.
    """
    explicit = _text(result.get("automation_decision") or result.get("booking_decision"))
    if explicit in {state.value for state in DECISION_STATES}:
        return AutomationState(explicit)

    # A reading kept as an alert without a quoted sentence proving it books
    # nothing, and is not retried either: it is final as an alert.
    if (_text(result.get("backend_validation_reason")) == "EVIDENCE_NOT_ENTAILED"
            or _text(result.get("ai_validation_status")) == "EVIDENCE_NOT_ENTAILED"):
        return AutomationState.AUTO_IGNORE

    status = _text(result.get("primary_status") or result.get("status"))
    validation = _text(result.get("validation_status"))
    classification = classification_of(result)
    if (
        bool(result.get("requires_manual_review"))
        or status in LEGACY_UNCERTAIN_STATUSES
        or validation in LEGACY_UNCERTAIN_STATUSES
        or classification == "needs_review"
    ):
        return AutomationState.AI_RETRY_PENDING
    if classification in ACTIONABLE_CLASSIFICATIONS:
        return (
            AutomationState.AUTO_BOOK
            if bool(result.get("evidence"))
            else AutomationState.AI_RETRY_PENDING
        )
    return AutomationState.AUTO_IGNORE


def apply_decision(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return a projection with no human-review action required."""
    value = dict(result)
    decision = decision_for(value)
    value["automation_decision"] = decision.value
    value["requires_manual_review"] = False
    value["automation_managed"] = True
    if decision is AutomationState.AI_RETRY_PENDING:
        value["automation_state"] = decision.value
        value["validation_status"] = "RETRY_PENDING"
        value["ai_status"] = decision.value
    return value


def outcome_for(booking_outcome: Mapping[str, Any]) -> AutomationState:
    """Translate an executor result without ever claiming an unpersisted slot."""
    status = _text(booking_outcome.get("status"))
    if status in {"AUTO BOOKED", "APPROVED & BOOKED", "ALREADY PROCESSED"}:
        return AutomationState.AUTO_BOOKED
    if status == "RESCHEDULED":
        return AutomationState.AUTO_RESCHEDULED
    if status == "CANCELLED":
        return AutomationState.AUTO_CANCELLED
    # A stale lifecycle replay or exact duplicate is terminal and has no work
    # left to do.  All other deterministic/infrastructure failures stay in the
    # automatic queue; a payment or candidate record can become valid later.
    if _text(booking_outcome.get("failure_code")) in {
        "STALE_INTERVIEW_EVENT", "DUPLICATE_BOOKING", "NOT_ACTIONABLE", "PAST_INTERVIEW",
    }:
        return AutomationState.AUTO_IGNORE
    return AutomationState.AI_RETRY_PENDING
