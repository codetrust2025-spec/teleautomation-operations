"""A calendar invite proves when an event is, not that it is an interview.

One real candidate was auto-booked into "ChatGPT & 10+ AI Tools - IND" from a
Zoom workshop registration. The registration carried a perfectly valid RFC5545
invite, and `process_message` took the calendar result and never called
`analyze()` at all -- so no model ever judged what the mail was. An operator
cancelled the slot three days later.

The `.ics` remains the authority on the schedule and keeps its priority: 77 of
91 confirmed interviews come from one, and the parser is exact where prose is
not. What is added is a question the invite cannot answer -- is this event this
candidate's interview -- and it is put to Ollama, whose relevance prompt already
names webinars, masterclasses, training and public events explicitly.

The gate is deliberately not the deterministic shortcut. That shortcut answered
ESTABLISHED / RECIPIENT_HIRING_PROCESS for three handwriting-therapy webinars,
which is exactly the judgement being tested.
"""

from __future__ import annotations

import pytest

from core.ai_gateway import AIGatewayError
from services.recruitment_mail_agent import (
    CANDIDATE_HIRING_MESSAGE_KIND,
    calendar_invite_is_a_candidate_interview,
)


class TestOnlyAHiringProcessMayBook:
    def test_a_recruiter_invite_is_a_candidate_interview(self):
        assert calendar_invite_is_a_candidate_interview({
            "decision": "ESTABLISHED", "message_kind": "RECIPIENT_HIRING_PROCESS",
        }) is True

    @pytest.mark.parametrize("kind", [
        "PUBLIC_EVENT",          # the Zoom workshop that was actually booked
        "MARKETING_OR_TRAINING",
        "NEWSLETTER",
        "JOB_ADVERTISEMENT",
        "GENERAL",
        "UNKNOWN",
    ])
    def test_every_other_kind_of_event_is_refused(self, kind):
        assert calendar_invite_is_a_candidate_interview({
            "decision": "ESTABLISHED", "message_kind": kind,
        }) is False

    def test_an_unestablished_relevance_is_refused_whatever_the_kind(self):
        assert calendar_invite_is_a_candidate_interview({
            "decision": "NOT_ESTABLISHED", "message_kind": "RECIPIENT_HIRING_PROCESS",
        }) is False

    @pytest.mark.parametrize("relevance", [
        {}, {"decision": None}, {"message_kind": "RECIPIENT_HIRING_PROCESS"},
        {"decision": "ESTABLISHED"}, {"decision": "established", "message_kind": None},
    ])
    def test_a_missing_answer_is_not_a_yes(self, relevance):
        assert calendar_invite_is_a_candidate_interview(relevance) is False

    def test_the_answer_is_read_case_insensitively(self):
        assert calendar_invite_is_a_candidate_interview({
            "decision": "established", "message_kind": "recipient_hiring_process",
        }) is True

    def test_the_required_kind_is_the_one_the_relevance_schema_defines(self):
        from services.recruitment_mail_agent import RELEVANCE_SCHEMA

        kinds = RELEVANCE_SCHEMA["properties"]["message_kind"]["enum"]
        assert CANDIDATE_HIRING_MESSAGE_KIND in kinds
        # Everything the schema can return that is *not* a hiring process must
        # be refused, so the gate cannot silently miss a new category.
        for kind in kinds:
            expected = kind == CANDIDATE_HIRING_MESSAGE_KIND
            assert calendar_invite_is_a_candidate_interview(
                {"decision": "ESTABLISHED", "message_kind": kind},
            ) is expected


class TestTheGateAsksTheModelDirectly:
    def test_it_uses_the_relevance_prompt_not_the_deterministic_shortcut(self):
        """The shortcut is what wrongly called three webinars a hiring process."""
        import inspect

        from services import recruitment_mail_agent as agent

        source = inspect.getsource(agent.calendar_invite_intent)
        assert "RELEVANCE_PROMPT" in source
        assert "RELEVANCE_SCHEMA" in source
        assert "_deterministic_relevance_result" not in source

    def test_the_invite_is_still_what_decides_the_schedule(self):
        """The gate judges intent only. Nothing here may touch date or time."""
        import inspect

        from services import recruitment_mail_agent as agent

        source = inspect.getsource(agent.calendar_invite_intent)
        for field in ("interview_date", "interview_time", "normalized_schedule"):
            assert field not in source


class TestWhereTheGateSitsInProcessMessage:
    """Order matters: the invite must not become a booking before this runs."""

    @staticmethod
    def _source():
        import inspect

        from services import recruitment_mail_agent as agent

        return inspect.getsource(agent.process_message)

    def test_the_intent_check_precedes_trusting_the_calendar_result(self):
        source = self._source()
        gate = source.index("calendar_invite_intent(")
        trust = source.index('calendar_result, "rfc5545-authenticated"')
        assert gate < trust, "intent must be decided before the invite is trusted"

    def test_a_non_interview_invite_is_ignored_with_a_nameable_reason(self):
        source = self._source()
        assert "CALENDAR_INVITE_NOT_A_CANDIDATE_INTERVIEW" in source
        # The dropped mail still leaves a trace, exactly like every other
        # ignore path -- a discarded invite must never vanish silently.
        gate = source.index("CALENDAR_INVITE_NOT_A_CANDIDATE_INTERVIEW")
        assert "_publish_ignored_interview" in source[gate:gate + 1200]

    def test_a_gateway_failure_parks_the_mail_instead_of_deciding(self):
        """Booking risks another workshop; dropping loses a real interview."""
        source = self._source()
        assert "CALENDAR_INTENT_UNAVAILABLE" in source
        failure = source.index("CALENDAR_INTENT_UNAVAILABLE")
        assert "AI_RETRY_PENDING" in source[failure - 400:failure + 400]

    def test_the_relevance_answer_is_kept_on_the_result(self):
        """So an audit can see which judgement allowed the booking."""
        assert 'calendar_result["recruitment_relevance_result"]' in self._source()


class TestTheRestOfThePipelineIsUntouched:
    def test_date_and_timezone_handling_is_unchanged(self):
        from services.recruitment_mail_agent import (
            _normalise_interview_date as nd,
            _normalise_interview_timezone as nz,
        )

        assert nd("tomorrow", "2026-08-12 09:15:00+00:00") == "2026-08-13"
        assert nz("EST") == "America/New_York"
        assert nz("IST") == "Asia/Kolkata"

    def test_booking_still_requires_the_deterministic_checks(self):
        import inspect

        from services import interview_auto_booking as ab

        source = inspect.getsource(ab)
        for code in ("CANDIDATE_MAPPING_FAILED", "PAYMENT_VALIDATION_FAILED",
                     "DUPLICATE_BOOKING", "SLOT_CONFLICT"):
            assert code in source

    def test_a_webinar_still_cannot_reach_an_actionable_status(self):
        """interview_shortlisted was never bookable; that stays true."""
        from services.interview_auto_booking import ACTIONABLE

        assert "interview_shortlisted" not in ACTIONABLE
        assert ACTIONABLE == {
            "interview_confirmed", "interview_rescheduled", "interview_cancelled",
        }


def test_the_gateway_error_type_is_what_the_caller_catches():
    """A guard against the exception type drifting apart from the handler."""
    import inspect

    from services import recruitment_mail_agent as agent

    assert "except AIGatewayError" in inspect.getsource(agent.process_message)
    assert issubclass(AIGatewayError, Exception)


class TestTheGateActuallyRunsInProcessMessage:
    """Structural checks prove the code is there; these prove it executes.

    No existing test drove `process_message` with a calendar invite at all,
    which is how the path shipped without a classification step in the first
    place.
    """

    @staticmethod
    def _harness(monkeypatch, intent, statuses, reasons, analyzed, created):
        from services import recruitment_mail_agent as agent
        from tests.test_recruitment_pipeline import structured

        invite = {
            **structured("INTERVIEW_CONFIRMED"),
            "primary_status": "INTERVIEW_CONFIRMED",
            "classification": "interview_confirmed",
            "candidate_status": "Interview Confirmed",
        }
        monkeypatch.setattr(agent.store, "insert_message",
                            lambda mailbox, decoded, score: ({"id": "stored-message"}, True))
        monkeypatch.setattr(agent.store, "is_duplicate_content", lambda *a: False)
        monkeypatch.setattr(agent.store, "is_duplicate_offer_attachment", lambda *a: False)
        monkeypatch.setattr(agent.store, "is_duplicate_thread_status", lambda *a, **k: False)
        monkeypatch.setattr(agent.store, "save_attachment", lambda *a: None)
        monkeypatch.setattr(
            agent.store, "mark_message_status",
            lambda mid, status, **kw: (statuses.append(status),
                                       reasons.append(kw.get("reason"))) and None)
        monkeypatch.setattr(
            agent.store, "create_event",
            lambda cid, mid, result, **meta: created.append(meta) or {
                "id": "event-1", "candidate_id": cid,
                "primary_status": result.get("primary_status"),
                "classification": result.get("classification"),
            })
        monkeypatch.setattr(agent, "analyze",
                            lambda *a, **k: analyzed.append(True) or (invite, "m", 1))
        monkeypatch.setattr("services.calendar_invite_parser.trusted_interview_result",
                            lambda decoded, attachments: dict(invite))
        monkeypatch.setattr(agent, "calendar_invite_intent", intent)
        monkeypatch.setattr("services.recruitment_notifications.notify_detection",
                            lambda event: None)
        return agent

    @staticmethod
    def _message(subject, body):
        return {"provider_message_id": "m-1", "provider_thread_id": "t-1",
                "sender_email": "no-reply" + "@" + "zoom.us",
                "recipient_email": "candidate" + "@" + "test.invalid",
                "subject": subject, "sent_at": "2026-08-25T22:26:39Z", "body": body}

    def test_a_workshop_invite_is_not_booked(self, monkeypatch):
        """The real mail: a Zoom registration whose invite was auto-booked."""
        statuses, reasons, analyzed, created = [], [], [], []
        agent = self._harness(
            monkeypatch,
            lambda *a, **k: {"decision": "NOT_ESTABLISHED", "message_kind": "PUBLIC_EVENT"},
            statuses, reasons, analyzed, created)

        outcome = agent.process_message(
            {"id": "mailbox-1", "candidate_id": "candidate-1"},
            self._message("Registration Confirmed - ChatGPT Workshop on August 28, at 7 PM",
                          "You are registered for the ChatGPT & 10+ AI Tools workshop."),
            [])

        assert outcome is None
        assert created == [], "a workshop must never become an interview event"
        assert statuses == ["IGNORED_NOT_OFFER_RELATED"]
        assert reasons == ["CALENDAR_INVITE_NOT_A_CANDIDATE_INTERVIEW"]

    def test_a_recruiter_invite_still_books(self, monkeypatch):
        """The gate must not cost the 77 of 91 interviews that come from .ics."""
        statuses, reasons, analyzed, created = [], [], [], []
        agent = self._harness(
            monkeypatch,
            lambda *a, **k: {"decision": "ESTABLISHED",
                             "message_kind": "RECIPIENT_HIRING_PROCESS"},
            statuses, reasons, analyzed, created)

        outcome = agent.process_message(
            {"id": "mailbox-1", "candidate_id": "candidate-1"},
            self._message("L1 Interview - Backend Engineer",
                          "Your L1 interview is scheduled."),
            [])

        assert outcome is not None
        assert outcome["classification"] == "interview_confirmed"
        assert created, "a real recruiter invite must still create the event"
        assert created[0]["model"] == "rfc5545-authenticated"
        assert analyzed == [], "the invite is still trusted for the schedule"

    def test_an_unreachable_model_parks_the_mail(self, monkeypatch):
        statuses, reasons, analyzed, created = [], [], [], []

        def unreachable(*a, **k):
            raise AIGatewayError("down", code="OLLAMA_REQUEST_TIMEOUT")

        agent = self._harness(monkeypatch, unreachable, statuses, reasons, analyzed, created)

        outcome = agent.process_message(
            {"id": "mailbox-1", "candidate_id": "candidate-1"},
            self._message("L1 Interview - Backend Engineer", "Your interview is scheduled."),
            [])

        assert outcome is None
        assert created == [], "nothing is booked while intent is unknown"
        assert statuses == ["AI_RETRY_PENDING"]
        assert reasons == ["CALENDAR_INTENT_UNAVAILABLE"]
