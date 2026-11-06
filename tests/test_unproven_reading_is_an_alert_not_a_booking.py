"""An AI reading without a quoted sentence proving it is an alert, never a booking.

6 Nov 2026, owner decision: the evidence check no longer decides whether an
important mail produces an alert -- it withheld a conditional offer, offer
signatures, onboarding and BGV mails on wording and parked them after two
attempts. It still decides everything that acts on a reading: interview and
assessment booking, and candidate stage changes. Driven through the real entry
points, because the booking hand-off is where a missing gate would hurt.
"""
from __future__ import annotations

import pytest

from core import recruitment_mail_store as store
from services import recruitment_mail_agent as agent
from services import recruitment_automation
from tests.test_recruitment_relevance_architecture import _message, _model_result

ESTABLISHED = dict(decision="ESTABLISHED", message_kind="RECIPIENT_HIRING_PROCESS")


@pytest.fixture(autouse=True)
def _pure(monkeypatch):
    monkeypatch.setattr(agent, "pure_ollama_enabled", lambda: True)


def test_an_unproven_offer_is_an_alert_that_moves_no_stage():
    value = _model_result("OFFER_IN_PROGRESS", "We will share the details with you.")
    agent.validate_result(value, _message("Conditional Offer", "Hi, we will share the details with you."), [],
                          relevance=dict(ESTABLISHED))
    assert value["classification"] == "offer_received"
    assert value["should_create_review_record"] is True
    assert value["backend_transition_validated"] is False
    assert value["validation_status"] != "AUTO_VALIDATED"  # apply_candidate_job_status needs it
    assert recruitment_automation.decision_for(value).value == "AUTO_IGNORE"


def test_it_reaches_the_alert_screen_when_relevance_was_established():
    value = _model_result("SELECTED", "Thank you for your time.")
    agent.validate_result(value, _message("Update", "Thank you for your time."), [], relevance=dict(ESTABLISHED))
    value.update(classification_source="OLLAMA", recruitment_relevance_result=dict(ESTABLISHED))
    event = {"primary_status": value["status"], "classification": value["classification"], "structured_result": value}
    assert store.should_route_to_mail_alert(event, {"classification": value["classification"]})


def test_interview_booking_refuses_it(monkeypatch):
    from services import interview_auto_booking as booking

    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    value = {"classification_source": "OLLAMA", "ai_validation_status": agent.EVIDENCE_NOT_ENTAILED,
             "confidence": 0.99, "interview": {"date": "2026-11-20", "time": "11:00 AM", "timezone": "Asia/Kolkata"}}
    with pytest.raises(booking.BookingValidationError) as refused:
        booking.validate_ai_for_booking(value, "interview_confirmed")
    assert refused.value.code == "AI_NOT_VALIDATED"


def test_assessment_booking_refuses_it(monkeypatch):
    from services import assessment_auto_booking as module

    audits = []
    monkeypatch.setattr(module.mail_store, "canonical_classification", lambda result: "assessment_invited")
    monkeypatch.setattr(module.mail_store, "record_interview_analysis", lambda **kw: {"id": "analysis-1"})
    monkeypatch.setattr(module.mail_store, "record_booking_audit", lambda **kw: audits.append(kw) or {"id": "audit-1"})
    monkeypatch.setattr(module.candidate_store, "assign_interview_slot",
                        lambda **kw: pytest.fail("an unproven assessment must never be booked"))
    outcome = module.execute_assessment_booking(
        mailbox={"id": "mailbox-1", "candidate_id": "candidate-1"},
        message={"provider_message_id": "gm-1", "subject": "Assessment", "body": "Complete the assessment."},
        event={"id": "event-1", "mailbox_message_id": "message-1", "notification": {}},
        result={"classification_source": "OLLAMA", "ai_validation_status": agent.EVIDENCE_NOT_ENTAILED},
    )
    assert outcome["failure_code"] == "AI_NOT_VALIDATED"
    assert audits[-1]["auto_booked"] is False


def test_the_pipeline_alerts_and_never_enters_booking(monkeypatch):
    """Through `process_message`: the alert is published and neither booking path runs."""
    monkeypatch.setattr(agent.store, "insert_message", lambda mailbox, decoded, score: (
        {"id": "stored-1", "processing_status": "FILTERED"}, True))
    for name in ("is_duplicate_content", "is_duplicate_offer_attachment", "is_duplicate_thread_status"):
        monkeypatch.setattr(agent.store, name, lambda *a, **k: False)
    monkeypatch.setattr(agent.store, "mark_message_status", lambda *a, **k: None)
    monkeypatch.setattr(agent.store, "save_attachment", lambda mid, attachment: attachment)
    monkeypatch.setattr(agent.store, "record_analysis", lambda *a, **k: None)
    monkeypatch.setattr(agent.store, "create_event", lambda cid, mid, result, **meta: {
        "id": "event-1", "mailbox_message_id": "stored-1", "candidate_id": cid,
        "classification": result["classification"], "primary_status": result["status"], "notification": {}})
    monkeypatch.setattr("services.recruitment_notifications.notify_detection", lambda event: None)
    result = {**_model_result("INTERVIEW_CONFIRMED", "Looking forward to speaking with you."),
              "classification": "interview_confirmed", "primary_status": "INTERVIEW_CONFIRMED",
              "classification_source": "OLLAMA", "ai_validation_status": agent.EVIDENCE_NOT_ENTAILED,
              "backend_validation_reason": agent.EVIDENCE_NOT_ENTAILED, "validation_status": "MEDIUM_CONFIDENCE",
              "backend_transition_validated": False, "evidence": [],
              "should_create_review_record": True, "is_selection_or_offer_related": True,
              "recruitment_relevance_result": dict(ESTABLISHED),
              "interview": {"date": "2026-11-20", "time": "11:00 AM", "timezone": "Asia/Kolkata"}}
    monkeypatch.setattr(agent, "analyze", lambda decoded, attachments: (result, "test-model", 10))
    import services.interview_auto_booking as interview_booking
    import services.assessment_auto_booking as assessment_booking
    monkeypatch.setattr(interview_booking, "execute_auto_booking",
                        lambda **kw: pytest.fail("an unproven interview must never enter booking"))
    monkeypatch.setattr(assessment_booking, "execute_assessment_booking",
                        lambda **kw: pytest.fail("an unproven assessment must never enter booking"))
    published = []
    monkeypatch.setattr(agent, "_publish", lambda event_type, **payload: published.append(event_type))

    agent.process_message(
        {"id": "mailbox-1", "candidate_id": "candidate-1"},
        {"provider_message_id": "gm-1", "provider_thread_id": "t-1", "sender_email": "hr@examplecorp.test",
         "subject": "Interview", "body": "Looking forward to speaking with you.", "sent_at": "2026-11-06T04:00:00Z"},
        [],
    )
    assert "important_mail_detected" in published
    assert "auto_booking_started" not in published


def test_analyze_withholds_validated_from_an_unproven_reading():
    import inspect

    source = inspect.getsource(agent.analyze) if hasattr(agent, "analyze") else ""
    full = inspect.getsource(agent)
    assert 'result["ai_validation_status"] = (\n            EVIDENCE_NOT_ENTAILED' in full.replace("\r\n", "\n")
    assert source is not None


@pytest.mark.parametrize("sender,alerted", [
    ("hr@examplecorp.test", True),
    ("recruiter.abc@naukri.com", False),
    ("alerts@indeed.com", False),
])
def test_job_boards_do_not_alert_on_an_unproven_reading(monkeypatch, sender, alerted):
    """Owner decision, 6 Nov 2026: a sender rule, not a wording one. Replayed over
    production, most withheld readings were job-board adverts labelled Selected."""
    monkeypatch.setattr(agent.store, "insert_message", lambda mailbox, decoded, score: (
        {"id": "stored-1", "processing_status": "FILTERED"}, True))
    for name in ("is_duplicate_content", "is_duplicate_offer_attachment", "is_duplicate_thread_status"):
        monkeypatch.setattr(agent.store, name, lambda *a, **k: False)
    monkeypatch.setattr(agent.store, "mark_message_status", lambda *a, **k: None)
    monkeypatch.setattr(agent.store, "save_attachment", lambda mid, attachment: attachment)
    monkeypatch.setattr(agent.store, "record_analysis", lambda *a, **k: None)
    created = []
    monkeypatch.setattr(agent.store, "create_event", lambda cid, mid, result, **meta: created.append(result) or {
        "id": "event-1", "mailbox_message_id": "stored-1", "candidate_id": cid,
        "classification": result["classification"], "primary_status": result["status"], "notification": {}})
    monkeypatch.setattr("services.recruitment_notifications.notify_detection", lambda event: None)
    result = {**_model_result("SELECTED", "You have been selected."), "classification": "job_selection_confirmed",
              "primary_status": "SELECTED", "classification_source": "OLLAMA",
              "ai_validation_status": agent.EVIDENCE_NOT_ENTAILED, "backend_validation_reason": agent.EVIDENCE_NOT_ENTAILED,
              "validation_status": "MEDIUM_CONFIDENCE", "backend_transition_validated": False, "evidence": [],
              "should_create_review_record": True, "is_selection_or_offer_related": True,
              "recruitment_relevance_result": dict(ESTABLISHED)}
    monkeypatch.setattr(agent, "analyze", lambda decoded, attachments: (dict(result), "test-model", 10))
    published = []
    monkeypatch.setattr(agent, "_publish", lambda event_type, **payload: published.append(event_type))
    agent.process_message(
        {"id": "mailbox-1", "candidate_id": "candidate-1"},
        {"provider_message_id": "gm-1", "provider_thread_id": "t-1", "sender_email": sender,
         "subject": "Update", "body": "You have been selected.", "sent_at": "2026-11-06T04:00:00Z"},
        [],
    )
    assert bool(created) is alerted
    assert ("important_mail_detected" in published) is alerted
