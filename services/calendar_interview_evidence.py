"""Deterministic resolution for contradictory calendar-invite relevance.

The model decides ordinary mail relevance.  This module only resolves the
small, auditable case where that decision contradicts an already authenticated
RFC5545 invitation addressed to the monitored candidate.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from services.calendar_invite_parser import employer_invitation_shape


_MARKETING = re.compile(
    r"\b(webinar|workshop|masterclass|bootcamp|open\s+day|career\s+fair|"
    r"newsletter|training\s+(?:session|program|course)?|public\s+event)\b",
    re.I,
)
_HIRING = re.compile(
    r"\b(interview|discussion|round|screening|assessment|recruit(?:er|ment)?|"
    r"hiring|candidate|shortlist(?:ed)?|selection)\b",
    re.I,
)
_ROLE = re.compile(
    r"\b(developer|engineer|devops|consultant|analyst|architect|tester|qa|"
    r"administrator|specialist|sre|java|python|servicenow|react)\b",
    re.I,
)


def _text(calendar_result: Mapping[str, Any], message: Mapping[str, Any]) -> str:
    interview = calendar_result.get("interview") or {}
    job = calendar_result.get("job") or {}
    calendar = calendar_result.get("calendar") or {}
    return "\n".join(
        str(value or "")
        for value in (
            message.get("subject"), message.get("body"), message.get("html_body"),
            job.get("title"), interview.get("round"), calendar.get("summary"),
            calendar_result.get("summary"), calendar_result.get("evidence_summary"),
        )
    )


def _address(value: Any) -> str:
    return str(value or "").strip().lower()


def _count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def evidence_for(
    calendar_result: Mapping[str, Any] | None,
    message: Mapping[str, Any] | None,
) -> dict[str, bool]:
    """Return explainable evidence flags; never infer a schedule here."""
    result = calendar_result or {}
    mail = message or {}
    interview = result.get("interview") or {}
    calendar = result.get("calendar") or {}
    text = _text(result, mail)
    trusted_request = (
        str(result.get("calendar_validation_status") or "").upper() == "TRUSTED"
        and str(calendar.get("method") or "").upper() in {"REQUEST", "CANCEL"}
        and bool(calendar.get("uid"))
        and bool(calendar.get("has_dtend"))
        and bool(interview.get("date") or str(result.get("classification") or "") == "interview_cancelled")
        and bool(interview.get("meeting_link"))
    )
    # The same test the parser applies when a trusted invite carries no
    # interview word. The result's own recruiter and candidate are the
    # addresses the parser authenticated; the message is only a fallback.
    recruiter = result.get("recruiter") or {}
    candidate = result.get("candidate") or {}
    employer_invitation = employer_invitation_shape(
        _address(recruiter.get("email") or mail.get("sender_email")),
        _address(candidate.get("email") or mail.get("recipient_email")),
        _count(calendar.get("attendee_count")),
    )
    return {
        "trusted_request": trusted_request,
        "hiring_context": bool(_HIRING.search(text)),
        "role_context": bool(_ROLE.search(text)),
        "clear_marketing": bool(_MARKETING.search(text)),
        "employer_invitation": employer_invitation,
    }


def contradiction_resolution(
    relevance: Mapping[str, Any],
    calendar_result: Mapping[str, Any] | None,
    message: Mapping[str, Any] | None,
) -> str:
    """Return ``BOOK``, ``IGNORE`` or ``RETRY`` for a contradictory result.

    Clear marketing always wins.  Otherwise, an authenticated calendar request
    with a meeting link is stronger evidence than a self-contradictory model
    label when either the text names hiring or a role, or the invitation has
    the employer shape the parser itself accepted -- an outside, non-consumer
    organisation inviting this candidate to a small meeting -- and no
    marketing word appears anywhere.  Anything short of that stays retryable
    rather than disappearing.
    """
    evidence = evidence_for(calendar_result, message)
    decision = str(relevance.get("decision") or "").upper()
    kind = str(relevance.get("message_kind") or "").upper()
    contradictory = (
        (decision == "NOT_ESTABLISHED" and kind == "RECIPIENT_HIRING_PROCESS")
        or (decision == "ESTABLISHED" and kind != "RECIPIENT_HIRING_PROCESS")
    )
    if not contradictory:
        return "IGNORE"
    if evidence["clear_marketing"] and kind in {
        "MARKETING_OR_TRAINING", "PUBLIC_EVENT", "NEWSLETTER", "JOB_ADVERTISEMENT",
    }:
        return "IGNORE"
    if evidence["trusted_request"] and (evidence["hiring_context"] or evidence["role_context"]):
        return "BOOK"
    # Recruiters title an invite by the candidate as often as by the role:
    # "<candidate>-TR1", "Call for <candidate>". Neither word list can match a
    # name, and an Exchange invite's body is Teams boilerplate, so both flags
    # stayed false and the mail retried until its attempts ran out. Two real
    # invites went that way: one arrived a day ahead and was still parked when
    # the interview took place, the other was exhausted after twelve attempts.
    #
    # `trusted_request` already means the parser accepted the invitation as
    # an interview, and with no interview word it accepted it on exactly this
    # shape. Re-asking with a word list overruled its own answer. A marketing
    # word anywhere still refuses, whatever the model labelled the mail.
    if (
        evidence["trusted_request"]
        and evidence["employer_invitation"]
        and not evidence["clear_marketing"]
    ):
        return "BOOK"
    return "RETRY"
