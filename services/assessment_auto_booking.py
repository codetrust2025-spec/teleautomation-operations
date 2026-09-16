"""Book the slot an assessment window resolves to, once per assessment.

Interviews keep their own path in `interview_auto_booking`; nothing here
touches it. What is shared is everything after the decision -- the candidate
store, the booking audit, the notification -- so an assessment appears on the
roster and in the audit trail exactly like an interview, marked as its own type.

The identity of an assessment is the window it names, not the mail that carried
it: a platform sends an invitation and then reminders about the same test, and
each of those must find the booking the first one made instead of adding
another. `assessment_key` on the booking row is that identity.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from core import recruitment_mail_store as mail_store
from features import candidate_store
from services import assessment_schedule
from services.interview_auto_booking import _confirmed_slots

logger = logging.getLogger("teleautomation.assessment_auto_booking")

ACTIONABLE = {"assessment_invited"}

#: Booked, or deliberately not booked and waiting for a person. Both are
#: outcomes; neither invents a time.
BOOKED = "Auto Booked"
PENDING = "Pending Manual Review"


def assessment_key(candidate_id: str, window: assessment_schedule.AssessmentWindow, *, company: str) -> str:
    """One assessment, however many mails describe it."""
    return hashlib.sha256("|".join([
        "assessment-v1", str(candidate_id or ""), str(company or "").strip().casefold(),
        str(window.name or "").strip().casefold(),
        window.deadline.isoformat() if window.deadline else "",
        window.start.isoformat() if window.start and not window.deadline else "",
    ]).encode()).hexdigest()[:32]


def _existing_booking(candidate: dict[str, Any], key: str) -> dict[str, Any] | None:
    for row in _confirmed_slots(candidate):
        if str(row.get("assessment_key") or "") == key:
            return row
    return None


def _company(result: dict[str, Any], message: dict[str, Any]) -> str:
    company = str(((result.get("company") or {}) if isinstance(result.get("company"), dict) else {}).get("name") or "")
    if company.strip():
        return company.strip()
    sender = str(message.get("sender_email") or "")
    return sender.split("@")[-1].split(".")[0].title() if "@" in sender else ""


def execute_assessment_booking(
    *, mailbox: dict[str, Any], message: dict[str, Any], event: dict[str, Any],
    result: dict[str, Any], now=None,
) -> dict[str, Any]:
    """Apply one assessment invitation, and describe the outcome either way."""
    classification = mail_store.canonical_classification(result)
    notification = event.get("notification") or {}
    candidate_id = str(mailbox.get("candidate_id") or "")
    window = assessment_schedule.parse_window(
        str(message.get("subject") or ""), str(message.get("body") or ""),
        attachments=message.get("attachments") or [], result=result,
    )
    company = _company(result, message)
    analysis = mail_store.record_interview_analysis(
        mailbox_message_id=event["mailbox_message_id"],
        email_analysis_id=notification.get("email_analysis_id"), mailbox_id=mailbox["id"],
        gmail_message_id=message["provider_message_id"],
        gmail_thread_id=message.get("provider_thread_id"), candidate_id=candidate_id,
        result=result, validation_status=str(result.get("ai_validation_status") or "UNAVAILABLE"),
        processing_status="VALIDATING",
    )
    snapshot = {
        "mailbox_message_id": event.get("mailbox_message_id"),
        "provider_message_id": message.get("provider_message_id"),
        "provider_thread_id": message.get("provider_thread_id"),
        "assessment": {"window": window.evidence, "duration_minutes": window.duration_minutes,
                       "name": window.name, "exact": window.exact},
    }

    def audit(*, booking_id, auto_booked, status, failure_code=None, failure_message=None, booking=None):
        return mail_store.record_booking_audit(
            analysis_id=analysis["id"], candidate_id=candidate_id,
            gmail_message_id=message["provider_message_id"],
            gmail_thread_id=message.get("provider_thread_id"), classification=classification,
            booking_id=booking_id, auto_booked=auto_booked,
            validation_status="PASSED" if auto_booked else "BLOCKED",
            payment_status="NOT_REQUIRED", duplicate_status="PASSED",
            conflict_status="PASSED" if auto_booked else "NOT_CHECKED",
            booking_status=status, failure_code=failure_code, failure_message=failure_message,
            new_booking=booking, source_event_id=event.get("id"), source_snapshot=snapshot,
        )

    candidate = candidate_store.get_candidate(candidate_id)
    if not candidate:
        record = audit(booking_id=None, auto_booked=False, status=PENDING,
                       failure_code="CANDIDATE_MAPPING_FAILED",
                       failure_message="The connected mailbox candidate could not be found.")
        return _outcome(PENDING, "assessment_booking_blocked", audit=record, notification=notification,
                        failure_code="CANDIDATE_MAPPING_FAILED",
                        reason="The connected mailbox candidate could not be found.")

    key = assessment_key(candidate_id, window, company=company)
    already = _existing_booking(candidate, key)
    if already:
        # A reminder about a test already on the roster. Report the slot that
        # exists; adding a second one is the bug this key exists to prevent.
        record = audit(booking_id=str(already.get("id") or ""), auto_booked=True,
                       status="Already Booked", booking=already)
        return _outcome("Already Booked", "assessment_already_booked", audit=record,
                        booking=already, notification=notification, duplicate=True)

    decision = assessment_schedule.resolve_slot(window, existing=_confirmed_slots(candidate), now=now)
    if not decision.bookable:
        record = audit(booking_id=None, auto_booked=False, status=PENDING,
                       failure_code=decision.reason_code, failure_message=decision.reason)
        logger.info("Assessment left for review candidate=%s code=%s", candidate_id, decision.reason_code)
        return _outcome(PENDING, "assessment_booking_blocked", audit=record, notification=notification,
                        failure_code=decision.reason_code, reason=decision.reason)

    booking = candidate_store.assign_interview_slot(
        candidate_id=candidate_id, date=decision.date, time=decision.time, time_end=decision.time_end,
        interview_company=company, interview_role=window.name or str(message.get("subject") or "")[:120],
        interview_source_thread_id=str(message.get("provider_thread_id") or ""),
        interview_source_message_id=str(message.get("provider_message_id") or ""),
        interview_source_timezone="Asia/Kolkata",
        interview_booking_source="ai_auto_booked",
        booking_type="Assessment", assessment_key=key,
    )
    record = audit(booking_id=str(booking.get("id") or ""), auto_booked=True, status=BOOKED, booking=booking)
    logger.info("Assessment booked candidate=%s booking=%s %s %s-%s",
                candidate_id, booking.get("id"), decision.date, decision.time, decision.time_end)
    return _outcome(BOOKED, "assessment_auto_booked", audit=record, booking=booking, notification=notification,
                    schedule={"date": decision.date, "time": decision.time, "time_end": decision.time_end})


def _outcome(status: str, event_type: str, *, audit: dict[str, Any], notification: dict[str, Any],
             booking: dict[str, Any] | None = None, failure_code: str | None = None,
             reason: str | None = None, schedule: dict[str, str] | None = None,
             duplicate: bool = False) -> dict[str, Any]:
    updated = notification
    if notification.get("id"):
        try:
            updated = mail_store.attach_booking_to_notification(
                notification["id"], audit_id=str(audit.get("id") or ""),
                booking_id=str((booking or {}).get("id") or "") or None,
                booking_status=status, result={"interview": {}}, priority="high" if booking else "retry_pending",
                display_status="Assessment Automatically Booked" if booking else "Assessment Needs a Slot",
                schedule=schedule,
                block_reason=None if booking else {"reason_code": failure_code or "", "reason": reason or ""},
            )
        except Exception:
            logger.exception("Assessment notification projection failed audit=%s", audit.get("id"))
    return {
        "status": status, "event_type": event_type, "booking": booking or {}, "audit": audit,
        "notification": updated, "failure_code": failure_code, "duplicate": duplicate,
        "block_reason": None if booking else {"reason_code": failure_code or "", "reason": reason or ""},
        "booking_type": "Assessment",
    }
