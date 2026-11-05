"""A shortlist after the interviews is a selection outcome.

Mail Alerts filed every shortlist under Interview Related. HCLTech's "you have
been shortlisted for the position you interviewed for ... Please share below
documents at earliest for HR Discussion" sat beside interview invitations, and
so did its "You've successfully progressed through the [Evaluation 2]" and
Fractal's "shortlisted ... undergo a background verification (BGV) check".

Such a shortlist is now `post_interview_shortlisted`, in the Selection Related
group. A shortlist that only leads to an interview keeps
`interview_shortlisted` and stays Interview Related. Nothing about booking
changes: a shortlist books nothing either way.

Texts keep production wording; names and companies are invented.
"""
from __future__ import annotations

import pytest

from core import recruitment_mail_store as store
from services import recruitment_mail_agent as agent
from services.recruitment_automation import decision_for
from services.recruitment_semantics import is_post_interview_shortlist
from tests.test_recruitment_relevance_architecture import _message, _model_result

ESTABLISHED = dict(decision="ESTABLISHED", message_kind="RECIPIENT_HIRING_PROCESS")

POST_INTERVIEW = [
    ("Documents Required for HR Discussion- Test Candidate",
     "We are pleased to inform you that you have been shortlisted for the position you interviewed for. "
     "Please share below documents at earliest for HR Discussion."),
    ("Examplecorp | Evaluation 2 Selection for 100001",
     "Dear Test, You’ve successfully progressed through the [Evaluation 2] for 100001. "
     "A recruiter will be in touch with you on the next steps."),
    ("Request for Documents- Examplecorp",
     "Your profile is shortlisted and to proceed ahead we require the following documents at the earliest. "
     "As a part of our standard recruitment process, we require all candidates to undergo a background "
     "verification (BGV) check."),
    ("Documentation Process",
     "We are pleased to inform you that you have been shortlisted for the role of Data Engineer. "
     "Kindly share the below set of documents: Last 3 months salary slips, Relieving letter."),
]

PRE_INTERVIEW = [
    ("Face-to-Face Interview Invitation",
     "We are pleased to inform you that your profile has been shortlisted for the Face-to-Face Interview "
     "scheduled on 31st October."),
    ("L1 Technical Interview Invitation",
     "We are pleased to inform you that your profile has been shortlisted for the next round. "
     "Please find the L1 Technical Discussion details below."),
    ("UAN no. and PAN Card Details",
     "Your profile has been shortlisted with Examplecorp for further interview process."),
    ("Next Stage of Interview Process",
     "We are pleased to inform you that your profile has been shortlisted for the next stage of our "
     "interview process."),
    ("Your profile has been Shortlisted for technical evaluation",
     "Your profile has been Shortlisted for technical evaluation. We will schedule the L1 Technical Interview."),
    ("Interview process",
     "Your profile has been shortlisted for the technical round. "
     "If you clear the technical round, we will schedule the HR round."),
]


def _validate(subject: str, body: str) -> dict:
    value = _model_result("INTERVIEW_SHORTLISTED", body.split(". ")[0].rstrip(".") + ".")
    agent.validate_result(value, _message(subject, body), [], relevance=dict(ESTABLISHED))
    return value


@pytest.fixture(autouse=True)
def _pure(monkeypatch):
    monkeypatch.setattr(agent, "pure_ollama_enabled", lambda: True)


@pytest.mark.parametrize("subject,body", POST_INTERVIEW, ids=[p[0][:30] for p in POST_INTERVIEW])
def test_a_shortlist_after_the_interviews_is_selection_related(subject, body):
    value = _validate(subject, body)
    assert value["status"] == "INTERVIEW_SHORTLISTED"
    assert value["classification"] == "post_interview_shortlisted"
    assert value["candidate_status"] == "Shortlisted After Interview"
    assert "post_interview_shortlisted" in store.CLASSIFICATION_GROUPS["selection"]
    assert store.canonical_classification(value) == "post_interview_shortlisted"
    # Booking is untouched: a shortlist never books.
    assert decision_for(value).value == "AUTO_IGNORE"


@pytest.mark.parametrize("subject,body", PRE_INTERVIEW, ids=[p[0][:30] for p in PRE_INTERVIEW])
def test_a_shortlist_that_leads_to_an_interview_stays_interview_related(subject, body):
    value = _validate(subject, body)
    assert value["classification"] == "interview_shortlisted"
    assert "interview_shortlisted" in store.CLASSIFICATION_GROUPS["interview"]


def test_an_unproven_shortlist_is_not_reclassified():
    value = _model_result("INTERVIEW_SHORTLISTED", "Thank you for applying.")
    agent.validate_result(value, _message("Application received", "Thank you for applying. "
                          "Please share your payslips."), [], relevance=dict(ESTABLISHED))
    assert value["classification"] != "post_interview_shortlisted"


def test_the_new_classification_is_tracked_and_in_exactly_one_group():
    groups = [name for name, members in store.CLASSIFICATION_GROUPS.items() if "post_interview_shortlisted" in members]
    assert groups == ["selection"]
    assert "post_interview_shortlisted" in store.TRACKED_NOTIFICATION_CLASSIFICATIONS
    assert store.notification_priority("post_interview_shortlisted", confidence=0.95) == "high"


@pytest.mark.parametrize("sentence", [
    "If you clear the technical round, we will schedule the HR discussion.",
    "You have not cleared the technical round.",
    "Once you complete your BGV, we will proceed.",
])
def test_a_condition_or_negation_is_not_post_interview(sentence):
    assert is_post_interview_shortlist(sentence) is False


def test_the_correction_moves_only_post_interview_shortlists():
    import importlib.util, pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "reclassify_post_interview_shortlists.py"
    spec = importlib.util.spec_from_file_location("reclassify", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [
        dict(id="a", classification="interview_shortlisted", email_subject="Documents Required for HR Discussion", body=""),
        dict(id="b", classification="interview_shortlisted", email_subject="L1 Interview Invitation",
             body="Your profile has been shortlisted for the L1 Technical Discussion."),
        dict(id="c", classification="interview_confirmed", email_subject="Interview after your HR discussion", body=""),
        dict(id="d", classification="interview_shortlisted", email_subject="Evaluation 2 Selection",
             body="You\u2019ve successfully progressed through the [Evaluation 2] for 100001."),
    ]
    chosen = module.candidates(rows, clean=lambda text: text, is_post=is_post_interview_shortlist)
    assert [row["id"] for row in chosen] == ["a", "d"]
