"""A released offer is filed as an offer, even when the models call it "in progress".

6 Nov 2026, live: a staffing firm's "Conditional Offer of Employment" to a
candidate ("Please find the attached Offer of Employment with your
Tentative DOJ ... You are required to sign, scan and send a copy") was read by
both models as OFFER_IN_PROGRESS -- the conditions ("You will be onboarded once
your selection is confirmed by client") made it sound unfinished. The backend
then refused it, because no sentence says an offer is *being prepared*, retried
it twice and dropped it: no Selection Related alert.

The validator now takes the stage the mail's own words assert, as it already
does for an assessment read as an interview: an earlier offer stage that the
source does not assert becomes the released stage it does assert. Every offer
stage files the same "Offer Received" alert. A mail that only says an offer is
being prepared keeps that stage, and a mail that asserts no offer at all is
still refused.

Replayed against all 5,032 stored analyses in production: this mail is the only
result that changes.
"""
from __future__ import annotations

import pytest

from core import recruitment_mail_store as store
from services import recruitment_mail_agent as agent
from services.recruitment_semantics import evidence_entails_transition
from tests.test_recruitment_relevance_architecture import _message, _model_result

ESTABLISHED = dict(decision="ESTABLISHED", message_kind="RECIPIENT_HIRING_PROCESS")

SUBJECT = "Examplecorp_ Conditional Offer of Employment_Test Candidate"
BODY = (
    "Hi Test Candidate, Congratulations! Please find the attached Offer of Employment with your Tentative DOJ "
    "12th Nov 2026, with this email. You are required to sign, scan and send a copy of the same to us so that we "
    "can initiate onboarding process. Note: You will be onboarded once your selection is confirmed by client. "
    "Actual DOJ will be confirmed upon generation of TCS BA ID."
)
ATTACHMENT = (
    "Congratulations !!! Further to your recent interview, we are pleased to offer you the position of Python "
    "Developer in Examplecorp Professional Services Pvt. Ltd."
)


@pytest.fixture(autouse=True)
def _pure(monkeypatch):
    monkeypatch.setattr(agent, "pure_ollama_enabled", lambda: True)


def _validate(status: str, evidence: str, body: str = BODY, attachments=None) -> dict:
    value = _model_result(status, evidence)
    agent.validate_result(value, _message(SUBJECT, body), attachments or [], relevance=dict(ESTABLISHED))
    return value


def test_the_vhs_offer_read_as_in_progress_becomes_an_offer_received_alert():
    value = _validate("OFFER_IN_PROGRESS", "Please find the attached Offer of Employment with your Tentative DOJ 12th Nov 2026, with this email.")
    assert value["status"] == "OFFER_LETTER_RECEIVED"
    assert value["promoted_from"] == "OFFER_IN_PROGRESS"
    assert value["classification"] == "offer_received"
    assert value["backend_transition_validated"] is not False
    assert "offer_received" in store.CLASSIFICATION_GROUPS["selection"]


def test_the_attachment_alone_proves_a_released_offer():
    body = "Hi Test Candidate, Congratulations! Please sign and return the attached document."
    value = _validate("OFFER_IN_PROGRESS", "Please sign and return the attached document.", body=body,
                      attachments=[{"text": ATTACHMENT}])
    assert value["status"] == "OFFER_RECEIVED"
    assert value["classification"] == "offer_received"


def test_an_offer_still_being_prepared_keeps_its_stage():
    body = "Hi Test Candidate, your offer is being processed and will be shared shortly."
    value = _validate("OFFER_IN_PROGRESS", "your offer is being processed", body=body)
    assert value["status"] == "OFFER_IN_PROGRESS"
    assert "promoted_from" not in value
    assert value["classification"] == "offer_received"


def test_a_mail_with_no_offer_in_it_is_still_refused():
    body = "Hi Test Candidate, thank you for attending the interview. We will get back to you."
    value = _validate("OFFER_IN_PROGRESS", "thank you for attending the interview", body=body)
    assert value["classification"] != "offer_received"
    assert value.get("ignore_reason") == "EVIDENCE_DOES_NOT_ENTAIL_TRANSITION"


@pytest.mark.parametrize("sentence,expected", [
    ("Please find the attached Offer of Employment with this email.", True),
    ("Kindly review your Offer of Employment and sign it.", True),
    ("Please find the enclosed offer of employment.", True),
    ("Please find the job description. An offer of employment is not a commitment.", False),
    ("Find out more about an offer of employment at our careers page.", False),
])
def test_the_released_offer_wording(sentence, expected):
    assert evidence_entails_transition("OFFER_LETTER_RECEIVED", sentence) is expected
