"""Pure mode owns intent end-to-end; only proven source evidence authorizes it."""
from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from services import recruitment_mail_agent as agent, interview_auto_booking as booking
from tests.test_jd_invite_evidence import ASSERTION, BODY, SUBJECT, payload
from tests.test_interview_auto_booking import install_store_fakes, slot_writer


def message(body=BODY, sender='recruiter@example.com'):
    return dict(provider_message_id='pure-invite', provider_thread_id='pure-thread',
                subject=SUBJECT, body=body, sender_email=sender,
                recipient_email='candidate@test.invalid', message_direction='INBOUND',
                sent_at='2026-09-11T06:38:07Z')


@pytest.mark.parametrize('pure', [False, True])
@pytest.mark.parametrize('incidental', [
    'We are hiring. Job description attached.',
    'https://www.linkedin.com/jobs/view/11111111 https://www.linkedin.com/jobs/view/22222222',
])
def test_keywords_cannot_override_a_proven_model_interview_in_pure_mode(monkeypatch, pure, incidental):
    monkeypatch.setattr(agent, 'pure_ollama_enabled', lambda: pure)
    value = payload()
    value['email_intent'] = 'INTERVIEW_CONFIRMATION'
    agent.validate_result(value, message(BODY + ' ' + incidental), [])
    if pure:
        assert value['automation_decision'] == 'AUTO_BOOK'
        assert value['classification'] == 'interview_confirmed'
        assert value['email_intent'] == 'INTERVIEW_CONFIRMATION'
        assert value['backend_transition_validated'] is True
    else:
        if 'linkedin.com' in incidental:
            # Legacy digest rejection is itself rescued by its later keyword
            # interview promotion. Preserve that OFF-mode behavior too.
            assert value['downgrade_reason'] == 'JOB_ADVERTISEMENT_DIGEST'
            assert value['automation_decision'] == 'AUTO_BOOK'
        else:
            assert value['automation_decision'] == 'AUTO_IGNORE'
    if pure:
        assert 'downgrade_reason' not in value


@pytest.mark.parametrize('pure', [False, True])
def test_model_ignore_is_not_upgraded_by_interview_keywords(monkeypatch, pure):
    monkeypatch.setattr(agent, 'pure_ollama_enabled', lambda: pure)
    value = payload()
    value.update(status='IGNORED_NOT_OFFER_RELATED', classification='not_relevant',
                 is_selection_or_offer_related=False, should_create_review_record=False)
    agent.validate_result(value, message(), [])
    assert value['automation_decision'] == ('AUTO_IGNORE' if pure else 'AUTO_BOOK')


@pytest.mark.parametrize('problem', ['fabricated', 'negated', 'no_assertion', 'uncertain', 'disagreement'])
def test_pure_mode_keeps_unproven_or_uncertain_decisions_pending(monkeypatch, problem):
    monkeypatch.setattr(agent, 'pure_ollama_enabled', lambda: True)
    value, mail = payload(), message()
    if problem == 'fabricated':
        value['evidence'][0]['text'] = 'Your interview has been confirmed with Invented Company.'
    elif problem in {'negated', 'no_assertion'}:
        quote = ('Your Client interview is not scheduled on 16th Sep at 3:30pm.'
                 if problem == 'negated' else 'Job Description: ServiceNow Developer. Apply now.')
        mail['body'] = quote
        value['evidence'][0]['text'] = quote
    elif problem == 'uncertain':
        value['status'] = 'SELECTION_NEEDS_REVIEW'
    else:
        value['risk_flags'] = ['MODEL_DISAGREEMENT']
    agent.validate_result(value, mail, [])
    assert value['automation_decision'] == 'AI_RETRY_PENDING'
    assert value['backend_transition_validated'] is False


@pytest.mark.parametrize('status,quote', [
    ('INTERVIEW_CANCELLED', 'Your client interview has been cancelled.'),
    ('INTERVIEW_RESCHEDULED', 'Your client interview has been rescheduled to 16th Sep at 3:30pm.'),
])
def test_pure_mode_preserves_proven_cancellation_and_reschedule(monkeypatch, status, quote):
    monkeypatch.setattr(agent, 'pure_ollama_enabled', lambda: True)
    value = payload()
    value['status'] = status
    value['evidence'] = [dict(source='EMAIL_BODY', meaning=status, text=quote)]
    agent.validate_result(value, message(quote + ' We are hiring. Job description attached.'), [])
    assert value['status'] == status
    assert value['backend_transition_validated'] is True


@pytest.mark.parametrize('pure', [False, True])
@pytest.mark.parametrize('kind', ['interview', 'marketing', 'outage'])
def test_real_pipeline_respects_mode_and_persists_only_proven_interview(monkeypatch, pure, kind):
    monkeypatch.setattr(agent, 'pure_ollama_enabled', lambda: pure)
    monkeypatch.setenv('AI_INTERVIEW_AUTO_BOOKING_ENABLED', 'true')
    candidate, audits = install_store_fakes(monkeypatch)
    writes, states = [], []
    monkeypatch.setattr(booking.candidate_store, 'assign_interview_slot', slot_writer('pure-slot', capture=writes))
    original = booking.normalized_schedule
    monkeypatch.setattr(booking, 'normalized_schedule', lambda v, **kwargs: original(
        v, **{**kwargs, 'now': datetime(2026, 9, 11, tzinfo=ZoneInfo('Asia/Kolkata'))}))
    mail = message(sender='recruiter@' + agent._JOB_BOARD_DOMAINS[0])
    relevance = dict(decision='ESTABLISHED', message_kind='RECIPIENT_HIRING_PROCESS', confidence=100,
                     evidence=[dict(source='EMAIL_BODY', text=ASSERTION)], reason='Candidate interview.')
    if kind == 'marketing':
        mail['body'] = 'New vacancies available. Apply now for ServiceNow developer roles.'
        relevance.update(decision='NOT_ESTABLISHED', message_kind='JOB_ADVERTISEMENT',
                         evidence=[dict(source='EMAIL_BODY', text=mail['body'])])
    outputs = iter([relevance, payload(), payload()])
    def respond(**kwargs):
        if kind == 'outage':
            raise agent.AIGatewayError('offline', code='OLLAMA_CONNECTION_FAILED')
        return SimpleNamespace(content=json.dumps(deepcopy(next(outputs))), model='test', duration_ms=1)
    monkeypatch.setattr(agent, 'chat_structured', respond)
    monkeypatch.setattr(agent, '_publish', lambda *a, **kw: None)
    monkeypatch.setattr(agent.store, 'insert_message', lambda *a: ({'id': 'mail-row'}, True))
    for name in ('is_duplicate_content', 'is_duplicate_offer_attachment', 'is_duplicate_thread_status'):
        monkeypatch.setattr(agent.store, name, lambda *a: False)
    monkeypatch.setattr(agent.store, 'mark_message_status', lambda mid, status, **kw: states.append(status))
    monkeypatch.setattr(agent.store, 'record_analysis', lambda *a, **kw: {})
    monkeypatch.setattr(agent.store, 'create_event', lambda cid, mid, value, **kw: dict(
        id='event', mailbox_message_id=mid, candidate_id=cid, classification=value['classification'], notification={'id':'n'}))
    monkeypatch.setattr('services.recruitment_notifications.notify_detection', lambda *a: None)
    event = agent.process_message(dict(id='mb1', candidate_id='c1', email_address='candidate@test.invalid'), mail, [])
    if pure and kind == 'interview':
        assert event['auto_booking']['automation_state'] == 'AUTO_BOOKED'
        assert len(writes) == 1 and audits[-1]['booking_id'] == 'pure-slot'
    else:
        assert not writes
    if pure and kind == 'outage':
        assert 'AI_RETRY_PENDING' in states
