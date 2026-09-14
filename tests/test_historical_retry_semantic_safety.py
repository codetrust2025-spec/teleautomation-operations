"""Sanitized historical failure shapes, exercised through existing AI gates.

Model responses are replayed locally, not regenerated. These tests prove safe
handling of bad/uncertain decisions, not corrected model accuracy. No real mail
is reprocessed and no classifier, prompt, or inference routing is changed.
"""
import json
from types import SimpleNamespace

import pytest

from services import recruitment_mail_agent as agent
from tests.test_recruitment_relevance_architecture import _message, _model_result


@pytest.mark.parametrize('subject,body,kind', [
    ('Application received', 'We have received your application. We will review it and contact you later.', 'RECIPIENT_HIRING_PROCESS'),
    ('Application update', 'You were not selected for the Software Engineer role.', 'RECIPIENT_HIRING_PROCESS'),
    ('Conflict of interest declaration', 'Please complete the conflict of interest declaration for your project profile.', 'UNKNOWN'),
    ('Hiring update', 'We are unable to proceed with the next steps for now.', 'UNKNOWN'),
])
def test_uncertain_relevance_preserves_nonbooking_mail_for_retry(monkeypatch, subject, body, kind):
    monkeypatch.setattr(agent, 'pure_ollama_enabled', lambda: True)
    monkeypatch.setattr(agent, 'configured_models', lambda: dict(primary='test-primary', validator='test-validator', fallback='test-fallback'))
    calls = []

    def respond(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(model=kwargs['model'], duration_ms=1, content=json.dumps(dict(
            decision='NOT_ESTABLISHED', message_kind=kind, confidence=95,
            evidence=[dict(source='EMAIL_BODY', text=body)], reason='Stored unresolved relevance shape.')))

    monkeypatch.setattr(agent, 'chat_structured', respond)
    result, _, _ = agent.analyze(_message(subject, body), [])
    assert len(calls) == 1
    assert calls[0]['schema'] is agent.RELEVANCE_SCHEMA
    assert result['automation_decision'] == 'AI_RETRY_PENDING'
    assert result['backend_transition_validated'] is False
    assert result['requires_manual_review'] is False
    assert result['_decision_trace']['primary_model_result'] is None


@pytest.mark.parametrize('subject,body,status', [
    ('API testing opportunity', 'You have been chosen from a large pool of candidates to apply for this job.', 'SELECTED'),
    ('AI assessment', 'Join the AI-led assessment whenever you are ready.', 'INTERVIEW_CONFIRMED'),
    ('Missed interview', 'You missed your interview. Use the link to update your slot.', 'INTERVIEW_RESCHEDULED'),
    ('Interview preparation', 'Install VS Code, Node and Git before your interview.', 'INTERVIEW_CONFIRMED'),
    ('Assessment update', 'We will share the AI test link shortly.', 'INTERVIEW_CONFIRMED'),
])
def test_unproven_model_transition_cannot_authorize_booking(monkeypatch, subject, body, status):
    monkeypatch.setattr(agent, 'pure_ollama_enabled', lambda: True)
    value = _model_result(status, body)
    agent.validate_result(value, _message(subject, body), [], relevance=dict(
        decision='ESTABLISHED', message_kind='RECIPIENT_HIRING_PROCESS'))
    assert value['automation_decision'] == 'AI_RETRY_PENDING'
    assert value['backend_transition_validated'] is False
    assert value['requires_manual_review'] is False
