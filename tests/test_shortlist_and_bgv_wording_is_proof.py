"""Shortlists and background-verification requests the models read correctly
were retried until they failed, with no alert.

Audit of every monitored mailbox, 5 Nov 2026: both models proposed
INTERVIEW_SHORTLISTED or BACKGROUND_VERIFICATION at 80-100% for mails such as

    "We are pleased to inform you that you have been shortlisted for the
     position you interviewed for. ... Please share below documents at
     earliest for HR Discussion."
    "You've successfully progressed through the [Evaluation 2] for 141850."
    "As part of the Background Verification (BGV) process, please fill in the
     table below ..."

and the backend refused each one as EVIDENCE_DOES_NOT_ENTAIL_TRANSITION,
because no sentence matched its closed vocabulary (it wanted the word
"interview" within 100 characters of "shortlisted", or "please complete" beside
"BGV"). In pure mode that is AI_RETRY_PENDING, retried until the attempts run
out: no event, no Mail Alert.

Replaying the change over all 12,188 stored model results in production
recovered 22 such mails and changed nothing else except three shortlist alerts
raised from conditional sentences ("If you are shortlisted for video/face to
face interview ...", "If your profile got shortlisted will scheduled your
interview ..."), which no longer count as proof.

The texts below keep the production wording; names, numbers and companies are
invented.
"""
from __future__ import annotations

import pytest

from services import recruitment_mail_agent as agent
from services.recruitment_semantics import evidence_entails_transition
from tests.test_recruitment_relevance_architecture import _message, _model_result

ESTABLISHED = dict(decision="ESTABLISHED", message_kind="RECIPIENT_HIRING_PROCESS")

GENUINE = [
    ("INTERVIEW_SHORTLISTED", "Documents Required for HR Discussion- Test Candidate",
     "Hi Test Candidate Good Day!! We are pleased to inform you that you have been shortlisted for the "
     "position you interviewed for. After reviewing your profile and performance, we are excited to move "
     "forward with your application. Please share below documents at earliest for HR Discussion.",
     "After reviewing your profile and performance, we are excited to move forward with your application."),
    ("INTERVIEW_SHORTLISTED", "Examplecorp | Evaluation 2 Selection for 100001",
     "Dear Test, You’ve successfully progressed through the [Evaluation 2] for 100001. "
     "A recruiter will be in touch with you on the next steps.",
     "A recruiter will be in touch with you on the next steps."),
    ("INTERVIEW_SHORTLISTED", "Application Shortlisted: Examplecorp: Data Testing",
     "Hi TEST CANDIDATE, Congratulations! Your profile has been shortlisted for the role of Data Testing - "
     "Quality Engineering at Examplecorp. Our team would like to connect with you to take this forward.",
     "Our team would like to connect with you to take this forward."),
    ("INTERVIEW_SHORTLISTED", "Profile Shortlisted_AWS Devops Engineer",
     "Hi, Congratulations! We are pleased to inform you that your profile has been shortlisted for the AWS "
     "Devops Engineer with EKS role at Exampleco. To expedite the next steps, please reply.",
     "To expedite the next steps, please reply."),
    ("INTERVIEW_SHORTLISTED", "Your profile has been Shortlisted for technical evaluation",
     "Dear Applicant, Your profile has been Shortlisted for technical evaluation. "
     "We will schedule the L1 Technical Interview accordingly.",
     "Dear Applicant"),
    ("INTERVIEW_SHORTLISTED", "Request for Documents- Examplecorp",
     "Hi, Your profile is shortlisted and to proceed ahead we require the following documents at the earliest: "
     "1. Current Employer offer letter",
     "1. Current Employer offer letter"),
    ("INTERVIEW_SHORTLISTED", "Congratulations! You have been shortlisted by Examplecorp",
     "Your Next Action: upload a recent photograph.",
     "Your Next Action: upload a recent photograph."),
    ("BACKGROUND_VERIFICATION", "Request for BGV Documentation and Employment Details",
     "Hi Test, As part of the Background Verification (BGV) process, please fill in the table below with "
     "details of all the companies you have worked for till date.",
     "details of all the companies you have worked for till date"),
    ("BACKGROUND_VERIFICATION", "Pre-Employment Verification documents",
     "Dear Test, As part of our reference check process please follow the link below to create a Candidate "
     "Home Account and submit the Pre-Employment Verification Documents requested as soon as possible. "
     "Should you have any questions please contact your recruiter.",
     "Should you have any questions please contact your recruiter."),
    ("BACKGROUND_VERIFICATION", "Re: BGV Clarification and Pending Documents Required",
     "Hi Test, This is our final follow-up regarding your Background Verification (BGV) process. "
     "Please confirm your interest by 4:00 PM today.",
     "Please confirm your interest by 4:00 PM today."),
    ("BACKGROUND_VERIFICATION", "Candidate invite for background check submission",
     "Dear Test, In partnership with Examplecorp Limited, we will be overseeing the background verification "
     "process. We kindly request your cooperation in completing the necessary steps within 48 hours.",
     "We kindly request your cooperation in completing the necessary steps within 48 hours."),
    ("BACKGROUND_VERIFICATION", "Onboarding Overview",
     "Background check: You will receive a separate email from our verification partner. Request you to "
     "kindly fill the details and initiate your Background check as soon as you receive the link.",
     "You will receive a separate email from our verification partner."),
    ("BACKGROUND_VERIFICATION", "BGV Form Completion and Required Documents Submission",
     "Hi Test, Please find attached BGV form. Kindly fill in all the required details.",
     "Kindly fill in all the required details."),
]


@pytest.mark.parametrize("status,subject,body,cited", GENUINE, ids=[g[1][:40] for g in GENUINE])
def test_a_genuine_mail_is_recorded_not_retried(monkeypatch, status, subject, body, cited):
    """The model quoted a sentence that proves nothing; the proof is elsewhere in the mail."""
    monkeypatch.setattr(agent, "pure_ollama_enabled", lambda: True)
    value = _model_result(status, cited)
    agent.validate_result(value, _message(subject, body), [], relevance=dict(ESTABLISHED))
    assert value["status"] == status
    assert value["backend_transition_validated"] is True
    assert value["should_create_review_record"] is True
    assert value.get("automation_decision") != "AI_RETRY_PENDING"
    # Nothing here is an interview at a time, so nothing can book.
    assert value.get("interview_event") in (None, "NONE")


NOT_PROOF = [
    # Conditionals: production raised shortlist alerts from both.
    ("INTERVIEW_SHORTLISTED", "If you are shortlisted for video/face to face interview at Examplecorp, we would "
                              "need your recent photograph for the next steps."),
    ("INTERVIEW_SHORTLISTED", "If your profile got shortlisted will scheduled your interview tomorrow at 3.00 PM."),
    ("INTERVIEW_SHORTLISTED", "If your profile is shortlisted for the role, our team will contact you."),
    # Resume sourcing names no role.
    ("INTERVIEW_SHORTLISTED", "While reviewing top talents, your profile has been shortlisted for our top client "
                              "for a position in your industry."),
    ("INTERVIEW_SHORTLISTED", "Shortlisted candidates will be contacted for the role of Data Engineer."),
    ("INTERVIEW_SHORTLISTED", "Your application will be reviewed against the criteria required for the role."),
    # Fee-charging immigration agency, production 8 Sep.
    ("BACKGROUND_VERIFICATION", "Kindly share all required documents which are required for the verification "
                                "process after that we can proceed further."),
    ("BACKGROUND_VERIFICATION", "Once you complete your BGV process, the offer will be released."),
    ("HR_CONFIRMATION", "If selected, please share your documents for the HR discussion."),
]


@pytest.mark.parametrize("status,sentence", NOT_PROOF)
def test_a_sentence_that_asserts_nothing_is_not_proof(status, sentence):
    assert evidence_entails_transition(status, sentence) is False


def test_the_hr_document_request_is_hr_proof():
    assert evidence_entails_transition(
        "HR_CONFIRMATION", "Please share below documents at earliest for HR Discussion.",
    ) is True


def test_a_conditional_shortlist_is_retried_not_alerted(monkeypatch):
    monkeypatch.setattr(agent, "pure_ollama_enabled", lambda: True)
    body = ("Your application will be reviewed by the Talent Acquisition Team. Your Next Action If you are "
            "shortlisted for video/face to face interview at Examplecorp, we would need your recent photograph "
            "for the next steps.")
    value = _model_result("INTERVIEW_SHORTLISTED", "Your application will be reviewed by the Talent Acquisition Team.")
    agent.validate_result(value, _message("Job application successful.", body), [], relevance=dict(ESTABLISHED))
    assert value["status"] == "AI_RETRY_PENDING"
    assert value["backend_transition_validated"] is False
