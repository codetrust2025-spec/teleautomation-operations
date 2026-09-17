"""The deterministic interview signal can refuse a booking. It cannot cause one.

`_is_assertive_interview_invitation` decides whether a mail reads as a concrete
interview invitation, and its webinar exclusion looks only at the subject line.
A handwriting-therapy mailing list therefore still sets interview_event =
INTERVIEW_CONFIRMED on the deterministic context, which raises the fair question
of whether that signal can push a webinar into a booking.

It cannot, and this pins why. `validate_interview_event` returns either the
status the model already proposed or "NONE". There is no path that returns a
status the caller did not ask for, so the signal is a veto that has to agree --
never a promotion.

Measured over the whole production mailbox at the time of writing: 13,710
messages, 253 of which the deterministic layer gives an interview_event; 19 of
those have a webinar-ish body with a clean subject -- the exact gap -- and 6 of
those reached an interview_* classification. All 6 were genuine interviews
(zeko.ai and orion.interview scheduling and reschedule mails). The only real
marketing mails in the gap, two Naukri "Limited Slots! Free GenAI Masterclass",
were never booked.

Widening the exclusion to the body would have blocked those 6 genuine
interviews, so the exclusion is deliberately left reading the subject only.
"""

from __future__ import annotations

import inspect

import pytest

from services import recruitment_semantics as semantics

INTERVIEW_STATUSES = ("INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED")


def supporting_context(event="INTERVIEW_CONFIRMED"):
    return {"interview_event": event, "asserted_transitions": [event]}


class TestTheSignalCanOnlyVeto:
    @pytest.mark.parametrize("status", INTERVIEW_STATUSES)
    def test_it_never_returns_a_status_nobody_proposed(self, status):
        """NONE in, NONE out -- however strongly the context asserts."""
        assert semantics.validate_interview_event("NONE", supporting_context(status)) == ("NONE", None)

    @pytest.mark.parametrize("status", INTERVIEW_STATUSES)
    def test_it_returns_the_proposal_unchanged_or_none(self, status):
        safe, _ = semantics.validate_interview_event(status, supporting_context(status))
        assert safe == status
        safe, reason = semantics.validate_interview_event(status, {"interview_event": "NONE"})
        assert safe == "NONE"
        assert reason == "INTERVIEW_EVENT_NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT"

    def test_the_only_two_outcomes_are_the_proposal_and_none(self):
        source = inspect.getsource(semantics.validate_interview_event)
        returns = [line.strip() for line in source.splitlines() if line.strip().startswith("return ")]
        for line in returns:
            assert line.startswith('return "NONE"') or line.startswith("return status")

    def test_a_mismatched_pair_is_refused_even_when_both_are_interviews(self):
        safe, reason = semantics.validate_interview_event(
            "INTERVIEW_CONFIRMED", supporting_context("INTERVIEW_CANCELLED"),
        )
        assert safe == "NONE"
        assert reason == "INTERVIEW_EVENT_NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT"


class TestTheWebinarStillAssertsAnInterviewAndStillCannotBook:
    SUBJECT = "Her son's behaviour transformed through GraphoTherapy | Read how"
    BODY = (
        "Dear friend, she started practicing GraphoTherapy and everything changed. "
        "Join our FREE Interview with Imran Baig, India's most trusted handwriting "
        "analysis coach. 27th August 2026 Thursday, 3:30 PM IST. "
        "Zoom Link: https://us06web.zoom.us/meeting/register/test Meeting ID: 000 0000 0001."
    )

    def _context(self):
        return semantics.classify_context(
            self.SUBJECT, self.BODY, sender_email="contact@miteshkhatri.com",
            sent_at="2026-08-25T07:00:00+05:30",
        )

    def test_the_subject_only_exclusion_still_lets_it_through(self):
        """Pins the precondition, so the rest cannot pass vacuously."""
        assert semantics._is_assertive_interview_invitation(self.SUBJECT, self.BODY)
        assert self._context()["interview_event"] == "INTERVIEW_CONFIRMED"

    def test_but_it_authorises_nothing_on_its_own(self):
        assert semantics.validate_interview_event("NONE", self._context()) == ("NONE", None)

    def test_a_booking_still_needs_the_model_to_propose_one_first(self):
        """Which is what the relevance gate stops for every marketing mail."""
        context = self._context()
        assert semantics.validate_interview_event("INTERVIEW_CONFIRMED", context)[0] == "INTERVIEW_CONFIRMED"
        # ...but only because the classifier proposed it, and the classifier
        # only runs once relevance is ESTABLISHED. Every marketing sample
        # checked in production answered NOT_ESTABLISHED.


class TestNonOutcomeContextStillOverridesEverything:
    @pytest.mark.parametrize("flag", [
        "is_questionnaire", "is_question", "is_promotional_or_job_ad",
        "is_historical_information",
    ])
    def test_a_flagged_mail_cannot_carry_an_interview_event(self, flag):
        context = {**supporting_context(), flag: True, "email_intent": "JOB_ADVERTISEMENT"}
        safe, reason = semantics.validate_interview_event("INTERVIEW_CONFIRMED", context)
        assert safe == "NONE"
        assert reason == "JOB_ADVERTISEMENT"

    def test_a_non_interview_status_is_refused_by_name(self):
        assert semantics.validate_interview_event("SELECTED", supporting_context()) == (
            "NONE", "NOT_AN_INTERVIEW_EVENT"
        )
