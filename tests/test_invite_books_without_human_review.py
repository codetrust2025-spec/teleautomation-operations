"""Calendar invite automation has no human-review outcome."""

from __future__ import annotations

import inspect

import pytest

from services import interview_auto_booking as booking
from services import recruitment_mail_agent as agent


def relevance(decision, kind):
    return {
        "decision": decision, "message_kind": kind, "confidence": 0.85,
        "evidence": [{"source": "EMAIL_BODY", "text": "Microsoft Teams meeting"}],
    }


def trusted_invite():
    return {
        "calendar_validation_status": "TRUSTED",
        "calendar": {"method": "REQUEST", "uid": "nitin-uid", "has_dtend": True},
        "interview": {"date": "2026-09-10", "meeting_link": "https://teams.microsoft.com/l/meetup"},
    }


class TestCalendarAutomation:
    def test_contradiction_without_strong_evidence_retries(self):
        assert agent.calendar_invite_verdict(
            relevance("NOT_ESTABLISHED", "RECIPIENT_HIRING_PROCESS"),
        ) == "RETRY"

    def test_trusted_hiring_invite_overcomes_a_contradiction(self):
        assert agent.calendar_invite_verdict(
            relevance("NOT_ESTABLISHED", "RECIPIENT_HIRING_PROCESS"),
            calendar_result=trusted_invite(),
            message={"subject": "ServiceNow Developer interview", "body": "Teams meeting"},
        ) == "BOOK"

    def test_clear_marketing_is_ignored(self):
        assert agent.calendar_invite_verdict(
            relevance("ESTABLISHED", "MARKETING_OR_TRAINING"),
            calendar_result=trusted_invite(),
            message={"subject": "Public webinar and training session"},
        ) == "IGNORE"

    def test_retry_branch_has_no_human_action(self):
        source = inspect.getsource(agent.process_message)
        block = source[source.index('if verdict == "RETRY"'):source.index('elif verdict == "IGNORE"')]
        assert "AI_RETRY_PENDING" in block
        for human in ("MANUAL_REVIEW_REQUIRED", "needs_review", "requires_manual_review=True"):
            assert human not in block


class TestNoHumanVetoRemainsInBooking:
    def test_the_model_flag_is_no_longer_read_by_the_gate(self):
        source = inspect.getsource(booking.validate_ai_for_booking)
        assert "AI_REQUIRES_REVIEW" not in source
        assert 'result.get("requires_manual_review")' not in source

    def test_a_flagged_result_now_books(self, monkeypatch):
        monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
        value = {
            "classification_source": "OLLAMA", "ai_validation_status": "VALIDATED",
            "confidence": 0.96, "requires_manual_review": True,
            "interview": {"date": "2026-09-10", "time": "06:30 PM", "timezone": "Asia/Kolkata"},
        }
        booking.validate_ai_for_booking(value, "interview_confirmed")


class TestEveryEvidenceGateSurvives:
    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch):
        monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")

    def _value(self, **over):
        value = {
            "classification_source": "OLLAMA", "ai_validation_status": "VALIDATED", "confidence": 0.96,
            "interview": {"date": "2026-09-10", "time": "06:30 PM", "timezone": "Asia/Kolkata"},
        }
        value.update(over)
        return value

    def test_an_unvalidated_source_is_still_refused(self):
        with pytest.raises(booking.BookingValidationError) as raised:
            booking.validate_ai_for_booking(self._value(ai_validation_status="NEEDS_REVIEW"), "interview_confirmed")
        assert raised.value.code == "AI_NOT_VALIDATED"

    def test_low_confidence_is_still_refused(self):
        with pytest.raises(booking.BookingValidationError) as raised:
            booking.validate_ai_for_booking(self._value(confidence=0.5), "interview_confirmed")
        assert raised.value.code == "LOW_CONFIDENCE"
