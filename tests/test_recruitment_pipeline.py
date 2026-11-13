from pathlib import Path

import pytest

from services import recruitment_mail_agent as agent


@pytest.fixture(autouse=True)
def rules_first_flow(monkeypatch):
    """This file describes the OFF flow: keyword and routing rules decide first.

    Pure Ollama AI Mail Detection is ON by default, and ON deliberately sends
    every inbound mail to the model rather than letting those rules drop it --
    so the assertions here about what never reaches AI only hold with the
    switch off. That flow is unchanged and still supported, which is what these
    tests exist to prove. The ON flow has its own file.
    """
    monkeypatch.setenv("PURE_OLLAMA_MAIL_DETECTION", "false")
    from core import pure_ollama_policy

    monkeypatch.setattr(pure_ollama_policy, "_read_state", dict)


def structured(status="OFFER_LETTER_RECEIVED", confidence=.95, evidence_text="offer letter"):
    return {
        "schema_version": "selection_offer_event_v1",
        "is_recruitment_related": True,
        "is_selection_or_offer_related": status not in agent.INTERNAL_STATUSES,
        "should_create_review_record": status not in agent.INTERNAL_STATUSES,
        "status": status,
        "confidence": confidence,
        "ignore_reason": None,
        "candidate": {"name": None, "email": None},
        "company": {"name": "Test Company", "domain": "test.invalid"},
        "job": {"title": "Test Role", "employment_type": None, "location": None},
        "recruiter": {"name": None, "email": None},
        "interview": {key: None for key in ["date", "time", "timezone", "mode", "round", "location", "meeting_link"]},
        "offer": {"offer_detected": True, "offer_letter_detected": status == "OFFER_LETTER_RECEIVED", "appointment_letter_detected": status == "APPOINTMENT_LETTER_RECEIVED", "offer_date": None, "offered_ctc": None, "currency": None, "joining_date": None, "offer_expiry_date": None},
        "attachments": [], "evidence": [{"source":"EMAIL_SUBJECT","meaning":status,"text":evidence_text}], "risk_flags": [],
        "requires_manual_review": False, "summary": "Detected.", "recommended_action": "Review.",
    }


def relevance(quote, decision="ESTABLISHED", kind="RECIPIENT_HIRING_PROCESS"):
    """The relevance gate's answer, which every analyze() run now needs.

    Mails like these used to skip the gate: when the deterministic layer
    asserted a lifecycle event, `_deterministic_relevance_result` answered
    ESTABLISHED itself and no model was called. It answered the same way for a
    handwriting-therapy webinar, so it was removed and Ollama decides intent for
    every mail. The quote has to appear in the body or the anti-hallucination
    guard downgrades the answer.
    """
    return {
        "decision": decision, "message_kind": kind, "confidence": 96,
        "evidence": [{"source": "EMAIL_BODY", "text": quote}],
        "reason": "The mail concerns this recipient's own hiring process.",
    }


def message(subject="Offer letter", body="We are pleased to offer you the Test Role."):
    return {"provider_message_id": "message-1", "provider_thread_id": "thread-1", "sender_email": "jobs" + "@" + "test.invalid", "recipient_email": "candidate" + "@" + "test.invalid", "subject": subject, "sent_at": "2026-07-13T10:00:00Z", "body": body}


def test_relevant_message_runs_pipeline_and_creates_event(monkeypatch):
    saved=[];created=[]
    monkeypatch.setattr(agent.store, "insert_message", lambda mailbox, decoded, score: ({"id": "stored-message"}, True))
    monkeypatch.setattr(agent.store, "is_duplicate_content", lambda *args: False)
    monkeypatch.setattr(agent.store, "is_duplicate_offer_attachment", lambda *args: False)
    monkeypatch.setattr(agent.store, "is_duplicate_thread_status", lambda *args: False)
    monkeypatch.setattr(agent.store, "save_attachment", lambda mid, attachment: saved.append((mid, attachment)))
    monkeypatch.setattr(agent.store, "create_event", lambda cid, mid, result, **meta: created.append((cid, mid, result, meta)) or {"id": "event-1", "primary_status": result["primary_status"]})
    monkeypatch.setattr(agent, "analyze", lambda decoded, attachments: ({**structured(),"primary_status":"OFFER_LETTER_RECEIVED"}, "configured-test-model", 12))
    monkeypatch.setattr("services.recruitment_notifications.notify_detection", lambda event: None)
    result=agent.process_message({"id": "mailbox-1", "candidate_id": "candidate-1"}, message(), [{"filename": "invite.txt", "data": None, "checksum": "checksum-1"}])
    assert result["primary_status"] == "OFFER_LETTER_RECEIVED"
    assert saved and created
    assert created[0][3]["model"] == "configured-test-model"


def test_irrelevant_and_duplicate_messages_do_not_reach_ai(monkeypatch):
    analyzed=[];statuses=[]
    monkeypatch.setattr(agent.store, "insert_message", lambda mailbox, decoded, score: ({"id": "stored-message"}, True))
    monkeypatch.setattr(agent.store, "is_duplicate_content", lambda *args: False)
    monkeypatch.setattr(agent.store, "mark_message_status", lambda mid, status, **kwargs: statuses.append(status))
    monkeypatch.setattr(agent, "analyze", lambda *args: analyzed.append(True))
    assert agent.process_message({"id": "mailbox-1", "candidate_id": "candidate-1"}, message("Weekly newsletter", "General product news."), []) is None
    assert not analyzed
    assert statuses == ["IGNORED_NOT_OFFER_RELATED"]
    statuses.clear()
    monkeypatch.setattr(agent.store, "is_duplicate_content", lambda *args: True)
    assert agent.process_message({"id": "mailbox-1", "candidate_id": "candidate-1"}, message(), []) is None
    assert statuses == ["DUPLICATE_CONTENT"] and not analyzed


def test_job_recommendations_are_ignored_and_interviews_are_tracked(monkeypatch):
    analyzed=[]; created=[]; notified=[]
    monkeypatch.setattr(agent.store, "insert_message", lambda mailbox, decoded, score: ({"id": "stored-message"}, True))
    monkeypatch.setattr(agent.store, "is_duplicate_content", lambda *args: False)
    monkeypatch.setattr(agent.store, "mark_message_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent.store, "is_duplicate_thread_status", lambda *args: False)
    monkeypatch.setattr(agent.store, "create_event", lambda *args, **kwargs: created.append(True) or {"id":"event-1","candidate_id":"candidate-1","classification":"interview_update"})
    interview={**structured("INTERVIEW_UPDATE"),"primary_status":"INTERVIEW_UPDATE","classification":"interview_update","candidate_status":"Interview In Progress"}
    monkeypatch.setattr(agent, "analyze", lambda *args: analyzed.append(True) or (interview,"test-model",1))
    monkeypatch.setattr("services.recruitment_notifications.notify_detection", lambda event: notified.append(event))
    mailbox={"id":"mailbox-1","candidate_id":"candidate-1"}
    assert agent.process_message(mailbox,message("Job recommendations for you | foundit","Apply now to matching jobs."),[]) is None
    # No exact time: Prompt 12 rule 2 makes it OTHER, so no interview alert.
    assert agent.process_message(mailbox,message("Technical interview confirmed","Your interview is scheduled tomorrow."),[]) is None
    assert len(analyzed) == 1 and created == [] and notified == []


@pytest.mark.parametrize(("subject", "body", "reason"), [
    ("Recommended jobs based on your profile", "New roles are waiting for you.", "JOB_RECOMMENDATION"),
    ("Coding test invitation", "Complete the assessment.", "ASSESSMENT"),
    ("Thank you for applying", "Your application is under review.", "APPLICATION_UPDATE"),
    ("Weekly career newsletter", "Upgrade account and apply now.", "JOB_PORTAL_MARKETING"),
])
def test_ordinary_recruitment_mail_is_deterministically_ignored(subject, body, reason):
    decision = agent.prefilter_decision(subject, body, sender_email="alerts@foundit.in")
    assert decision == {
        "qualified": False,
        "score": 0.0,
        "status": "IGNORED_NOT_OFFER_RELATED",
        "evidence": [],
        "ignore_reason": reason,
    }


@pytest.mark.parametrize(("subject","body","status"),[
    ("Your interview has been scheduled","Join the interview tomorrow.","INTERVIEW_UPDATE"),
    ("Regret to inform","You were not selected for the role.","CANDIDATE_REJECTED"),
])
def test_interview_and_rejection_are_informational_status_updates(subject,body,status):
    decision=agent.prefilter_decision(subject,body)
    assert decision["qualified"] is True
    assert decision["status"] == status


def test_negated_offer_disclaimer_is_not_offer_evidence():
    disclaimer = (
        "Unless there is a formal offer of employment from Accenture, any communication "
        "about the selection process shall not be assumed or treated as a commitment or "
        "an offer of employment or guarantee of employment."
    )
    scheduling = agent.prefilter_decision(
        "Your Interview has been successfully Scheduled.",
        f"Your technical interview has been scheduled for tomorrow. {disclaimer}",
    )
    availability = agent.prefilter_decision(
        "Reminder: Your Availability Required to Proceed for an Interview",
        f"Please share your availability so that we can schedule the interview. {disclaimer}",
    )
    assert scheduling["status"] == "INTERVIEW_UPDATE"
    assert all(item["meaning"] != "OFFER_LETTER_RECEIVED" for item in scheduling["evidence"])
    assert availability["status"] != "OFFER_LETTER_RECEIVED"


def test_selected_and_joining_confirmations_pass_the_strict_filter():
    assert agent.relevance_score("Congratulations on your selection","You have been selected for the role.") >= .55
    assert agent.relevance_score("Joining confirmation","Your date of joining is 20 July 2026.") >= .55
    assert agent.relevance_score("Job recommendations for you","This employer may offer a good opportunity.") == 0


def test_shortlisted_with_explicit_joining_date_uses_strongest_full_message_evidence():
    decision=agent.prefilter_decision(
        "Congratulations and Next Steps – Data Engineer Role",
        "Congratulations on being shortlisted for the role of Data Engineer. Your date of joining will be 15th July 2026. ONNI GLOBAL SERVICES INDIA PVT. LTD.",
        sender_email="hr@onniglobal.in",
    )
    assert decision["qualified"] is True
    assert decision["status"] == "JOINING_CONFIRMED"
    assert decision["joining_date"] == "2026-07-15"
    assert decision["job_title"] == "Data Engineer"
    assert "ONNI GLOBAL SERVICES" in decision["company_name"]
    assert decision["score"] >= .90
    assert decision["requires_manual_review"] is True
    assert "WORDING_STATUS_CONFLICT" in decision["risk_flags"]
    assert {item["source"] for item in decision["evidence"]} >= {"EMAIL_BODY","EMAIL_SUBJECT"}


def test_interview_shortlist_is_distinct_and_stronger_offer_evidence_wins():
    generic=agent.prefilter_decision("Application update","You have been shortlisted for the technical interview.")
    assert generic["qualified"] is True
    assert generic["status"] == "INTERVIEW_SHORTLISTED"
    assert agent.prefilter_decision("Next steps","You have been shortlisted. We are planning to release your offer.")["status"] == "OFFER_INDICATION"
    assert agent.prefilter_decision("Documents","You have been shortlisted. Please find your offer letter attached.")["status"] == "OFFER_LETTER_RECEIVED"
    onboarding=agent.prefilter_decision("Next steps","You have been shortlisted. Please complete onboarding before joining on July 15.")
    assert onboarding["status"] in {"POST_SELECTION_ONBOARDING","JOINING_CONFIRMED"}


@pytest.mark.parametrize(("subject", "body", "expected"), [
    ("Congratulations! You have been selected", "You have been selected for the role.", "SELECTED"),
    ("Offer update", "Your offer is currently being processed.", "OFFER_IN_PROGRESS"),
    ("Offer Letter - Data Engineer", "Please find your offer letter attached.", "OFFER_LETTER_RECEIVED"),
    ("Offer acceptance confirmed", "We have accepted your offer acceptance.", "OFFER_ACCEPTED"),
    ("Joining confirmation", "Your joining date is 20 July 2026.", "JOINING_CONFIRMED"),
])
def test_strong_success_signals_are_qualified(subject, body, expected):
    decision=agent.prefilter_decision(subject,body)
    assert decision["qualified"] is True
    assert decision["status"] == expected
    assert decision["evidence"]


def test_appointment_attachment_requires_document_content():
    filename_only=agent.prefilter_decision("Document attached","",attachments=[{"filename":"Appointment_Letter.pdf","text":""}])
    confirmed=agent.prefilter_decision("Document attached","",attachments=[{"filename":"Appointment_Letter.pdf","text":"This document confirms employment with ABC."}])
    assert filename_only["qualified"] is False
    assert confirmed["status"] == "APPOINTMENT_LETTER_RECEIVED"


def test_context_dependent_joining_background_and_salary_rules():
    assert agent.prefilter_decision("Joining","Please confirm your date of joining.")["qualified"] is False
    thread=[{"subject":"Offer letter","body":"You have been selected for the role."}]
    # A request remains a request even when an older thread contains an offer.
    assert agent.prefilter_decision("Joining","Please confirm your date of joining.",thread_context=thread)["status"] == "IGNORED_NOT_OFFER_RELATED"
    assert agent.prefilter_decision("Background verification initiated","Please provide documents.")["status"] == "BACKGROUND_VERIFICATION"
    assert agent.prefilter_decision("Salary discussion","Let us discuss compensation.")["qualified"] is False
    background=agent.prefilter_decision("Background verification initiated","Your offer is approved. Complete background verification.")
    salary=agent.prefilter_decision("Salary discussion","You have been selected. Let us continue the salary discussion.")
    assert background["status"] in {"OFFER_APPROVED", "POST_SELECTION_ONBOARDING"}
    assert salary["status"] in {"SELECTED", "OFFER_INDICATION"}


def test_portal_mail_with_explicit_selection_is_not_blocked_by_sender():
    decision=agent.prefilter_decision(
        "Congratulations, you have been selected by ABC Company",
        "You have been selected for the position.",
        sender_name="foundit", sender_email="alerts@foundit.in",
    )
    assert decision["qualified"] is True
    assert decision["status"] == "SELECTED"


def test_application_stage_shortlist_is_semantically_checked_then_excluded(monkeypatch):
    analyzed=[]; statuses=[]; created=[]
    monkeypatch.setattr(agent.store,"insert_message",lambda *args:({"id":"stored-message","processing_status":"FILTERED"},True))
    monkeypatch.setattr(agent.store,"is_duplicate_content",lambda *args:False)
    monkeypatch.setattr(agent.store,"is_duplicate_offer_attachment",lambda *args:False)
    monkeypatch.setattr(agent.store,"mark_message_status",lambda mid,status,**kwargs:statuses.append((status,kwargs.get("reason"))))
    monkeypatch.setattr(agent.store,"create_event",lambda *args,**kwargs:created.append(True))
    ignored={**structured("IGNORED_NOT_OFFER_RELATED",.98,"complete your application"),
        "primary_status":"IGNORED_NOT_OFFER_RELATED","is_selection_or_offer_related":False,
        "should_create_review_record":False,"evidence":[],"ignore_reason":"INCOMPLETE_APPLICATION"}
    monkeypatch.setattr(agent,"analyze",lambda *args:analyzed.append(True) or (ignored,"qwen3.6",5))
    result=agent.process_message(
        {"id":"mailbox-1","candidate_id":"candidate-1"},
        message("Your profile has been shortlisted", "To move forward, please complete your application by answering a few short questions."),
        [],
    )
    assert result is None
    assert analyzed == [True]
    assert statuses == [("IGNORED_NOT_OFFER_RELATED","INCOMPLETE_APPLICATION")]
    assert not created


def test_historical_false_positive_is_archived_from_all_consumers(monkeypatch):
    archived=[]; reprocessed=[]
    monkeypatch.setattr(agent.store,"insert_message",lambda *args:({"id":"stored-message","processing_status":"EVENT_CREATED"},False))
    monkeypatch.setattr(agent.store,"is_duplicate_content",lambda *args:False)
    monkeypatch.setattr(agent.store,"is_duplicate_offer_attachment",lambda *args:False)
    monkeypatch.setattr(agent.store,"archive_event_for_message",lambda mid,**kwargs:archived.append((mid,kwargs)))
    monkeypatch.setattr(agent.store,"mark_message_status",lambda *args,**kwargs:None)
    monkeypatch.setattr(agent.store,"mark_reprocessed",lambda *args:reprocessed.append(args))
    ignored={**structured("IGNORED_NOT_OFFER_RELATED",.98,"complete your application"),
        "primary_status":"IGNORED_NOT_OFFER_RELATED","is_selection_or_offer_related":False,
        "should_create_review_record":False,"evidence":[],"ignore_reason":"INCOMPLETE_APPLICATION"}
    monkeypatch.setattr(agent,"analyze",lambda *args:(ignored,"qwen3.6",5))
    result=agent.process_message(
        {"id":"mailbox-1","candidate_id":"candidate-1"},
        message("Your profile has been shortlisted", "Complete your application to move forward."),
        [],reprocess=True,
    )
    assert result is None
    assert archived[0][0] == "stored-message"
    assert archived[0][1]["status"] == "IGNORED_NOT_OFFER_RELATED"


def test_ai_failure_never_creates_keyword_derived_lifecycle_event(monkeypatch):
    statuses=[]; created=[]
    monkeypatch.setattr(agent.store,"insert_message",lambda *args:({"id":"stored-message","processing_status":"FILTERED"},True))
    monkeypatch.setattr(agent.store,"is_duplicate_content",lambda *args:False)
    monkeypatch.setattr(agent.store,"is_duplicate_offer_attachment",lambda *args:False)
    monkeypatch.setattr(agent.store,"is_duplicate_thread_status",lambda *args:False)
    monkeypatch.setattr(agent.store,"mark_message_status",lambda mid,status,**kwargs:statuses.append(status))
    monkeypatch.setattr(agent.store,"create_event",lambda *args,**kwargs:created.append((args,kwargs)) or {"id":"event-1","primary_status":args[2]["primary_status"]})
    monkeypatch.setattr(agent,"analyze",lambda *args:(_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr("services.recruitment_notifications.notify_detection",lambda event:None)
    result=agent.process_message(
        {"id":"mailbox-1","candidate_id":"candidate-1"},
        message("Congratulations and Next Steps", "Your date of joining will be 15th July 2026."),
        [],
    )
    assert result is None
    assert statuses == ["AI_RETRY_PENDING"]
    assert created == []


def test_ai_timeout_keeps_ambiguous_mail_hidden_for_background_retry(monkeypatch):
    statuses=[]; created=[]; analyses=[]
    monkeypatch.setattr(agent.store,"insert_message",lambda *args:({"id":"stored-message","processing_status":"FILTERED"},True))
    monkeypatch.setattr(agent.store,"is_duplicate_content",lambda *args:False)
    monkeypatch.setattr(agent.store,"is_duplicate_offer_attachment",lambda *args:False)
    monkeypatch.setattr(agent.store,"mark_message_status",lambda mid,status,**kwargs:statuses.append(status))
    monkeypatch.setattr(agent.store,"record_analysis",lambda *args,**kwargs:analyses.append((args,kwargs)))
    monkeypatch.setattr(agent.store,"create_event",lambda *args,**kwargs:created.append(True))
    monkeypatch.setattr(agent,"analyze",lambda *args:(_ for _ in ()).throw(RuntimeError("offline")))
    result=agent.process_message(
        {"id":"mailbox-1","candidate_id":"candidate-1"},
        message("Update on your application", "We wanted to share an update about your application."),
        [],
    )
    assert result is None
    assert statuses == ["AI_RETRY_PENDING"]
    assert not created
    assert analyses[0][1]["processing_status"] == "RETRY_PENDING"


def test_ollama_outage_on_obvious_job_ad_is_audit_only_end_to_end(monkeypatch):
    """TEST 3 (end-to-end): the deterministic noise gate keeps an obvious
    job-portal ad out of AI entirely, so an Ollama outage never turns it into
    Needs Review — reproduces the production Naukri walk-in reminder email."""
    from core.ai_gateway import AIGatewayError
    statuses=[]; created=[]
    monkeypatch.setattr(agent.store,"insert_message",lambda *args:({"id":"stored-message","processing_status":"FILTERED"},True))
    monkeypatch.setattr(agent.store,"is_duplicate_content",lambda *args:False)
    monkeypatch.setattr(agent.store,"mark_message_status",lambda mid,status,**kwargs:statuses.append(status))
    monkeypatch.setattr(agent.store,"record_analysis",lambda *args,**kwargs:None)
    monkeypatch.setattr(agent.store,"create_event",lambda *args,**kwargs:created.append(True))

    def boom(*args):
        raise AIGatewayError("down", code="OLLAMA_CONNECTION_FAILED")
    monkeypatch.setattr(agent,"analyze",boom)

    naukri_message = message(
        subject="Reminder: Don’t Forget to attend these Walk-in's today",
        body=(
            "Reminder! Don’t forget to attend the walk-in job(s) you have applied to. "
            "Your walk-in reminder Urgent Opening DevOps Engineer Tcs 18 Jul walkin Interview "
            "Concepts Unlimited Date & time 2026-7-18, 9.00 AM - 11.30 AM Location Will share "
            "Be prepared to answer as well as ask questions to the recruiters. Team Naukri"
        ),
    )
    naukri_message["sender_email"] = "reminder@naukri.com"
    result = agent.process_message({"id":"mailbox-1","candidate_id":"candidate-1"}, naukri_message, [])
    assert result is None
    assert not created, "no review-queue event should be created for deterministic noise, even with Ollama down"
    assert statuses == ["IGNORED_NOT_OFFER_RELATED"]


def test_reprocessing_same_thread_status_does_not_duplicate_events(monkeypatch):
    """TEST 8: reprocessing the same email must not create a second
    lifecycle/interview event or a second Review Queue row."""
    created=[]
    monkeypatch.setattr(agent.store,"insert_message",lambda *args:({"id":"stored-message","processing_status":"EVENT_CREATED"},False))
    monkeypatch.setattr(agent.store,"is_duplicate_content",lambda *args:False)
    monkeypatch.setattr(agent.store,"is_duplicate_offer_attachment",lambda *args:False)
    monkeypatch.setattr(agent.store,"is_duplicate_thread_status",lambda *args:True)
    monkeypatch.setattr(agent.store,"mark_message_status",lambda *args,**kwargs:None)
    monkeypatch.setattr(agent.store,"create_event",lambda *args,**kwargs:created.append(True))
    monkeypatch.setattr(agent.store,"create_or_reprocess_event",lambda *args,**kwargs:created.append(True))
    monkeypatch.setattr(agent,"analyze",lambda *args:({**structured("OFFER_LETTER_RECEIVED"),"primary_status":"OFFER_LETTER_RECEIVED"},"model",1))
    result = agent.process_message(
        {"id":"mailbox-1","candidate_id":"candidate-1"}, message(), [], reprocess=True,
    )
    assert result is None
    assert not created, "a duplicate thread status must not create a second review-queue event"


def test_high_impact_joining_result_uses_independent_validator(monkeypatch):
    source=message(
        "Congratulations and Next Steps - Data Engineer Role",
        "Congratulations on being shortlisted for the role of Data Engineer. Your date of joining will be 15th July 2026.",
    )
    outcome=structured("JOINING_CONFIRMED",.96,"Your date of joining will be 15th July 2026")
    outcome["evidence"]=[{"source":"EMAIL_BODY","meaning":"JOINING_CONFIRMED","text":"Your date of joining will be 15th July 2026"}]
    outcome["offer"]["joining_date"]="2026-07-15"
    calls=[]
    outputs=iter([relevance("Your date of joining will be 15th July 2026"),outcome,outcome])
    class Response:
        def __init__(self,value,model):
            self.content=__import__("json").dumps(value);self.model=model;self.duration_ms=7
    monkeypatch.setattr(agent,"configured_models",lambda:{"primary":"qwen3.6","validator":"gemma4"})
    monkeypatch.setattr(agent,"chat_structured",lambda **kwargs:calls.append(kwargs["model"]) or Response(next(outputs),kwargs["model"]))
    result,model,duration=agent.analyze(source,[])
    # Relevance, then the primary, then the independent validator.
    assert calls == ["qwen3.6","qwen3.6","gemma4"]
    assert result["primary_status"] == "JOINING_CONFIRMED"
    assert result["model_validation"]["agreed"] is True
    assert model == "qwen3.6|validator:gemma4"
    assert duration == 21


def test_critical_validator_never_falls_back_to_lightweight_model(monkeypatch):
    source=message("Selection confirmed", "You have been selected for the role.")
    outcome=structured("SELECTED",.96,"You have been selected")
    outcome["evidence"]=[{"source":"EMAIL_BODY","meaning":"SELECTED","text":"You have been selected"}]
    calls=[]
    outputs=iter([relevance("You have been selected"),outcome])
    class Response:
        def __init__(self,value):
            self.content=__import__("json").dumps(value);self.model="qwen2.5:7b";self.duration_ms=7
    monkeypatch.setattr(agent,"configured_models",lambda:{"primary":"qwen2.5:7b","validator":"validator-model","fallback":"gemma2:2b"})
    def fake_chat(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "validator-model":
            raise agent.AIGatewayError("validator unavailable",code="OLLAMA_REQUEST_TIMEOUT")
        return Response(next(outputs))
    monkeypatch.setattr(agent,"chat_structured",fake_chat)
    with pytest.raises(agent.AIGatewayError):
        agent.analyze(source,[])
    # The relevance gate uses the primary too; neither call may reach the
    # lightweight fallback.
    assert calls == ["qwen2.5:7b","qwen2.5:7b","validator-model"]
    assert "gemma2:2b" not in calls


def test_validator_disagreement_never_overrides_primary_or_creates_lifecycle(monkeypatch):
    source=message(
        "Congratulations and Next Steps - Data Engineer Role",
        "Congratulations on being shortlisted for the role of Data Engineer. Your date of joining will be 15th July 2026.",
    )
    ignored={**structured("IGNORED_NOT_OFFER_RELATED",.91,"shortlisted"),
        "is_selection_or_offer_related":False,"should_create_review_record":False,
        "evidence":[],"ignore_reason":"SHORTLIST_ONLY"}
    joining=structured("JOINING_CONFIRMED",.96,"Your date of joining will be 15th July 2026")
    joining["evidence"]=[{"source":"EMAIL_BODY","meaning":"JOINING_CONFIRMED","text":"Your date of joining will be 15th July 2026"}]
    joining["offer"]["joining_date"]="2026-07-15"
    outputs=iter([relevance("Your date of joining will be 15th July 2026"),ignored,joining])
    class Response:
        def __init__(self,value,model):
            self.content=__import__("json").dumps(value);self.model=model;self.duration_ms=3
    monkeypatch.setattr(agent,"configured_models",lambda:{"primary":"qwen3.6","validator":"gemma4"})
    monkeypatch.setattr(agent,"chat_structured",lambda **kwargs:Response(next(outputs),kwargs["model"]))
    result,_,_=agent.analyze(source,[])
    # A positive validator still cannot override a different primary reading,
    # and still creates no lifecycle event. The unresolved disagreement returns
    # for an automatic retry rather than waiting on a person.
    assert result["primary_status"] == "AI_RETRY_PENDING"
    assert result["requires_manual_review"] is False
    assert result["should_create_review_record"] is False
    assert result["lifecycle_event"] == "NONE"
    assert result["backend_transition_validated"] is False
    assert "MODEL_DISAGREEMENT" in result["risk_flags"]


def test_validation_enforces_evidence_and_manual_review_confidence():
    medium=structured("OFFER_INDICATION",.85,"we are pleased to offer you")
    source=message("We are pleased to offer you","Details follow.")
    agent.validate_result(medium,source,[])
    # Medium confidence keeps its reading and is recorded, but is not
    # auto-validated, so no candidate advances and nothing books on it.
    assert medium["status"] != "MANUAL_REVIEW_REQUIRED"
    assert medium["requires_manual_review"] is False
    assert medium["validation_status"] != "AUTO_VALIDATED"
    assert medium["confidence"] == .85
    # Evidence that is not verbatim is never trusted -- that part is unchanged
    # and is what this test exists for. What changed is where the message goes
    # afterwards: the source here does say "You have been selected", so the
    # classification may well be right and the model merely failed to quote it.
    # Silently ignoring that hid real outcomes, so it now goes to a human.
    # Nothing is validated, nothing is recorded as an outcome, nothing books.
    unsupported=structured("SELECTED",.95,"invented evidence")
    agent.validate_result(unsupported,message("Congratulations","You have been selected."),[])
    # 6 Nov 2026: wording no longer decides whether there is an alert. The
    # reading is kept, but invented evidence is dropped and nothing is validated.
    assert unsupported["backend_transition_validated"] is False
    assert unsupported["backend_validation_reason"] == "EVIDENCE_NOT_ENTAILED"
    assert unsupported["status"] == "SELECTED"
    assert unsupported["evidence"] == []
    assert unsupported["validation_status"] != "AUTO_VALIDATED"


def test_medium_confidence_result_keeps_its_reading_without_a_status_overwrite():
    """0.79 is above the review threshold, so this is the medium band.

    The reading is kept whole -- both the classification and the status, where
    previously the status was overwritten with MANUAL_REVIEW_REQUIRED. Nothing
    is auto-validated, so no candidate advances and `interview_auto_booking`
    never sees an actionable classification here.
    """
    low=structured("SELECTED",.79,"you have been selected")
    agent.validate_result(low,message("You have been selected","Details."),[])
    assert low["status"] == "SELECTED"
    assert low["classification"] == "job_selection_confirmed"
    assert low["requires_manual_review"] is False
    assert low["validation_status"] != "AUTO_VALIDATED"

    from services.interview_auto_booking import ACTIONABLE
    assert low["classification"] not in ACTIONABLE


def test_non_outcome_ai_status_is_discarded(monkeypatch):
    statuses=[];created=[]
    monkeypatch.setattr(agent.store,"insert_message",lambda *args:({"id":"stored-message"},True))
    monkeypatch.setattr(agent.store,"is_duplicate_content",lambda *args:False)
    monkeypatch.setattr(agent.store,"is_duplicate_offer_attachment",lambda *args:False)
    monkeypatch.setattr(agent.store,"mark_message_status",lambda mid,status,**kwargs:statuses.append(status))
    monkeypatch.setattr(agent.store,"create_event",lambda *args,**kwargs:created.append(True))
    ignored={**structured("IGNORED_NOT_OFFER_RELATED"),"primary_status":"IGNORED_NOT_OFFER_RELATED","is_selection_or_offer_related":False,"should_create_review_record":False,"ignore_reason":"INTERVIEW"}
    monkeypatch.setattr(agent,"analyze",lambda *args:(ignored,"test",1))
    result=agent.process_message({"id":"mailbox-1","candidate_id":"candidate-1"},message(),[])
    assert result is None
    assert statuses == ["IGNORED_NOT_OFFER_RELATED"]
    assert not created


def test_migration_contains_all_required_tables_and_indexes():
    sql=(Path(__file__).parents[1] / "core" / "migrations" / "001_recruitment_mail_tracking.sql").read_text("utf-8").lower()
    tables={"candidate_mailboxes", "mailbox_messages", "mailbox_attachments", "mailbox_attachment_cache", "ai_recruitment_events", "candidate_status_history", "offer_verification_cases", "mailbox_sync_jobs", "recruitment_audit_log", "recruitment_review_flags"}
    for table in tables:
        assert f"create table if not exists {table}" in sql
    assert "unique(mailbox_id,provider_message_id)" in sql
    assert sql.count("create index if not exists") >= 6


def test_job_outcome_migration_archives_old_broad_matches():
    sql=(Path(__file__).parents[1] / "core" / "migrations" / "002_recruitment_mail_job_outcomes.sql").read_text("utf-8").lower()
    assert "false_positive" in sql
    assert "ignored_not_job_outcome" in sql
    assert "manual_review_required" in sql
    assert "job-outcome filter v2" in sql
    precision=(Path(__file__).parents[1] / "core" / "migrations" / "003_recruitment_mail_selection_offer_precision.sql").read_text("utf-8").lower()
    assert "ignored_not_offer_related" in precision
    assert "ignore_reason" in precision
    assert "ignored_at" in precision
    assert "cleanup_version" in precision
    assert "offer_case_key" in precision


def test_legacy_offer_cleanups_protect_and_restore_interview_reviews():
    migrations=Path(__file__).parents[1] / "core" / "migrations"
    for name in (
        "002_recruitment_mail_job_outcomes.sql",
        "003_recruitment_mail_selection_offer_precision.sql",
        "004_recruitment_mail_offer_review_cleanup.sql",
    ):
        sql=(migrations / name).read_text("utf-8").lower()
        assert "not like 'interview_%'" in sql
    restore=(migrations / "012_recruitment_mail_restore_interview_reviews.sql").read_text("utf-8").lower()
    assert "review_status='pending'" in restore
    assert "confidence>=0.8" in restore
    assert "validation_status in ('needs_review','retry_pending')" in restore
    assert "jsonb_array_length" in restore
    assert "interview_cleanup_restore_v1" in restore
    guard=(migrations / "013_recruitment_mail_restore_review_guard.sql").read_text("utf-8").lower()
    assert "review_status='rejected'" in guard
    assert "validation_status in ('rejected','false_positive')" in guard


# ── calendar revisions must survive deduplication ───────────────────────────

def _body_hash_for(monkeypatch, attachments):
    """The identity process_message computes for a message with these parts."""
    captured = {}
    monkeypatch.setattr(
        agent.store, "insert_message",
        lambda mailbox, decoded, score: captured.update(decoded) or ({"id": "m"}, True),
    )
    monkeypatch.setattr(agent.store, "is_duplicate_content", lambda *args: True)
    monkeypatch.setattr(agent.store, "mark_message_status", lambda *a, **k: None)
    agent.process_message(
        {"id": "mailbox-1", "candidate_id": "candidate-1"},
        message("#CGO#_Round L1_React", "Hi,\nPlease be available."),
        attachments,
    )
    return captured["body_hash"]


def test_an_updated_invite_is_not_mistaken_for_a_resend(monkeypatch):
    """An organiser moving a meeting re-sends the identical covering note with
    a new invite.ics. Hashing the body alone dropped that revision before
    anything read the new time."""
    first = _body_hash_for(monkeypatch, [
        {"filename": "invite.ics", "data": None, "checksum": "ics-sequence-2"},
        {"filename": "cv.pdf", "data": None, "checksum": "cv-unchanged"},
    ])
    second = _body_hash_for(monkeypatch, [
        {"filename": "invite.ics", "data": None, "checksum": "ics-sequence-4"},
        {"filename": "cv.pdf", "data": None, "checksum": "cv-unchanged"},
    ])
    assert first != second


def test_a_genuine_resend_is_still_one_message(monkeypatch):
    attachments = [{"filename": "invite.ics", "data": None, "checksum": "ics-sequence-2"}]
    assert _body_hash_for(monkeypatch, attachments) == _body_hash_for(monkeypatch, attachments)


def test_attachment_order_does_not_change_the_identity(monkeypatch):
    forward = _body_hash_for(monkeypatch, [
        {"filename": "invite.ics", "data": None, "checksum": "aaa"},
        {"filename": "cv.pdf", "data": None, "checksum": "bbb"},
    ])
    reversed_order = _body_hash_for(monkeypatch, [
        {"filename": "cv.pdf", "data": None, "checksum": "bbb"},
        {"filename": "invite.ics", "data": None, "checksum": "aaa"},
    ])
    assert forward == reversed_order


def test_a_message_without_attachments_hashes_on_its_body_alone(monkeypatch):
    # Unchanged behaviour for the ordinary case.
    assert _body_hash_for(monkeypatch, []) == agent.content_hash(
        agent.clean_email("Hi,\nPlease be available.")
    )
