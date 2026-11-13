"""Prompt 12: the single authority for whether a mail is an interview.

The owner's tested prompt, word for word (13 Nov 2026), run as it was tested
on 12 Nov: qwen2.5:7b, temperature 0, seed 12, an 8192-token context, the
prompt as the system message and the mail as "SUBJECT: ...\\n\\nBODY:\\n...".
It answers one of three words:

- SCHEDULED -- an individual interview is booked with a definite date and time;
- CANCELED  -- an interview is explicitly cancelled;
- OTHER     -- everything else: shortlists, assessments, screening requests,
               recruiter outreach, application acknowledgements, OTPs, account
               mails, job alerts, undated or proposed meetings.

Nothing else may turn a mail into an interview. Which interview details a
SCHEDULED mail carries (date, time, zone, link) is read elsewhere and can
never change this verdict.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any

from core.ai_gateway import AIGatewayError, chat

PROMPT12 = """You are an expert interview email classifier. Read the complete SUBJECT and BODY.

Apply these rules strictly in order:
1. CANCELED: If the interview is explicitly canceled in the subject or body, return CANCELED. Cancellation overrides any original invitation details.
2. SCHEDULED: Return SCHEDULED only when the email affirmatively books or invites the candidate to a specific interview with BOTH an exact date and time.
3. OTHER: Return OTHER for shortlists, assessments, screening requests, recruiter outreach, undated meetings, and proposed interviews awaiting scheduling.
4. IMPORTANT: An interview drive announcement, walk-in recruitment event, or invitation requiring registration, an application, or additional shortlisting is OTHER, even if the event date is given. An individual interview must already be booked with a definite date AND time to qualify as SCHEDULED. A proposed date/time asking whether the candidate is available is OTHER.

Important: "Would you be available on [date] at [time]?" is OTHER, even if a meeting link is attached. A confirmed interview invitation with stated date/time remains SCHEDULED even if RSVP is requested.

Examples:
- "Canceled: Round 1 Interview" => CANCELED
- "Your L1 interview is scheduled for 19 Sep at 5 PM" => SCHEDULED
- "Would you be available for a video call on 21 Sep at 2 PM?" => OTHER

Return ONLY: SCHEDULED, CANCELED, or OTHER."""

#: Pinned so that any edit to the wording fails a test instead of shipping.
PROMPT12_SHA256 = hashlib.sha256(PROMPT12.encode("utf-8")).hexdigest()
PROMPT_NAME = "prompt12"
PROMPT_VERSION = "prompt12@" + PROMPT12_SHA256[:12]

OUTCOMES = ("SCHEDULED", "CANCELED", "OTHER")
#: The settings it was tested with. Not configurable on purpose: a different
#: seed or context is a different classifier.
MODEL_DEFAULT = "qwen2.5:7b"
SEED = 12
NUM_CTX = 8192

_LABEL = re.compile(r"\b(SCHEDULED|CANCELED|CANCELLED|OTHER)\b")


def model() -> str:
    """qwen2.5:7b unless PROMPT12_MODEL names another (an owner decision)."""
    return (os.getenv("PROMPT12_MODEL") or MODEL_DEFAULT).strip()


def user_message(subject: Any, body: Any) -> str:
    return f"SUBJECT: {subject or ''}\n\nBODY:\n{body or ''}"


def parse(raw: str) -> str | None:
    """The one label the model gave, or None when it gave none or several."""
    found = {label.replace("CANCELLED", "CANCELED") for label in _LABEL.findall(str(raw or "").upper())}
    return found.pop() if len(found) == 1 else None


def classify(subject: Any, body: Any, *, timeout: float | None = None,
             deadline_monotonic: float | None = None) -> dict[str, Any]:
    """Run Prompt 12 on one mail. Raises AIGatewayError when there is no clear answer.

    An answer that is not exactly one of the three labels is not guessed at:
    it raises PROMPT12_UNPARSED, which the caller treats like any other
    unreadable result (the mail is retried, never filed on a guess).
    """
    started = time.monotonic()
    response = chat(
        messages=[{"role": "system", "content": PROMPT12},
                  {"role": "user", "content": user_message(subject, body)}],
        model=model(), temperature=0, seed=SEED, num_ctx=NUM_CTX,
        timeout=timeout, deadline_monotonic=deadline_monotonic,
        workload="interview_prompt12", max_retries=1,
    )
    verdict = parse(response.content)
    if verdict is None:
        raise AIGatewayError(
            f"Prompt 12 gave no single label: {str(response.content or '')[:80]!r}",
            code="PROMPT12_UNPARSED",
        )
    return {
        "verdict": verdict,
        "raw": str(response.content or "").strip()[:200],
        "model": response.model,
        "node": getattr(response, "node_id", "") or "",
        "prompt_name": PROMPT_NAME,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": PROMPT12_SHA256,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


# ── The verdict decides; nothing downstream may disagree ─────────────────────

#: Every status that would put a mail in Interview Related. Prompt 12 alone
#: decides whether a mail may carry one of them.
INTERVIEW_STATUSES = frozenset({
    "INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED",
    "INTERVIEW_SHORTLISTED", "INTERVIEW_UPDATE", "INTERVIEW_PROPOSED",
    "FINAL_ROUND_CLEARED", "SHORTLISTED",
})
INTERVIEW_CLASSIFICATIONS = frozenset({
    "interview_confirmed", "interview_rescheduled", "interview_cancelled",
    "interview_shortlisted", "post_interview_shortlisted", "final_round_cleared",
    "interview_update",
})
#: The interview statuses each verdict allows, and the classification it files under.
VERDICT_STATUSES = {
    "SCHEDULED": frozenset({"INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED"}),
    "CANCELED": frozenset({"INTERVIEW_CANCELLED"}),
}
VERDICT_CLASSIFICATIONS = {
    "SCHEDULED": frozenset({"interview_confirmed", "interview_rescheduled"}),
    "CANCELED": frozenset({"interview_cancelled"}),
}
_CLASSIFICATION_FOR = {
    "INTERVIEW_CONFIRMED": "interview_confirmed",
    "INTERVIEW_RESCHEDULED": "interview_rescheduled",
    "INTERVIEW_CANCELLED": "interview_cancelled",
}
#: Shared with recruitment_mail_agent: a reading kept as an alert that may not
#: book, release or move anything.
NOT_BOOKABLE = "EVIDENCE_NOT_ENTAILED"


def _status(result: dict[str, Any]) -> str:
    return str(result.get("primary_status") or result.get("status") or "").upper()


def apply_verdict(result: dict[str, Any], verdict_record: dict[str, Any]) -> str:
    """Make the analysis say what Prompt 12 said. Returns what happened.

    - OTHER: an interview reading is dropped (no interview alert, no booking).
      A non-interview reading -- selection, offer, joining, BGV -- is left to
      its own workflow untouched.
    - SCHEDULED / CANCELED: the mail is that interview event, whatever the
      other reader called it. When the other reader agrees (same interview
      status), its details stand and booking runs exactly as before. When it
      does not, the alert still appears but is marked not bookable, so no slot
      is booked or released on a reading nobody confirmed: a person acts.

    Returns "dropped", "unchanged", "agreed" or "unconfirmed".
    """
    record = dict(verdict_record)
    verdict = record.get("verdict")
    if verdict not in OUTCOMES:
        raise ValueError(f"not a Prompt 12 verdict: {verdict!r}")
    result["prompt12"] = record
    status = _status(result)
    classification = str(result.get("classification") or "").lower()

    if verdict == "OTHER":
        if status in INTERVIEW_STATUSES or classification in INTERVIEW_CLASSIFICATIONS:
            result.update(
                is_selection_or_offer_related=False, should_create_review_record=False,
                interview_event="NONE", ignore_reason="PROMPT12_OTHER",
            )
            return "dropped"
        return "unchanged"

    agreed = status in VERDICT_STATUSES[verdict]
    target = status if agreed else ("INTERVIEW_CONFIRMED" if verdict == "SCHEDULED" else "INTERVIEW_CANCELLED")
    result.update(
        status=target, primary_status=target, classification=_CLASSIFICATION_FOR[target],
        interview_event=target, business_domain="INTERVIEW_TRACKING",
        is_selection_or_offer_related=True, should_create_review_record=True,
        ignore_reason=None,
    )
    if agreed:
        return "agreed"
    result.update(
        backend_transition_validated=False, backend_validation_reason=NOT_BOOKABLE,
        ai_validation_status=NOT_BOOKABLE, requires_manual_review=False,
        prompt12_details_unconfirmed=True,
        reason=(f"Prompt 12: {verdict}. The interview details were not confirmed by the "
                "details reader, so nothing was booked or released automatically."),
    )
    return "unconfirmed"


def verdict_allows(classification: str, structured: dict[str, Any] | None) -> bool:
    """Whether a new Interview Related alert of this classification may be created."""
    verdict = ((structured or {}).get("prompt12") or {}).get("verdict")
    return classification in VERDICT_CLASSIFICATIONS.get(verdict, frozenset())
