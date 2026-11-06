"""The proof has to be in the mail, not in the sentence the model happened to pick.

The Karat reminder was classified INTERVIEW_CONFIRMED correctly and then thrown
away as EVIDENCE_DOES_NOT_ENTAIL_TRANSITION. Every excerpt the model cited had
to entail the transition itself, and the one it chose described the interview's
*format*:

    "Your interview will be a live video call lasting approximately 60 minutes
     in our interactive coding environment, Karat Studio."

That proves nothing about the interview being scheduled. The sentence that does
was sitting in the same body, unquoted:

    "This is a quick reminder that your Altimetrik interview for the Citi
     Scaled Hiring FPC - NAM project is coming up soon!"

So a correct classification hung on which sentence the model reached for.

The rule is unchanged: an entailing, verbatim excerpt is still required. It is
now looked for in the verified source rather than only among the model's picks,
which is why a rejection, a job advert or a format-only mail still recovers
nothing and stays rejected.
"""

from __future__ import annotations

import pytest

from services import recruitment_mail_agent as agent
from tests.legacy_detection import legacy_rules_first  # noqa: F401


SUBJECT = (
    "Your Altimetrik Interview for the Citi Scaled Hiring FPC - NAM Project "
    "Is Coming Up!"
)
BODY = (
    "This is a quick reminder that your Altimetrik interview for the Citi "
    "Scaled Hiring FPC - NAM project is coming up soon!\n"
    "Your Interview: Friday, September 11, 2026 from 8:30am to 9:30am UTC "
    "(+0000).\n"
    "Your interview will be a live video call lasting approximately 60 minutes "
    "in our interactive coding environment, Karat Studio.\n"
    "This interview will cover the following topics: Discussion and Analysis "
    "Questions."
)

# What the model actually cited, read from mail_ai_analyses in production.
CITED_FORMAT_SENTENCE = (
    "Your interview will be a live video call lasting approximately 60 minutes "
    "in our interactive coding environment, Karat Studio."
)


def sources_for(subject: str = SUBJECT, body: str = BODY):
    return agent._source_texts(subject, body, None, None)


class TestTheSentenceTheModelMissed:
    def test_the_cited_format_sentence_proves_nothing(self):
        """Restating the defect: this is why the classification was discarded."""
        from services.recruitment_semantics import evidence_entails_transition

        assert evidence_entails_transition(
            "INTERVIEW_CONFIRMED", CITED_FORMAT_SENTENCE,
        ) is False

    def test_an_entailing_sentence_is_recovered_from_the_source(self):
        recovered = agent._entailing_evidence_from_source(
            sources_for(), "INTERVIEW_CONFIRMED",
        )
        assert recovered, "the mail says it; the recovery must find it"
        assert len(recovered) == 1

    def test_the_recovered_quote_is_verbatim_from_the_source(self):
        """It must survive the same verbatim check the model's evidence does."""
        sources = sources_for()
        recovered = agent._entailing_evidence_from_source(
            sources, "INTERVIEW_CONFIRMED",
        )
        assert agent._evidence_supported(recovered[0], sources) is True

    def test_the_recovered_quote_entails_the_transition(self):
        sources = sources_for()
        recovered = agent._entailing_evidence_from_source(
            sources, "INTERVIEW_CONFIRMED",
        )
        assert agent._evidence_entails_source_transition(
            recovered[0], sources, "INTERVIEW_CONFIRMED",
        ) is True

    def test_it_is_marked_as_recovered(self):
        """Traceable: this quote came from the backend, not the model."""
        recovered = agent._entailing_evidence_from_source(
            sources_for(), "INTERVIEW_CONFIRMED",
        )
        assert recovered[0]["recovered_by_backend"] is True
        assert recovered[0]["source"] in {
            "EMAIL_SUBJECT", "EMAIL_BODY", "ATTACHMENT", "THREAD_CONTEXT",
        }

    def test_a_format_only_mail_recovers_nothing(self):
        """The defence: no entailing sentence anywhere means no recovery."""
        assert agent._entailing_evidence_from_source(
            sources_for("Karat", CITED_FORMAT_SENTENCE), "INTERVIEW_CONFIRMED",
        ) == []


class TestItRecoversNothingItShouldNot:
    """Rejection and job-ad protection are unchanged."""

    @pytest.mark.parametrize("subject,body", [
        ("Interview outcome",
         "Thank you for your interview. We regret to inform you your "
         "application was unsuccessful."),
        ("Application update",
         "Unfortunately you were not selected after your interview."),
    ])
    def test_a_rejection_recovers_nothing(self, subject, body):
        assert agent._entailing_evidence_from_source(
            sources_for(subject, body), "INTERVIEW_CONFIRMED",
        ) == []

    @pytest.mark.parametrize("subject,body", [
        ("Java Developer jobs",
         "Apply now. Interview rounds include technical and HR discussions."),
        ("We are hiring",
         "5 new jobs match your profile. Our interview process has three rounds."),
    ])
    def test_a_job_advert_recovers_nothing(self, subject, body):
        assert agent._entailing_evidence_from_source(
            sources_for(subject, body), "INTERVIEW_CONFIRMED",
        ) == []

    @pytest.mark.parametrize("subject,body", [
        ("Interview tips",
         "Your interview preparation guide is coming up in our newsletter."),
        ("Newsletter", "Your upcoming interview questions are listed below."),
    ])
    def test_advice_recovers_nothing(self, subject, body):
        assert agent._entailing_evidence_from_source(
            sources_for(subject, body), "INTERVIEW_CONFIRMED",
        ) == []

    def test_an_empty_source_recovers_nothing(self):
        assert agent._entailing_evidence_from_source({}, "INTERVIEW_CONFIRMED") == []

    def test_it_recovers_only_for_the_status_asked_for(self):
        """A reminder is not a cancellation, however hard you look."""
        assert agent._entailing_evidence_from_source(
            sources_for(), "INTERVIEW_CANCELLED",
        ) == []


class TestTheGateStillGates:
    def test_recovery_needs_the_model_to_have_quoted_the_mail(self):
        """The invented-evidence protection must survive this change.

        `supported` holds excerpts that are verbatim in the source. Empty means
        the model fabricated its evidence, and that result is discarded even
        when the source would have supported the conclusion -- a model
        inventing quotes is not to be trusted on that message at all. Recovery
        is only for the other case: it quoted the mail, and picked the wrong
        sentence.
        """
        import inspect

        source = inspect.getsource(agent)
        gate = source.index("_entailing_evidence_from_source(sources, safe_status)")
        preceding = source[:gate]
        assert "if supported and not entailing:" in preceding

    def test_invented_evidence_is_never_recovered_or_validated(self):
        """The case the existing pipeline test protects, restated here.

        The source genuinely says "You have been selected." The model claims
        SELECTED but quotes something that is not in the mail. Recovery must
        not rescue it, and nothing may be validated or booked from it.

        It does now reach a human rather than vanishing -- the classification
        may be right and only the quoting was wrong -- but that is visibility,
        not trust.
        """
        from tests.test_recruitment_pipeline import message, structured

        unsupported = structured("SELECTED", .95, "invented evidence")
        agent.validate_result(
            unsupported, message("Congratulations", "You have been selected."), [],
        )
        assert unsupported.get("backend_evidence_recovered") is not True
        assert unsupported["backend_transition_validated"] is False
        # 6 Nov 2026: kept as an alert (no wording-based rejection), never acted on.
        assert unsupported["backend_validation_reason"] == "EVIDENCE_NOT_ENTAILED"
        assert unsupported["validation_status"] != "AUTO_VALIDATED"
        assert unsupported["evidence"] == []

    def test_a_mail_with_no_proof_is_still_rejected(self):
        """The whole point of the gate survives: recovery returns nothing, so
        the rejection below it still fires."""
        assert agent._entailing_evidence_from_source(
            sources_for("Weekly update", "The team meets on Monday."),
            "INTERVIEW_CONFIRMED",
        ) == []
