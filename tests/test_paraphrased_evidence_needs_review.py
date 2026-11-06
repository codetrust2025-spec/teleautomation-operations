"""A mail the AI could not quote properly is reviewed, not discarded.

Two different failures were landing in the same silent ignore:

  1. the mail says no such thing -- a rejection, a job advert. Rightly ignored.
  2. the mail does say it, and the model paraphrased instead of quoting. The
     anti-hallucination guard refuses to trust a non-verbatim quote, which is
     correct, and the outcome was that a real interview disappeared as "not
     offer related".

The Karat reminder was the second kind: its single cited excerpt matched the
body for 120 characters and then diverged, so nothing was verbatim, so
`supported` was empty and the classification was thrown away silently.

The guard is unchanged. Nothing auto-books without verified verbatim evidence
that entails the transition. The second case now goes to Needs Review, where a
human can see it.
"""

from __future__ import annotations

import pytest

from services import recruitment_mail_agent as agent
from tests.legacy_detection import legacy_rules_first  # noqa: F401
from tests.test_recruitment_pipeline import message, structured


def validated(status: str, confidence: float, quote: str, subject: str, body: str):
    """Run validate_result and hand back what it decided."""
    value = structured(status, confidence, quote)
    agent.validate_result(value, message(subject, body), [])
    return value


REMINDER_SUBJECT = (
    "Your Altimetrik Interview for the Citi Scaled Hiring FPC - NAM Project "
    "Is Coming Up!"
)
REMINDER_BODY = (
    "This is a quick reminder that your Altimetrik interview for the Citi "
    "Scaled Hiring FPC - NAM project is coming up soon! "
    "Your Interview: Friday, September 11, 2026 from 8:30am to 9:30am UTC. "
    "Your interview will be a live video call lasting approximately 60 minutes "
    "in our interactive coding environment, Karat Studio."
)
# Starts verbatim, then diverges -- what the model actually produced.
PARAPHRASE = (
    "Your interview will be a live video call lasting approximately 60 minutes "
    "in our interactive coding environment, and covers several topics."
)


class TestAParaphraseGoesToReview:
    def test_it_is_not_silently_ignored(self):
        result = validated(
            "INTERVIEW_CONFIRMED", .95, PARAPHRASE, REMINDER_SUBJECT, REMINDER_BODY,
        )
        assert result["status"] != "IGNORED_NOT_OFFER_RELATED"

    def test_it_is_kept_as_an_alert(self):
        """6 Nov 2026: wording no longer decides whether there is an alert."""
        result = validated(
            "INTERVIEW_CONFIRMED", .95, PARAPHRASE, REMINDER_SUBJECT, REMINDER_BODY,
        )
        # interview_update here only because this fixture carries no readable
        # schedule; that downgrade is the schedule check, not the evidence one.
        assert result["classification"] in {"interview_confirmed", "interview_update"}
        assert result["should_create_review_record"] is True

    def test_it_says_why(self):
        result = validated(
            "INTERVIEW_CONFIRMED", .95, PARAPHRASE, REMINDER_SUBJECT, REMINDER_BODY,
        )
        assert result["backend_validation_reason"] == "EVIDENCE_NOT_ENTAILED"

    def test_no_one_is_asked_and_it_is_not_retried(self):
        result = validated(
            "INTERVIEW_CONFIRMED", .95, PARAPHRASE, REMINDER_SUBJECT, REMINDER_BODY,
        )
        assert result["requires_manual_review"] is False
        assert result["automation_decision"] == "AUTO_IGNORE"

    def test_it_cannot_auto_book(self):
        """No verified verbatim evidence entails it, so nothing may be booked."""
        result = validated(
            "INTERVIEW_CONFIRMED", .95, PARAPHRASE, REMINDER_SUBJECT, REMINDER_BODY,
        )
        assert result["backend_transition_validated"] is False
        assert result["validation_status"] != "AUTO_VALIDATED"


class TestTheGuardIsUnchanged:
    def test_invented_evidence_on_a_supporting_mail_is_still_reviewed_not_booked(self):
        """Fabrication never books. It is now visible rather than silent, which
        is the whole change -- but it is still not an outcome."""
        result = validated(
            "SELECTED", .95, "invented evidence",
            "Congratulations", "You have been selected.",
        )
        assert result["backend_transition_validated"] is False
        assert result["evidence"] == []
        assert result["validation_status"] != "AUTO_VALIDATED"

    @pytest.mark.parametrize("subject,body", [
        ("Interview outcome",
         "Thank you for your interview. We regret to inform you your "
         "application was unsuccessful."),
        ("Java Developer jobs",
         "Apply now. Interview rounds include technical and HR discussions."),
        ("Interview tips",
         "Your interview preparation guide is coming up in our newsletter."),
    ])
    def test_a_mail_that_proves_nothing_is_still_ignored(self, subject, body):
        """The protection: no entailing sentence in the source means the silent
        rejection stands, so rejections and job adverts do not reach a human."""
        result = validated("INTERVIEW_CONFIRMED", .95, "made up", subject, body)
        # Ignored, and never routed to a human. Which gate stops it varies --
        # these are rejected earlier, by the assertive-context and job-advert
        # checks -- so what is pinned is the outcome, not the reason code.
        assert result["status"] == "IGNORED_NOT_OFFER_RELATED"
        assert result["ignore_reason"] != "EVIDENCE_NOT_VERBATIM"
        assert result.get("requires_manual_review") is not True

    def test_a_verbatim_entailing_quote_still_passes_straight_through(self):
        entailing_quote = (
            "This is a quick reminder that your Altimetrik interview for the "
            "Citi Scaled Hiring FPC - NAM project is coming up soon!"
        )
        result = validated(
            "INTERVIEW_CONFIRMED", .95, entailing_quote,
            REMINDER_SUBJECT, REMINDER_BODY,
        )
        assert result["status"] != "IGNORED_NOT_OFFER_RELATED"
        assert result["ignore_reason"] != "EVIDENCE_NOT_VERBATIM"


class TestThePromptAsksForAnExactQuote:
    def test_it_says_copied_not_written(self):
        assert "EVIDENCE MUST BE COPIED, NOT WRITTEN" in agent.CLASSIFIER_PROMPT

    def test_it_forbids_joining_and_tidying(self):
        prompt = agent.CLASSIFIER_PROMPT
        for rule in ("do not join two sentences", "do not tidy wording",
                     "do not summarise"):
            assert rule in prompt

    def test_it_says_a_near_miss_is_treated_as_invented(self):
        assert "treated as invented" in agent.CLASSIFIER_PROMPT

    def test_it_asks_for_the_proving_sentence_not_the_format_one(self):
        prompt = agent.CLASSIFIER_PROMPT
        assert "PROVES the transition" in prompt
        assert "format, duration, platform or" in prompt
