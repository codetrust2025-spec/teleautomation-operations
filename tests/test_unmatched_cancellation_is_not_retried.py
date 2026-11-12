"""A cancellation that matches no booking is final, not another trip through the AI.

Before 13 Nov 2026 every cancellation refused as BOOKING_NOT_FOUND or
BOOKING_AMBIGUOUS went back to the AI queue. The mails had been read correctly
(0.99 confidence), so each was re-read 12 to 18 times and then parked as
AI_ATTEMPTS_EXHAUSTED; the booking step ran once. Nothing a second reading says
can make a missing booking appear or tell two bookings apart.
"""

from __future__ import annotations

import pytest

from services import booking_block_reasons as reasons
from services import interview_auto_booking as booking
from services import recruitment_automation as automation
from tests.test_interview_auto_booking import install_store_fakes, result

FINAL, RETRY = automation.AutomationState.AUTO_IGNORE, automation.AutomationState.AI_RETRY_PENDING


@pytest.mark.parametrize("code", ["BOOKING_NOT_FOUND", "BOOKING_AMBIGUOUS"])
def test_an_unmatched_cancellation_is_final(code):
    outcome = {"status": "Blocked", "failure_code": code, "classification": "interview_cancelled"}
    assert automation.outcome_for(outcome) is FINAL


@pytest.mark.parametrize(("code", "classification"), [
    ("BOOKING_NOT_FOUND", "interview_rescheduled"),   # a reschedule still retries, as before
    ("BOOKING_AMBIGUOUS", "interview_rescheduled"),
    ("BOOKING_NOT_FOUND", None),                      # an outcome that does not say what it was
    ("BOOKING_NOT_PERSISTED", "interview_cancelled"),  # storage failures still retry
    ("PAYMENT_VALIDATION_FAILED", "interview_confirmed"),
    ("CANDIDATE_MAPPING_FAILED", "interview_cancelled"),
])
def test_everything_else_keeps_its_retry(code, classification):
    outcome = {"status": "Blocked", "failure_code": code}
    if classification:
        outcome["classification"] = classification
    assert automation.outcome_for(outcome) is RETRY


def run_cancellation(monkeypatch, rows):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch, rows=[dict(row) for row in rows])
    projected = []
    monkeypatch.setattr(booking.mail_store, "record_automation_state",
                        lambda **kwargs: projected.append((kwargs["state"], kwargs["reason"])))
    cancelled = []
    monkeypatch.setattr(booking.candidate_store, "cancel_interview_slot",
                        lambda **kwargs: cancelled.append(kwargs["candidate_id"]) or {"id": kwargs["candidate_id"]})
    value = result("interview_cancelled", date=None, time=None, timezone=None, round=None)
    value["company"]["name"] = None
    value["job"]["title"] = None
    outcome = booking.execute_auto_booking(
        mailbox={"id": "mb1", "candidate_id": "c1"},
        message={"provider_message_id": "cancel-1", "provider_thread_id": "cancel-thread",
                 "subject": "Interview cancelled", "body_text": "Your interview has been cancelled."},
        event={"id": "ev1", "mailbox_message_id": "mm1", "notification": {"id": "n1"}}, result=value,
    )
    return outcome, projected, cancelled


def test_the_executor_parks_an_unmatched_cancellation_and_keeps_its_alert(monkeypatch):
    rows = [{"id": "a", "name": "Rahul", "slot_confirmed": True, "date": "2099-11-12", "time": "14:00"},
            {"id": "b", "name": "Rahul", "slot_confirmed": True, "date": "2099-11-13", "time": "11:00"}]

    outcome, projected, cancelled = run_cancellation(monkeypatch, rows)

    assert cancelled == []
    assert outcome["failure_code"] == "BOOKING_NOT_FOUND"
    assert outcome["automation_state"] == "AUTO_IGNORE"
    assert projected[-1] == ("AUTO_IGNORE", "BOOKING_NOT_FOUND")
    # The person still sees it, under Booking failed, with what to do.
    assert outcome["notification"]["priority"] == "booking_failed"
    words = reasons.explain(outcome["block_reason"]["reason_code"], outcome["failure_code"],
                            classification="interview_cancelled")
    assert "cancel it there" in words["action"]
    assert "try again" not in words["action"]


def test_a_cancellation_that_matches_still_cancels(monkeypatch):
    outcome, projected, cancelled = run_cancellation(
        monkeypatch, [{"id": "only", "name": "Rahul", "slot_confirmed": True, "date": "2099-11-12", "time": "14:00"}])

    assert cancelled == ["only"]
    assert outcome["automation_state"] == "AUTO_CANCELLED"
