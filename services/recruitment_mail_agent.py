"""Precision-first selection and offer email detection."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import time
from copy import deepcopy
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any

from core import ollama_nodes
from core import recruitment_mail_store as store
from core.ai_gateway import AIGatewayError, chat_structured, configured_models
from services.recruitment_semantics import (
    DOCUMENT_TYPES,
    EMAIL_INTENTS,
    classify_context,
    evidence_entails_transition,
    extract_interview_schedule,
    redact_sensitive_text,
    validate_interview_event,
    validate_lifecycle_event,
)
from core.pure_ollama_policy import pure_ollama_enabled

logger = logging.getLogger(__name__)


def _publish(event_type: str, **payload: Any) -> None:
    try:
        from core.recruitment_realtime import publish
        publish(event_type, **payload)
    except Exception:
        # Persistence remains the source of truth; real-time delivery is best
        # effort and API recovery will return missed notifications.
        logger.debug("Mail real-time event unavailable type=%s", event_type, exc_info=True)


def _publish_ignored_interview(
    mailbox: dict[str, Any],
    decoded: dict[str, Any],
    attachments: list[dict[str, Any]] | None,
    status: str,
    reason: str,
) -> dict[str, Any] | None:
    """Surface a mail that was dropped while carrying an interview.

    Both the duplicate check and the routing filter drop a message by returning
    early, which wrote a processing_status on the row and nothing else — no
    notification, nothing on any screen. An invite could therefore disappear
    with no trace an operator would ever see, which is exactly how a Sourcebae
    invite for a 4:15pm interview went missing.

    Only messages that still parse as an interview are surfaced, so ordinary
    marketing noise being filtered does not become operator work. Returns the
    interview signal it found, for callers that want to log it.
    """
    try:
        from services.calendar_invite_parser import trusted_interview_result

        signal = trusted_interview_result(decoded, list(attachments or []))
    except Exception:  # a broken parser must not break ingestion
        logger.debug("Interview signal check failed", exc_info=True)
        return None
    if not signal:
        return None

    logger.warning(
        "Interview-bearing mail dropped candidate=%s status=%s reason=%s subject=%r date=%s time=%s",
        mailbox.get("candidate_id"), status, reason,
        str(decoded.get("subject") or "")[:120],
        signal.get("interview_date"), signal.get("interview_time"),
    )
    _publish(
        "interview_mail_ignored",
        candidate_id=mailbox.get("candidate_id"),
        gmail_message_id=decoded.get("provider_message_id"),
        subject=decoded.get("subject"),
        processing_status=status,
        reason=reason,
        interview_date=signal.get("interview_date"),
        interview_time=signal.get("interview_time"),
    )
    return signal


def _failure_review_result(message: dict[str, Any], exc: Exception) -> dict[str, Any]:
    code = getattr(exc, "code", None) or type(exc).__name__
    context = classify_context(
        str(message.get("subject") or ""), str(message.get("body") or ""),
        sender_email=str(message.get("sender_email") or ""),
        sent_at=message.get("sent_at"), attachments=message.get("attachments") or [],
    )
    is_deterministic_noise = bool(
        context["is_questionnaire"] or context["is_promotional_or_job_ad"]
        or context["is_historical_information"] or context.get("is_question")
    )
    if is_deterministic_noise:
        # The deterministic filter already conclusively classified this
        # message as non-actionable noise (ad/questionnaire/historical
        # document/question). Ollama is not required to reject noise, so an
        # AI outage must never force this into Needs Review or retry-pending
        # — it goes straight to the same audit-only path a healthy AI run
        # would have produced.
        return {
            "schema_version": "selection_offer_event_v1",
            "is_recruitment_related": True,
            "is_selection_or_offer_related": False,
            "should_create_review_record": False,
            "status": "IGNORED_NOT_OFFER_RELATED",
            "primary_status": "IGNORED_NOT_OFFER_RELATED",
            "classification": "not_relevant",
            "candidate_status": "Profile Active",
            "confidence": 0.0,
            "ignore_reason": context["email_intent"],
            "reason": f"Deterministic noise filter classified this email as {context['email_intent']}; AI analysis was not required.",
            "candidate": {"name": None, "email": message.get("recipient_email")},
            "company": {"name": None, "domain": None},
            "job": {"title": None, "employment_type": None, "location": None},
            "recruiter": {"name": message.get("sender_name"), "email": message.get("sender_email")},
            "interview": {key: None for key in ("date", "time", "end_time", "duration_minutes", "timezone", "mode", "round", "location", "meeting_link")},
            "offer": {"offer_detected": False, "offer_letter_detected": False,
                      "appointment_letter_detected": False, "offer_date": None,
                      "offered_ctc": None, "currency": None, "joining_date": None,
                      "offer_expiry_date": None},
            "attachments": [], "evidence": [], "risk_flags": [],
            "requires_manual_review": False,
            "summary": context["evidence_summary"],
            "recommended_action": "No action required; this message was classified as non-actionable noise.",
            "classification_source": "DETERMINISTIC_NOISE_FILTER",
            "ai_validation_status": "NOT_REQUIRED",
            "ai_status": "NOT_REQUIRED",
            "validation_status": "NOT_REQUIRED",
            "email_intent": context["email_intent"],
            "document_type": context["document_type"],
            "is_candidate_specific": context["is_candidate_specific"],
            "is_job_outcome": False,
            "is_current_event": False,
            "is_questionnaire": context["is_questionnaire"],
            "is_promotional_or_job_ad": context["is_promotional_or_job_ad"],
            "is_historical_information": context["is_historical_information"],
            "historical_employment_evidence": context["historical_employment_evidence"],
            "lifecycle_event": "NONE",
            "interview_event": "NONE",
            "business_domain": "NONE",
            "evidence_summary": context["evidence_summary"],
        }
    return {
        "schema_version": "selection_offer_event_v1",
        "is_recruitment_related": True,
        "is_selection_or_offer_related": True,
        "should_create_review_record": True,
        "status": "MANUAL_REVIEW_REQUIRED",
        "primary_status": "MANUAL_REVIEW_REQUIRED",
        "classification": "needs_review",
        "candidate_status": "Needs Review",
        "confidence": 0.0,
        "ignore_reason": None,
        "reason": f"AI validation unavailable ({code})",
        "candidate": {"name": None, "email": message.get("recipient_email")},
        "company": {"name": None, "domain": None},
        "job": {"title": None, "employment_type": None, "location": None},
        "recruiter": {"name": message.get("sender_name"), "email": message.get("sender_email")},
        "interview": {key: None for key in ("date", "time", "end_time", "duration_minutes", "timezone", "mode", "round", "location", "meeting_link")},
        "offer": {"offer_detected": False, "offer_letter_detected": False,
                  "appointment_letter_detected": False, "offer_date": None,
                  "offered_ctc": None, "currency": None, "joining_date": None,
                  "offer_expiry_date": None},
        "attachments": [], "evidence": [], "risk_flags": ["AI_VALIDATION_UNAVAILABLE"],
        "requires_manual_review": True,
        "summary": "AI validation is unavailable; this recruitment email requires administrator review.",
        "recommended_action": "Review the email metadata and evidence, then retry AI analysis or correct the result manually.",
        "classification_source": "FAILURE_REVIEW",
        "ai_validation_status": "RETRY_PENDING",
        "ai_status": "RETRY_PENDING",
        "validation_status": "RETRY_PENDING",
        "email_intent": context["email_intent"],
        "document_type": context["document_type"],
        "is_candidate_specific": context["is_candidate_specific"],
        "is_job_outcome": False,
        "is_current_event": False,
        "is_questionnaire": context["is_questionnaire"],
        "is_promotional_or_job_ad": context["is_promotional_or_job_ad"],
        "is_historical_information": context["is_historical_information"],
        "historical_employment_evidence": context["historical_employment_evidence"],
        "lifecycle_event": "NONE",
        "evidence_summary": "AI analysis could not complete. No candidate lifecycle event was created; retry is pending.",
    }

VISIBLE_STATUSES = [
    "SELECTED", "FINAL_SELECTION_CONFIRMED", "FINAL_ROUND_CLEARED", "OFFER_INDICATION",
    "OFFER_IN_PROGRESS", "OFFER_APPROVED", "OFFER_LETTER_RECEIVED", "OFFER_RECEIVED",
    "APPOINTMENT_LETTER_RECEIVED", "OFFER_ACCEPTED", "JOINING_CONFIRMED",
    "JOINED", "POST_SELECTION_ONBOARDING", "OFFER_DECLINED", "OFFER_REVOKED",
    "JOINING_DATE_UPDATED", "BACKGROUND_VERIFICATION", "DOCUMENT_VERIFICATION",
    "HR_CONFIRMATION", "COMPENSATION_CONFIRMATION", "INTERVIEW_UPDATE", "INTERVIEW_SHORTLISTED",
    "ASSESSMENT_INVITED", "INTERVIEW_PROPOSED", "OFFER_NEEDS_REVIEW", "JOINING_NEEDS_REVIEW", "SELECTION_NEEDS_REVIEW",
    "INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED",
    "CANDIDATE_REJECTED", "MANUAL_REVIEW_REQUIRED",
]
INTERNAL_STATUSES = ["IGNORED_NOT_OFFER_RELATED", "IGNORED_LOW_CONFIDENCE"]
STATUSES = VISIBLE_STATUSES + INTERNAL_STATUSES
TRACKED_STATUSES = set(VISIBLE_STATUSES)
OFFER_CASE_STATUSES = {
    "OFFER_INDICATION", "OFFER_IN_PROGRESS", "OFFER_APPROVED",
    "OFFER_LETTER_RECEIVED", "OFFER_RECEIVED", "APPOINTMENT_LETTER_RECEIVED", "OFFER_ACCEPTED",
    "JOINING_CONFIRMED", "JOINED", "POST_SELECTION_ONBOARDING",
}

STATUS_PRIORITY = [
    "JOINED", "JOINING_CONFIRMED", "POST_SELECTION_ONBOARDING",
    "OFFER_ACCEPTED", "APPOINTMENT_LETTER_RECEIVED", "OFFER_LETTER_RECEIVED", "OFFER_RECEIVED",
    "OFFER_APPROVED", "OFFER_IN_PROGRESS", "FINAL_SELECTION_CONFIRMED",
    "OFFER_REVOKED", "OFFER_DECLINED", "JOINING_DATE_UPDATED",
    "BACKGROUND_VERIFICATION", "DOCUMENT_VERIFICATION", "HR_CONFIRMATION",
    "COMPENSATION_CONFIRMATION", "CANDIDATE_REJECTED",
    "INTERVIEW_CANCELLED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CONFIRMED",
    "FINAL_ROUND_CLEARED", "SELECTED", "OFFER_INDICATION", "INTERVIEW_SHORTLISTED", "INTERVIEW_UPDATE", "SHORTLISTED",
]

STATUS_SIGNALS = [
    ("JOINED", ("welcome aboard", "welcome to the organization", "reported for joining", "joined the company", "employment commenced")),
    ("JOINING_CONFIRMED", ("your date of joining will be", "your joining date is", "date of joining", "expected joining date", "please join on", "report for joining on", "reporting date", "joining is confirmed", "joining confirmed", "welcome to the team")),
    ("POST_SELECTION_ONBOARDING", ("employee onboarding", "post-selection onboarding", "complete onboarding formalities", "complete pre-joining formalities", "pre-joining formalities", "onboarding has started", "complete onboarding before joining")),
    ("OFFER_ACCEPTED", ("offer acceptance", "accepted your offer", "accept the offer", "offer has been accepted")),
    ("OFFER_DECLINED", ("declined the offer", "offer has been declined", "will not accept the offer")),
    ("OFFER_REVOKED", ("offer has been revoked", "withdrawn the offer", "offer stands withdrawn", "offer is rescinded")),
    ("JOINING_DATE_UPDATED", ("revised joining date", "joining date has changed", "joining date has moved", "updated date of joining", "new joining date")),
    ("BACKGROUND_VERIFICATION", ("background verification", "pre-employment verification", "background check", "digital employment", "digiverifier", "bgv_", "loa accepetence", "loa acceptance")),
    ("HR_CONFIRMATION", ("minimal documents", "capgemini documenation", "capgemini documentation", "documents required for offer", "documents required - ey", "documents required for onboarding", "pre-offer documents", "pre-offer document", "uan number and updated cv", "post selection document", "ltimindtree selection process - pre-offer")),
    ("COMPENSATION_CONFIRMATION", ("compensation confirmation", "confirmed compensation", "annual ctc is", "salary package is")),
    ("FINAL_ROUND_CLEARED", ("cleared the final round", "cleared all rounds", "cleared the technical round", "successfully cleared the l1", "successfully cleared the l2", "cleared the l1 round", "cleared the l2 round", "cleared the l1", "cleared the l2", "cleared l1", "cleared l2", "final round cleared")),
    # Shortlisting is an outcome in its own right, not only a preamble to an
    # interview. These phrases used to require the word "interview" right after
    # "shortlisted for", so a plain selection mail — "your profile is
    # provisionally shortlisted for Python Django with <company>" — matched
    # nothing here, fell through to the model, and was ignored for carrying no
    # interview date or time. Every phrase below states the selection outcome
    # explicitly, so a bare document request with no such wording still cannot
    # reach this status.
    ("INTERVIEW_SHORTLISTED", (
        "shortlisted for the next interview", "shortlisted for the technical interview",
        "shortlisted for hr interview",
        "provisionally shortlisted", "profile is shortlisted",
        "profile has been shortlisted", "profile is provisionally shortlisted",
        "you have been shortlisted", "you are shortlisted",
        "we have shortlisted your", "shortlisted for the role",
        "shortlisted for the position", "candidature has been shortlisted",
        "candidature has been provisionally shortlisted",
        "shortlisted for further discussion", "shortlisted for hr discussion",
        "moved forward to the next stage", "moving forward to the hr round",
    )),
    # These phrases route interview mail to Ollama but never determine the
    # actionable outcome. Only the validated contextual model may upgrade an
    # update to confirmed/rescheduled/cancelled.
    ("INTERVIEW_UPDATE", ("interview invitation", "interview scheduled", "interview has been scheduled", "interview confirmed", "interview rescheduled", "interview cancelled", "technical interview", "technical round", "managerial interview", "hr interview", "hr round")),
    ("CANDIDATE_REJECTED", ("regret to inform", "not moving forward", "not selected for the role", "application was unsuccessful")),
    ("APPOINTMENT_LETTER_RECEIVED", ("appointment letter attached", "letter of appointment", "appointment letter")),
    ("OFFER_LETTER_RECEIVED", (
        "offer letter attached", "find your offer letter",
        "offer letter has been released", "offer has been released",
        "offer has been successfully released", "employment offer attached",
        "offer of employment", "offer letter inside",
        "congratulations, you're in! offer letter", "deployment with", "fulltime with",
    )),
    ("OFFER_APPROVED", ("offer has been approved", "offer is approved", "offer approved")),
    ("OFFER_IN_PROGRESS", ("offer is currently being processed", "processing your offer", "offer is being prepared", "offer under preparation")),
    ("FINAL_SELECTION_CONFIRMED", ("final selection confirmed", "selection has been confirmed", "finally selected")),
    ("SELECTED", ("you have been selected", "you are selected", "selected for the position", "selected for the role", "congratulations on your selection", "selected for the post", "selection confirmation", "shortlisted for offer", "shortlisted for the offer")),
    ("OFFER_INDICATION", ("we are pleased to offer you", "we are delighted to offer you", "we would like to offer you", "planning to release your offer", "intent to offer", "employment offer", "compensation offered", "annual ctc offered")),
    ("SHORTLISTED", ("you have been shortlisted", "being shortlisted", "shortlisted for the role", "shortlisted for the position")),
]

NOISE_RULES = [
    ("JOB_RECOMMENDATION", ("job recommendation", "recommended jobs", "jobs matching your profile", "new jobs for you", "jobs for you", "similar jobs", "suggested opportunities")),
    ("JOB_ALERT", ("job alert", "hiring alert", "featured jobs", "new openings", "daily job", "weekly job")),
    ("JOB_PORTAL_MARKETING", ("apply now", "increase profile visibility", "upgrade account", "premium subscription", "career newsletter", "unsubscribe")),
    ("PROFILE_NOTIFICATION", ("resume viewed", "profile viewed", "searched your profile")),
    ("APPLICATION_UPDATE", ("application received", "thank you for applying", "application submitted", "application under review")),
    ("ASSESSMENT", ("assessment invitation", "coding test invitation", "complete the assessment")),
    ("INTERVIEW", ("interview invitation", "interview scheduled", "interview has been scheduled", "interview rescheduled", "interview reminder", "interview cancelled", "technical round", "hr round")),
    ("REJECTION", ("not selected", "regret to inform", "not moving forward", "rejection")),
]

# A job board links every advert it lists. Two or more distinct postings in one
# mail is a catalogue, whoever sent it and however it is worded.
_JOB_POSTING_LINK = re.compile(
    r"(?:linkedin\.com/comm)?/jobs/view/(\d+)"          # LinkedIn
    r"|naukri\.com/job-listings-[\w-]*?(\d{6,})"        # Naukri
    r"|/job(?:s)?/(\d{6,})/(?:apply|view)",             # Indeed / Monster shapes
    re.I,
)

# Wording a catalogue uses about itself. Kept as a secondary signal only: the
# exact phrasing changes without notice — LinkedIn writes "Jobs that match your
# profile" while NOISE_RULES only knew "jobs matching your profile", and that
# one word is why a six-advert digest was sent to the model as though it might
# be news about this candidate.
_JOB_DIGEST_MARKERS = (
    "jobs that match your profile", "jobs matching your profile",
    "jobs picked for you", "jobs for you", "recommended jobs",
    "based on your title and location", "view job:", "see all jobs",
    "similar jobs", "job alert", "new jobs posted",
)

_JOB_BOARD_SENDERS = (
    "jobs-noreply@linkedin.com", "jobalerts-noreply@linkedin.com",
    "jobs-listings@linkedin.com", "info@naukri.com", "alerts@naukri.com",
    "jobalerts@naukri.com", "noreply@indeed.com", "alert@indeed.com",
    "no-reply@monsterindia.com", "jobs@shine.com",
)

# Aggregators and career-marketing platforms. No employer lifecycle mail
# originates from these domains: they send "your profile was shortlisted",
# "jobs found for you" and profile-completion nags, phrased exactly like a real
# status update because that is what makes them worth opening.
#
# Matched by domain, not address. The list above is by address, which is why it
# missed every one of these in production: it names `jobs@shine.com` while the
# mail actually arrives from `alerts@jobs.shine.com`, and talent500 alone sent
# 29 of them from `aditi@talent500.co`. Any new mailbox at the same company
# would have needed its own entry.
#
# Deliberately excluded, because these are real senders whose mail must keep
# flowing: employer domains, and applicant-tracking systems such as ripplehire,
# curatal, myworkday, ambitionhire and wecreateproblems. An ATS relays genuine
# employer decisions and is not an aggregator.
_JOB_BOARD_DOMAINS = (
    "talent500.co",
    "timesjobs.com",
    "shine.com",
    "indeed.com",
    "naukri.com",
    "monsterindia.com",
    "abekus.co",
    "yocket.in",
    # foundit sends "Your CV was downloaded", which a model reads as a
    # selection: it came back job_selection_confirmed three times in the first
    # 140 messages of the July rescan. It was the largest single sender still
    # reaching inference, at 49 messages.
    "foundit.in",
    "ziprecruiter.in",
    "ambitionbox.com",
    # Found by benchmarking the verifier: Jobrapido's "A new company is showing
    # interest in your profile" was the one false positive qwen3:14b let
    # through as job_selection_confirmed. An aggregator that survives to the
    # last gate should not have reached the first one.
    "jobrapido.com",
    "jobrapidoalert.com",
    "instahyre.com",
    "hirist.tech",
)

# Not job boards - banks, travel sites, telcos, course marketing. They reach
# inference only because they say nothing about jobs either way, so routing has
# no reason to refuse them, and then the classifier is asked to judge a credit
# card offer as a career event. It answers, because the schema requires an
# answer.
#
# This list is separate from the one above on purpose: they are reviewed
# against different questions. "Is this an aggregator?" and "is this a company
# we would ever hear from about a candidate?" are not the same test, and
# merging them makes both harder to audit.
#
# Every entry here was read from the July-August population with a sample
# subject, never inferred from the name. Employer and ATS domains that look
# like noise are deliberately absent: google.com carries Google's own
# recruiting, and read.ai carries interview meeting notes.
_SERVICE_NOISE_DOMAINS = (
    "bankbazaar.com",
    "icici.bank.in",
    "easemytrip.com",
    "jio.com",
    "cdr.bsnl.co.in",
    "miteshkhatri.com",
    "namastedev.com",
    "students.udemy.com",
    "email.openai.com",
    "infomails.microsoft.com",
    "hackingflix.com",
    "hyrefast.io",
    "talenttitanletters.com",
    # Second pass over the long tail of the same population. The first pass
    # read the high-volume senders and stopped; resumeworded sends one mail a
    # week and produced a live false Selection alert within 105 messages of
    # starting the July run - "CS#367: I love being nervous (+ more)" came back
    # job_selection_confirmed.
    "resumeworded.com",
    "money.hindustantimes.com",
    "safeopt.com",
    "confirmtkt.com",
    "federalbank.co.in",
    "axis.bank.in",
    "joinhandshake.com",
    "joinhyra.com",
    "abekus.co.in",
    "primepathway.in",
    "ibrowsejobs.com",
    "sernexuss.in",
)

# LinkedIn is kept to specific mailboxes rather than the whole domain: a
# recruiter's InMail can carry a real conversation, so blocking linkedin.com
# outright would lose genuine mail to stop notification digests.
_JOB_BOARD_ADDRESSES = _JOB_BOARD_SENDERS + (
    "notifications-noreply@linkedin.com",
    "messages-noreply@linkedin.com",
    # updates-noreply reached the classifier during the July sweep and a
    # ScienceLogic job posting came back joining_confirmed. These are all
    # LinkedIn's own broadcast mailboxes; none of them carries a person
    # writing to this candidate. inmail-hit-reply is deliberately absent,
    # because that one does.
    "updates-noreply@linkedin.com",
    "newsletters-noreply@linkedin.com",
    "invitations-noreply@linkedin.com",
    "jobs-noreply@linkedin.com",
    "groups-noreply@linkedin.com",
)


def job_board_notification(sender_email: str) -> bool:
    """True when the sender is an aggregator, not an employer or its ATS.

    These mails are the single largest source of false lifecycle statuses. A
    talent500 "Shortlisted but your profile is incomplete" and a timesjobs
    "Your profile has been Shortlisted for EMBA" both read to a model as a
    genuine shortlisting, and were classified as one; the routing gate then
    refused them for lack of corroborating evidence, which is why they never
    reached an operator. Stopping them here means the gate no longer has to be
    the thing standing between marketing and the alert queue.
    """
    address = str(sender_email or "").strip().casefold()
    if not address:
        return False
    if address in _JOB_BOARD_ADDRESSES:
        return True
    domain = address.rpartition("@")[2]
    if not domain:
        return False
    return any(
        domain == known or domain.endswith("." + known)
        for known in _JOB_BOARD_DOMAINS + _SERVICE_NOISE_DOMAINS
    )


def job_advertisement_digest(
    subject: str, body: str, sender_email: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> bool:
    """True when the mail is a list of vacancies, not news about this candidate.

    A catalogue of adverts contains company names, role titles and locations, so
    a model asked "what happened to this candidate?" can assemble a convincing
    answer out of two unrelated listings. That is exactly what happened: a
    LinkedIn digest of six vacancies produced INTERVIEW_SHORTLISTED at 95%
    confidence, "Birlasoft and FactSet", and a summary saying the candidate had
    been shortlisted and should prepare for an interview. Every quoted evidence
    string was verbatim — they were the advert lines themselves — so the
    verbatim check could not catch it. The claim was about *meaning*, and the
    meaning was never in the mail.

    The test is structural rather than phrase-based, because the phrasing is the
    part that changes. Nothing here reads the model's answer.
    """
    text = " ".join(
        [str(subject or ""), str(body or "")]
        + [str(item.get("text") or "") for item in (attachments or [])]
    )
    postings = {
        next(group for group in match.groups() if group)
        for match in _JOB_POSTING_LINK.finditer(text)
    }
    if len(postings) >= 2:
        return True
    sender = str(sender_email or "").strip().casefold()
    if sender in _JOB_BOARD_SENDERS:
        haystack = text.casefold()
        return any(marker in haystack for marker in _JOB_DIGEST_MARKERS)
    return False


SPECIAL_CONTEXT = {
    "BACKGROUND_VERIFICATION": ("background verification", "pre-employment verification", "document verification"),
    "SALARY": ("salary discussion", "compensation discussion", "ctc discussion"),
    "JOINING_REQUEST": ("confirm your date of joining", "please confirm your joining date"),
}

SCHEMA = {
    "type": "object",
    "required": [
        "schema_version", "is_recruitment_related", "is_selection_or_offer_related",
        "should_create_review_record", "status", "confidence", "ignore_reason",
        "candidate", "company", "job", "recruiter", "interview", "offer",
        "attachments", "evidence", "risk_flags", "requires_manual_review",
        "summary", "reason", "recommended_action", "classification", "candidate_status",
        "email_intent", "document_type", "is_candidate_specific", "is_job_outcome",
        "is_current_event", "is_questionnaire", "is_promotional_or_job_ad",
        "is_historical_information", "lifecycle_event", "evidence_summary",
        "business_domain", "interview_event",
    ],
    "properties": {
        "schema_version": {"const": "selection_offer_event_v1"},
        "is_recruitment_related": {"type": "boolean"},
        "is_selection_or_offer_related": {"type": "boolean"},
        "should_create_review_record": {"type": "boolean"},
        "status": {"type": "string", "enum": STATUSES},
        "classification": {"type": "string", "enum": sorted(store.CANONICAL_CLASSIFICATIONS)},
        "candidate_status": {"type": "string", "enum": sorted(set(store._CLASSIFICATION_STATUS.values()))},
        "email_intent": {"type": "string", "enum": sorted(EMAIL_INTENTS)},
        "document_type": {"type": "string", "enum": sorted(DOCUMENT_TYPES)},
        "is_candidate_specific": {"type": "boolean"},
        "is_job_outcome": {"type": "boolean"},
        "is_current_event": {"type": "boolean"},
        "is_questionnaire": {"type": "boolean"},
        "is_promotional_or_job_ad": {"type": "boolean"},
        "is_historical_information": {"type": "boolean"},
        "historical_employment_evidence": {"type": "boolean"},
        "lifecycle_event": {"type": "string"},
        "business_domain": {"type": "string", "enum": ["SELECTION_TRACKING", "INTERVIEW_TRACKING", "NONE"]},
        "interview_event": {"type": "string", "enum": ["NONE", "INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED"]},
        "evidence_summary": {"type": "string", "maxLength": 1000},
        "validation_status": {"type": "string"},
        "ai_status": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 100},
        "ignore_reason": {"type": ["string", "null"]},
        "candidate": {"type": "object", "properties": {"name": {"type": ["string", "null"]}, "email": {"type": ["string", "null"]}}, "required": ["name", "email"]},
        "company": {"type": "object", "properties": {"name": {"type": ["string", "null"]}, "domain": {"type": ["string", "null"]}}, "required": ["name", "domain"]},
        "job": {"type": "object", "properties": {"title": {"type": ["string", "null"]}, "employment_type": {"type": ["string", "null"]}, "location": {"type": ["string", "null"]}}, "required": ["title", "employment_type", "location"]},
        "recruiter": {"type": "object", "properties": {"name": {"type": ["string", "null"]}, "email": {"type": ["string", "null"]}}, "required": ["name", "email"]},
        "interview": {"type": "object", "properties": {
            **{key: {"type": ["string", "null"]} for key in [
                "date", "time", "end_time", "timezone", "mode", "round", "location", "meeting_link",
                "original_date", "original_time", "original_timezone",
            ]},
            "duration_minutes": {"type": ["integer", "null"], "minimum": 5, "maximum": 720},
        }, "required": ["date", "time", "timezone", "mode", "round", "location", "meeting_link"]},
        "offer": {"type": "object", "properties": {
            "offer_detected": {"type": "boolean"}, "offer_letter_detected": {"type": "boolean"},
            "appointment_letter_detected": {"type": "boolean"}, "offer_date": {"type": ["string", "null"]},
            "offered_ctc": {"type": ["number", "null"]}, "currency": {"type": ["string", "null"]},
            "joining_date": {"type": ["string", "null"]}, "offer_expiry_date": {"type": ["string", "null"]},
        }, "required": ["offer_detected", "offer_letter_detected", "appointment_letter_detected", "offer_date", "offered_ctc", "currency", "joining_date", "offer_expiry_date"]},
        "attachments": {"type": "array", "items": {"type": "object", "properties": {"type": {"type": "string"}, "filename": {"type": "string"}, "confidence": {"type": "number"}}, "required": ["type", "filename", "confidence"]}},
        "evidence": {"type": "array", "items": {"type": "object", "properties": {
            "source": {"type": "string", "enum": ["EMAIL_SUBJECT", "EMAIL_BODY", "ATTACHMENT", "THREAD_CONTEXT"]},
            "meaning": {"type": "string"}, "text": {"type": "string", "minLength": 3, "maxLength": 500},
        }, "required": ["source", "meaning", "text"]}},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "requires_manual_review": {"type": "boolean"}, "summary": {"type": "string", "maxLength": 1000},
        "reason": {"type": "string", "maxLength": 1000},
        "recommended_action": {"type": "string", "maxLength": 1000},
        "model_validation": {"type": "object"},
    },
    "additionalProperties": False,
}


RELEVANCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "message_kind", "confidence", "evidence", "reason"],
    "properties": {
        "decision": {"type": "string", "enum": ["ESTABLISHED", "NOT_ESTABLISHED"]},
        "message_kind": {
            "type": "string",
            "enum": [
                "RECIPIENT_HIRING_PROCESS", "MARKETING_OR_TRAINING",
                "NEWSLETTER", "PUBLIC_EVENT", "JOB_ADVERTISEMENT",
                "GENERAL", "UNKNOWN",
            ],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 100},
        "evidence": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "text"],
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["EMAIL_SUBJECT", "EMAIL_BODY", "ATTACHMENT", "THREAD_CONTEXT"],
                    },
                    "text": {"type": "string", "minLength": 1, "maxLength": 500},
                },
            },
        },
        "reason": {"type": "string", "maxLength": 1000},
    },
}


def clean_email(text: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    value = re.split(r"(?im)^\s*(?:on .+ wrote:|from:|unsubscribe|confidentiality notice)", value, maxsplit=1)[0]
    return re.sub(r"\s+", " ", value).strip()[:30000]


def _source_texts(subject: str, body: str, attachments: list[dict[str, Any]] | None, thread_context: list[dict[str, Any]] | None) -> dict[str, list[str]]:
    return {
        "EMAIL_SUBJECT": [clean_email(subject)],
        "EMAIL_BODY": [clean_email(body)],
        "ATTACHMENT": [clean_email(str(item.get("text") or "")) for item in (attachments or [])],
        "THREAD_CONTEXT": [clean_email(" ".join(str(item.get(key) or "") for key in ("subject", "body"))) for item in (thread_context or [])[-5:]],
    }


def _matching_statuses(text: str) -> list[tuple[str, str]]:
    lowered = text.lower()
    matches = []
    for status, phrases in STATUS_SIGNALS:
        for phrase in phrases:
            if phrase in lowered:
                # Large employers commonly append a disclaimer saying that an
                # interview message must *not* be treated as an offer of
                # employment.  The phrase alone is therefore not positive
                # offer evidence when it occurs inside that negated clause.
                if status in {"OFFER_LETTER_RECEIVED", "OFFER_INDICATION"} and phrase in {
                    "employment offer", "offer of employment",
                }:
                    start = max(0, lowered.find(phrase) - 220)
                    end = min(len(lowered), lowered.find(phrase) + len(phrase) + 220)
                    context = lowered[start:end]
                    if (
                        "unless there is a formal offer" in context
                        and any(token in context for token in (
                            "shall not be assumed", "not be assumed",
                            "not be treated", "no guarantee of employment",
                        ))
                    ):
                        continue
                if status in {"APPOINTMENT_LETTER_RECEIVED", "OFFER_LETTER_RECEIVED"}:
                    position = lowered.find(phrase)
                    context = lowered[max(0, position - 240):position + len(phrase) + 240]
                    requested_history = any(token in context for token in (
                        "previous companies", "previous company", "mandatory checklist",
                        "documents are required", "please upload", "please submit", "required documents",
                    ))
                    actual_outcome = any(token in context for token in (
                        "attached", "we are pleased to appoint", "your appointment letter",
                        "we are pleased to offer", "offer letter has been released",
                    ))
                    if requested_history and not actual_outcome:
                        continue
                matches.append((status, phrase))
                break
    return matches


def _evidence_excerpt(text: str, phrase: str) -> str:
    clean = clean_email(text)
    start = clean.casefold().find(phrase.casefold())
    if start < 0:
        return phrase
    left = max(clean.rfind(".", 0, start), clean.rfind("!", 0, start), clean.rfind("?", 0, start)) + 1
    endings = [pos for mark in ".!?" if (pos := clean.find(mark, start)) >= 0]
    right = min(endings) + 1 if endings else min(len(clean), start + 240)
    return clean[left:right].strip()[:500]


def _extract_joining_date(text: str) -> str | None:
    month = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    patterns = [rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month})\s*,?\s*(\d{{4}})\b", rf"\b({month})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(\d{{4}})\b"]
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        parts = match.groups()
        candidate = " ".join(parts if index == 0 else (parts[1], parts[0], parts[2]))
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                pass
    return None


def _extract_context(subject: str, body: str, sender_email: str) -> tuple[str | None, str | None, str | None]:
    job = None
    for pattern in (r"\brole of\s+([A-Za-z][A-Za-z0-9 /&+.#-]{1,80}?)(?=[.,;\n]|\byour date\b)", r"[-–—]\s*([A-Za-z][A-Za-z0-9 /&+.#-]{1,80}?)\s+Role\b"):
        match = re.search(pattern, subject + "\n" + body, re.I)
        if match:
            job = match.group(1).strip(" -–—")
            break
    company = None
    company_match = re.search(r"\b([A-Z][A-Z0-9 &.,'-]{2,100}?(?:PVT\.?\s*LTD\.?|PRIVATE LIMITED|SERVICES INDIA PVT\.?\s*LTD\.?|LIMITED))\b", body)
    if company_match:
        company = re.sub(r"\s+", " ", company_match.group(1)).strip()
    domain = sender_email.rsplit("@", 1)[-1].lower() if "@" in sender_email else None
    return company, job, domain


def prefilter_decision(subject: str, body: str, sender_name: str = "", sender_email: str = "", attachments: list[dict[str, Any]] | None = None, thread_context: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    sources = _source_texts(subject, body, attachments, thread_context)
    semantic = classify_context(
        subject, body, sender_email=sender_email, attachments=attachments,
    )
    if (
        semantic["is_questionnaire"]
        or semantic["is_promotional_or_job_ad"]
        or semantic["is_historical_information"]
        or semantic.get("is_question")
    ):
        haystack = " ".join([subject, sender_name, sender_email, body]).lower()
        for reason, phrases in NOISE_RULES:
            if reason in {"JOB_RECOMMENDATION","JOB_ALERT","JOB_PORTAL_MARKETING","PROFILE_NOTIFICATION","APPLICATION_UPDATE","ASSESSMENT"} and any(phrase in haystack for phrase in phrases):
                return {"qualified": False, "score": 0.0, "status": "IGNORED_NOT_OFFER_RELATED", "evidence": [], "ignore_reason": reason}
        return {
            "qualified": False, "score": 0.0,
            "status": "IGNORED_NOT_OFFER_RELATED", "evidence": [],
            "ignore_reason": semantic["email_intent"], "semantic_context": semantic,
        }
    evidence = []
    detected = []
    for source, values in sources.items():
        for value in values:
            for status, phrase in _matching_statuses(value):
                detected.append(status)
                evidence.append({"source": source, "meaning": status, "text": _evidence_excerpt(value, phrase)})
    for attachment in attachments or []:
        filename = str(attachment.get("filename") or "").lower()
        attachment_text = clean_email(str(attachment.get("text") or ""))
        lowered_text = attachment_text.lower()
        if "appointment" in filename and any(token in lowered_text for token in ("employment", "appointed", "appointment")):
            detected.append("APPOINTMENT_LETTER_RECEIVED")
            evidence.append({"source": "ATTACHMENT", "meaning": "APPOINTMENT_LETTER_RECEIVED", "text": next(token for token in ("employment", "appointed", "appointment") if token in lowered_text)})
        elif "offer" in filename and any(token in lowered_text for token in ("employment offer", "offered employment", "offer of employment")):
            detected.append("OFFER_LETTER_RECEIVED")
            evidence.append({"source": "ATTACHMENT", "meaning": "OFFER_LETTER_RECEIVED", "text": next(token for token in ("employment offer", "offered employment", "offer of employment") if token in lowered_text)})
    direct_text = " ".join(sources["EMAIL_SUBJECT"] + sources["EMAIL_BODY"])
    if "JOINING_CONFIRMED" in detected and not _extract_joining_date(direct_text) and "please confirm your date of joining" in direct_text.lower():
        detected=[value for value in detected if value!="JOINING_CONFIRMED"]
        evidence=[item for item in evidence if item.get("meaning")!="JOINING_CONFIRMED"]
    combined_context = " ".join(sources["EMAIL_BODY"] + sources["ATTACHMENT"] + sources["THREAD_CONTEXT"]).lower()
    has_confirmed_context = any(token in combined_context for token in ("selected", "selection confirmed", "offer letter", "employment offer", "offer approved", "onboarding"))
    for source, values in sources.items():
        for value in values:
            lowered = value.lower()
            if has_confirmed_context and any(phrase in lowered for phrase in SPECIAL_CONTEXT["BACKGROUND_VERIFICATION"]):
                detected.append("POST_SELECTION_ONBOARDING")
                phrase = next(p for p in SPECIAL_CONTEXT["BACKGROUND_VERIFICATION"] if p in lowered)
                evidence.append({"source": source, "meaning": "POST_SELECTION_ONBOARDING", "text": phrase})
            if has_confirmed_context and any(phrase in lowered for phrase in SPECIAL_CONTEXT["SALARY"]):
                detected.append("OFFER_INDICATION")
                phrase = next(p for p in SPECIAL_CONTEXT["SALARY"] if p in lowered)
                evidence.append({"source": source, "meaning": "OFFER_INDICATION", "text": phrase})
            if has_confirmed_context and any(phrase in lowered for phrase in SPECIAL_CONTEXT["JOINING_REQUEST"]):
                detected.append("JOINING_CONFIRMED")
                phrase = next(p for p in SPECIAL_CONTEXT["JOINING_REQUEST"] if p in lowered)
                evidence.append({"source": source, "meaning": "JOINING_CONFIRMED", "text": phrase})
    if detected:
        status = next((candidate for candidate in STATUS_PRIORITY if candidate in detected), detected[0])
        # A shortlist by itself remains ordinary recruitment noise. Stronger
        # evidence later in the complete message always wins.
        if status == "SHORTLISTED":
            status = "INTERVIEW_SHORTLISTED"
            for item in evidence:
                if item.get("meaning") == "SHORTLISTED": item["meaning"] = status
        combined = " ".join(sources["EMAIL_SUBJECT"] + sources["EMAIL_BODY"])
        company, job, domain = _extract_context(subject, body, sender_email)
        conflict = "SHORTLISTED" in detected and status != "SHORTLISTED"
        if subject and any(token in subject.casefold() for token in ("congratulations", "next steps")):
            evidence.append({"source":"EMAIL_SUBJECT","meaning":status,"text":clean_email(subject)[:500]})
        return {
            "qualified": True, "score": max(0.94 if status == "JOINING_CONFIRMED" else 0.92, min(0.99, 0.9 + 0.02 * len(evidence))),
            "status": status, "evidence": evidence[:8], "ignore_reason": None,
            "joining_date": _extract_joining_date(combined) if status in {"JOINING_CONFIRMED", "POST_SELECTION_ONBOARDING", "JOINED"} else None,
            "company_name": company, "company_domain": domain, "job_title": job,
            "risk_flags": ["WORDING_STATUS_CONFLICT"] if conflict else [],
            "requires_manual_review": conflict,
        }
    # Both fields use the literal string "NONE" for absence, which is truthy.
    # `interview_event or lifecycle_event` therefore masked a real lifecycle
    # assertion whenever interview_event was NONE (including shortlisting).
    # Run the same fail-closed validators used after the models before letting
    # either deterministic field qualify routing; legacy descriptive booleans
    # must not turn a negated offer disclaimer into a positive route.
    semantic_status = None
    proposed_interview = str(semantic.get("interview_event") or "NONE").upper()
    if proposed_interview != "NONE":
        safe_interview, _ = validate_interview_event(proposed_interview, semantic)
        if safe_interview != "NONE":
            semantic_status = safe_interview
    if semantic_status is None:
        proposed_lifecycle = str(semantic.get("lifecycle_event") or "NONE").upper()
        if proposed_lifecycle != "NONE":
            safe_lifecycle, _ = validate_lifecycle_event(proposed_lifecycle, semantic)
            if safe_lifecycle != "NONE":
                semantic_status = safe_lifecycle
    if semantic_status and semantic_status != "NONE":
        # classify_context's assertive-context regexes (e.g. "your interview
        # ... is confirmed today") found a candidate-specific outcome that the
        # literal STATUS_SIGNALS phrase list did not match verbatim. Route it
        # to the model rather than silently dropping it as no-signal noise;
        # validate_lifecycle_event/validate_interview_event re-check
        # assertiveness independently before any status is ever accepted.
        return {
            "qualified": True, "score": 0.6, "status": semantic_status,
            "evidence": [{
                "source": "EMAIL_BODY", "meaning": semantic_status,
                "text": redact_sensitive_text(semantic["evidence_summary"]),
            }],
            "ignore_reason": None, "semantic_context": semantic,
        }
    haystack = " ".join([subject, sender_name, sender_email, body]).lower()
    for reason, phrases in NOISE_RULES:
        if any(phrase in haystack for phrase in phrases):
            return {"qualified": False, "score": 0.0, "status": "IGNORED_NOT_OFFER_RELATED", "evidence": [], "ignore_reason": reason}
    return {"qualified": False, "score": 0.0, "status": "IGNORED_NOT_OFFER_RELATED", "evidence": [], "ignore_reason": "NO_SELECTION_OR_OFFER_SIGNAL"}


_INVITE_STRUCTURE_CUES = (
    "microsoft teams meeting", "teams.microsoft.com", "meet.google.com",
    "zoom.us", "webex.com", "calendar invitation", "when:", "dtstart",
    "begin:vevent", "organiser:", "organizer:", "join the meeting",
    "join microsoft teams", "meeting id:", "add to calendar",
)
_ROLE_TITLE_CUES = (
    "engineer", "developer", "analyst", "architect", "consultant", "devops",
    "sre", "tester", "designer", "administrator", "specialist", "lead",
    "scientist", "programmer", "full stack", "fullstack", "backend",
    "frontend", "qa",
    # Stacks are named as the role in Indian recruiting subject lines:
    # "Interview schedule for Charan - ReactJS" names no "developer".
    "reactjs", "react", "angular", "node", "java", "python", ".net", "dotnet",
    "spring", "django", "aws", "azure",
)
# Wording that frames the meeting as being *about a person for a role*. This is
# the part an ordinary internal meeting does not have: "Discussion with Ramu
# about the budget" carries no role title, and a sprint invite carries neither.
_MEETING_ABOUT_PERSON_CUES = (
    "discussion with", "discussion for", "discussion regarding",
    "technical discussion", "call with", "screening", "screening round",
    "round with", "meeting with", "conversation with", "profile discussion",
    # "Interview schedule for <candidate>" frames the meeting around a person
    # just as "discussion with" does.
    "schedule for", "availability for", "interview for",
)


def recruiting_invite_signal(
    subject: str, body: str, sender_email: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> bool:
    """Does this look like a recruiting calendar invite that never says "interview"?

    Real invites arrive titled "Discussion with <candidate> for <role>" on a
    Teams/Meet link, and were dropped as NO_RECRUITMENT_ROUTING_SIGNAL because
    no keyword matched. Routing on "discussion" alone would pull in every
    internal meeting, so three independent structured signals are required
    together: a calendar/meeting invite structure, a job-role-like title, and
    wording framing the meeting around a person. Any one or two of those is not
    enough, so an ordinary business discussion still fails closed.
    """
    subject_text = str(subject or "").casefold()
    body_text = str(body or "").casefold()
    attachment_text = " ".join(
        str(item.get("text") or "") + " " + str(item.get("filename") or "")
        for item in (attachments or [])
    ).casefold()
    everything = " ".join((subject_text, body_text, attachment_text))

    has_invite_structure = (
        any(cue in everything for cue in _INVITE_STRUCTURE_CUES)
        or ".ics" in attachment_text
    )
    # The role must be named in the subject line: a signature block or a
    # footer mentioning "engineer" elsewhere is not what this is about.
    has_role_title = any(cue in subject_text for cue in _ROLE_TITLE_CUES)
    is_about_a_person = any(cue in subject_text for cue in _MEETING_ABOUT_PERSON_CUES)
    return bool(has_invite_structure and has_role_title and is_about_a_person)


_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
)
_MONTH_PATTERN = "|".join(_MONTH_NAMES) + "|" + "|".join(m[:3] for m in _MONTH_NAMES)
# "September 11, 2026", "11 September 2026", "2026-09-11".
_SCHEDULED_DATE_RE = re.compile(
    rf"\b(?:(?:{_MONTH_PATTERN})\w*\s+\d{{1,2}},?\s+\d{{4}}"
    rf"|\d{{1,2}}\s+(?:{_MONTH_PATTERN})\w*\s+\d{{4}}"
    rf"|\d{{4}}-\d{{2}}-\d{{2}})\b",
    re.IGNORECASE,
)
# "8:30am", "2:00 PM", "08:30".
_SCHEDULED_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\b", re.IGNORECASE)
_INTERVIEW_WORD_RE = re.compile(r"\binterviews?\b", re.IGNORECASE)


def scheduled_interview_signal(
    subject: str, body: str, sender_email: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> bool:
    """Does this name an interview and say when it is?

    A reminder from an interview platform — "Your Altimetrik Interview for the
    Citi Scaled Hiring FPC - NAM Project Is Coming Up!", carrying the date and
    the UTC start and end times — was dropped as NO_RECRUITMENT_ROUTING_SIGNAL.
    It qualified nowhere: the prefilter wants a selection or offer signal and
    this is neither, "interview" is not one of the ambiguous cues, and
    recruiting_invite_signal needs a job title in the subject, which a project
    codename is not.

    Two signals together, so it fails closed. Saying "interview" is not enough
    on its own — a rejection says it too, and so does a job advert — and a date
    and time alone are most of the mail anyone receives. Both, and only both.

    Routing is not booking: this decides that a message deserves semantic
    analysis, and every validation, persistence and confirmation check downstream
    still has to pass before anything is booked.
    """
    subject_text = str(subject or "")
    body_text = str(body or "")
    attachment_text = " ".join(
        str(item.get("text") or "") + " " + str(item.get("filename") or "")
        for item in (attachments or [])
    )
    everything = " ".join((subject_text, body_text, attachment_text))
    if not _INTERVIEW_WORD_RE.search(everything):
        return False
    return bool(
        _SCHEDULED_DATE_RE.search(everything) and _SCHEDULED_TIME_RE.search(everything)
    )


def relevance_score(subject: str, body: str, filenames: list[str] | None = None, thread_context: list[dict[str, Any]] | None = None) -> float:
    # Filenames alone are intentionally excluded from qualification.
    return float(prefilter_decision(subject, body, thread_context=thread_context)["score"])


def routing_decision(
    subject: str,
    body: str,
    sender_name: str = "",
    sender_email: str = "",
    attachments: list[dict[str, Any]] | None = None,
    thread_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Decide whether an email needs semantic analysis, never its outcome.

    This intentionally uses rules only to discard obvious noise and messages
    with no recruitment signal. Ambiguous language such as "shortlisted" is
    always sent to the model because later sentences can confirm joining.
    """
    context = prefilter_decision(
        subject, body, sender_name, sender_email, attachments, thread_context
    )
    if context.get("qualified"):
        return {"send_to_ai": True, "score": context["score"], "reason": "POTENTIAL_OUTCOME", "context": context}
    # A catalogue of vacancies carries no outcome for this candidate, so there
    # is nothing for the model to decide. It reaches here because
    # `is_promotional_or_job_ad` did not recognise the digest, which is what let
    # a LinkedIn "Jobs that match your profile" mail through as
    # AMBIGUOUS_RECRUITMENT on cues like "position" and "candidate".
    if job_advertisement_digest(subject, body, sender_email, attachments):
        return {"send_to_ai": False, "score": 0.0, "reason": "JOB_RECOMMENDATION", "context": context}
    reason = str(context.get("ignore_reason") or "")
    if reason in {
        "JOB_RECOMMENDATION", "JOB_ALERT", "JOB_PORTAL_MARKETING",
        "PROFILE_NOTIFICATION", "ASSESSMENT", "INTERVIEW", "REJECTION",
    }:
        return {"send_to_ai": False, "score": 0.0, "reason": reason, "context": context}
    # The deterministic classifier already conclusively identified this
    # message as promotional/questionnaire/historical/question noise. Trust
    # that verdict outright: an incidental keyword match below (e.g. a job
    # portal's generic tips paragraph mentioning "recruiters") must never
    # override a conclusive semantic determination and force a model call.
    if context.get("semantic_context"):
        return {"send_to_ai": False, "score": 0.0, "reason": reason or "DETERMINISTIC_NOISE_FILTER", "context": context}
    combined = " ".join(
        [subject, body, sender_name, sender_email]
        + [str(item.get("text") or "") for item in (attachments or [])]
        + [" ".join(str(item.get(key) or "") for key in ("subject", "body")) for item in (thread_context or [])[-5:]]
    ).casefold()
    ambiguous_recruitment_cues = (
        "shortlist", "application", "selection", "selected", "offer", "joining",
        "onboarding", "appointment", "employment", "compensation", "recruiter",
        "candidate", "job role", "position", "background verification",
    )
    # Word-boundary match, not substring: a plain `cue in combined` check let
    # "offer" match inside "offering"/"offered" anywhere in the email (a bank
    # fraud-warning footer, a newsletter's "bond offering"), forcing routine
    # marketing noise through the model.
    if any(re.search(rf"\b{re.escape(cue)}\b", combined) for cue in ambiguous_recruitment_cues):
        return {"send_to_ai": True, "score": max(0.25, float(context.get("score") or 0)), "reason": "AMBIGUOUS_RECRUITMENT", "context": context}
    if recruiting_invite_signal(subject, body, sender_email, attachments):
        return {"send_to_ai": True, "score": max(0.3, float(context.get("score") or 0)), "reason": "RECRUITING_CALENDAR_INVITE", "context": context}
    # Last, and after every deterministic noise verdict above has had its say.
    if scheduled_interview_signal(subject, body, sender_email, attachments):
        return {"send_to_ai": True, "score": max(0.3, float(context.get("score") or 0)), "reason": "SCHEDULED_INTERVIEW", "context": context}
    return {"send_to_ai": False, "score": 0.0, "reason": "NO_RECRUITMENT_ROUTING_SIGNAL", "context": context}


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


#: A risk flag that says there are no risks. Ollama writes the answer "none" as
#: prose inside the list rather than returning an empty list, and every reader
#: of `risk_flags` tests the list's truthiness. Matching requires a leading
#: negation *and* a risk word, so "No specific interview schedule provided"
#: -- which names something genuinely missing -- is not caught.
_RISK_FLAG_MEANS_NONE = re.compile(
    r"^\s*(?:no|none|nil)\b.{0,60}?\b(?:risk|flag|concern|issue|warning)s?\b",
    re.IGNORECASE,
)


def _evidence_supported(item: dict[str, Any], sources: dict[str, list[str]]) -> bool:
    needle = clean_email(str(item.get("text") or "")).casefold()
    return bool(needle) and any(needle in value.casefold() for value in sources.get(str(item.get("source") or ""), []))


def _canonicalise_evidence_source(
    item: dict[str, Any], sources: dict[str, list[str]],
) -> dict[str, Any] | None:
    """Correct a model's source label only when its verbatim quote proves one source.

    Models sometimes quote the body exactly while labelling it EMAIL_SUBJECT.
    The quote remains untrusted unless it occurs verbatim in exactly one source
    category; absent or ambiguous quotes still fail closed.
    """
    value = dict(item)
    needle = clean_email(str(value.get("text") or "")).casefold()
    if not needle:
        return None
    matches = [
        source_name for source_name, texts in sources.items()
        if any(needle in text.casefold() for text in texts)
    ]
    declared = str(value.get("source") or "")
    if declared in matches:
        return value
    if len(matches) != 1:
        return None
    value["evidence_source_corrected_from"] = declared or None
    value["source"] = matches[0]
    return value


_EVIDENCE_SENTENCE_RE = re.compile(r"[^.!?\n]{20,400}(?:[.!?]|\n|$)")


def _entailing_evidence_from_source(
    sources: dict[str, list[str]], status: str,
) -> list[dict[str, Any]]:
    """Quote the sentence in the source that proves this transition, if any.

    Used only when the model cited evidence that does not entail. It reads the
    same verified sources the verbatim check reads, so nothing here can invent
    a quote, and it returns nothing at all when the source genuinely does not
    say it -- which keeps a rejection or a job advert rejected.

    One sentence, from the first source that carries one, in the same shape the
    model's own evidence uses.
    """
    for source_name in ("EMAIL_SUBJECT", "EMAIL_BODY", "ATTACHMENT", "THREAD_CONTEXT"):
        for raw in sources.get(source_name) or []:
            normalized = clean_email(raw)
            for match in _EVIDENCE_SENTENCE_RE.finditer(normalized):
                sentence = match.group(0).strip()
                if not sentence or not evidence_entails_transition(status, sentence):
                    continue
                return [{
                    "source": source_name,
                    "text": redact_sensitive_text(sentence),
                    "recovered_by_backend": True,
                }]
    return []


def _source_asserts(sources: dict[str, list[str]], status: str) -> bool:
    """Whether the verified source says this transition happened, anywhere in it.

    Used to choose *which* status a mail is about, never to satisfy the evidence
    gate: the gate below still needs a verbatim, entailing excerpt. Reading the
    whole source rather than one sentence is deliberate here -- a mail that
    mentions an interview anywhere is not an assessment mail, however it phrases
    the assessment.
    """
    return any(
        evidence_entails_transition(status, clean_email(raw))
        for values in sources.values() for raw in values
    )


def _evidence_entails_source_transition(
    item: dict[str, Any], sources: dict[str, list[str]], status: str,
) -> bool:
    """Check entailment in the verbatim quote and its immediate source context."""
    quote = clean_email(str(item.get("text") or ""))
    if evidence_entails_transition(status, quote):
        return True
    needle = quote.casefold()
    if not needle:
        return False
    for source in sources.get(str(item.get("source") or ""), []):
        normalized = clean_email(source)
        position = normalized.casefold().find(needle)
        if position < 0:
            continue
        passage = normalized[max(0, position - 180):position + len(quote) + 180]
        if evidence_entails_transition(status, passage):
            return True
    return False


# Unambiguous employer language, mapped to the meaning codes the routing gate
# recognises.
#
# The model is asked for a `meaning` code and returns a sentence instead - 950
# prose values against 55 coded ones in production - so genuine employer mail
# was dropped for want of a code it never produced. Rather than teach the gate
# to accept prose, which would also admit the marketing that phrases itself the
# same way, only these phrases are promoted.
#
# Kept deliberately short and specific. "Shortlisted" is absent because it is
# the single most common word in job-board marketing: "Shortlisted but your
# profile is incomplete", "Shortlisted for EMBA". A phrase earns a place here
# only if an aggregator would have no reason to send it.
_MEANING_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("OFFER_LETTER_RECEIVED", (
        "offer letter has been sent", "offer letter has been released",
        "offer has been released", "released the offer",
        "offer letter is attached", "offer letter attached",
        "we are pleased to offer you",
    )),
    ("SELECTED", (
        "has been selected for the position", "has been selected for the role",
        "selected for the position of", "confirming your selection",
        "candidate has been selected",
    )),
    ("CANDIDATE_REJECTED", (
        "not matching the requirements", "profile not matching",
        "has been declined", "regret to inform", "we will not be moving forward",
    )),
    ("JOINING_CONFIRMED", (
        "joining has been confirmed", "expected to join on",
        "date of joining is", "confirmed your date of joining",
    )),
    # Onboarding paperwork only ever follows an accepted offer. Innominds'
    # "please complete the pre-onboarding formalities" classified correctly as
    # joining_confirmed and was then refused by the routing gate, because the
    # model wrote its evidence meaning as prose and none of it matched
    # POST_SELECTION_ONBOARDING. Kept to wording no job advert uses: an advert
    # describes a role, it does not ask you to complete your own onboarding.
    ("POST_SELECTION_ONBOARDING", (
        "pre-onboarding formalities", "pre onboarding formalities",
        "onboarding formalities", "complete the pre-onboarding",
        "as our valuable new employee",
    )),
    # Employment BGV is commissioned after selection, never before. The phrases
    # name an initiated check on a specific person; a JD that merely warns
    # "background verification is mandatory" matches none of them, so it still
    # stops at the gate.
    ("BACKGROUND_VERIFICATION", (
        "digital employment bgv", "employment bgv",
        "background verification has been initiated",
        "initiated your background verification",
        "third party verification on behalf of",
    )),
)


def normalise_evidence_meaning(item: dict[str, Any]) -> dict[str, Any]:
    """Promote unambiguous employer prose to a recognised meaning code.

    The original wording is kept in `meaning_text`, because the prose is the
    audit trail: it is what the model actually said about the mail, and a code
    derived from it is an interpretation.

    Anything that does not match stays exactly as it was, so the routing gate
    still refuses it. That is the point - this recovers genuine mail without
    becoming a general-purpose way around the gate.
    """
    meaning = str(item.get("meaning") or "").strip()
    if not meaning:
        return item
    if meaning.upper() in store.IMPORTANT_ALERT_EVIDENCE_MEANINGS:
        return item
    haystack = f"{meaning} {item.get('text') or ''}".casefold()
    for code, phrases in _MEANING_PHRASES:
        if any(phrase in haystack for phrase in phrases):
            promoted = dict(item)
            promoted["meaning_text"] = meaning
            promoted["meaning"] = code
            return promoted
    return item


_OFFER_FAMILY = {
    "OFFER_INDICATION", "OFFER_IN_PROGRESS", "OFFER_APPROVED",
    "OFFER_LETTER_RECEIVED", "APPOINTMENT_LETTER_RECEIVED", "OFFER_ACCEPTED",
    "OFFER_DECLINED", "OFFER_REVOKED", "COMPENSATION_CONFIRMATION",
}
_JOINING_FAMILY = {
    "JOINING_CONFIRMED", "JOINING_DATE_UPDATED", "JOINED",
    "POST_SELECTION_ONBOARDING",
}
_ASSERTIVE_EMPLOYMENT_LIFECYCLE = {
    "SELECTED", "FINAL_SELECTION_CONFIRMED", *_OFFER_FAMILY, *_JOINING_FAMILY,
}


def _needs_review_status(proposed: str) -> str | None:
    """The visible review status a distrusted offer/joining result becomes.

    Deliberately NOT members of OFFER_CASE_STATUSES: a review status must never
    feed offer-case, booking, acceptance or payment workflows. It exists only so
    the record stays auditable instead of being deleted.
    """
    status = str(proposed or "").upper()
    if status in _OFFER_FAMILY:
        return "OFFER_NEEDS_REVIEW"
    if status in _JOINING_FAMILY:
        return "JOINING_NEEDS_REVIEW"
    if status.startswith("INTERVIEW_"):
        return "INTERVIEW_PROPOSED"
    # Everything else the model may legitimately return — SELECTED,
    # FINAL_SELECTION_CONFIRMED, BACKGROUND_VERIFICATION, DOCUMENT_VERIFICATION,
    # CANDIDATE_REJECTED — was still collapsing to NONE and disappearing. A
    # rejection in particular must never vanish: the candidate outcome is the
    # whole point of the record.
    if status in TRACKED_STATUSES:
        return "SELECTION_NEEDS_REVIEW"
    return None


def validate_result(
    value: dict[str, Any], message: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    *, deterministic_context: dict[str, Any] | None = None,
    relevance: dict[str, Any] | None = None,
) -> None:
    """Validate evidence, then expose only an automated operational decision."""
    _validate_result(value, message, attachments, deterministic_context=deterministic_context, relevance=relevance)
    from services.recruitment_automation import normalize_analysis
    normalize_analysis(value)


def _validate_result(
    value: dict[str, Any], message: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    *, deterministic_context: dict[str, Any] | None = None,
    relevance: dict[str, Any] | None = None,
) -> None:
    from jsonschema import Draft202012Validator
    pure_mode = pure_ollama_enabled()
    # Backward-compatible normalization for v1 responses while the configured
    # model transitions to the canonical lowercase classification contract.
    raw_confidence = float(value.get("confidence") or 0)
    if raw_confidence < 0 or raw_confidence > 100:
        raise ValueError("confidence must be between 0 and 100")
    if raw_confidence > 1:
        value["confidence"] = raw_confidence / 100.0
    context = deterministic_context or classify_context(
        str((message or {}).get("subject") or ""),
        str((message or {}).get("body") or ""),
        sender_email=str((message or {}).get("sender_email") or ""),
        sent_at=(message or {}).get("sent_at"), attachments=attachments,
    )
    for key in (
        "email_intent", "document_type", "is_candidate_specific", "is_job_outcome",
        "is_current_event", "is_questionnaire", "is_promotional_or_job_ad",
        "is_historical_information", "historical_employment_evidence",
        "lifecycle_event", "evidence_summary",
        "business_domain", "interview_event",
    ):
        default = context[key]
        if pure_mode:
            # Missing descriptive AI fields are unknown, not permission for
            # the legacy keyword classifier to supply a second intent.
            default = False if isinstance(default, bool) else (
                "NONE" if key in {"document_type", "lifecycle_event", "business_domain", "interview_event"}
                else "UNKNOWN" if key == "email_intent" else ""
            )
        value.setdefault(key, default)
    value.setdefault("ai_status", "ANALYZED")
    value.setdefault("validation_status", "AI_DETECTED")
    value.setdefault("classification", store.canonical_classification(value))
    value.setdefault("candidate_status", store._CLASSIFICATION_STATUS[value["classification"]])
    value.setdefault("reason", str(value.get("ignore_reason") or "Contextual employment classification"))
    errors = list(Draft202012Validator(SCHEMA).iter_errors(value))
    if errors:
        raise ValueError("invalid selection/offer JSON: " + errors[0].message)
    value["backend_validation_policy"] = "OLLAMA_HARD_SAFETY" if pure_mode else "LEGACY_RULES_FIRST"
    # Second layer, in case such a mail reaches the model by another route. The
    # answer is left intact in the result so it stays auditable; it simply stops
    # being something the system tracks, which is the disposition a catalogue of
    # vacancies deserves however confidently it was read. Recorded the way every
    # other downgrade here is, and after schema validation because the schema
    # forbids the extra keys.
    if not pure_mode and message is not None and job_advertisement_digest(
        str(message.get("subject") or ""), str(message.get("body") or ""),
        str(message.get("sender_email") or ""), attachments,
    ):
        value["downgraded_from"] = str(value.get("status") or "")
        value["downgrade_reason"] = "JOB_ADVERTISEMENT_DIGEST"
        value["is_selection_or_offer_related"] = False
        value["should_create_review_record"] = False
        value["requires_manual_review"] = False
        value["ignore_reason"] = "JOB_RECOMMENDATION"
        value["reason"] = "Job advertisement listing; no outcome for this candidate."
    # "No risk flags detected." is the model answering "none" in prose, and an
    # empty answer written into a list is not an empty list: every consumer
    # reads `bool(risk_flags)` and sees a risk. That alone forced a Karat
    # interview reminder to manual review at confidence 1.0.
    #
    # Only a self-negating entry is dropped. Real prose risks the model writes
    # -- "No specific interview schedule provided", "No Offer Detected" -- name
    # a thing that is missing and are kept, as is every code-shaped flag.
    value["risk_flags"] = [
        flag for flag in (value.get("risk_flags") or [])
        if not _RISK_FLAG_MEANS_NONE.match(str(flag))
    ]
    confidence = float(value["confidence"])
    interview_statuses = {"INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED"}
    proposed_status = str(value.get("status") or "").upper()
    assertive_interview_status = str(context.get("interview_event") or "NONE").upper()
    if proposed_status in interview_statuses or (not pure_mode and assertive_interview_status in interview_statuses):
        # Ollama can return a correct interview event and schedule while also
        # returning a contradictory generic workflow boolean or a speculative
        # lifecycle status such as JOINING_CONFIRMED. The model must not veto
        # or replace an assertive interview event recognized from the original
        # source text.
        safe_interview_status, _ = (proposed_status, None) if pure_mode else validate_interview_event(
            assertive_interview_status
            if assertive_interview_status in interview_statuses
            else proposed_status,
            context,
        )
        if safe_interview_status != "NONE":
            source_schedule = extract_interview_schedule(
                str((message or {}).get("subject") or ""),
                str((message or {}).get("body") or ""),
                sent_at=(message or {}).get("sent_at"),
            )
            interview = value.setdefault("interview", {})
            # Canonicalize a model-supplied 24-hour time only when it describes
            # the same minute as the explicit 12-hour source value. Missing or
            # conflicting model fields still fail the normal safety checks.
            model_time = str(interview.get("time") or "").strip()
            source_time = str(source_schedule.get("time") or "").strip()
            if model_time and source_time:
                try:
                    model_parsed = datetime.strptime(model_time, "%H:%M")
                    source_parsed = datetime.strptime(source_time.upper(), "%I:%M %p")
                    if (
                        model_parsed.hour * 60 + model_parsed.minute
                        == source_parsed.hour * 60 + source_parsed.minute
                    ):
                        interview["time"] = source_time
                except ValueError:
                    pass
            model_timezone = str(interview.get("timezone") or "").strip()
            source_timezone = str(source_schedule.get("timezone") or "").strip()
            if (
                model_timezone.upper() in {"IST", "ASIA/KOLKATA"}
                and source_timezone == "Asia/Kolkata"
            ):
                interview["timezone"] = source_timezone
            # The deterministic source parser reads an explicit visible range
            # from the email body. Preserve it even if a model omitted the end.
            source_end_time = str(source_schedule.get("end_time") or "").strip()
            if source_end_time:
                interview["end_time"] = source_end_time
                interview["duration_minutes"] = source_schedule.get("duration_minutes")
            classification = store._STATUS_CLASSIFICATION[safe_interview_status]
            value.update(
                status=safe_interview_status,
                classification=classification,
                candidate_status=store._CLASSIFICATION_STATUS[classification],
                is_selection_or_offer_related=True,
                should_create_review_record=True,
                is_job_outcome=True,
                is_current_event=True,
                business_domain="INTERVIEW_TRACKING",
                interview_event=safe_interview_status,
                lifecycle_event="NONE",
                ignore_reason=None,
            )
            if safe_interview_status != proposed_status:
                # The model's status has just been replaced, so its request for
                # review described a verdict that no longer exists. Karat sent
                # "Your Altimetrik Interview ... Is Coming Up!" with the date,
                # the hour and the joining link; both models read it as
                # SELECTION_NEEDS_REVIEW / interview_shortlisted and set
                # requires_manual_review. The source text assertively entails
                # INTERVIEW_CONFIRMED, so the status was corrected here -- and
                # the stale boolean rode along into
                # validate_ai_for_booking, which refused the booking as
                # AI_REQUIRES_REVIEW at confidence 1.0.
                #
                # This clears only that boolean, and only where the source
                # itself carried the assertion: validate_interview_event has
                # already refused anything the deterministic context does not
                # support. A model that agrees on the status keeps its veto,
                # MODEL_DISAGREEMENT still forces review immediately below, and
                # every later gate -- confidence, evidence, payment, duplicate,
                # conflict, slot -- is untouched.
                value["requires_manual_review"] = False
                value["manual_review_cleared_from"] = proposed_status
    # An unresolved disagreement about a booking-relevant status. `_reconcile_
    # model_results` has already decided that this one cannot be settled from
    # the two readings alone, so nothing here may accept a transition. It goes
    # back for an automatic retry rather than to a person: a later attempt runs
    # against a different execution state and frequently settles it.
    if "MODEL_DISAGREEMENT" in {str(flag).upper() for flag in value.get("risk_flags") or []}:
        value.update(
            status="AI_RETRY_PENDING", classification="ai_retry_pending",
            candidate_status="AI Retry Pending", is_selection_or_offer_related=False,
            should_create_review_record=False, requires_manual_review=False,
            ignore_reason="MODEL_DISAGREEMENT", validation_status="RETRY_PENDING",
            lifecycle_event="NONE", interview_event="NONE", business_domain="NONE",
            is_job_outcome=False, is_current_event=False,
            backend_transition_validated=False,
            backend_validation_reason="MODEL_DISAGREEMENT",
        )
        return
    # Status is the model's lifecycle assertion; the two booleans are merely
    # descriptive duplicates and small models can contradict their own status.
    # Route every tracked assertion through closed-world source validation.
    # The positive flags are restored only after that validation succeeds.
    if pure_mode:
        from services.recruitment_automation import LEGACY_UNCERTAIN_STATUSES, normalize_analysis
        if str(value["status"]).upper() in LEGACY_UNCERTAIN_STATUSES | {"AI_RETRY_PENDING"}:
            normalize_analysis(value)
            return
    positive = value["status"] in TRACKED_STATUSES
    if not positive:
        value["status"] = "IGNORED_NOT_OFFER_RELATED"
        value["classification"] = "not_relevant"
        value["candidate_status"] = "Profile Active"
        value["should_create_review_record"] = False
        value["requires_manual_review"] = False
        value["ignore_reason"] = value.get("ignore_reason") or "AI_NOT_OFFER_RELATED"
        value["validation_status"] = "REJECTED"
        value["lifecycle_event"] = "NONE"
        value["interview_event"] = "NONE"
        value["business_domain"] = "NONE"
        value["is_job_outcome"] = False
        value["backend_transition_validated"] = False
        value["backend_validation_reason"] = value["ignore_reason"]
        return
    proposed_status = str(value.get("status") or "").upper()
    if pure_mode:
        # Ollama owns intent. Keyword-derived context is retained in the trace,
        # never used to veto or replace its proposed transition. This is not
        # acceptance: the shared verbatim/entailment, confidence and schedule
        # guards below still have to prove it before any booking can run.
        safe_status, rejection_reason = proposed_status, None
    elif value["status"] in interview_statuses:
        safe_status, rejection_reason = validate_interview_event(value["status"], context)
    else:
        safe_status, rejection_reason = validate_lifecycle_event(value["status"], context)
        # Primary and validator can agree that a message is a genuine lifecycle
        # event while choosing an adjacent stage (for example, calling an
        # attached appointment letter JOINING_CONFIRMED). The backend-final
        # layer may correct that stage only when the deterministic source parser
        # has already selected a canonical, explicit lifecycle transition and
        # that transition passes its own closed-world validator. This does not
        # rescue NONE/promotional context, unknown statuses, or model
        # disagreement; each of those continues to fail closed above/below.
        if (
            safe_status == "NONE"
            and rejection_reason == "PROPOSED_EVENT_NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT"
        ):
            asserted_status = str(context.get("lifecycle_event") or "NONE").upper()
            corrected_status, _corrected_reason = validate_lifecycle_event(
                asserted_status, context,
            )
            if corrected_status != "NONE":
                safe_status = corrected_status
                rejection_reason = None
                value["backend_stage_corrected_from"] = proposed_status
                value["backend_stage_resolution_reason"] = (
                    "MODEL_STAGE_MISMATCH_CORRECTED_BY_EXPLICIT_CONTEXT"
                )
    if safe_status == "NONE":
        # "Not supported" is a disagreement, not proof of noise. Content the
        # deterministic layer positively recognises as non-outcome leaves
        # through the branch above carrying its own reason -- JOB_ADVERTISEMENT,
        # RECRUITER_QUESTIONNAIRE, JOB_BOARD_NOTIFICATION -- so the only
        # proposals that reach here with a NOT_SUPPORTED reason are ones the
        # model asserted and the source could not corroborate either way.
        #
        # Dropping those silently lost real interviews. Two mails from one
        # genuine Zealogics AI interview, "Interview Access Code" and "Reminder:
        # Interview Link Expires in 1 hour", were discarded this way at
        # confidence 1.0 with no event, no notification and nothing on any
        # screen -- and, because the model is not reproducible run to run, the
        # same mail could land on needs_review instead on the next pass.
        #
        # Nothing here books anything. backend_transition_validated stays
        # False, which is exactly what should_route_to_mail_alert requires of an
        # OLLAMA result, so these reach the review queue and never the alert
        # list. The transition itself is still refused: safe_status is NONE and
        # no lifecycle or interview event is recorded.
        # Narrow on purpose, because this veto is mostly right. Measured over
        # the whole production corpus, 51 proposals were refused this way and
        # most were the veto catching a hallucination: SELECTED on a TCS "OTP
        # for login", HR_CONFIRMATION on "your Uber account is active",
        # SELECTED on a GDPR retention notice and on "New jobs posted"
        # blasts. Surfacing those would hand an operator ~36 mails that are
        # correctly ignored today.
        #
        # One group behaves differently. Of the 51, six were a claim that a
        # specific interview is scheduled or moved, and all six were genuine:
        # the Zealogics access code and expiry reminder, two Sourcebae
        # confirmations, Accenture's "We're looking forward to your interview",
        # and a flocareer "Missed Interview Opportunity". A scheduled-interview
        # claim is the one the source can least afford to lose, and the one the
        # model is least prone to invent.
        #
        # Both conditions matter: the relevance gate must have established this
        # recipient's own hiring process, so a newsletter promising an
        # "interview preparation guide" still proves nothing and stays quietly
        # ignored.
        # Interview activity with nothing bookable in it.
        #
        # Measured on production: "Time to schedule your interview with
        # Accenture!" and "Action Required: Select Your Preferred Interview
        # Slots" are real, and the candidate has to act on them -- but neither
        # carries a time, so there is nothing to book and re-reading cannot
        # invent one. Parked for review they sat unseen; ignored they would
        # vanish, which is how an interview goes missing.
        #
        # INTERVIEW_UPDATE is where they belong and it already exists: it is
        # not in `interview_auto_booking.ACTIONABLE`, so it cannot book, and
        # `advance_candidate_status` excludes it, so it cannot move a stage. It
        # is visible and it commits nothing.
        #
        # The relevance gate is the only discriminator available, and it is not
        # a clean one: "TCS || JD || ServiceNow developer" comes back
        # ESTABLISHED / RECIPIENT_HIRING_PROCESS exactly as the Accenture mail
        # does, so a JD discussion lands here too. That is the direction to err
        # -- it books nothing, and separating them would need a subject keyword
        # rule, which is the shortcut this file has been removing.
        # Only when there is no time in it. A claim that *does* carry a
        # schedule the source has not corroborated is a different thing: a
        # later read may quote it, so `normalize_analysis` sends that one back
        # for another attempt, which is right. Retrying a mail that names no
        # time can only loop until it is parked, and those terminal parks are
        # the backlog this work is trying to empty.
        proposed_interview = value.get("interview") or {}
        has_a_time = all(
            str(proposed_interview.get(key) or "").strip()
            for key in ("date", "time")
        )
        unsupported_proposal = (
            rejection_reason in {
                "INTERVIEW_EVENT_NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT",
                "PROPOSED_EVENT_NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT",
            }
            and proposed_status in {"INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED"}
            and str((relevance or {}).get("decision") or "").upper() == "ESTABLISHED"
            and str((relevance or {}).get("message_kind") or "").upper()
                == CANDIDATE_HIRING_MESSAGE_KIND
            and not has_a_time
        )
        value.update(
            status="INTERVIEW_UPDATE" if unsupported_proposal else "IGNORED_NOT_OFFER_RELATED",
            classification="interview_update" if unsupported_proposal else "not_relevant",
            candidate_status="Interview In Progress" if unsupported_proposal else "Profile Active",
            is_selection_or_offer_related=False,
            should_create_review_record=unsupported_proposal,
            requires_manual_review=False,
            ignore_reason=None if unsupported_proposal else (rejection_reason or context["email_intent"]),
            validation_status="INTERVIEW_ACTIVITY" if unsupported_proposal else "REJECTED",
            lifecycle_event="NONE",
            is_job_outcome=False,
            evidence_summary=context["evidence_summary"],
            summary=context["evidence_summary"],
            interview_event="NONE",
            business_domain="NONE",
            backend_transition_validated=False,
            backend_validation_reason=rejection_reason or "OUTCOME_NOT_ASSERTED",
            downgraded_from=proposed_status,
        )
        if unsupported_proposal:
            value["reason"] = (
                f"The model read this as {proposed_status} and the source did not "
                "corroborate it, so this is recorded as interview activity. "
                "Nothing is booked, and no date or time is inferred."
            )
            value["risk_flags"] = list(dict.fromkeys(
                (value.get("risk_flags") or []) + ["PROPOSAL_NOT_CORROBORATED"]
            ))
        return
    sources = _source_texts((message or {}).get("subject", ""), (message or {}).get("body", ""), attachments, (message or {}).get("thread_context"))
    # An assessment invitation is not an interview invitation. Until this status
    # existed the model had no way to say so, and expressed a test window as an
    # interview: the guard below then refused it -- correctly, since no sentence
    # in the mail asserts an interview -- and the mail was retried forever.
    #
    # Promote it to the status its own words assert, and only when the source
    # asserts no interview at all, so an interview mail that happens to mention
    # an assessment round stays an interview.
    if (safe_status in {"INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_SHORTLISTED", "INTERVIEW_UPDATE"}
            and not _source_asserts(sources, safe_status)
            and _source_asserts(sources, "ASSESSMENT_INVITED")):
        value["promoted_from"] = safe_status
        safe_status = "ASSESSMENT_INVITED"
    # The status is the validated transition. Model-supplied classification and
    # candidate labels are descriptive duplicates and must not contradict it.
    classification = store._STATUS_CLASSIFICATION[safe_status]
    value.update(
        status=safe_status,
        classification=classification,
        candidate_status=store._CLASSIFICATION_STATUS[classification],
        is_recruitment_related=True,
        is_selection_or_offer_related=True,
        should_create_review_record=True,
        is_job_outcome=True,
        is_current_event=True,
        ignore_reason=None,
    )
    is_interview_event = safe_status in interview_statuses
    value["lifecycle_event"] = "NONE" if is_interview_event else safe_status
    value["interview_event"] = safe_status if is_interview_event else "NONE"
    value["business_domain"] = "INTERVIEW_TRACKING" if is_interview_event else "SELECTION_TRACKING"
    if not pure_mode:
        value["email_intent"] = context["email_intent"]
        value["document_type"] = context["document_type"]
    value["is_job_outcome"] = True
    value["is_current_event"] = True
    if not pure_mode:
        value["evidence_summary"] = context["evidence_summary"]
    value["evidence"] = [
        {**item, "text": redact_sensitive_text(str(item.get("text") or ""))}
        for item in value.get("evidence") or []
    ]
    # Keep only evidence that is verbatim in the source, and require at least
    # one such item — rather than discarding the whole classification because a
    # single item was paraphrased. The verbatim test is what protects against
    # invented evidence, and it still applies to everything that survives:
    # unsupported items are dropped, never trusted. Rejecting outright threw
    # away a correct OFFER_IN_PROGRESS at 95% whose body quote was verbatim,
    # purely because a second item was a paraphrase.
    source_canonical = [
        corrected for item in value["evidence"]
        if (corrected := _canonicalise_evidence_source(item, sources)) is not None
    ]
    supported = [item for item in source_canonical if _evidence_supported(item, sources)]
    entailing = [
        item for item in supported
        if _evidence_entails_source_transition(item, sources, safe_status)
    ]
    if supported and not entailing:
        # Only when the model quoted the mail and picked the wrong sentence --
        # never when it invented one.
        #
        # `supported` holds the excerpts that are verbatim in the source. If it
        # is empty the model fabricated its evidence, and that is exactly what
        # the verbatim test exists to catch: the conclusion is discarded even
        # if the source would have supported it, because a model inventing
        # quotes cannot be trusted on this message at all.
        #
        # This branch is the other case.
        #
        # Karat's reminder was classified INTERVIEW_CONFIRMED correctly and then
        # thrown away, because the excerpt cited described the interview's
        # *format* -- "Your interview will be a live video call lasting
        # approximately 60 minutes" -- while the sentence that proves the
        # interview exists sat elsewhere in the same body: "This is a quick
        # reminder that your Altimetrik interview ... is coming up soon!"
        #
        # So look for an entailing sentence in the verified source. This does
        # not weaken the rule: an entailing verbatim excerpt is still required,
        # it is quoted from the source rather than invented, and a mail that
        # contains no such sentence is still rejected exactly as before. It
        # only stops the outcome depending on which sentence the model picked.
        entailing = _entailing_evidence_from_source(sources, safe_status)
        if entailing:
            supported = supported + entailing
            value["backend_evidence_recovered"] = True
    if not entailing:
        if pure_mode:
            # Unproved AI intent is uncertainty, not proof of a job ad. Keep
            # the mail on the automatic retry path without authorizing a
            # confirmation, reschedule or cancellation from keywords alone.
            value.update(
                status="AI_RETRY_PENDING", classification="ai_retry_pending",
                candidate_status="AI Retry Pending", is_selection_or_offer_related=False,
                should_create_review_record=False, requires_manual_review=False,
                ignore_reason="EVIDENCE_DOES_NOT_ENTAIL_TRANSITION",
                validation_status="RETRY_PENDING", lifecycle_event="NONE",
                interview_event="NONE", business_domain="NONE", is_job_outcome=False,
                is_current_event=False, evidence=supported,
                backend_transition_validated=False,
                backend_validation_reason="EVIDENCE_DOES_NOT_ENTAIL_TRANSITION",
                downgraded_from=proposed_status,
            )
            return
        # Two different failures were landing in the same silent ignore.
        #
        # If the mail itself contains a sentence entailing the transition, the
        # classification is plausibly right and the model simply failed to
        # quote it -- it paraphrased, so nothing it cited is verbatim and the
        # anti-hallucination guard refused to trust it. That is not the same as
        # a mail that says no such thing. It must never auto-book, because no
        # verified verbatim evidence entails the transition, but disappearing
        # as "not offer related" hides a real interview from everyone.
        #
        # A mail whose source proves nothing stays rejected exactly as before,
        # which is what keeps rejections and job adverts out.
        if _entailing_evidence_from_source(sources, safe_status):
            value.update(
                status="MANUAL_REVIEW_REQUIRED", classification="needs_review",
                candidate_status="Needs Review", is_selection_or_offer_related=False,
                should_create_review_record=True, requires_manual_review=True,
                ignore_reason="EVIDENCE_NOT_VERBATIM", validation_status="NEEDS_REVIEW",
                lifecycle_event="NONE", interview_event="NONE", business_domain="NONE",
                is_job_outcome=False, is_current_event=False, evidence=supported,
                backend_transition_validated=False,
                backend_validation_reason="EVIDENCE_NOT_VERBATIM",
                downgraded_from=proposed_status,
                summary=(
                    "The email supports this outcome but the AI did not quote it "
                    "verbatim, so it needs a human to confirm before booking."
                ),
            )
            return
        # The source can prove the transition without any single sentence
        # entailing it. `classify_context` reads the whole mail -- interview
        # wording, a real date, a time, a joining link -- and names the
        # transition from that shape, while `_entailing_evidence_from_source`
        # matches a sentence against `_TRANSITION_ASSERTIONS`. One question,
        # two vocabularies, and they disagree: flocareer writes "Remember to
        # attend the video interview today at 06:30 PM IST", which the shape
        # reader calls INTERVIEW_CONFIRMED and the sentence matcher does not
        # recognise at all.
        #
        # Silently ignoring those hid real interviews. Of the 26 mails refused
        # this way in production, 23 had the deterministic layer naming the
        # very transition the model proposed, and they read "Interview
        # scheduled with Mphasis on Sat, August 15", "You are invited for
        # interview with Deloitte", "Interview Call Letter".
        #
        # This books nothing. backend_transition_validated stays False, so
        # should_route_to_mail_alert still refuses it and auto-booking still
        # refuses it; the mail reaches the review queue instead of vanishing.
        # A mail whose deterministic reading does not name this transition is
        # still rejected exactly as before, which is what keeps rejections and
        # job adverts out.
        asserted_by_source = safe_status in {
            str(context.get("interview_event") or "NONE").upper(),
            str(context.get("lifecycle_event") or "NONE").upper(),
        }
        if asserted_by_source and safe_status == "INTERVIEW_CANCELLED":
            # Deterministic source evidence may release a booking. It may never
            # create one.
            #
            # Both readings agree the interview is off and only the verbatim
            # quote is missing, and the asymmetry is what makes acting safe: a
            # cancellation books nothing, cannot put a candidate at a wrong
            # time, and a later invitation simply books again. Holding it back
            # is the option with a victim -- Pujitha's Persistent Systems slot
            # stayed confirmed for an interview the mail said she had missed,
            # because the model paraphrased "you missed your interview slot"
            # instead of quoting it.
            #
            # Confirmations and reschedules are not covered: those would commit
            # a candidate to a time no quoted sentence supports, which is the
            # anti-hallucination guard doing its job.
            value.update(
                classification="interview_cancelled", candidate_status="Interview Cancelled",
                is_selection_or_offer_related=True, should_create_review_record=True,
                requires_manual_review=False, ignore_reason=None,
                validation_status="AUTO_VALIDATED",
                interview_event="INTERVIEW_CANCELLED", evidence=supported,
                backend_transition_validated=True,
                backend_validation_reason="SOURCE_ASSERTS_CANCELLATION_UNQUOTED",
                summary=(
                    "The source parser reads this as the interview being called off and the "
                    "model agrees; releasing a booking needs no quoted sentence, because it "
                    "commits the candidate to nothing."
                ),
            )
            return
        if asserted_by_source:
            # The source names this transition but nothing quoted entails it,
            # and it is not a release. Another attempt against a different
            # execution state often quotes it; a person is never asked.
            value.update(
                status="AI_RETRY_PENDING", classification="ai_retry_pending",
                candidate_status="AI Retry Pending", is_selection_or_offer_related=False,
                should_create_review_record=False, requires_manual_review=False,
                ignore_reason="TRANSITION_UNQUOTED", validation_status="RETRY_PENDING",
                lifecycle_event="NONE", interview_event="NONE", business_domain="NONE",
                is_job_outcome=False, is_current_event=False, evidence=supported,
                backend_transition_validated=False,
                backend_validation_reason="SOURCE_ASSERTS_TRANSITION_UNQUOTED",
                downgraded_from=proposed_status,
                summary=(
                    "The email reads as this outcome and the source parser agrees, but no "
                    "quoted sentence entails it, so it is read again rather than booked."
                ),
            )
            value["risk_flags"] = list(dict.fromkeys(
                (value.get("risk_flags") or []) + ["TRANSITION_UNQUOTED"]
            ))
            return
        value.update(
            status="IGNORED_NOT_OFFER_RELATED", classification="not_relevant",
            candidate_status="Profile Active", is_selection_or_offer_related=False,
            should_create_review_record=False, requires_manual_review=False,
            ignore_reason="EVIDENCE_DOES_NOT_ENTAIL_TRANSITION",
            validation_status="REJECTED", lifecycle_event="NONE",
            interview_event="NONE", business_domain="NONE", is_job_outcome=False,
            is_current_event=False, evidence=supported,
            backend_transition_validated=False,
            backend_validation_reason="EVIDENCE_DOES_NOT_ENTAIL_TRANSITION",
            downgraded_from=proposed_status,
            summary="Source evidence does not entail the proposed candidate lifecycle transition.",
        )
        return
    # Promote unambiguous employer phrasing to a meaning code before the value
    # is persisted, so the routing gate sees a code without being loosened.
    value["evidence"] = [normalise_evidence_meaning(item) for item in entailing]
    value["backend_transition_validated"] = True
    value["backend_validation_reason"] = (
        "EXPLICIT_TRANSITION_ENTAILED_AFTER_STAGE_CORRECTION"
        if value.get("backend_stage_corrected_from")
        else "EXPLICIT_TRANSITION_ENTAILED"
    )
    auto_threshold = max(0.8, min(0.99, float(os.getenv("AI_RECRUITMENT_AUTO_ACCEPT_THRESHOLD", "0.90"))))
    review_threshold = max(0.0, min(1.0, float(os.getenv("OLLAMA_CONFIDENCE_THRESHOLD", "0.75"))))
    if confidence < review_threshold:
        # Too unsure to record as anything. Another attempt runs against a
        # different execution state and often is sure, so it retries rather
        # than waiting on a person. `should_create_review_record` is cleared so
        # no event is written and the retry exit in `process_message` claims it.
        value.update(status="AI_RETRY_PENDING", classification="ai_retry_pending",
                     candidate_status="AI Retry Pending",
                     should_create_review_record=False, requires_manual_review=False,
                     ignore_reason="AI_CONFIDENCE_BELOW_THRESHOLD",
                     reason="AI confidence is below the configured automatic-update threshold")
        value["validation_status"] = "RETRY_PENDING"
    elif confidence < auto_threshold:
        # Medium confidence. Sure enough to record, not sure enough to act on
        # by itself -- which `validation_status` already expresses, because
        # `advance_candidate_status` moves a candidate only on AUTO_VALIDATED
        # and `interview_auto_booking` applies its own thresholds on top.
        #
        # Overwriting the status with MANUAL_REVIEW_REQUIRED added nothing to
        # that and cost the mail its reading. It is also where every resolved
        # model disagreement landed: the reconciler caps a reconciled result at
        # 0.89, just under the 0.90 auto-accept threshold, so all 466 of them
        # arrived here and were relabelled "Needs Review" despite the two
        # readers having been reconciled. The stage is kept instead.
        #
        # The carve-out for a fully scheduled interview is unchanged, and so is
        # every gate below: a non-actionable classification cannot reach
        # `interview_auto_booking.ACTIONABLE` at all, and an actionable one
        # still faces the booking thresholds, the evidence guard, payment,
        # duplicate and conflict.
        actionable_interview = value.get("classification") in {"interview_confirmed", "interview_rescheduled"}
        explicit_schedule = all(str((value.get("interview") or {}).get(key) or "").strip() for key in ("date", "time", "timezone"))
        if actionable_interview and not (explicit_schedule and not value.get("risk_flags")):
            # An interview this system could act on, without the schedule that
            # would let it. Undecided, so it retries; it must not be recorded
            # as a confirmed interview nobody can book.
            value.update(status="AI_RETRY_PENDING", classification="ai_retry_pending",
                         candidate_status="AI Retry Pending",
                         should_create_review_record=False, requires_manual_review=False,
                         ignore_reason="MEDIUM_CONFIDENCE_INCOMPLETE_SCHEDULE")
            value["validation_status"] = "RETRY_PENDING"
        else:
            value["requires_manual_review"] = False
            value["ignore_reason"] = None
            value["validation_status"] = "MEDIUM_CONFIDENCE"
    else:
        # The obsolete UI flag cannot veto a source-entailed transition. Real
        # risk flags still fail closed and are normalized into automatic retry.
        value["requires_manual_review"] = bool(value.get("risk_flags"))
        value["ignore_reason"] = None
        value["validation_status"] = "NEEDS_REVIEW" if value["requires_manual_review"] else "AUTO_VALIDATED"
    # A date the model mis-spelled in one auxiliary field is a formatting slip,
    # not grounds to throw away a correct classification. Raising here sent the
    # message back down the deterministic-failure path, where two retries said
    # the same thing and it was parked: an Innominds "Welcome aboard, complete
    # the pre-onboarding formalities" and a digiverifier BGV invitation were
    # both lost this way and never reached Mail Alerts.
    #
    # Interview dates have had `_normalise_interview_date` for exactly this
    # since ValueMomentum's "20-Jul-2026"; offer dates never got it. Read what
    # can be read, drop what cannot, and record that it was dropped. An
    # all-numeric "12/07/2026" is still refused rather than guessed, because
    # day-first and month-first cannot be told apart and the wrong joining date
    # is worse than none.
    offer = value.get("offer") or {}
    for field in ("offer_date", "joining_date", "offer_expiry_date"):
        raw = offer.get(field)
        if not raw:
            continue
        normalised = _normalise_interview_date(raw)
        offer[field] = normalised or None
        if not normalised:
            value["risk_flags"] = list(dict.fromkeys(
                (value.get("risk_flags") or []) + [f"UNREADABLE_OFFER_DATE_{field.upper()}"]
            ))
    value["offer"] = offer
    if value.get("classification") in {"interview_confirmed", "interview_rescheduled"}:
        interview = value.get("interview") or {}
        date_valid = True
        time_valid = True
        tz_valid = True
        received = (message or {}).get("sent_at") or (message or {}).get("email_date")
        normalised_date = _normalise_interview_date(interview.get("date"), received)
        if normalised_date:
            interview["date"] = normalised_date
            value["interview"] = interview
        else:
            date_valid = False
        normalised_time = _normalise_interview_time(interview.get("time"))
        if not normalised_time:
            # The model sometimes reformats a clearly-stated AM/PM time into
            # 24-hour ("14:00 - 15:00") while quoting the source's own
            # "2:00 PM - 3:00 PM IST" verbatim in its evidence. Recover the
            # stated time from the subject or from evidence quotes, which the
            # check above already verified appear verbatim in the source.
            # Nothing is invented: a source with no AM/PM anywhere still fails,
            # so a bare 17:00 is rejected exactly as before.
            source_texts = [str((message or {}).get("subject") or "")] + [
                str(item.get("text") or "") for item in value.get("evidence") or []
            ]
            for candidate_text in source_texts:
                recovered = _normalise_interview_time(candidate_text)
                if recovered:
                    normalised_time = recovered
                    break
            if not normalised_time:
                # Some recruiters simply write the schedule on a 24-hour clock:
                # EY sent "Time: 16:30 to 17:30" and Accenture "Time: 12:00:00
                # until 13:00:00 IST (24 Hours)". Requiring AM/PM used the
                # meridiem as a proxy for "the source really stated a time",
                # which is too strict for an hour that has only one possible
                # reading, so both interviews looped until they were parked.
                # Recovery still reads only source-verified text, so a source
                # with no clock time at all recovers nothing, and an ambiguous
                # bare 1-12 is still refused.
                normalised_time = _recover_unambiguous_24_hour_time(source_texts)
        if normalised_time:
            interview["time"] = normalised_time
            value["interview"] = interview
        else:
            time_valid = False
        normalised_zone = _normalise_interview_timezone(interview.get("timezone"))
        if normalised_zone:
            interview["timezone"] = normalised_zone
            value["interview"] = interview
        else:
            tz_valid = False
        if not (date_valid and time_valid and tz_valid):
            missing = []
            if not date_valid: missing.append("date")
            if not time_valid: missing.append("time")
            if not tz_valid: missing.append("timezone")
            # Booking still must not fire on a schedule nobody could read, but
            # raising discarded the whole detection: an Accenture "Your
            # Interview has been successfully Scheduled" looped on
            # OLLAMA_SCHEMA_VALIDATION_FAILED and was parked, so the interview
            # never surfaced anywhere. Downgrading keeps the finding, and the
            # classification is no longer interview_confirmed, so auto-booking
            # cannot pick it up.
            #
            # It is recorded as interview activity rather than sent to a person.
            # "Action Required: Select Your Preferred Interview Slots with
            # Accenture" has no time because the candidate has not chosen one
            # yet -- there is nothing to book and nothing a re-read would find,
            # so a retry would only loop. The unreadable fields are cleared
            # rather than guessed: no date or time is ever inferred here.
            for field, usable in (("date", date_valid), ("time", time_valid), ("timezone", tz_valid)):
                if not usable:
                    interview[field] = None
            value["interview"] = interview
            value.update(
                status="INTERVIEW_UPDATE", classification="interview_update",
                candidate_status="Interview In Progress", should_create_review_record=True,
                requires_manual_review=False, ignore_reason=None,
                reason="Interview schedule could not be read: " + ", ".join(missing),
            )
            value["validation_status"] = "INTERVIEW_ACTIVITY"
            value["risk_flags"] = list(dict.fromkeys(
                (value.get("risk_flags") or []) + ["INTERVIEW_SCHEDULE_UNREADABLE"]
            ))


_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

# "20-Jul-2026" / "06th July 2026" / "20 July, 2026"
_DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})(?:ST|ND|RD|TH)?[\s\-/,]+([A-Z]{3,9})[\s\-/,]+(\d{4})\b", re.I,
)
# "Jul 20, 2026" / "July 20 2026"
_MONTH_DAY_YEAR = re.compile(
    r"\b([A-Z]{3,9})[\s\-/,]+(\d{1,2})(?:ST|ND|RD|TH)?[\s\-/,]+(\d{4})\b", re.I,
)


#: What the relevance gate must answer before a calendar invite may be booked
#: as an interview. Every other kind -- MARKETING_OR_TRAINING, NEWSLETTER,
#: PUBLIC_EVENT, JOB_ADVERTISEMENT, GENERAL, UNKNOWN -- describes an event that
#: is not this recipient's interview, whatever its invite says.
CANDIDATE_HIRING_MESSAGE_KIND = "RECIPIENT_HIRING_PROCESS"


def calendar_invite_intent(
    message: dict[str, Any], attachment_texts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Ollama's own reading of what a mail carrying a calendar invite really is.

    The .ics stays the authority on *when* -- it is exact, and 77 of 91 confirmed
    interviews come from one. What it cannot say is *whether the event is this
    candidate's interview*. A Zoom workshop registration carries a perfectly
    valid invite, and one was auto-booked for a real candidate as an interview
    for "ChatGPT & 10+ AI Tools - IND" before an operator cancelled it.

    Until now this path skipped classification entirely: `process_message` took
    the calendar result and never called `analyze()`, so no model ever judged
    the mail. The relevance gate already knows how to make this call -- its
    prompt names webinars, masterclasses, training and public events explicitly
    -- it simply was not being asked.

    The deterministic shortcut is deliberately not used here. It answered
    ESTABLISHED / RECIPIENT_HIRING_PROCESS for three handwriting-therapy
    webinars, which is precisely the judgement under test, so the model is
    asked directly.

    Raises AIGatewayError if the model cannot be reached: the caller sends those
    to review rather than guessing in either direction.
    """
    payload = _analysis_payload(message, attachment_texts)
    response = chat_structured(
        messages=[
            {"role": "system", "content": RELEVANCE_PROMPT},
            {"role": "user", "content": _prompt_json(payload)},
        ],
        schema=RELEVANCE_SCHEMA,
        model=configured_models()["primary"],
        temperature=0,
        max_retries=0,
        workload="calendar_invite_intent",
    )
    return _validate_relevance_result(parse_model_json(response.content), payload)


def calendar_invite_is_a_candidate_interview(relevance: dict[str, Any]) -> bool:
    """Only a recipient's own hiring process may be booked from an invite."""
    return (
        str(relevance.get("decision") or "").upper() == "ESTABLISHED"
        and str(relevance.get("message_kind") or "").upper() == CANDIDATE_HIRING_MESSAGE_KIND
    )


def calendar_invite_verdict(
    relevance: dict[str, Any], *, calendar_result: dict[str, Any] | None = None,
    message: dict[str, Any] | None = None,
) -> str:
    """BOOK, RETRY or IGNORE for a mail carrying a calendar invite.

    `decision` is the question the relevance prompt actually asks -- ESTABLISHED
    means the source ties this recipient to a real hiring process, and the same
    prompt says marketing, training, webinars and public events are
    NOT_ESTABLISHED. `message_kind` is a label describing what the mail is.

    Requiring both agreed with each other silently dropped real interviews.
    Three genuine cancellations for named candidates -- an eOne L1 interview, a
    Skillmine round one, an Altisource discussion -- came back ESTABLISHED with
    the kind given as MARKETING_OR_TRAINING or GENERAL, quoting the candidate's
    own interview line as evidence. Their bodies are disclaimer and stylesheet
    boilerplate, because a cancellation carries its meaning in the subject and
    the .ics, so the model has little to label the mail from and gets the label
    wrong while getting the decision right.

    Nothing is loosened for webinars: every marketing sample checked in
    production -- the Zoom workshop, the Naukri bootcamp, Yocket, Talent500,
    Impacteers and the GraphoTherapy mailing list -- answered NOT_ESTABLISHED,
    which still ignores them here. What changes is that a contradictory answer
    is no longer read as a rejection. It is not read as a booking either.
    """
    decision = str(relevance.get("decision") or "").upper()
    kind = str(relevance.get("message_kind") or "").upper()
    if decision == "ESTABLISHED" and kind == CANDIDATE_HIRING_MESSAGE_KIND:
        return "BOOK"
    # NOT_ESTABLISHED, and the model named this the recipient's own hiring
    # process in the same breath. That pair contradicts itself, and reading it
    # as a rejection lost a real interview: a Microsoft Teams invite from
    # "Thaga, Mohamed" -- METHOD:REQUEST, STATUS:CONFIRMED, SEQUENCE:0, the
    # candidate an ATTENDEE, DTSTART 2026-09-10T18:30 India Standard Time --
    # came back NOT_ESTABLISHED / RECIPIENT_HIRING_PROCESS at 0.85 and was
    # dropped with no event, no alert and no booking. The interview was that
    # evening.
    #
    # This is the mirror of the pairing already handled above, and it is
    # treated the same way: an answer at odds with itself is a question for an
    # operator, never a booking and never silence.
    #
    # A confident non-candidate answer still ignores, which is the whole
    # webinar defence: every marketing sample checked in production -- the Zoom
    # workshop, the Naukri bootcamp, Yocket, Talent500, Impacteers and the
    # GraphoTherapy list -- answered NOT_ESTABLISHED with one of these kinds,
    # never RECIPIENT_HIRING_PROCESS.
    # An explicit NOT_ESTABLISHED is required: a missing or unreadable decision
    # is not a contradiction, it is junk, and junk still fails closed.
    if (
        (decision == "NOT_ESTABLISHED" and kind == CANDIDATE_HIRING_MESSAGE_KIND)
        or (decision == "ESTABLISHED" and kind != CANDIDATE_HIRING_MESSAGE_KIND)
    ):
        # This is a deterministic evidence tie-breaker, not another model
        # decision: authenticated invite structure plus hiring/role context
        # can safely overcome a contradictory label; a clear webinar remains
        # ignored; every other ambiguity returns to the automatic retry queue.
        from services.calendar_interview_evidence import contradiction_resolution
        return contradiction_resolution(relevance, calendar_result, message)
    return "IGNORE"


def calendar_invite_needs_review(relevance: dict[str, Any]) -> bool:
    """Legacy compatibility: ambiguity now retries automatically."""
    return calendar_invite_verdict(relevance) == "RETRY"


def _normalise_interview_timezone(raw) -> str:
    """Canonical name for the zone the sender wrote, or "" when unresolvable.

    The check this replaces only asked whether the field was non-empty, so
    "IST" was stored verbatim and every consumer downstream had to guess again
    what it meant.

    What is stored here is the *source* zone, not Asia/Kolkata. Operations are
    on India time and every booking is converted to it, but the conversion needs
    something to convert from: an interview written as 9:00 AM EST is 6:30 PM
    IST, and that arithmetic is only possible while the sender's own zone is
    still known. `normalized_schedule` does the conversion and records
    Asia/Kolkata as the booking zone alongside this one.

    Resolution is shared with booking so both ends agree on what a name means.
    """
    from services import interview_timezones

    return interview_timezones.label(interview_timezones.resolve(raw))


_RELATIVE_OFFSETS = {
    "day after tomorrow": 2,
    "tomorrow": 1,
    "today": 0,
    "tonight": 0,
    "this afternoon": 0,
    "this morning": 0,
    "this evening": 0,
}

_WEEKDAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_WEEKDAY_PHRASE = re.compile(
    r"\b(this|next|coming|upcoming)?\s*(" + "|".join(_WEEKDAY_NAMES) + r")\b",
    re.IGNORECASE,
)


def _received_date(email_date) -> date | None:
    """The day the mail arrived, which anchors every relative phrase."""
    text = str(email_date or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _resolve_relative_date(raw, email_date) -> str:
    """Turn "tomorrow" or "this Thursday" into a real date, using the arrival day.

    Only the model's own date field is read, never the body: the model has
    already decided this phrase is the interview date, so this canonicalises its
    wording rather than mining the mail for one. With no arrival date to anchor
    against, nothing is resolved -- a relative phrase with no anchor is exactly
    the case that must go to review instead of being guessed at.
    """
    anchor = _received_date(email_date)
    if anchor is None:
        return ""
    text = " ".join(str(raw or "").lower().split())
    if not text:
        return ""
    for phrase, offset in _RELATIVE_OFFSETS.items():
        if re.search(r"\b" + re.escape(phrase) + r"\b", text):
            return (anchor + timedelta(days=offset)).isoformat()
    hit = _WEEKDAY_PHRASE.search(text)
    if not hit:
        return ""
    qualifier = (hit.group(1) or "").lower()
    target = _WEEKDAY_NAMES[hit.group(2).lower()]
    # Always the coming occurrence: a scheduling mail never means a day that
    # has already passed. Same weekday as the mail means the next one, a week
    # out, rather than the day the mail arrived.
    ahead = (target - anchor.weekday()) % 7 or 7
    if qualifier == "next":
        ahead += 7
    return (anchor + timedelta(days=ahead)).isoformat()


def _normalise_interview_date(raw, email_date=None):
    """Canonicalise an interview date to ISO, or "" if it is not a real date.

    `date.fromisoformat` alone rejected values that are unambiguous dates in
    every other respect: ValueMomentum sent "20-Jul-2026" and Cangra sent the
    full ISO timestamp "2026-07-30T12:00:00+05:30". Both looped on
    OLLAMA_SCHEMA_VALIDATION_FAILED until they were parked, so the interviews
    never surfaced.

    Only spellings with a named month or an explicit ISO form are accepted.
    All-numeric forms like "07/08/2026" stay rejected because day-first and
    month-first cannot be told apart, and guessing one would book the wrong day.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    # A full ISO timestamp carries the date unambiguously in its first token.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    for pattern, order in ((_DAY_MONTH_YEAR, "dmy"), (_MONTH_DAY_YEAR, "mdy")):
        hit = pattern.search(text)
        if not hit:
            continue
        first, second, year = hit.group(1), hit.group(2), hit.group(3)
        day_part, month_name = (first, second) if order == "dmy" else (second, first)
        month = _MONTH_NAMES.get(str(month_name).lower())
        if not month:
            continue
        try:
            return date(int(year), month, int(day_part)).isoformat()
        except ValueError:
            return ""
    # Last, and only once every explicit spelling has failed, so a real date
    # always wins: "tomorrow" and "this Thursday" resolved against the day the
    # mail arrived. A benchmark on real production mail put every date the
    # model got wrong in this shape, one of them a day early -- and a booking
    # on the wrong day is the worst outcome this pipeline has.
    return _resolve_relative_date(text, email_date)


# A 24-hour clock reading of 13:00 or later, or 00:xx, has exactly one meaning.
# Only a colon separates the parts: "18.30" is far more often money or a version
# than a time, and a false time is worse than an unread one.
_TWENTY_FOUR_HOUR = re.compile(r"(?<![:.\d])([01]?\d|2[0-3]):([0-5]\d)(?!:?\d*\s*(?:AM|PM))", re.I)


def _recover_unambiguous_24_hour_time(source_texts) -> str:
    """The stated start time when a source writes it on a 24-hour clock.

    Reads only text already verified to appear verbatim in the source, so this
    can never invent a time the sender did not write. An hour of 13-23 (or 00)
    has a single possible reading and is taken as stated. An hour of 1-12 is
    ambiguous on its own and is only accepted when the same passage also states
    an hour of 13 or more, which proves the passage is on a 24-hour clock --
    Accenture's "12:00:00 until 13:00:00" is exactly that shape.
    """
    for text in source_texts:
        found = [
            (int(hit.group(1)), hit.group(2))
            for hit in _TWENTY_FOUR_HOUR.finditer(str(text or ""))
        ]
        if not found:
            continue
        passage_is_24_hour = any(hour >= 13 for hour, _minute in found)
        for hour, minute in found:
            if hour >= 13:
                return "%02d:%s PM" % (hour - 12, minute)
            if hour == 0:
                return "12:%s AM" % minute
            if passage_is_24_hour:
                # 1-11 are morning on a 24-hour clock; 12 is noon.
                return "%02d:%s %s" % (hour, minute, "PM" if hour == 12 else "AM")
    return ""


def _normalise_interview_time(raw):
    """Canonicalise a 12-hour time's formatting, or "" if it is not one.

    The validator demanded exactly "H:MM AM/PM", so a real invite reading
    "2:00 PM - 3:00 PM IST" failed and raised OLLAMA_SCHEMA_VALIDATION_FAILED
    on every retry - identical input, identical failure - so the interview
    never surfaced. A range, an attached zone and spacing are formatting, not
    evidence, so the first stated 12-hour time is taken as the start.

    A 24-hour time is deliberately NOT accepted: the prompt requires an
    explicit AM/PM as evidence the source stated the time unambiguously, and
    an existing test pins that. Forgiving formatting must not forgive
    missing evidence.
    """
    text = str(raw or "").upper()
    hit = re.search(r"(\d{1,2})[:.]([0-5]\d)\s*(AM|PM)", text)
    if hit:
        hour = int(hit.group(1))
        return "%02d:%s %s" % (hour, hit.group(2), hit.group(3)) if 1 <= hour <= 12 else ""
    hit = re.search(r"(?<![:.\d])(\d{1,2})\s*(AM|PM)", text)
    if hit:
        hour = int(hit.group(1))
        return "%02d:00 %s" % (hour, hit.group(2)) if 1 <= hour <= 12 else ""
    return ""


def parse_model_json(raw: str) -> dict[str, Any]:
    value = (raw or "").strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        value = re.sub(r"^```(?:json)?|```$", "", value, flags=re.I | re.M).strip()
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("AI output did not contain a JSON object")
        return json.loads(re.sub(r",\s*([}\]])", r"\1", value[start:end + 1]))


RELEVANCE_PROMPT = """You are the recruitment-relevance gate for TeleAutomation.
Decide only whether the source explicitly describes this recipient's actual hiring or
recruitment process. Return ESTABLISHED only when the message ties this recipient to a
real application, interview process, selection, offer, verification, onboarding, or
joining process. A personalized greeting, a jobs-domain sender, a company name, a
date/time/timezone, a meeting link, a live session, a clinic, a cohort, or event-like
structure is never sufficient by itself.

Marketing, training, masterclasses, webinars, newsletters, public events, educational
sessions, and promotional invitations are NOT_ESTABLISHED unless the source separately
and explicitly states that this recipient is in an actual hiring process. Do not infer
recruitment from audience targeting or from skills/career language. Quote only short
verbatim evidence from the provided source. Return JSON matching the relevance schema."""


CLASSIFIER_PROMPT = """You are TeleAutomation recruitment_email_status_extraction_v4.
A separate relevance gate has already established that this message concerns the
recipient's actual hiring process. Analyze the complete business meaning of the subject,
cleaned body, sender, recipient, thread context, and extracted attachment text. Do not
classify from a single word. Choose the furthest stage that the complete evidence
actually confirms.

Track employment outcomes and material status updates: SELECTED,
FINAL_SELECTION_CONFIRMED, OFFER_INDICATION, OFFER_IN_PROGRESS, OFFER_APPROVED,
OFFER_LETTER_RECEIVED, APPOINTMENT_LETTER_RECEIVED, OFFER_ACCEPTED,
OFFER_DECLINED, OFFER_REVOKED, JOINING_CONFIRMED, JOINING_DATE_UPDATED,
POST_SELECTION_ONBOARDING, BACKGROUND_VERIFICATION, DOCUMENT_VERIFICATION,
COMPENSATION_CONFIRMATION, INTERVIEW_UPDATE, INTERVIEW_SHORTLISTED,
INTERVIEW_CONFIRMED, INTERVIEW_RESCHEDULED, INTERVIEW_CANCELLED,
CANDIDATE_REJECTED, or JOINED. A specific confirmed joining date and post-selection
logistics can establish JOINING_CONFIRMED even when the same email says
"shortlisted". In that case add WORDING_STATUS_CONFLICT.

Use these stages literally and do not collapse one explicit transition into a nearby
stage:
- APPOINTMENT_LETTER_RECEIVED means the recipient's actual appointment letter is
  attached, enclosed, released, or otherwise delivered. It is not JOINING_CONFIRMED
  unless the source separately confirms a joining arrangement.
- OFFER_LETTER_RECEIVED means the recipient's actual formal or official offer letter
  is attached, enclosed, released, or otherwise delivered. Released is not approved;
  use OFFER_APPROVED only when the source explicitly says the offer was approved.
- POST_SELECTION_ONBOARDING means the recipient is asked to complete actual
  pre-onboarding, pre-joining, or onboarding formalities. A mention of a joining date
  as the deadline or context does not make it JOINING_DATE_UPDATED.
- JOINING_DATE_UPDATED requires an explicit change, revision, or replacement of an
  existing joining date. JOINING_CONFIRMED requires an explicit confirmed joining
  arrangement or date.
- BACKGROUND_VERIFICATION means the recipient is asked to begin or complete an actual
  employment background-check or BGV process.
- DOCUMENT_VERIFICATION means the recipient is asked to submit or upload documents
  for candidate, employment, or hiring verification. That request alone does not mean
  SELECTED or JOINING_CONFIRMED.

Do not track job recommendations, alerts, profile matches, invitations to apply,
incomplete applications, recruiter introductions, application acknowledgements,
marketing, or other non-candidate-specific activity. Interview and rejection mail
must use their explicit informational classifications, not offer classifications.

An interview classification first requires explicit text saying that this recipient
has an actual interview/invitation/shortlist in the hiring process. Only after that is
established may date, time, duration, timezone, meeting link, or event structure refine
the interview details. Those schedule fields must never create an interview status.
Use INTERVIEW_CONFIRMED only when a candidate-specific interview has an explicit
date, time, and timezone. Use INTERVIEW_RESCHEDULED only when the
message clearly changes an existing interview and includes the new schedule. Use
INTERVIEW_CANCELLED only for an explicit cancellation. A shortlist without a
schedule is INTERVIEW_SHORTLISTED or INTERVIEW_UPDATE and must never be confirmed.
When an explicit interview end time or duration is present, return it as
interview.end_time or interview.duration_minutes. Never replace a visible duration
with a default value.
For a reschedule or cancellation, preserve any stated prior schedule in
interview.original_date, interview.original_time, and interview.original_timezone.
Never invent schedule, round, company, or meeting link.
SCHEDULE FORMAT. interview.date is YYYY-MM-DD. interview.time and
interview.end_time are 24-hour HH:MM. interview.timezone is a full IANA zone
name such as Asia/Kolkata, UTC, America/New_York -- never a bare abbreviation
like IST, EST or CST, because those name more than one zone. Write IST as
Asia/Kolkata.
Report the time exactly as the sender wrote it, paired with the zone they wrote
it in. Do not convert between zones; a UTC time stays UTC with timezone UTC.
For a range such as 8:30 AM - 9:30 AM, put the start in time and the end in
end_time.
Relative wording is resolved against email_date, which is the day the message
arrived: "tomorrow" is the day after email_date, "this Thursday" and "coming
Thursday" are the next Thursday on or after it. Never resolve a relative phrase
against today's date.
If the date, the time or the zone is not stated, or you are not sure which one
the sender meant, return null for that field. A null sends the mail to a human,
which is the correct outcome. A guessed schedule books a real candidate into the
wrong slot, so never guess one.
First classify email_intent, document_type, business_domain, lifecycle_event, and
interview_event. Questions, requested fields,
questionnaires, job advertisements, payslips, and historical employment documents
must return lifecycle_event NONE and is_job_outcome false. JOINING_CONFIRMED means
a confirmed joining arrangement; JOINED requires an explicit statement that work
actually started. Return confidence as 0-100; the backend normalizes it after parsing.

Requested actions and conditional wording matter. "Complete your application to
move forward" is application-stage, not selection. Evidence must be short verbatim
text present in EMAIL_SUBJECT, EMAIL_BODY, ATTACHMENT, or THREAD_CONTEXT.
Also return the canonical lowercase classification, user-facing candidate_status,
and a concise evidence_summary/reason. Never include bank, PAN, Aadhaar, UAN, PF,
or other financial/government identifiers. Return only JSON matching
selection_offer_event_v1.

EVIDENCE MUST BE COPIED, NOT WRITTEN. Every evidence `text` has to be an exact
character-for-character substring of the source you were given. Copy one whole
sentence and stop; do not join two sentences, do not tidy wording, do not
shorten, do not summarise. A quote that is nearly right is treated as invented
and the whole result is discarded, so a shorter exact quote always beats a
longer approximate one.

Quote the sentence that PROVES the transition, not one that merely mentions the
topic. For an interview that means the sentence saying it is scheduled,
confirmed, or coming up -- not one describing the format, duration, platform or
agenda."""

VALIDATOR_PROMPT = CLASSIFIER_PROMPT + """

You are now the independent second reader for this high-impact employment outcome.
Apply the exact lifecycle-stage definitions above. You are not given the primary
model's conclusion. Re-read the complete source and the already-established
recruitment-relevance decision, then classify from scratch.
Date, time, duration, timezone, meeting links, greetings, sender domain, live-session
structure, and event logistics never establish an interview. They may only refine an
interview that the source explicitly ties to this recipient's hiring process.

INTERVIEW_SHORTLISTED requires source text showing that this candidate was shortlisted
or invited into an interview process without a confirmed schedule. When the source
explicitly says this candidate's interview is scheduled or confirmed and supplies a
date, time, and timezone, return INTERVIEW_CONFIRMED, not INTERVIEW_SHORTLISTED. A public
session schedule is not shortlist evidence. Return a complete selection_offer_event_v1
JSON result supported by short verbatim source evidence."""


def _analysis_payload(message: dict[str, Any], attachment_texts: list[dict[str, str]] | None) -> dict[str, Any]:
    return {
        "subject": message.get("subject"), "sender_name": message.get("sender_name"),
        "sender_email": message.get("sender_email"), "recipient": message.get("recipient_email"),
        "email_date": str(message.get("sent_at")), "body": clean_email(message.get("body") or ""),
        "labels": message.get("labels") or message.get("label_ids") or [],
        "thread_context": (message.get("thread_context") or [])[-5:],
        "attachments": attachment_texts or [],
}


def _validate_relevance_result(value: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    from jsonschema import Draft202012Validator

    errors = list(Draft202012Validator(RELEVANCE_SCHEMA).iter_errors(value))
    if errors:
        raise ValueError("invalid recruitment-relevance JSON: " + errors[0].message)
    sources = _source_texts(
        str(payload.get("subject") or ""), str(payload.get("body") or ""),
        payload.get("attachments") or [], payload.get("thread_context") or [],
    )
    # Correct a mislabelled source before judging the quote, exactly as the
    # classifier does. `_evidence_supported` searches only the category the
    # model declared, so a quote that is verbatim in the body but labelled
    # ATTACHMENT was thrown away and the whole ESTABLISHED answer downgraded.
    # That is what turned away a genuine "Rescheduling interview for
    # Application Security Engineer": the model read it correctly, quoted the
    # body word for word, and named the wrong source.
    #
    # This corrects the label, never the quote. `_canonicalise_evidence_source`
    # keeps an item only when its text occurs verbatim in exactly one source,
    # so invented evidence still has nowhere to match, and an ambiguous quote
    # still fails closed.
    canonical = [
        corrected for item in value.get("evidence") or []
        if (corrected := _canonicalise_evidence_source(item, sources)) is not None
    ]
    supported = [item for item in canonical if _evidence_supported(item, sources)]
    value["confidence"] = float(value.get("confidence") or 0) / (
        100.0 if float(value.get("confidence") or 0) > 1 else 1.0
    )
    value["evidence"] = supported
    if value.get("decision") == "ESTABLISHED" and not supported:
        value.update(
            decision="NOT_ESTABLISHED",
            message_kind="UNKNOWN",
            reason="The relevance model did not provide source-supported evidence tying the recipient to a hiring process.",
        )
    value["source"] = "RELEVANCE_MODEL"
    return value


def _neutral_non_alert_result(
    message: dict[str, Any], context: dict[str, Any], *, reason: str,
    status: str = "IGNORED_NOT_OFFER_RELATED", risk_flags: list[str] | None = None,
) -> dict[str, Any]:
    classification = "needs_review" if status == "MANUAL_REVIEW_REQUIRED" else "not_relevant"
    return {
        "schema_version": "selection_offer_event_v1",
        "is_recruitment_related": False,
        "is_selection_or_offer_related": False,
        "should_create_review_record": False,
        "status": status,
        "classification": classification,
        "candidate_status": "Needs Review" if classification == "needs_review" else "Profile Active",
        "confidence": 0.0,
        "ignore_reason": "RECRUITMENT_RELEVANCE_NOT_ESTABLISHED" if classification == "not_relevant" else "MODEL_DISAGREEMENT",
        "candidate": {"name": None, "email": message.get("recipient_email")},
        "company": {"name": None, "domain": None},
        "job": {"title": None, "employment_type": None, "location": None},
        "recruiter": {"name": message.get("sender_name"), "email": message.get("sender_email")},
        "interview": {key: None for key in ("date", "time", "end_time", "duration_minutes", "timezone", "mode", "round", "location", "meeting_link")},
        "offer": {
            "offer_detected": False, "offer_letter_detected": False,
            "appointment_letter_detected": False, "offer_date": None,
            "offered_ctc": None, "currency": None, "joining_date": None,
            "offer_expiry_date": None,
        },
        "attachments": [], "evidence": [], "risk_flags": risk_flags or [],
        "requires_manual_review": status == "MANUAL_REVIEW_REQUIRED",
        "summary": reason, "reason": reason,
        "recommended_action": "Review the source manually; no lifecycle transition or alert was created." if status == "MANUAL_REVIEW_REQUIRED" else "No action required.",
        "email_intent": context.get("email_intent") or "UNKNOWN",
        "document_type": context.get("document_type") or "NONE",
        "is_candidate_specific": bool(context.get("is_candidate_specific")),
        "is_job_outcome": False, "is_current_event": False,
        "is_questionnaire": bool(context.get("is_questionnaire")),
        "is_promotional_or_job_ad": bool(context.get("is_promotional_or_job_ad")),
        "is_historical_information": bool(context.get("is_historical_information")),
        "historical_employment_evidence": bool(context.get("historical_employment_evidence")),
        "lifecycle_event": "NONE", "interview_event": "NONE", "business_domain": "NONE",
        "evidence_summary": reason,
        "validation_status": "NEEDS_REVIEW" if status == "MANUAL_REVIEW_REQUIRED" else "REJECTED",
        "ai_status": "ANALYZED",
        "backend_transition_validated": False,
    }


def _manual_review_from_strong_context(
    message: dict[str, Any], routing_context: dict[str, Any], failure: Exception | None = None
) -> dict[str, Any] | None:
    """Preserve a strongly evidenced outcome when local AI is unavailable.

    This never auto-verifies or mutates a candidate.  It only creates a
    pending administrator review record when the deterministic semantic
    router already found a tracked outcome with quoted source evidence.
    """
    status = str(routing_context.get("status") or "")
    evidence = list(routing_context.get("evidence") or [])
    fallback_statuses = {
        "SELECTED", "FINAL_SELECTION_CONFIRMED", "FINAL_ROUND_CLEARED", "OFFER_INDICATION",
        "OFFER_IN_PROGRESS", "OFFER_APPROVED", "OFFER_LETTER_RECEIVED", "OFFER_RECEIVED",
        "APPOINTMENT_LETTER_RECEIVED", "OFFER_ACCEPTED", "JOINING_CONFIRMED",
        "JOINED", "POST_SELECTION_ONBOARDING", "BACKGROUND_VERIFICATION",
        "DOCUMENT_VERIFICATION", "HR_CONFIRMATION", "COMPENSATION_CONFIRMATION",
        "INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED", "INTERVIEW_SHORTLISTED",
    }
    if not routing_context.get("qualified") or status not in fallback_statuses or not evidence:
        return None
    confidence = min(0.89, max(0.80, float(routing_context.get("score") or 0.80)))
    failure_code = getattr(failure, "code", None) or "OLLAMA_INTERNAL_ERROR"
    fallback_reason = str(failure) if failure else "Local AI validation did not complete."
    offer_statuses = {
        "OFFER_INDICATION", "OFFER_IN_PROGRESS", "OFFER_APPROVED",
        "OFFER_LETTER_RECEIVED", "APPOINTMENT_LETTER_RECEIVED",
        "OFFER_ACCEPTED", "JOINING_CONFIRMED", "JOINED",
        "POST_SELECTION_ONBOARDING",
    }
    is_interview = status in {"INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED"}
    interview = (
        extract_interview_schedule(
            str(message.get("subject") or ""), str(message.get("body") or ""),
            sent_at=message.get("sent_at"),
        ) if is_interview else
        {key: None for key in ("date", "time", "end_time", "duration_minutes", "timezone", "mode", "round", "location", "meeting_link")}
    )
    from services.recruitment_automation import normalize_analysis
    return normalize_analysis({
        "schema_version": "selection_offer_event_v1",
        "is_recruitment_related": True,
        "is_selection_or_offer_related": True,
        "should_create_review_record": True,
        "status": status,
        "primary_status": status,
        "confidence": confidence,
        "ignore_reason": failure_code,
        "candidate": {"name": None, "email": message.get("recipient_email")},
        "company": {
            "name": routing_context.get("company_name"),
            "domain": routing_context.get("company_domain"),
        },
        "job": {
            "title": routing_context.get("job_title"),
            "employment_type": None,
            "location": None,
        },
        "recruiter": {
            "name": message.get("sender_name"),
            "email": message.get("sender_email"),
        },
        "interview": interview,
        "offer": {
            "offer_detected": status in offer_statuses,
            "offer_letter_detected": status == "OFFER_LETTER_RECEIVED",
            "appointment_letter_detected": status == "APPOINTMENT_LETTER_RECEIVED",
            "offer_date": None,
            "offered_ctc": None,
            "currency": None,
            "joining_date": routing_context.get("joining_date"),
            "offer_expiry_date": None,
        },
        "attachments": [],
        "evidence": evidence[:8],
        "risk_flags": list(dict.fromkeys(
            list(routing_context.get("risk_flags") or []) + ["AI_UNAVAILABLE"]
        )),
        "requires_manual_review": True,
        "manual_review_required": True,
        "classification_source": "FALLBACK",
        "ai_validation_status": "UNAVAILABLE",
        "ai_status": "UNAVAILABLE",
        "validation_status": "NEEDS_REVIEW",
        "lifecycle_event": "NONE",
        "interview_event": status if is_interview else "NONE",
        "business_domain": "INTERVIEW_TRACKING" if is_interview else "SELECTION_TRACKING",
        "fallback_reason": failure_code,
        "fallback_confidence": confidence,
        "summary": (
            f"Fallback evidence indicates {status.replace('_', ' ').lower()}. "
            f"AI validation unavailable ({failure_code}); automatic retry is pending."
        ),
        "ai_diagnostic_message": fallback_reason,
        "recommended_action": "Automatic evidence validation will retry.",
    })


def _requires_independent_validation(result: dict[str, Any], routing_context: dict[str, Any] | None = None) -> bool:
    threshold = float(os.getenv("AI_RECRUITMENT_AUTO_ACCEPT_THRESHOLD", "0.90"))
    confidence = float(result.get("confidence") or 0)
    if confidence > 1:
        confidence /= 100.0
    flags = {str(flag).upper() for flag in result.get("risk_flags") or []}
    return (
        confidence < threshold
        or bool(flags)
        or bool(result.get("requires_manual_review"))
        # A tracked status is itself a high-impact assertion. Do not let a
        # contradictory model boolean bypass the independent reader and the
        # backend evidence validator.
        or result.get("status") in TRACKED_STATUSES
        or bool((routing_context or {}).get("qualified"))
        or bool((routing_context or {}).get("risk_flags"))
    )


def _reconcile_model_results(primary: dict[str, Any], validator: dict[str, Any]) -> dict[str, Any]:
    primary_confidence = float(primary.get("confidence") or 0)
    validator_confidence = float(validator.get("confidence") or 0)
    if primary_confidence > 1: primary_confidence /= 100.0
    if validator_confidence > 1: validator_confidence /= 100.0
    # Status is the lifecycle conclusion. The booleans are duplicate model
    # fields and may contradict that conclusion; backend evidence validation,
    # not a duplicate boolean, decides whether an agreed status can survive.
    primary_positive = primary.get("status") in TRACKED_STATUSES
    validator_positive = validator.get("status") in TRACKED_STATUSES
    same = primary_positive == validator_positive and primary.get("status") == validator.get("status")
    if same:
        chosen = deepcopy(validator)
        chosen["confidence"] = min(primary_confidence, validator_confidence)
        chosen["model_validation"] = {"agreed": True, "primary_status": primary.get("status"), "validator_status": validator.get("status")}
        return chosen
    # A second reader is evidence, not an override authority. Detection of the
    # disagreement is unchanged; what follows only decides what to do about it,
    # and in no case is that a booking or a person.
    #
    # Measured over 30 days of production, all 489 disagreements were "both
    # readers say this is a real recruitment event, but name a different
    # stage" -- 242 INTERVIEW_SHORTLISTED against SELECTED, 97 INTERVIEW_UPDATE
    # against SELECTED, 76 INTERVIEW_UPDATE against INTERVIEW_SHORTLISTED. Not
    # one was positive against negative. Requiring two independent readings of
    # a thirty-value enum to produce the identical string is a far stricter
    # test than the decision needs, and it was consuming about a fifth of the
    # model's output to produce no decision at all: 497 real recruitment mails
    # were parked with no outcome.
    #
    # Only three classifications can move a booking -- confirmed, rescheduled
    # and cancelled. When neither reading is one of those, the two disagree
    # about which label to show and not about what to do, so the disagreement
    # is resolved to the more conservative stage and left to earn its way
    # through every ordinary guard below. Nothing is skipped for it.
    primary_rank = store.stage_rank(primary.get("status"))
    validator_rank = store.stage_rank(validator.get("status"))
    booking_relevant = {"INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED"}
    # Both readings must be positive. One reader saying "recruitment event" and
    # the other "not relevant" is a disagreement about whether anything
    # happened at all, and taking the conservative side of that would quietly
    # ignore the mail -- which is precisely how a real interview goes missing.
    # That case is uncertain, so it retries.
    resolvable = (
        primary_positive
        and validator_positive
        and primary_rank is not None
        and validator_rank is not None
        and not booking_relevant & {
            str(primary.get("status") or "").upper(), str(validator.get("status") or "").upper(),
        }
    )
    verdict = {
        "agreed": False,
        "primary_status": primary.get("status"),
        "validator_status": validator.get("status"),
    }
    if resolvable:
        # The earlier stage wins. Advancing a candidate on a contested reading
        # is the only irreversible half of this choice, and the evidence that
        # survives is the evidence belonging to the reading actually adopted.
        conservative = primary if primary_rank <= validator_rank else validator
        chosen = deepcopy(conservative)
        chosen["confidence"] = min(primary_confidence, validator_confidence, 0.89)
        # Deliberately not a risk flag. The tail of `validate_result` turns any
        # risk flag into `requires_manual_review`, so recording the
        # reconciliation there would send every one of these straight back to
        # the review state this exists to remove. `model_validation` is
        # persisted in the stored analysis, so the audit trail survives either
        # way -- with `resolution` and `resolved_status` naming what happened.
        chosen["reason"] = (
            "Primary and independent validator named different stages; neither can move a "
            f"booking, so the earlier stage ({chosen.get('status')}) was taken."
        )
        chosen["summary"] = chosen["reason"]
        chosen["model_validation"] = {
            **verdict, "resolution": "CONSERVATIVE_STAGE", "resolved_status": chosen.get("status"),
        }
        return chosen
    # The two readings differ in what they would do, not merely in what they
    # would say. That must never book on the strength of one of them, and it is
    # never a question for a person either: it goes back for an automatic
    # retry, which a different execution state can resolve on its own.
    #
    # `status` stays whatever the primary reader said, because `validate_result`
    # schema-validates this result before anything else and the schema is the
    # model's own output contract -- `AI_RETRY_PENDING` is not one of its
    # values. Writing it here raised `invalid selection/offer JSON` on every
    # booking-relevant disagreement, which surfaced as a validation failure
    # against the wrong cause and spent the retry allowance toward a terminal
    # park. The MODEL_DISAGREEMENT risk flag carries the decision instead, and
    # the branch in `validate_result` converts it once the schema has passed.
    chosen = deepcopy(primary)
    chosen["is_recruitment_related"] = True
    chosen["is_selection_or_offer_related"] = False
    chosen["should_create_review_record"] = False
    chosen["requires_manual_review"] = False
    chosen["ignore_reason"] = "MODEL_DISAGREEMENT"
    chosen["lifecycle_event"] = "NONE"
    chosen["interview_event"] = "NONE"
    chosen["business_domain"] = "NONE"
    chosen["is_job_outcome"] = False
    chosen["is_current_event"] = False
    chosen["evidence"] = []
    chosen["confidence"] = min(primary_confidence, validator_confidence, 0.89)
    chosen["risk_flags"] = list(dict.fromkeys((chosen.get("risk_flags") or []) + ["MODEL_DISAGREEMENT"]))
    chosen["reason"] = (
        "Primary and independent validator disagree on a booking-relevant status; "
        "no lifecycle transition was accepted and the mail returns for automatic retry."
    )
    chosen["summary"] = chosen["reason"]
    chosen["model_validation"] = {**verdict, "resolution": "AUTOMATIC_RETRY"}
    return chosen


def _prompt_json(value: Any) -> str:
    """Serialize provider timestamps and other scalar metadata for model prompts."""
    return json.dumps(
        value,
        ensure_ascii=False,
        default=lambda item: item.isoformat() if hasattr(item, "isoformat") else str(item),
    )


_SHORTLIST_CANDIDATE_STATUSES = {
    "interview shortlisted", "shortlisted", "candidate shortlisted",
    "profile shortlisted",
}
# Wording that states the shortlist outcome. A label alone is not enough: the
# mail itself has to say it, so a generic document request can never be
# promoted no matter what the model puts in candidate_status.
_SHORTLIST_EVIDENCE_PHRASES = (
    "provisionally shortlisted", "profile is shortlisted",
    "profile has been shortlisted", "profile is provisionally shortlisted",
    "you have been shortlisted", "you are shortlisted",
    "we have shortlisted your", "shortlisted for the role",
    "shortlisted for the position", "candidature has been shortlisted",
    "candidature has been provisionally shortlisted",
    "shortlisted for further discussion", "shortlisted for hr discussion",
)
# Only these are promotable. A confirmed/cancelled interview, an offer or a
# rejection already carries a stronger outcome and must never be overwritten.
_SHORTLIST_PROMOTABLE_STATUSES = {"MANUAL_REVIEW_REQUIRED", "INTERVIEW_UPDATE"}


def normalise_shortlist_status(value: dict[str, Any], message: dict[str, Any] | None = None) -> bool:
    """Map a model result that plainly describes a shortlist onto the canonical status.

    The model reads these mails correctly — it returned candidate_status
    "Interview Shortlisted", is_selection_or_offer_related true and a reason
    naming the provisional shortlist — but parked the result at
    MANUAL_REVIEW_REQUIRED/INTERVIEW_UPDATE because no interview slot was
    offered. Shortlisting is the outcome; the document list is the next action.

    Runs after the schema guard, on the validated result, so the guard still
    sees exactly what the model produced. Returns whether it promoted anything.
    """
    status = str(value.get("status") or "").upper()
    if status not in _SHORTLIST_PROMOTABLE_STATUSES:
        return False
    if not value.get("is_selection_or_offer_related"):
        return False

    label = str(value.get("candidate_status") or "").strip().lower()
    # Evidence must come from the mail itself, never from the model's own prose:
    # a summary that says "shortlisted" about a bare document request would
    # otherwise promote it on the strength of the model's wording alone.
    haystack = " ".join(str(part or "").lower() for part in (
        (message or {}).get("subject"), (message or {}).get("body"),
    ))
    stated = evidence_entails_transition("INTERVIEW_SHORTLISTED", haystack)
    entailing_evidence = any(
        evidence_entails_transition("INTERVIEW_SHORTLISTED", str(item.get("text") or ""))
        for item in value.get("evidence") or []
    )
    # Both the model's own label and the wording in the mail must agree, so a
    # merely ambiguous recruitment update stays in manual review.
    if label not in _SHORTLIST_CANDIDATE_STATUSES or not stated or not entailing_evidence:
        return False

    value["status"] = "INTERVIEW_SHORTLISTED"
    value["is_selection_or_offer_related"] = True
    value["should_create_review_record"] = True
    value["requires_manual_review"] = False
    value["backend_transition_validated"] = True
    value["backend_validation_reason"] = "EXPLICIT_INTERVIEW_SHORTLIST_ENTAILED"
    value["shortlist_normalised_from"] = status
    return True


def analyze(
    message: dict[str, Any], attachment_texts: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], str, int]:
    """Read one mail, on one machine.

    The relevance gate, the classifier and the validator are only comparable if
    they ran in the same place. The model is not random -- pinned to one warm
    node it returned the identical answer 9 times out of 9 -- but it answers
    differently on different hardware: on byte-identical input rtx4060 said
    INTERVIEW_UPDATE and jagadeesh INTERVIEW_SHORTLISTED, each repeatably, and
    reloading the model on one node moved it again, to SELECTED.

    That matters because a call which exceeds OLLAMA_REQUEST_TIMEOUT fails over
    to the next node. The validator could then answer from a different machine
    than the classifier, so the disagreement check compared two machines rather
    than two readings -- which is how a genuine Karat interview reminder was
    refused as AI_REQUIRES_REVIEW at confidence 1.0.

    Inside this session the first call chooses a node normally, honouring the
    model pin, health and cooldown; the rest are held to it. If that node stops
    being able to serve, selection raises instead of moving, the error reaches
    `process_message`, and the mail is parked for retry. A decision is never
    assembled from two machines.
    """
    with ollama_nodes.decision_session():
        return _analyze_on_one_node(message, attachment_texts)


def _analyze_on_one_node(message: dict[str, Any], attachment_texts: list[dict[str, str]] | None = None) -> tuple[dict[str, Any], str, int]:
    payload = _analysis_payload(message, attachment_texts)
    deterministic_context = classify_context(
        str(message.get("subject") or ""), str(message.get("body") or ""),
        sender_email=str(message.get("sender_email") or ""),
        sent_at=message.get("sent_at"), attachments=attachment_texts,
    )
    routing_context = routing_decision(
        message.get("subject", ""), message.get("body", ""),
        message.get("sender_name", ""), message.get("sender_email", ""),
        attachment_texts, message.get("thread_context"),
    ).get("context") or {}
    models = configured_models()
    deadline = time.monotonic() + max(
        20.0,
        float(os.getenv("AI_JOB_TIMEOUT", os.getenv("AI_RECRUITMENT_JOB_TIMEOUT_SECONDS", "660"))),
    )

    def remaining_timeout() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            raise AIGatewayError("Recruitment AI job deadline exceeded.", code="OLLAMA_REQUEST_TIMEOUT")
        request_limit = max(
            5.0,
            float(os.getenv("OLLAMA_REQUEST_TIMEOUT", os.getenv("AI_RECRUITMENT_REQUEST_TIMEOUT_SECONDS", "300"))),
        )
        return min(request_limit, remaining)

    def request_model(
        *, messages: list[dict[str, Any]], model: str,
        max_retries: int | None = None, allow_fallback: bool = True,
        workload: str = "recruitment_mail_classification",
        output_schema: dict[str, Any] = SCHEMA,
    ):
        """Use the lightweight fallback when the configured runner cannot serve."""
        try:
            return chat_structured(
                messages=messages, schema=output_schema, model=model, max_retries=max_retries,
                timeout=remaining_timeout(),
                deadline_monotonic=deadline,
                workload=workload,
            )
        except AIGatewayError as exc:
            fallback = str(models.get("fallback") or "").strip()
            eligible = {
                "OLLAMA_INTERNAL_ERROR", "OLLAMA_MODEL_LOAD_FAILED",
                "OLLAMA_REQUEST_TIMEOUT",
            }
            if not allow_fallback or not fallback or fallback == model or exc.code not in eligible:
                raise
            logger.warning(
                "Recruitment model failed; retrying fallback primary=%s fallback=%s code=%s",
                model, fallback, exc.code,
            )
            return chat_structured(
                messages=messages, schema=output_schema, model=fallback, max_retries=0,
                timeout=remaining_timeout(),
                deadline_monotonic=deadline,
                workload=f"{workload}_fallback",
            )

    try:
        # Ollama decides whether this mail is about the recipient's own hiring
        # process. There is no keyword path around this call.
        #
        # There used to be one. When the deterministic layer asserted an
        # interview, `_deterministic_relevance_result` returned ESTABLISHED /
        # RECIPIENT_HIRING_PROCESS at confidence 100 and the relevance model was
        # never asked. That layer matches vocabulary, not meaning: a
        # handwriting-therapy mailing list saying "Join our FREE Interview with
        # Imran Baig", with a date, a time and a Zoom link, satisfied
        # `_is_assertive_interview_invitation` -- the webinar/workshop exclusion
        # inspects only the subject, and the subject was
        # "Her son's behaviour transformed through GraphoTherapy". 128 mails
        # reached the classifier this way, among them nine from that list, a
        # Naukri newsletter and an Uber account notice.
        #
        # `calendar_invite_intent` stopped using the shortcut when the same
        # thing happened to three webinars. This is the other entry point.
        relevance_response = request_model(
            messages=[
                {"role": "system", "content": RELEVANCE_PROMPT},
                {"role": "user", "content": _prompt_json(payload)},
            ],
            model=models["primary"], max_retries=0,
            workload="recruitment_mail_relevance",
            output_schema=RELEVANCE_SCHEMA,
        )
        try:
            relevance = _validate_relevance_result(
                parse_model_json(relevance_response.content), payload,
            )
        except (ValueError, json.JSONDecodeError):
            relevance_response = request_model(
                messages=[
                    {"role": "system", "content": RELEVANCE_PROMPT + " Return valid JSON only; no markdown or commentary."},
                    {"role": "user", "content": _prompt_json(payload)},
                ],
                model=relevance_response.model, max_retries=0,
                workload="recruitment_mail_relevance_json_repair",
                output_schema=RELEVANCE_SCHEMA,
            )
            try:
                relevance = _validate_relevance_result(
                    parse_model_json(relevance_response.content), payload,
                )
            except (ValueError, json.JSONDecodeError) as exc:
                raise AIGatewayError(
                    "Ollama returned invalid recruitment-relevance JSON after one repair retry.",
                    code="OLLAMA_INVALID_JSON",
                ) from exc
        relevance_model = relevance_response.model
        relevance_duration = relevance_response.duration_ms
        relevance["model"] = relevance_model

        if relevance.get("decision") != "ESTABLISHED":
            unresolved = (
                relevance.get("message_kind") in {"UNKNOWN", "RECIPIENT_HIRING_PROCESS"}
                or float(relevance.get("confidence") or 0) < float(os.getenv("OLLAMA_CONFIDENCE_THRESHOLD", "0.75"))
                or not relevance.get("evidence")
            )
            result = _neutral_non_alert_result(
                message, deterministic_context,
                reason=str(relevance.get("reason") or "Recruitment relevance was not established."),
            )
            result.update(
                primary_status=result["status"], classification_source="OLLAMA_RELEVANCE_GATE",
                ai_validation_status="VALIDATED",
                recruitment_relevance_result=deepcopy(relevance),
            )
            if unresolved:
                result.update(status='AI_RETRY_PENDING', primary_status='AI_RETRY_PENDING',
                              ignore_reason='RECRUITMENT_RELEVANCE_UNRESOLVED')
                from services.recruitment_automation import normalize_analysis
                normalize_analysis(result)
            result["_decision_trace"] = {
                "deterministic_context": {
                    "semantic_context": deepcopy(deterministic_context),
                    "routing_context": deepcopy(routing_context),
                },
                "recruitment_relevance_result": deepcopy(relevance),
                "primary_model_result": None,
                "validator_model_result": None,
                "reconciled_result": None,
                "backend_validated_final_result": deepcopy(result),
            }
            return result, f"relevance:{relevance_model or 'deterministic'}", relevance_duration

        classifier_input = {"source": payload, "recruitment_relevance": relevance}
        primary_response = request_model(
            messages=[{"role": "system", "content": CLASSIFIER_PROMPT}, {"role": "user", "content": _prompt_json(classifier_input)}],
            model=models["primary"],
            max_retries=0,
            workload="recruitment_mail_primary",
        )
        try:
            primary = parse_model_json(primary_response.content)
        except (ValueError, json.JSONDecodeError):
            # One bounded repair retry with an explicit JSON-only instruction.
            repair_response = request_model(
                messages=[{"role": "system", "content": CLASSIFIER_PROMPT + " Return valid JSON only; no markdown or commentary."},
                          {"role": "user", "content": _prompt_json(classifier_input)}],
                model=primary_response.model, max_retries=0,
                workload="recruitment_mail_primary_json_repair",
            )
            try:
                primary = parse_model_json(repair_response.content)
                primary_response = repair_response
            except (ValueError, json.JSONDecodeError) as exc:
                raise AIGatewayError("Ollama returned malformed classification JSON after one repair retry.", code="OLLAMA_INVALID_JSON") from exc
        logger.info("Ollama response JSON extracted for recruitment classification")
        primary_model_result = deepcopy(primary)
        result = primary
        model_label = primary_response.model
        duration = relevance_duration + primary_response.duration_ms
        validator = None
        validator_model_result = None
        if _requires_independent_validation(primary, routing_context):
            validator_response = request_model(
                messages=[{"role": "system", "content": VALIDATOR_PROMPT}, {"role": "user", "content": _prompt_json(classifier_input)}],
                model=models["validator"],
                max_retries=0,
                allow_fallback=False,
                workload="recruitment_mail_validator",
            )
            try:
                validator = parse_model_json(validator_response.content)
            except (ValueError, json.JSONDecodeError):
                validator_response = request_model(
                    messages=[{"role": "system", "content": VALIDATOR_PROMPT + " Return valid JSON only; no markdown or commentary."},
                              {"role": "user", "content": _prompt_json(classifier_input)}],
                    model=validator_response.model, max_retries=0, allow_fallback=False,
                    workload="recruitment_mail_validator_json_repair",
                )
                try:
                    validator = parse_model_json(validator_response.content)
                except (ValueError, json.JSONDecodeError) as exc:
                    raise AIGatewayError("Ollama validator returned malformed JSON after one repair retry.", code="OLLAMA_INVALID_JSON") from exc
            validator_model_result = deepcopy(validator)
            result = _reconcile_model_results(primary, validator)
            model_label = f"{primary_response.model}|validator:{validator_response.model}"
            duration += validator_response.duration_ms
        reconciled = deepcopy(result)
        try:
            validate_result(
                result, message, attachment_texts,
                deterministic_context=deterministic_context,
                relevance=relevance,
            )
        except ValueError as exc:
            raise AIGatewayError(f"Ollama response failed schema validation: {exc}", code="OLLAMA_SCHEMA_VALIDATION_FAILED") from exc
        logger.info("Ollama recruitment response schema validated")
        if not pure_ollama_enabled():
            normalise_shortlist_status(result, message)
        result["primary_status"] = result["status"]
        result["classification_source"] = "OLLAMA"
        result["ai_validation_status"] = "VALIDATED"
        result["recruitment_relevance_result"] = deepcopy(relevance)
        result["_decision_trace"] = {
            "deterministic_context": {
                "semantic_context": deepcopy(deterministic_context),
                "routing_context": deepcopy(routing_context),
            },
            "recruitment_relevance_result": deepcopy(relevance),
            "primary_model_result": primary_model_result,
            "validator_model_result": validator_model_result,
            "reconciled_result": reconciled,
            "backend_validated_final_result": deepcopy(result),
        }
        return result, model_label, duration
    except AIGatewayError:
        raise
    except Exception as exc:
        logger.exception("Recruitment AI analysis failed with unexpected error type=%s message=%s", type(exc).__name__, str(exc))
        raise AIGatewayError("AI semantic analysis failed.", code="OLLAMA_INTERNAL_ERROR") from exc


# A refusal by our own schema check is a statement about the model's answer, not
# about the service being reachable, so it does not belong on the infrastructure
# retry path.
_DETERMINISTIC_AI_FAILURES = frozenset({"OLLAMA_SCHEMA_VALIDATION_FAILED"})

# Sampling can turn a rejected answer into a valid one, so a couple of genuine
# attempts run first. Beyond that the loop is only spending inference to be told
# the same thing again.
_VALIDATION_RETRY_ALLOWANCE = 2


def process_message(mailbox: dict[str, Any], decoded: dict[str, Any], attachment_texts: list[dict[str, str]] | None = None, *, reprocess: bool = False, defer_ai: bool = False) -> dict[str, Any] | None:
    from services.mail_attachment_processor import extract_attachment
    # Re-select from both stored MIME alternatives on every processing pass.
    # That lets a historical rescan recover messages ingested by an older body
    # selector without requiring a Gmail refetch or a data migration.
    from services.gmail_mailbox_provider import select_message_body
    decoded["body"] = clean_email(select_message_body(decoded.get("body"), decoded.get("html_body")))
    decoded["message_hash"] = content_hash("|".join([decoded.get("sender_email") or "", decoded.get("subject") or "", str(decoded.get("sent_at"))]))
    processed = [extract_attachment(item) if item.get("data") is not None else item for item in (attachment_texts or [])]
    safe = [{key: item.get(key) for key in ("filename", "mime_type", "text", "attachment_type", "extraction_status", "checksum")} for item in processed]
    decoded["attachments"] = safe
    # Attachments are part of what makes a message distinct. A calendar
    # organiser who moves a meeting re-sends the identical covering note with a
    # new invite.ics; hashing the body alone made that revision look like a
    # resend and dropped it before anything read the new time.
    fingerprint = "|".join(sorted(
        str(item.get("checksum") or "") for item in safe if item.get("checksum")
    ))
    decoded["body_hash"] = content_hash(
        decoded["body"] + ("#attachments:" + fingerprint if fingerprint else "")
    )
    critical_attachment_failure = any(
        str(item.get("attachment_type") or "") in {"OFFER_LETTER","APPOINTMENT_LETTER","JOINING_LETTER","COMPENSATION_BREAKUP"}
        and str(item.get("extraction_status") or "") in {"FAILED","MANUAL_REVIEW_REQUIRED"}
        for item in safe
    )
    route = routing_decision(decoded.get("subject", ""), decoded["body"], decoded.get("sender_name", ""), decoded.get("sender_email", ""), safe, decoded.get("thread_context"))
    if critical_attachment_failure and not route["send_to_ai"]:
        route = {"send_to_ai":True,"score":0.25,"reason":"CRITICAL_ATTACHMENT_EXTRACTION_FAILED","context":route.get("context") or {}}
    row, created = store.insert_message(mailbox, decoded, float(route["score"]))
    _publish("mail_received", candidate_id=mailbox.get("candidate_id"), gmail_message_id=decoded.get("provider_message_id"), processing_status="Email Received")
    # A crash can happen after the durable message insert but before it is put
    # on the AI queue.  Resume only that transient state when Gmail retries;
    # terminal/queued rows remain idempotent.
    if not created and not reprocess and str(row.get("processing_status") or "").upper() != "FILTERED":
        return None
    previous_status=row.get("processing_status")
    if not reprocess and store.is_duplicate_content(
        mailbox["candidate_id"], row["id"], decoded["message_hash"], decoded["body_hash"],
        decoded.get("subject"),
    ):
        store.mark_message_status(row["id"], "DUPLICATE_CONTENT", reason="DUPLICATE_MESSAGE")
        # A dropped mail that was carrying an interview must not vanish without
        # trace. Dedupe runs before the calendar fallback below, so this is the
        # one place that can see an invite being discarded.
        _publish_ignored_interview(mailbox, decoded, safe, "DUPLICATE_CONTENT", "DUPLICATE_MESSAGE")
        return None
    for attachment in processed:
        if attachment.get("checksum"):
            store.save_attachment(row["id"], attachment)
    if str(decoded.get("message_direction") or "").upper() == "OUTBOUND":
        if reprocess:
            store.archive_event_for_message(row["id"], status="IGNORED_NOT_OFFER_RELATED", reason="OUTBOUND_MESSAGE")
        store.mark_message_status(row["id"], "IGNORED_NOT_OFFER_RELATED", reason="OUTBOUND_MESSAGE")
        if reprocess:
            store.mark_reprocessed(row["id"], previous_status, "IGNORED_NOT_OFFER_RELATED", "MESSAGE_DIRECTION_CORRECTION")
        return None
    from services.calendar_invite_parser import trusted_interview_result
    calendar_result = trusted_interview_result(decoded, safe)
    # Pure Ollama AI Mail Detection, ON by default.
    #
    # The duplicate and direction checks above have already run; those are
    # cheap, deterministic and about the message rather than its meaning, so
    # they stay in front either way. From here the keyword and routing rules
    # are what decide relevance, and they are exactly what dropped a real
    # interview reminder as NO_RECRUITMENT_ROUTING_SIGNAL. With the switch ON
    # the model reads every inbound mail and decides for itself.
    #
    # This changes who classifies, not what is trusted afterwards: evidence
    # validation, the deterministic booking checks and the persistence re-read
    # are all downstream of this line and untouched.
    if not route["send_to_ai"] and pure_ollama_enabled():
        route = {
            "send_to_ai": True,
            "score": max(0.25, float(route.get("score") or 0)),
            "reason": "PURE_OLLAMA_DETECTION",
            "context": route.get("context") or {},
        }
    if not route["send_to_ai"] and not calendar_result:
        if reprocess:
            store.archive_event_for_message(row["id"], status="IGNORED_NOT_OFFER_RELATED", reason=route["reason"])
        store.mark_message_status(row["id"], "IGNORED_NOT_OFFER_RELATED", reason=route["reason"])
        _publish_ignored_interview(mailbox, decoded, safe, "IGNORED_NOT_OFFER_RELATED", route["reason"])
        if reprocess: store.mark_reprocessed(row["id"],previous_status,"IGNORED_NOT_OFFER_RELATED","HISTORICAL_RULE_RESCAN")
        return None
    routed_status = str((route.get("context") or {}).get("status") or "")
    if not reprocess and routed_status in OFFER_CASE_STATUSES and store.is_duplicate_offer_attachment(mailbox["candidate_id"], row["id"]):
        store.mark_message_status(row["id"], "DUPLICATE_OFFER_ATTACHMENT", reason="DUPLICATE_OFFER_ATTACHMENT")
        return None
    if calendar_result:
        # A valid invite proves when an event is, never that it is this
        # candidate's interview. Ollama decides the intent of the whole mail
        # before the invite is allowed to become a booking; a Zoom workshop
        # registration carries an invite just as valid as a recruiter's, and one
        # was auto-booked for a real candidate before an operator caught it.
        try:
            calendar_relevance = calendar_invite_intent(decoded, safe)
        except AIGatewayError as exc:
            # Neither guess is safe when the model is unreachable: booking risks
            # another workshop, dropping loses a real interview. Park it.
            logger.warning("Calendar invite intent unavailable code=%s; sending to review", exc.code)
            store.mark_message_status(
                row["id"], "AI_RETRY_PENDING", reason="CALENDAR_INTENT_UNAVAILABLE",
                error_code=getattr(exc, "code", "OLLAMA_INTERNAL_ERROR"),
            )
            return None
        verdict = calendar_invite_verdict(
            calendar_relevance, calendar_result=calendar_result, message=decoded,
        )
        if verdict == "RETRY":
            # Contradictory relevance with insufficient deterministic proof is
            # neither an ignore nor a human action.  Preserve the source mail
            # and let the leased worker retry it with backoff.
            logger.warning(
                "Calendar invite relevance requires automatic retry kind=%s decision=%s subject=%r",
                calendar_relevance.get("message_kind"), calendar_relevance.get("decision"),
                str(decoded.get("subject") or "")[:120],
            )
            store.mark_message_status(
                row["id"], "AI_RETRY_PENDING",
                reason="CALENDAR_RELEVANCE_CONTRADICTION",
            )
            return None
        elif verdict == "IGNORE":
            reason = "CALENDAR_INVITE_NOT_A_CANDIDATE_INTERVIEW"
            logger.info(
                "Calendar invite is not a candidate interview kind=%s decision=%s",
                calendar_relevance.get("message_kind"), calendar_relevance.get("decision"),
            )
            if reprocess:
                store.archive_event_for_message(
                    row["id"], status="IGNORED_NOT_OFFER_RELATED", reason=reason,
                )
            store.mark_message_status(row["id"], "IGNORED_NOT_OFFER_RELATED", reason=reason)
            _publish_ignored_interview(
                mailbox, decoded, safe, "IGNORED_NOT_OFFER_RELATED", reason,
            )
            if reprocess:
                store.mark_reprocessed(
                    row["id"], previous_status, "IGNORED_NOT_OFFER_RELATED", reason,
                )
            return None
        else:
            calendar_result = dict(calendar_result)
            calendar_result["recruitment_relevance_result"] = deepcopy(calendar_relevance)
            result, model, duration = calendar_result, "rfc5545-authenticated", 0
    elif defer_ai:
        store.mark_message_status(row["id"], "AI_QUEUED", reason="DURABLE_AI_QUEUE")
        _publish("mail_ai_queued", candidate_id=mailbox.get("candidate_id"), gmail_message_id=decoded.get("provider_message_id"), processing_status="AI Queued")
        return None
    else:
        try:
            _publish("mail_ai_analyzing", candidate_id=mailbox.get("candidate_id"), gmail_message_id=decoded.get("provider_message_id"), processing_status="AI Analyzing")
            result, model, duration = analyze(decoded, safe)
        except Exception as exc:
            # Infrastructure failure is never evidence of a recruitment outcome.
            # Persist only a neutral retry result; do not derive candidate,
            # interview, payment, offer, or lifecycle state from keywords.
            result = _failure_review_result(decoded, exc)
            model = f"unavailable:{getattr(exc, 'code', type(exc).__name__).lower()}"
            duration = 0
            failure_code = getattr(exc, "code", None) or "OLLAMA_INTERNAL_ERROR"
            if (
                failure_code in _DETERMINISTIC_AI_FAILURES
                and int(row.get("ai_retry_count") or 0) >= _VALIDATION_RETRY_ALLOWANCE
            ):
                # The model answered; the answer did not validate. Re-running
                # identical input mostly reproduces the identical refusal, and
                # the queue proved it: two mails reached ten attempts on
                # OLLAMA_SCHEMA_VALIDATION_FAILED and would have been parked as
                # MAX_ATTEMPTS_EXHAUSTED, which names the wrong cause and hides
                # a decision an operator can act on. A few attempts still run,
                # because sampling occasionally produces a valid result; after
                # that the mail is parked where the audit already looks for it.
                logger.warning(
                    "Recruitment email parked after repeated validation failure code=%s attempts=%s",
                    failure_code, row.get("ai_retry_count"),
                )
                store.mark_message_status(
                    row["id"], "VALIDATION_FAILED", reason=failure_code, error_code=failure_code,
                )
                try:
                    store.record_analysis(
                        row["id"], mailbox["candidate_id"], result,
                        model=model, processing_status="VALIDATION_FAILED",
                        error_code=failure_code, error_message=str(exc),
                    )
                except Exception:
                    logger.debug("Unable to persist validation-failure analysis", exc_info=True)
                return None
            logger.warning("Recruitment email queued for semantic retry code=%s", failure_code)
            store.mark_message_status(
                row["id"], "AI_RETRY_PENDING", reason=failure_code, error_code=failure_code,
            )
            if (
                result.get("primary_status") == "MANUAL_REVIEW_REQUIRED"
                and not critical_attachment_failure
            ):
                # Unknown/ambiguous mail is not user-actionable while AI is down.
                # Keep its analysis and retry state for recovery, but do not turn
                # infrastructure failure into a false-positive review record.
                try:
                    store.record_analysis(
                        row["id"], mailbox["candidate_id"], result,
                        model=model, processing_status="RETRY_PENDING",
                        error_code=failure_code, error_message=str(exc),
                    )
                except Exception:
                    logger.debug("Unable to persist hidden retry analysis", exc_info=True)
                return None
    if critical_attachment_failure and (not result.get("is_selection_or_offer_related") or not result.get("should_create_review_record")):
        result = _failure_review_result(decoded, RuntimeError("Critical employment attachment extraction failed"))
        result["reason"] = "A potentially important employment attachment could not be extracted"
        result["risk_flags"] = ["ATTACHMENT_EXTRACTION_FAILED"]
    # Includes infrastructure fallback and attachment failures which do not
    # pass through validate_result. No manual exit may bypass the retry queue.
    from services.recruitment_automation import normalize_analysis
    normalize_analysis(result)
    if str(result.get("classification_source") or "").upper() == "OLLAMA":
        relevance = result.get("recruitment_relevance_result") or {}
        if (
            str(relevance.get("decision") or "").upper() != "ESTABLISHED"
            or result.get("backend_transition_validated") is not True
        ):
            result.update(
                is_selection_or_offer_related=False,
                should_create_review_record=False,
                is_job_outcome=False,
                lifecycle_event="NONE",
                interview_event="NONE",
                business_domain="NONE",
            )
    if not pure_ollama_enabled() and job_board_notification(decoded.get("sender_email", "")):
        # Marked not-relevant so it takes the existing ignore path: no event, no
        # lifecycle status, no notification. The analysis is still recorded, so
        # the decision stays auditable.
        result["is_selection_or_offer_related"] = False
        result["should_create_review_record"] = False
        result["ignore_reason"] = "JOB_BOARD_NOTIFICATION"
    def _queued_for_another_attempt() -> bool:
        """Park an undecided result for automatic retry. Never a person's queue.

        `AI_RETRY_PENDING` is deliberately not a tracked status, so without
        this the mail falls into the ignore branch below and is marked
        not-relevant -- silently discarding a mail the readers agreed was a
        real recruitment event. Two separate checks reach here, one before the
        ignore branch and one after the evidence guard, so both share this exit.
        """
        if "AI_RETRY_PENDING" not in {
            str(result.get("status") or "").upper(),
            str(result.get("primary_status") or "").upper(),
        }:
            return False
        reason = str(result.get("ignore_reason") or "MODEL_DISAGREEMENT")
        logger.info(
            "Recruitment email returned for automatic retry reason=%s subject=%r",
            reason, str(decoded.get("subject") or "")[:120],
        )
        store.mark_message_status(row["id"], "AI_RETRY_PENDING", reason=reason, error_code=reason)
        try:
            store.record_analysis(
                row["id"], mailbox["candidate_id"], result,
                model=model, processing_status="RETRY_PENDING",
            )
        except Exception:
            logger.debug("Unable to persist automatic-retry analysis", exc_info=True)
        return True

    if _queued_for_another_attempt():
        return None
    if not result.get("is_selection_or_offer_related") or not result.get("should_create_review_record") or result.get("primary_status") not in TRACKED_STATUSES:
        status = "IGNORED_LOW_CONFIDENCE" if result.get("primary_status") == "IGNORED_LOW_CONFIDENCE" else "IGNORED_NOT_OFFER_RELATED"
        if reprocess:
            store.archive_event_for_message(row["id"], status=status, reason=result.get("ignore_reason") or "AI_NOT_OFFER_RELATED", result=result)
        store.mark_message_status(row["id"], status, reason=result.get("ignore_reason") or "AI_NOT_OFFER_RELATED")
        try:
            store.record_analysis(row["id"],mailbox["candidate_id"],result,model=model,processing_status="COMPLETED")
        except Exception:
            logger.debug("Unable to persist non-relevant AI analysis", exc_info=True)
        if reprocess:
            store.mark_reprocessed(row["id"], previous_status, status, "HISTORICAL_SEMANTIC_RESCAN")
        return None
    if not result.get("evidence") and result.get("primary_status") != "MANUAL_REVIEW_REQUIRED":
        # The anti-hallucination guard is unchanged: a result with no
        # source-supported evidence still cannot become a lifecycle transition
        # or a booking. Only its destination changes -- another attempt rather
        # than a person's queue, since the quoting failure is usually a
        # property of the sampling and not of the mail.
        result.update(primary_status="AI_RETRY_PENDING", status="AI_RETRY_PENDING",
                      classification="ai_retry_pending", candidate_status="AI Retry Pending",
                      requires_manual_review=False, should_create_review_record=False,
                      validation_status="RETRY_PENDING",
                      ignore_reason=result.get("ignore_reason") or "EVIDENCE_NOT_SOURCE_SUPPORTED",
                      reason="The AI result lacks source-supported evidence")
        if _queued_for_another_attempt():
            return None
    if reprocess:
        # Downstream booking must distinguish delayed historical recovery from
        # a live message. This marker is persisted in the structured analysis
        # so retries preserve the same safety decision.
        result["_historical_reprocess"] = True
        historical_classification = store.canonical_classification(result)
        if historical_classification in {
            "interview_confirmed", "interview_rescheduled",
        }:
            from services.interview_auto_booking import (
                should_suppress_historical_notification,
            )
            result["_suppress_monitoring_notification"] = (
                should_suppress_historical_notification(
                    result, historical_classification,
                )
            )
    if store.is_duplicate_thread_status(mailbox["candidate_id"], row["id"], result["primary_status"]):
        store.mark_message_status(row["id"], "DUPLICATE_OFFER_EVENT", reason="DUPLICATE_THREAD_STATUS")
        return None
    event = (store.create_or_reprocess_event(mailbox["candidate_id"],row["id"],result,model=model,duration_ms=duration,reason="HISTORICAL_RULE_RESCAN") if reprocess else store.create_event(mailbox["candidate_id"], row["id"], result, model=model, duration_ms=duration))
    logger.info("Recruitment classification saved: event=%s source=%s", event.get("id"), result.get("classification_source", "OLLAMA"))
    if reprocess: store.mark_reprocessed(row["id"],previous_status,"EVENT_CREATED","HISTORICAL_RULE_RESCAN")
    suppress_notification = bool(result.get("_suppress_monitoring_notification"))
    if not suppress_notification:
        from services.recruitment_notifications import notify_detection
        notify_detection(event)
    notification = event.get("notification") or {}
    created_realtime_event = notification.pop("_created_realtime_event", None)
    common = {
        "notification_id": notification.get("id"), "candidate_id": event.get("candidate_id"),
        "candidate_name": notification.get("candidate_name"), "company_name": notification.get("company_name"),
        "classification": event.get("classification") or result.get("classification"),
        "status": event.get("candidate_status") or result.get("candidate_status"),
        "confidence": round(float(event.get("confidence") or 0) * 100),
        "priority": notification.get("priority"),
        "provider_message_id": decoded.get("provider_message_id"),
    }
    _publish("mail_classified", **common)
    if not suppress_notification:
        _publish("mail_retry_pending" if common["classification"] == "ai_retry_pending" else "important_mail_detected", **common)
    if event.get("candidate_status_updated"):
        _publish("candidate_status_updated", **common)
    if notification:
        if created_realtime_event:
            # Creation was committed atomically with the notification row.
            # This call only fans out that exact durable event; it does not
            # create a second event ID.
            from core.recruitment_realtime import deliver_persisted
            deliver_persisted(created_realtime_event)
        else:
            _publish("notification_updated", **common)
    if common["classification"] == "assessment_invited":
        # An assessment names a window, so its slot is chosen by rule rather
        # than read from the mail. It books through its own path and appears on
        # the same roster, typed as an assessment; the interview path below is
        # untouched by it.
        _publish("auto_booking_started", **common, processing_status="Validating Booking")
        try:
            from services.assessment_auto_booking import execute_assessment_booking
            # `decoded` already carries the extracted attachments; the local
            # name for them in this function is `attachment_texts`, and reading
            # the wrong one crashed the booking in production while every test
            # that called the booking module directly stayed green.
            outcome = execute_assessment_booking(
                mailbox=mailbox, message=decoded, event=event, result=result,
            )
            booking = outcome.get("booking") or {}
            # `common` already carries a status -- the candidate's -- so the
            # booking's own status has to replace it in one payload rather than
            # arrive as a second keyword.
            _publish(outcome["event_type"], **{
                **common, "status": outcome.get("status"),
                "booking_id": booking.get("id"), "booking_audit_id": (outcome.get("audit") or {}).get("id"),
                "booking_type": "Assessment",
                "interview_date": booking.get("date"), "interview_time": booking.get("time"),
                "start_time": booking.get("time"), "end_time": booking.get("time_end"),
                "timezone": "Asia/Kolkata" if booking.get("date") else None,
                "booking_url": f"/daily-ops?bookingId={booking.get('id')}" if booking.get("id") else "",
                "failure_code": outcome.get("failure_code"),
                "block_reason": (outcome.get("block_reason") or {}).get("reason"),
                "block_reason_code": (outcome.get("block_reason") or {}).get("reason_code"),
            })
            event["auto_booking"] = outcome
            if outcome.get("notification"):
                event["notification"] = outcome["notification"]
                _publish("notification_updated", **common, booking_id=booking.get("id"), booking_status=outcome.get("status"))
        except Exception as exc:
            logger.exception("Automatic assessment booking failed event=%s code=%s", event.get("id"), type(exc).__name__)
            _publish("mail_processing_failed", **common, processing_status="Processing Failed", error_code=type(exc).__name__)
    if common["classification"] in {"interview_confirmed", "interview_rescheduled", "interview_cancelled"}:
        interview = result.get("interview") or {}
        _publish(
            "interview_detected", **common,
            interview_round=interview.get("round"), interview_date=interview.get("date"),
            interview_time=interview.get("time"), timezone=interview.get("timezone"),
        )
        _publish("auto_booking_started", **common, processing_status="Validating Booking")
        try:
            from services.interview_auto_booking import execute_auto_booking
            outcome = execute_auto_booking(mailbox=mailbox, message=decoded, event=event, result=result)
            booking = outcome.get("booking") or {}
            audit = outcome.get("audit") or {}
            # The schedule comes from the booking that was actually written,
            # not from the model's reading of the email: the two can differ
            # (a timezone is normalised, a reschedule moves an existing slot),
            # and a notification that announces a time nobody is booked for is
            # worse than no notification.
            booked_round = booking.get("interview_round") or interview.get("round")
            booking_event = {
                **common, "status": outcome.get("status"), "booking_id": booking.get("id"),
                "booking_audit_id": audit.get("id"),
                "candidate_name": booking.get("name") or common.get("candidate_name"),
                "company_name": booking.get("interview_company") or common.get("company_name"),
                "interview_round": booked_round,
                "interview_date": booking.get("date") or interview.get("date"),
                "interview_time": booking.get("time") or interview.get("time"),
                "start_time": booking.get("time") or interview.get("time"),
                "end_time": booking.get("time_end") or interview.get("end_time"),
                "timezone": (
                    "Asia/Kolkata" if booking.get("date") else interview.get("timezone")
                ),
                "booking_url": (
                    f"/daily-ops?bookingId={booking.get('id')}" if booking.get("id") else ""
                ),
                "failure_code": outcome.get("failure_code"),
                # Present only when the booking was refused, and already phrased
                # for a person by the validator.
                "block_reason": (outcome.get("block_reason") or {}).get("reason"),
                "block_reason_code": (outcome.get("block_reason") or {}).get("reason_code"),
            }
            _publish(outcome["event_type"], **booking_event)
            event["auto_booking"] = outcome
            if outcome.get("notification"):
                event["notification"] = outcome["notification"]
                _publish("notification_updated", **common, booking_id=booking.get("id"), booking_status=outcome.get("status"))
        except Exception as exc:
            logger.exception("Automatic interview booking failed event=%s code=%s", event.get("id"), type(exc).__name__)
            _publish("mail_processing_failed", **common, processing_status="Processing Failed", error_code=type(exc).__name__)
    return event
