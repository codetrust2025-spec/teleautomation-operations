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
#: The body is cut here so the prompt always stays inside the 8192-token
#: context. Replayed over 972 production mails (13 Nov 2026), every mail it
#: could not answer was a 15,000+ character job-alert digest: the overflow
#: pushed the instructions out and the model summarised the jobs instead.
#: Shorter mails -- all but a handful -- reach the model exactly as tested.
MAX_BODY_CHARS = 12000
#: Verified calendar invites keep booking as before (owner, 13 Nov 2026): their
#: date and time are in the invite, which Prompt 12 never sees, and a screening
#: call or HR discussion is a meeting operators book. Prompt 12 decides
#: everything else.
CALENDAR_SOURCES = frozenset({"ICALENDAR_VERIFIED"})

_LABEL = re.compile(r"\b(SCHEDULED|CANCELED|CANCELLED|OTHER)\b")
_REPLY_SUBJECT = re.compile(r"^\s*(?:(?:re|fw|fwd|aw|wg)\s*(?:\[\d+\])?\s*:\s*)+", re.I)
#: Where a reading may draw the proof for a booking: the mail itself. Older
#: messages of the thread (THREAD_CONTEXT) and the subject a reply inherits
#: are history -- they show what was arranged before, not what this mail says.
CURRENT_SOURCES = frozenset({"EMAIL_BODY", "ATTACHMENT"})


def model() -> str:
    """qwen2.5:7b unless PROMPT12_MODEL names another (an owner decision)."""
    return (os.getenv("PROMPT12_MODEL") or MODEL_DEFAULT).strip()


def user_message(subject: Any, body: Any) -> str:
    return f"SUBJECT: {subject or ''}\n\nBODY:\n{str(body or '')[:MAX_BODY_CHARS]}"


def parse(raw: str) -> str | None:
    """The one label the model gave, or None when it gave none or several."""
    found = {label.replace("CANCELLED", "CANCELED") for label in _LABEL.findall(str(raw or "").upper())}
    return found.pop() if len(found) == 1 else None


def classify(subject: Any, body: Any, *, timeout: float | None = None,
             deadline_monotonic: float | None = None) -> dict[str, Any]:
    """Run Prompt 12 on one mail. Raises AIGatewayError only when the model is unreachable.

    An answer that is not exactly one of the three labels is filed OTHER, with
    `unparsed` set and the raw answer kept: at temperature 0 and a fixed seed
    a retry repeats the same text, and a mail nobody could read as an
    interview must never become one.
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
    return {
        "verdict": verdict or "OTHER",
        "unparsed": verdict is None,
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


def is_reply_subject(subject: Any) -> bool:
    """RE:/FW:/Fwd: -- a subject carried over from an earlier message of the thread."""
    return bool(_REPLY_SUBJECT.match(str(subject or "")))


def split_evidence(result: dict[str, Any], subject: Any = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(current, historical) evidence of a reading.

    Current: quoted from this mail's own body or attachments, or from its
    subject when the subject is its own. Historical: quoted from earlier
    messages of the thread, or from a reply's inherited subject. Historical
    evidence is kept and labelled -- it records what was arranged before --
    but it can never be the reason to book, cancel or move a slot.
    """
    current: list[dict[str, Any]] = []
    historical: list[dict[str, Any]] = []
    for item in result.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").upper()
        if source in CURRENT_SOURCES or (source == "EMAIL_SUBJECT" and not is_reply_subject(subject)):
            current.append(item)
        else:
            historical.append(item)
    return current, historical


def _mark_history(result: dict[str, Any], record: dict[str, Any], subject: Any) -> bool:
    """Label historical evidence; True when the reading has proof in this mail."""
    current, historical = split_evidence(result, subject)
    for item in historical:
        item["historical"] = True
    if historical:
        record["historical_evidence"] = [
            {"source": item.get("source"), "text": str(item.get("text") or "")[:300]} for item in historical
        ]
    return bool(current)


def verified_reschedule(result: dict[str, Any]) -> bool:
    """A reading that moves an existing interview to a new slot the reader proved.

    The details reader must say RESCHEDULED -- or CONFIRMED with the original
    slot recorded, which is how it describes a moved interview -- and must have
    proved the new date and time (`ai_validation_status` VALIDATED, the
    backend transition check passed). A mail that merely mentions a time, or
    whose new slot was not proved, is not one.
    """
    status = _status(result)
    interview = result.get("interview") or {}
    moved = status == "INTERVIEW_RESCHEDULED" or (
        status == "INTERVIEW_CONFIRMED"
        and bool(interview.get("original_date") or interview.get("original_time"))
    )
    return bool(
        moved
        and interview.get("date") and interview.get("time")
        and str(result.get("ai_validation_status") or "").upper() == "VALIDATED"
        and result.get("backend_transition_validated") is True
    )


def apply_verdict(result: dict[str, Any], verdict_record: dict[str, Any], *, subject: Any = None) -> str:
    """Make the analysis say what Prompt 12 said. Returns what happened.

    - OTHER: an interview reading is dropped (no interview alert, no booking).
      A non-interview reading -- selection, offer, joining, BGV -- is left to
      its own workflow untouched.
    - SCHEDULED / CANCELED: the mail is that interview event, whatever the
      other reader called it. When the other reader agrees (same interview
      status), its details stand and booking runs exactly as before. When it
      does not, the alert still appears but is marked not bookable, so no slot
      is booked or released on a reading nobody confirmed: a person acts.

    The one exception: an OTHER that the details reader proved is a move of an
    existing interview to a new slot (`verified_reschedule`) keeps the reading
    and its booking move.

    Threads: a booking, release or move needs proof in this mail. A reading
    whose only evidence is history -- earlier messages of the thread or a
    reply's inherited subject -- is never bookable, and never keeps the
    reschedule exception; that evidence is kept, labelled historical.

    Returns "dropped", "unchanged", "agreed", "unconfirmed" or "reschedule_kept".
    """
    record = dict(verdict_record)
    verdict = record.get("verdict")
    if verdict not in OUTCOMES:
        raise ValueError(f"not a Prompt 12 verdict: {verdict!r}")
    result["prompt12"] = record
    status = _status(result)
    classification = str(result.get("classification") or "").lower()

    has_current_proof = _mark_history(result, record, subject)
    if verdict == "OTHER":
        if verified_reschedule(result) and has_current_proof:
            # The one exception (owner, 13 Nov 2026): a mail that moves an
            # existing interview to a new, verified slot keeps moving the
            # booking even when Prompt 12 calls it OTHER. Replayed, Prompt 12
            # called both reschedule notices of Sep 2026 OTHER; left alone the
            # roster would have kept the old time.
            record["reschedule_exception"] = True
            result["prompt12"] = record
            result["classification"] = _CLASSIFICATION_FOR[status]
            return "reschedule_kept"
        if status in INTERVIEW_STATUSES or classification in INTERVIEW_CLASSIFICATIONS:
            result.update(
                is_selection_or_offer_related=False, should_create_review_record=False,
                interview_event="NONE", ignore_reason="PROMPT12_OTHER",
            )
            return "dropped"
        return "unchanged"

    agreed = status in VERDICT_STATUSES[verdict] and has_current_proof
    if status in VERDICT_STATUSES[verdict] and not has_current_proof:
        record["only_historical_evidence"] = True
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
    structured = structured or {}
    if str(structured.get("classification_source") or "").upper() in CALENDAR_SOURCES:
        return classification in VERDICT_CLASSIFICATIONS["SCHEDULED"] | VERDICT_CLASSIFICATIONS["CANCELED"]
    prompt12 = structured.get("prompt12") or {}
    if prompt12.get("reschedule_exception"):
        return classification in VERDICT_CLASSIFICATIONS["SCHEDULED"]
    verdict = prompt12.get("verdict")
    return classification in VERDICT_CLASSIFICATIONS.get(verdict, frozenset())
