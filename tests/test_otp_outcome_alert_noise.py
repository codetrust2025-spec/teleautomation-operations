"""An OTP or bank notice never becomes an employment-outcome Mail Alert unless the source says so.

Production, 10 Nov 2026: an HCLTech "Verification Code" mail was filed as
job_selection_confirmed at confidence 1.0 and shown on Mail Alerts. The
deterministic layer had called it transactional and the backend refused the
transition, but Pure Ollama detection plus MEDIUM_CONFIDENCE recording still
alerted it. The event is still recorded; only the alert is withheld.
"""
from __future__ import annotations

import inspect

from services import recruitment_mail_agent as agent
from services.recruitment_semantics import classify_context, is_transactional_notice

HCL_SUBJECT = "Verification Code"
HCL_BODY = (
    "Dear Candidate, Thank you for your interest in HCL Technologies Limited. To proceed with your "
    "application, please enter the OTP. Your OTP is: 482913. If you have any questions, please feel "
    "free to reach out to the Recruiter. Best regards, Caution/Fraud Alert: HCLTech or its "
    "BGV/recruitment partners never ask for monetary consideration during the recruitment process."
)
HCL = {"subject": HCL_SUBJECT, "body": HCL_BODY}


def _result(classification: str, *, validated: bool = False) -> dict:
    return {"classification": classification, "backend_transition_validated": validated}


def test_the_hcltech_otp_mail_is_recognised_as_a_transactional_notice():
    assert is_transactional_notice(HCL_SUBJECT, HCL_BODY)
    context = classify_context(subject=HCL_SUBJECT, body=HCL_BODY, sender_email="noreply@hcltech.com")
    assert context["recruitment_relevance"] == "NOT_ESTABLISHED"
    assert context["lifecycle_event"] == "NONE"


def test_a_selection_read_off_an_otp_mail_is_not_alerted():
    assert agent.transactional_outcome_without_entailment(_result("job_selection_confirmed"), HCL)
    for outcome in ("offer_received", "joining_confirmed", "hr_confirmation", "interview_shortlisted"):
        assert agent.transactional_outcome_without_entailment(_result(outcome), HCL), outcome


def test_interview_events_on_otp_shaped_mail_still_alert():
    # A real "Interview Access Code" mail is OTP-shaped and about a genuine interview.
    access_code = {"subject": "Interview Access Code",
                   "body": "Your interview is at 3 PM IST today. Your OTP is: 551204. Join with this code."}
    for event in ("interview_confirmed", "interview_rescheduled", "interview_cancelled"):
        assert not agent.transactional_outcome_without_entailment(_result(event), access_code), event


def test_an_outcome_the_source_actually_states_is_never_suppressed():
    assert not agent.transactional_outcome_without_entailment(
        _result("job_selection_confirmed", validated=True), HCL)


def test_an_ordinary_selection_mail_is_unaffected():
    selection = {"subject": "Congratulations - you have been selected",
                 "body": "We are happy to inform you that you have been selected for the role of QA Engineer."}
    assert not is_transactional_notice(selection["subject"], selection["body"])
    assert not agent.transactional_outcome_without_entailment(_result("job_selection_confirmed"), selection)


def test_process_message_withholds_the_alert_but_still_records_the_event():
    source = inspect.getsource(agent.process_message)
    guard = source.index("transactional_outcome_without_entailment(result, decoded)")
    assert '_suppress_monitoring_notification"] = True' in source[guard:guard + 400]
    # The suppression is decided before the event is written, so the event is
    # still created (audit trail) and only notify_detection is skipped.
    assert guard < source.index("store.create_event(")
    assert guard < source.index("notify_detection(event)")
