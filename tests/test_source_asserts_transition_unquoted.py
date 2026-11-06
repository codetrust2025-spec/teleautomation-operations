"""A mail can prove an interview without any one sentence saying so.

`classify_context` reads the whole mail -- interview wording, a real date, a
time, a joining link -- and names the transition from that shape.
`_entailing_evidence_from_source` matches a single sentence against
`_TRANSITION_ASSERTIONS`. One question, two vocabularies, and on real mail they
disagree.

flocareer writes "Remember to attend the video interview today at 06:30 PM IST"
with the job title, date, time and duration laid out beneath it. The shape
reader calls that INTERVIEW_CONFIRMED; the sentence matcher recognises nothing,
`asserted_transitions()` on the same text returns an empty set, and the mail was
classified not_relevant with no review record -- a genuine interview for that
same day, gone.

Checked across production: 26 mails were refused as
EVIDENCE_DOES_NOT_ENTAIL_TRANSITION, and in 23 of them the deterministic layer
named the very transition the model proposed. They read "Interview scheduled
with Mphasis on Sat, August 15", "You are invited for interview with Deloitte",
"Interview Call Letter", "Interview Link - 11:30 AM - 25th Aug 2026".

Those now reach the review queue. Nothing about booking changes: the transition
is still refused, backend_transition_validated stays False, and both
should_route_to_mail_alert and validate_ai_for_booking still say no.
"""

from __future__ import annotations

import pytest

from core import recruitment_mail_store as store
from services import recruitment_mail_agent as agent
from tests.legacy_detection import legacy_rules_first  # noqa: F401

# The real flocareer reminder, with the candidate's name removed.
EY_SUBJECT = "Reminder for interview today at 06:30 PM IST for EY"
EY_BODY = (
    "Dear Candidate, Opportunity is knocking your door! Remember to attend the video "
    "interview today at 06:30 PM IST. Job Title Specialist, AI SRE Engineer Location "
    "Hyderabad, Telangana, India Type of Interview Video Date Sep 09, 2026 Time "
    "06:30 PM IST Duration 30-45 minutes Please Click here to JOIN your interview and "
    "follow the instructions."
)
EY = {
    "subject": EY_SUBJECT, "body": EY_BODY, "sender_name": "FloCareer",
    "sender_email": "no-reply@flocareer.com", "recipient_email": "candidate@example.com",
    "sent_at": "2026-09-09T12:25:00+05:30",
}
ESTABLISHED = {"decision": "ESTABLISHED", "message_kind": "RECIPIENT_HIRING_PROCESS"}


def model_result(status="INTERVIEW_CONFIRMED", *, quote="Opportunity is knocking your door!"):
    """The model proposes the transition but quotes a sentence that entails nothing."""
    classification = store._STATUS_CLASSIFICATION[status]
    return {
        "schema_version": "selection_offer_event_v1",
        "is_recruitment_related": True, "is_selection_or_offer_related": True,
        "should_create_review_record": True, "status": status,
        "classification": classification,
        "candidate_status": store._CLASSIFICATION_STATUS[classification],
        "confidence": 96, "ignore_reason": None,
        "candidate": {"name": None, "email": "candidate@example.com"},
        "company": {"name": "EY", "domain": "ey.com"},
        "job": {"title": "AI SRE Engineer", "employment_type": None, "location": None},
        "recruiter": {"name": None, "email": None},
        "interview": {
            "date": "2026-09-09", "time": "06:30 PM", "end_time": None,
            "duration_minutes": None, "timezone": "Asia/Kolkata", "mode": "Video",
            "round": None, "location": None, "meeting_link": None,
        },
        "offer": {"offer_detected": False, "offer_letter_detected": False,
                  "appointment_letter_detected": False, "offer_date": None,
                  "offered_ctc": None, "currency": None, "joining_date": None,
                  "offer_expiry_date": None},
        "attachments": [],
        "evidence": [{"source": "EMAIL_BODY", "meaning": status, "text": quote}],
        "risk_flags": [], "requires_manual_review": False,
        "summary": "Interview reminder.", "reason": "Interview reminder.",
        "recommended_action": "Attend.",
        "email_intent": "UNKNOWN", "document_type": "NONE",
        "is_candidate_specific": True, "is_job_outcome": True,
        "is_current_event": True, "is_questionnaire": False,
        "is_promotional_or_job_ad": False, "is_historical_information": False,
        "lifecycle_event": status, "business_domain": "INTERVIEW_TRACKING",
        "interview_event": "NONE", "evidence_summary": "Interview reminder.",
    }


def validated(message=EY, **kwargs):
    value = model_result(**kwargs)
    agent.validate_result(value, message, [], relevance=ESTABLISHED)
    return value


class TestThePreconditionThisRestsOn:
    """Without these the rest could pass for the wrong reason."""

    def test_the_shape_reader_names_the_transition(self):
        from services.recruitment_semantics import classify_context

        context = classify_context(EY_SUBJECT, EY_BODY,
                                   sender_email="no-reply@flocareer.com",
                                   sent_at="2026-09-09T12:25:00+05:30")
        assert context["interview_event"] == "INTERVIEW_CONFIRMED"

    def test_but_no_sentence_entails_it(self):
        from services.recruitment_semantics import asserted_transitions, evidence_entails_transition

        text = f"{EY_SUBJECT}\n{EY_BODY}"
        assert evidence_entails_transition("INTERVIEW_CONFIRMED", text) is False
        assert asserted_transitions(text) == set()


class TestTheReminderSurvivesAsAnAutomaticRetry:
    """It must not vanish, and it must not book. Nobody is asked.

    A confirmation the source names but nothing quoted entails would commit the
    candidate to a time no sentence supports, so it is read again rather than
    booked. The release case is the exception, and only the release case --
    see test_missed_slot_is_not_a_booking.py.
    """

    # 6 Nov 2026: no longer retried -- kept as an alert that books nothing.
    def test_it_is_no_longer_silently_ignored(self):
        value = validated()
        assert value["status"] == "INTERVIEW_CONFIRMED"
        assert value["status"] != "IGNORED_NOT_OFFER_RELATED"

    def test_it_books_nothing_and_asks_no_one(self):
        value = validated()
        assert value["should_create_review_record"] is True
        assert value["requires_manual_review"] is False
        assert value["backend_transition_validated"] is False
        assert value["automation_decision"] == "AUTO_IGNORE"

    def test_it_records_why(self):
        value = validated()
        assert value["backend_validation_reason"] == "EVIDENCE_NOT_ENTAILED"


class TestItIsStillNotABooking:
    def test_the_transition_is_still_not_validated(self):
        value = validated()
        assert value["backend_transition_validated"] is False
        assert value["validation_status"] != "AUTO_VALIDATED"

    def test_it_cannot_reach_the_alert_screen(self):
        value = validated()
        value["classification_source"] = "OLLAMA"
        value["recruitment_relevance_result"] = dict(ESTABLISHED)
        event = {"primary_status": value["status"], "classification": value["classification"],
                 "structured_result": value}
        assert not store.should_route_to_mail_alert(
            event, {"classification": value["classification"]})

    def test_auto_booking_still_refuses_it(self, monkeypatch):
        from services import interview_auto_booking as booking

        monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
        value = validated()
        value["classification_source"] = "OLLAMA"
        value["ai_validation_status"] = "EVIDENCE_NOT_ENTAILED"
        with pytest.raises(booking.BookingValidationError):
            booking.validate_ai_for_booking(value, value["classification"])


class TestWhatMustStillBeIgnored:
    NEWSLETTER = {**EY, "subject": "Weekly careers newsletter",
                  "body": "Interview tips and resources for your job search. Unsubscribe."}

    def test_a_mail_whose_source_names_nothing_never_reaches_this_branch(self):
        """The newsletter is refused earlier, for not being supported at all.

        It still ends in review rather than silence, but by the separate
        uncorroborated-proposal path -- so this rescue is not what is holding
        it, and the guard here is untouched.
        """
        value = validated(message=self.NEWSLETTER)
        assert value["backend_validation_reason"] == (
            "INTERVIEW_EVENT_NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT")
        assert value["backend_validation_reason"] != "SOURCE_ASSERTS_TRANSITION_UNQUOTED"

    def test_and_stays_silent_when_relevance_was_never_established(self):
        """Nothing rescues a mail the gate did not let through."""
        value = model_result()
        agent.validate_result(value, self.NEWSLETTER, [],
                              relevance={"decision": "NOT_ESTABLISHED",
                                         "message_kind": "NEWSLETTER"})
        assert value["status"] == "IGNORED_NOT_OFFER_RELATED"
        assert value["should_create_review_record"] is False

    def test_a_job_advert_is_refused_before_any_of_this(self):
        message = {**EY, "subject": "Opening for Automation Testing-Hyderabad",
                   "body": "Job description: 5 years experience required. Apply now."}
        value = model_result()
        agent.validate_result(value, message, [], relevance=ESTABLISHED)
        assert value["backend_validation_reason"] != "SOURCE_ASSERTS_TRANSITION_UNQUOTED"
        assert value["backend_transition_validated"] is False

    def test_the_source_reading_is_what_decides_not_the_model_s_label(self):
        """The model said SHORTLISTED; the source says CONFIRMED, and the
        source wins -- the promotion block replaces the status before this
        runs, so the rescue applies to the transition the mail proves."""
        value = validated(status="INTERVIEW_SHORTLISTED")
        assert value["backend_transition_validated"] is False

    def test_nothing_here_can_produce_a_bookable_result(self):
        for status in ("INTERVIEW_CONFIRMED", "INTERVIEW_SHORTLISTED"):
            value = validated(status=status)
            assert value["backend_transition_validated"] is False
            assert value["automation_decision"] == "AUTO_IGNORE"

    def test_model_disagreement_still_wins(self):
        value = model_result()
        value["risk_flags"] = ["MODEL_DISAGREEMENT"]
        agent.validate_result(value, EY, [], relevance=ESTABLISHED)
        assert value["status"] == "AI_RETRY_PENDING"
        assert value["backend_validation_reason"] == "MODEL_DISAGREEMENT"
