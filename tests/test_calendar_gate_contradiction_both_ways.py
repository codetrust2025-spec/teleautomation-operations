"""A calendar invite is never dropped on an answer that contradicts itself.

Nitin's ServiceNow interview was missed. The mail carried a valid Microsoft
Teams invite -- METHOD:REQUEST, STATUS:CONFIRMED, SEQUENCE:0, the candidate
listed as an ATTENDEE, DTSTART 2026-09-10T18:30 India Standard Time, organiser
"Thaga, Mohamed" -- and the relevance model answered:

    decision = NOT_ESTABLISHED      message_kind = RECIPIENT_HIRING_PROCESS

Those two disagree. The kind says this is the recipient's own hiring process;
the decision says it is not established. `calendar_invite_verdict` mapped every
non-ESTABLISHED decision to IGNORE, so the invite was dropped with no event, no
Mail Alert and no booking audit row. The interview was that evening.

The opposite pairing -- ESTABLISHED with a conflicting kind -- was already sent
to review. This is its mirror, and it is treated the same way: an answer at odds
with itself is a question for an operator, never a booking and never silence.

Twenty-seven mails had been dropped by this gate, twenty-six of them carrying an
.ics, and they read as genuine recruiter invites: Capgemini "Technical Interview
|| Java +React", Cognizant "L2 Interview", Infosys "R1 Interview", Wipro "L1
Round discussion", Synechron, Lancesoft, CGI.

The webinar defence is untouched. A confident non-candidate answer still
ignores, and every marketing sample checked in production -- the Zoom workshop,
the Naukri bootcamp, Yocket, Talent500, Impacteers, the GraphoTherapy list --
answers NOT_ESTABLISHED with a marketing-shaped kind, never
RECIPIENT_HIRING_PROCESS.
"""

from __future__ import annotations

import pytest

from services.recruitment_mail_agent import (
    calendar_invite_is_a_candidate_interview,
    calendar_invite_verdict,
)
from services.calendar_interview_evidence import evidence_for


def answer(decision, kind):
    return {
        "decision": decision, "message_kind": kind, "confidence": 0.85,
        "evidence": [{"source": "EMAIL_BODY", "text": "Microsoft Teams meeting"}],
        "reason": "checked against production",
    }


class TestTheInviteThatWasMissed:
    def test_the_exact_answer_without_structural_proof_retries_automatically(self):
        assert calendar_invite_verdict(
            answer("NOT_ESTABLISHED", "RECIPIENT_HIRING_PROCESS")) == "RETRY"

    def test_strong_calendar_and_role_evidence_books_the_missed_invite(self):
        value = answer("NOT_ESTABLISHED", "RECIPIENT_HIRING_PROCESS")
        calendar = {
            "calendar_validation_status": "TRUSTED",
            "calendar": {"method": "REQUEST", "uid": "nitin-thaga", "has_dtend": True},
            "interview": {"date": "2099-09-10", "meeting_link": "https://teams.microsoft.com/l/meetup-join/test"},
        }
        message = {"subject": "ServiceNow Developer", "body": "Please join the discussion."}
        assert calendar_invite_verdict(value, calendar_result=calendar, message=message) == "BOOK"

    def test_both_contradictions_retry_without_strong_calendar_context(self):
        assert calendar_invite_verdict(
            answer("NOT_ESTABLISHED", "RECIPIENT_HIRING_PROCESS")) == "RETRY"
        assert calendar_invite_verdict(
            answer("ESTABLISHED", "MARKETING_OR_TRAINING")) == "RETRY"

    def test_clear_webinar_wins_over_a_contradictory_label(self):
        calendar = {
            "calendar_validation_status": "TRUSTED",
            "calendar": {"method": "REQUEST", "uid": "webinar", "has_dtend": True},
            "interview": {"date": "2099-09-10", "meeting_link": "https://zoom.us/j/1"},
        }
        message = {"subject": "ServiceNow webinar", "body": "Join our public training workshop."}
        assert calendar_invite_verdict(
            answer("ESTABLISHED", "MARKETING_OR_TRAINING"),
            calendar_result=calendar, message=message,
        ) == "IGNORE"

    def test_thin_teams_calendar_requires_dtend_before_overriding_ai(self):
        calendar = {
            "calendar_validation_status": "TRUSTED",
            "calendar": {"method": "REQUEST", "uid": "thin", "has_dtend": False},
            "interview": {"date": "2099-09-10", "meeting_link": "https://teams.microsoft.com/l/meetup-join/test"},
        }
        message = {"subject": "L1 Discussion with Nitin - ServiceNow DevOps"}
        assert evidence_for(calendar, message)["trusted_request"] is False
        assert calendar_invite_verdict(
            answer("NOT_ESTABLISHED", "RECIPIENT_HIRING_PROCESS"),
            calendar_result=calendar, message=message,
        ) == "RETRY"


class TestTheWebinarDefenceIsUntouched:
    @pytest.mark.parametrize("kind", [
        "PUBLIC_EVENT",            # Zoom workshop, Impacteers, Yocket
        "MARKETING_OR_TRAINING",   # Talent500, GraphoTherapy
        "NEWSLETTER",              # Naukri bootcamp
        "JOB_ADVERTISEMENT",
    ])
    def test_a_confident_marketing_answer_still_ignores(self, kind):
        assert calendar_invite_verdict(answer("NOT_ESTABLISHED", kind)) == "IGNORE"

    @pytest.mark.parametrize("kind", ["GENERAL", "UNKNOWN"])
    def test_an_uninformative_answer_still_ignores(self, kind):
        assert calendar_invite_verdict(answer("NOT_ESTABLISHED", kind)) == "IGNORE"


class TestJunkStillFailsClosed:
    @pytest.mark.parametrize("value", [
        {}, {"decision": None}, {"decision": ""}, {"decision": "nonsense"},
        {"message_kind": "RECIPIENT_HIRING_PROCESS"},
        {"decision": None, "message_kind": "RECIPIENT_HIRING_PROCESS"},
    ])
    def test_a_missing_decision_is_not_a_contradiction(self, value):
        """Junk is not disagreement. It must not buy an invite a review."""
        assert calendar_invite_verdict(value) == "IGNORE"


class TestBookingIsUnchanged:
    def test_only_an_agreed_answer_books(self):
        assert calendar_invite_verdict(
            answer("ESTABLISHED", "RECIPIENT_HIRING_PROCESS")) == "BOOK"

    def test_and_the_booking_predicate_stays_strict(self):
        for decision, kind in (
            ("NOT_ESTABLISHED", "RECIPIENT_HIRING_PROCESS"),
            ("ESTABLISHED", "MARKETING_OR_TRAINING"),
            ("NOT_ESTABLISHED", "PUBLIC_EVENT"),
        ):
            assert calendar_invite_is_a_candidate_interview(answer(decision, kind)) is False
        assert calendar_invite_is_a_candidate_interview(
            answer("ESTABLISHED", "RECIPIENT_HIRING_PROCESS")) is True
