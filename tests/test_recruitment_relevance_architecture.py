import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import recruitment_mail_store as store
from services import recruitment_mail_agent as agent
from tests.legacy_detection import legacy_rules_first  # noqa: F401
from services import recruitment_semantics as semantics


def _message(subject: str, body: str, sender: str = "recruiting@example.com"):
    return {
        "subject": subject,
        "body": body,
        "sender_name": "Recruiting Team",
        "sender_email": sender,
        "recipient_email": "candidate@example.com",
        "sent_at": "2026-08-30T07:00:00+05:30",
    }


def _model_result(status: str, evidence: str, *, source: str = "EMAIL_BODY"):
    classification = store._STATUS_CLASSIFICATION[status]
    interview = {
        key: None for key in (
            "date", "time", "end_time", "duration_minutes", "timezone",
            "mode", "round", "location", "meeting_link",
        )
    }
    if status == "INTERVIEW_CONFIRMED":
        interview.update(date="2026-08-31", time="03:00 PM", timezone="Asia/Kolkata")
    return {
        "schema_version": "selection_offer_event_v1",
        "is_recruitment_related": True,
        "is_selection_or_offer_related": True,
        "should_create_review_record": True,
        "status": status,
        "classification": classification,
        "candidate_status": store._CLASSIFICATION_STATUS[classification],
        "confidence": 96,
        "ignore_reason": None,
        "candidate": {"name": None, "email": "candidate@example.com"},
        "company": {"name": "Example", "domain": "example.com"},
        "job": {"title": "Engineer", "employment_type": None, "location": None},
        "recruiter": {"name": "Recruiting Team", "email": "recruiting@example.com"},
        "interview": interview,
        "offer": {
            "offer_detected": status.startswith("OFFER_") or status == "APPOINTMENT_LETTER_RECEIVED",
            "offer_letter_detected": status == "OFFER_LETTER_RECEIVED",
            "appointment_letter_detected": status == "APPOINTMENT_LETTER_RECEIVED",
            "offer_date": None, "offered_ctc": None, "currency": None,
            "joining_date": None, "offer_expiry_date": None,
        },
        "attachments": [],
        "evidence": [{"source": source, "meaning": status, "text": evidence}],
        "risk_flags": [], "requires_manual_review": False,
        "summary": "Explicit lifecycle transition.",
        "reason": "Explicit lifecycle transition.",
        "recommended_action": "Review.",
        "email_intent": "UNKNOWN", "document_type": "NONE",
        "is_candidate_specific": True, "is_job_outcome": True,
        "is_current_event": True, "is_questionnaire": False,
        "is_promotional_or_job_ad": False, "is_historical_information": False,
        "lifecycle_event": status, "business_domain": "SELECTION_TRACKING",
        "interview_event": "NONE", "evidence_summary": "Explicit transition.",
    }


IMPACTEERS_SUBJECT = "Last call: Free no-code masterclass today"
IMPACTEERS_BODY = (
    "Dear Swathi, Join our free no-code masterclass today. In this live session "
    "you will learn application building and selection components. Time: 7:00 PM "
    "IST. Duration: 60 minutes. Reserve your seat now."
)


@pytest.mark.parametrize(
    ("message", "kind", "quote"),
    [
        (
            _message(IMPACTEERS_SUBJECT, IMPACTEERS_BODY, "events@impacteers.com"),
            "MARKETING_OR_TRAINING", "free no-code masterclass today",
        ),
        (
            _message(
                "Your weekly career toolkit from ResumeWorded",
                "Hi, this newsletter contains resume tips, interview resources, and a public webinar. Unsubscribe anytime.",
                "updates@resumeworded.com",
            ),
            "NEWSLETTER", "this newsletter contains resume tips",
        ),
    ],
)
def test_non_lifecycle_content_stops_at_relevance_gate(monkeypatch, message, kind, quote):
    calls = []

    class Response:
        model = "relevance-model"
        duration_ms = 4
        content = json.dumps({
            "decision": "NOT_ESTABLISHED", "message_kind": kind,
            "confidence": 99, "evidence": [{"source": "EMAIL_BODY", "text": quote}],
            "reason": "The email is informational or promotional and does not describe this recipient's hiring process.",
        })

    monkeypatch.setattr(agent, "configured_models", lambda: {
        "primary": "relevance-model", "validator": "validator-model", "fallback": "fallback-model",
    })
    monkeypatch.setattr(agent, "chat_structured", lambda **kwargs: calls.append(kwargs) or Response())

    result, _, _ = agent.analyze(message, [])

    assert len(calls) == 1
    assert calls[0]["schema"] is agent.RELEVANCE_SCHEMA
    assert result["primary_status"] == "IGNORED_NOT_OFFER_RELATED"
    assert result["classification"] == "not_relevant"
    assert result["should_create_review_record"] is False
    assert result["backend_transition_validated"] is False
    assert result["_decision_trace"]["primary_model_result"] is None


def test_impacteers_still_fails_closed_if_relevance_and_both_models_are_wrong(monkeypatch):
    message = _message(IMPACTEERS_SUBJECT, IMPACTEERS_BODY, "events@impacteers.com")
    wrong = _model_result("INTERVIEW_SHORTLISTED", "Time: 7:00 PM IST. Duration: 60 minutes.")
    outputs = iter([
        {
            "decision": "ESTABLISHED", "message_kind": "RECIPIENT_HIRING_PROCESS",
            "confidence": 90, "evidence": [{"source": "EMAIL_BODY", "text": "Dear Swathi"}],
            "reason": "Incorrect relevance decision used to exercise the backend guard.",
        },
        wrong,
        wrong,
    ])

    class Response:
        def __init__(self, value, model):
            self.content = json.dumps(value)
            self.model = model
            self.duration_ms = 3

    monkeypatch.setattr(agent, "configured_models", lambda: {
        "primary": "primary-model", "validator": "validator-model", "fallback": "fallback-model",
    })
    monkeypatch.setattr(
        agent, "chat_structured",
        lambda **kwargs: Response(next(outputs), kwargs["model"]),
    )

    result, _, _ = agent.analyze(message, [])

    assert result["primary_status"] == "IGNORED_NOT_OFFER_RELATED"
    assert result["lifecycle_event"] == "NONE"
    assert result["backend_transition_validated"] is False
    assert result["backend_validation_reason"] == "PROPOSED_EVENT_NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT"
    assert result["should_create_review_record"] is False


@pytest.mark.parametrize(
    ("status", "subject", "body", "evidence"),
    [
        (
            "INTERVIEW_CONFIRMED", "Your Accenture interview is scheduled",
            "Dear Candidate, Your technical interview has been scheduled for 31 August 2026 at 03:00 PM IST.",
            "Your technical interview has been scheduled for 31 August 2026 at 03:00 PM IST.",
        ),
        (
            "APPOINTMENT_LETTER_RECEIVED", "Your appointment letter",
            "Dear Candidate, Your appointment letter is attached.",
            "Your appointment letter is attached.",
        ),
        (
            "OFFER_LETTER_RECEIVED", "Your offer has been released",
            "Dear Candidate, Your official offer letter has been released.",
            "Your official offer letter has been released.",
        ),
        (
            "BACKGROUND_VERIFICATION", "Background verification invitation",
            "You have been invited to complete your digital employment background verification process.",
            "You have been invited to complete your digital employment background verification process.",
        ),
        (
            "POST_SELECTION_ONBOARDING", "Welcome - pre-onboarding",
            "Please complete the pre-onboarding formalities before your joining date.",
            "Please complete the pre-onboarding formalities before your joining date.",
        ),
        (
            "DOCUMENT_VERIFICATION", "Documents required for verification",
            "Please upload the documents for verification to continue your hiring process.",
            "Please upload the documents for verification to continue your hiring process.",
        ),
    ],
)
def test_genuine_lifecycle_transitions_survive_backend_entailment(status, subject, body, evidence):
    value = _model_result(status, evidence)
    agent.validate_result(value, _message(subject, body), [])

    assert value["status"] == status
    assert value["backend_transition_validated"] is True
    assert value["validation_status"] == "AUTO_VALIDATED"


def test_ollama_alert_requires_relevance_not_backend_validation():
    structured = _model_result("SELECTED", "You have been selected for the role.")
    structured.update(
        classification_source="OLLAMA", primary_status="SELECTED",
        recruitment_relevance_result={"decision": "ESTABLISHED"},
        backend_transition_validated=False,
    )
    event = {
        "primary_status": "SELECTED", "classification": "job_selection_confirmed",
        "validation_status": "AUTO_VALIDATED", "requires_manual_review": False,
        "structured_result": structured,
    }
    # 6 Nov 2026: the quoted-sentence proof no longer decides the alert.
    assert store.should_route_to_mail_alert(event, {"classification": "job_selection_confirmed"})
    structured["recruitment_relevance_result"] = {"decision": "NOT_ESTABLISHED"}
    assert not store.should_route_to_mail_alert(event, {"classification": "job_selection_confirmed"})


def test_decision_trace_migration_is_additive_and_complete():
    sql = Path("core/migrations/027_recruitment_mail_decision_trace.sql").read_text(encoding="utf-8").lower()
    for column in (
        "deterministic_context", "recruitment_relevance_result", "primary_model_result",
        "validator_model_result", "reconciled_result", "backend_validated_final_result",
    ):
        assert f"{column} jsonb" in sql
    assert "drop " not in sql and "delete " not in sql


def test_every_tracked_transition_has_an_explicit_closed_world_validator():
    review_only = {
        "INTERVIEW_PROPOSED", "OFFER_NEEDS_REVIEW", "JOINING_NEEDS_REVIEW",
        "SELECTION_NEEDS_REVIEW", "MANUAL_REVIEW_REQUIRED",
    }
    interview_mutations = semantics.INTERVIEW_EVENTS - {"NONE"}
    expected = agent.TRACKED_STATUSES - review_only
    registered = set(semantics._LIFECYCLE_VALIDATION_REQUIREMENTS) | interview_mutations
    assert expected == registered

    neutral, reason = semantics.validate_lifecycle_event(
        "FUTURE_UNHANDLED_STATUS", {"asserted_transitions": ["FUTURE_UNHANDLED_STATUS"]},
    )
    assert neutral == "NONE"
    assert reason == "UNHANDLED_TRACKED_STATUS"


def test_validator_uses_primary_stage_contract_without_primary_anchoring():
    assert agent.VALIDATOR_PROMPT.startswith(agent.CLASSIFIER_PROMPT)
    prompt = " ".join(agent.VALIDATOR_PROMPT.split())
    assert "not given the primary model's conclusion" in prompt
    assert "return INTERVIEW_CONFIRMED, not INTERVIEW_SHORTLISTED" in agent.VALIDATOR_PROMPT


@pytest.mark.parametrize(
    ("status", "distinction"),
    [
        ("APPOINTMENT_LETTER_RECEIVED", "It is not JOINING_CONFIRMED"),
        ("OFFER_LETTER_RECEIVED", "Released is not approved"),
        ("POST_SELECTION_ONBOARDING", "does not make it JOINING_DATE_UPDATED"),
        ("JOINING_DATE_UPDATED", "requires an explicit change"),
        ("BACKGROUND_VERIFICATION", "employment background-check or BGV process"),
        ("DOCUMENT_VERIFICATION", "does not mean SELECTED or JOINING_CONFIRMED"),
    ],
)
def test_both_model_passes_share_literal_lifecycle_stage_distinctions(status, distinction):
    classifier = " ".join(agent.CLASSIFIER_PROMPT.split())
    validator = " ".join(agent.VALIDATOR_PROMPT.split())
    assert status in classifier
    assert distinction in classifier
    assert distinction in validator


def test_backend_forces_classification_to_match_validated_status():
    value = _model_result(
        "INTERVIEW_SHORTLISTED",
        "You have been shortlisted for the technical interview.",
    )
    value.update(
        classification="candidate_rejected",
        candidate_status=store._CLASSIFICATION_STATUS["candidate_rejected"],
    )

    agent.validate_result(
        value,
        _message(
            "Interview shortlist",
            "You have been shortlisted for the technical interview.",
        ),
        [],
    )

    assert value["status"] == "INTERVIEW_SHORTLISTED"
    assert value["classification"] == "interview_shortlisted"
    assert value["candidate_status"] == "Interview Shortlisted"
    assert value["backend_transition_validated"] is True


@pytest.mark.parametrize(
    ("model_status", "source_status", "subject", "body"),
    [
        (
            "OFFER_LETTER_RECEIVED", "APPOINTMENT_LETTER_RECEIVED",
            "Your appointment letter", "Dear Candidate, Your appointment letter is attached.",
        ),
        (
            "OFFER_APPROVED", "OFFER_LETTER_RECEIVED",
            "Your offer has been released", "Dear Candidate, Your official offer letter has been released.",
        ),
        (
            "JOINING_DATE_UPDATED", "POST_SELECTION_ONBOARDING",
            "Welcome - pre-onboarding", "Please complete the pre-onboarding formalities before your joining date.",
        ),
        (
            "SELECTED", "DOCUMENT_VERIFICATION",
            "Documents required for verification", "Please upload the documents for verification to continue your hiring process.",
        ),
    ],
)
def test_backend_final_corrects_only_to_an_explicit_validated_source_stage(
    model_status, source_status, subject, body,
):
    value = _model_result(model_status, body)
    if source_status == "DOCUMENT_VERIFICATION":
        # The deployed qwen model returned exactly this contradiction: a
        # tracked status plus false lifecycle booleans. Those booleans must not
        # bypass independent/backend validation of the status assertion.
        value.update(
            is_selection_or_offer_related=False,
            should_create_review_record=False,
            is_job_outcome=False,
        )
        value["evidence"][0]["source"] = "EMAIL_SUBJECT"

    agent.validate_result(value, _message(subject, body), [])

    classification = store._STATUS_CLASSIFICATION[source_status]
    assert value["status"] == source_status
    assert value["classification"] == classification
    assert value["candidate_status"] == store._CLASSIFICATION_STATUS[classification]
    assert value["backend_transition_validated"] is True
    assert value["backend_stage_corrected_from"] == model_status
    assert value["backend_validation_reason"] == "EXPLICIT_TRANSITION_ENTAILED_AFTER_STAGE_CORRECTION"
    if source_status == "DOCUMENT_VERIFICATION":
        assert value["evidence"][0]["source"] == "EMAIL_BODY"
        assert value["evidence"][0]["evidence_source_corrected_from"] == "EMAIL_SUBJECT"


def test_tracked_status_requires_independent_validation_even_when_model_booleans_disagree():
    assert agent._requires_independent_validation(
        {
            "status": "SELECTED", "confidence": 99,
            "is_selection_or_offer_related": False,
            "should_create_review_record": False,
            "risk_flags": [], "requires_manual_review": False,
        },
        {},
    )


def test_same_tracked_status_is_model_agreement_despite_duplicate_boolean_difference():
    primary = _model_result("SELECTED", "You have been selected for the role.")
    validator = _model_result("SELECTED", "You have been selected for the role.")
    primary["is_selection_or_offer_related"] = False

    reconciled = agent._reconcile_model_results(primary, validator)

    assert reconciled["status"] == "SELECTED"
    assert reconciled["model_validation"]["agreed"] is True
    assert "MODEL_DISAGREEMENT" not in reconciled["risk_flags"]


def test_record_analysis_writes_each_decision_layer_to_its_own_column(monkeypatch):
    calls = []

    class Cursor:
        description = [SimpleNamespace(name="id")]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params):
            assert query.count("%s") == len(params)
            calls.append((query, params))

        def fetchall(self):
            return [("analysis-1",)]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    trace = {
        "deterministic_context": {"lifecycle_event": "NONE"},
        "recruitment_relevance_result": {"decision": "NOT_ESTABLISHED"},
        "primary_model_result": {"status": "INTERVIEW_UPDATE"},
        "validator_model_result": {"status": "INTERVIEW_SHORTLISTED"},
        "reconciled_result": {"status": "MANUAL_REVIEW_REQUIRED"},
        "backend_validated_final_result": {"status": "IGNORED_NOT_OFFER_RELATED"},
    }
    result = {
        "schema_version": "selection_offer_event_v1", "classification": "not_relevant",
        "candidate_status": "Profile Active", "confidence": 0,
        "_decision_trace": trace,
    }
    monkeypatch.setattr(store, "get_connection", lambda: Connection())

    store.record_analysis(
        "message-1", "candidate-1", result,
        model="test-model", processing_status="COMPLETED",
    )

    insert_sql, params = calls[0]
    assert "deterministic_context,recruitment_relevance_result,primary_model_result" in insert_sql
    persisted = [json.loads(value) for value in params[13:19]]
    assert persisted == [
        trace["deterministic_context"], trace["recruitment_relevance_result"],
        trace["primary_model_result"], trace["validator_model_result"],
        trace["reconciled_result"], trace["backend_validated_final_result"],
    ]
