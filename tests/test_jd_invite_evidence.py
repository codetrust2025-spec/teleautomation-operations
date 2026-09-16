"""Nitin's JD-and-invite failure, with private meeting credentials redacted."""
from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from services import recruitment_mail_agent as agent, recruitment_semantics as semantics
from services import interview_auto_booking as booking
from tests.test_backend_entailment_clears_stale_review import model_result
from tests.test_interview_auto_booking import install_store_fakes, slot_writer

SUBJECT = 'JD and Invite -'
ASSERTION = "Your Client interview scheduled on Wednesday,16th Sep'26 at 3:30pm."
MEETING = 'Microsoft Teams meeting Join: https://teams.microsoft.com/meet/111222333444555?p=test Meeting ID: 111 222 333 444 555'
JD = ('Job details: Payroll: Vidhai Technologies Pvt Ltd Client: NTT Data ServiceNow Developer. '
      'Experience Level: 3+ years experience in ServiceNow support & development. '
      'Onsite/Remote: Hybrid, Bangalore. Job Description: We are seeking a ServiceNow Developer '
      'with 3+ years of development experience. Roles and Responsibilities: Support, maintain, '
      'develop and customize solutions across ServiceNow ITSM modules using platform best practices.')
BODY = 'Hi Nitin, As discussed, please find the JD and interview invite details below. Kindly acknowledge receipt. ' + ASSERTION + ' ' + MEETING + ' ' + JD
HTML = '<div>Hi Nitin,</div><div><b>Your Client interview scheduled on Wednesday,16</b><sup><b>th</b></sup><b>&nbsp;Sep\'26 at 3:30pm.</b></div><div>' + MEETING + '</div><div>' + JD + '</div>'


def payload():
    value = model_result('INTERVIEW_CONFIRMED', requires_review=False, risk_flags=[])
    value.update(company={'name': 'Vidhai Technologies Pvt Ltd', 'domain': 'vidhaitech.com'},
                 job={'title': 'ServiceNow Developer', 'employment_type': 'Hybrid', 'location': 'Bangalore'},
                 candidate={'name': 'Nitin', 'email': 'candidate@test.invalid'},
                 evidence=[{'source': 'EMAIL_BODY', 'text': ASSERTION, 'meaning': 'INTERVIEW_CONFIRMED'}],
                 interview={'date': '2026-09-16', 'time': '15:30', 'timezone': 'Asia/Kolkata',
                            'mode': 'Microsoft Teams', 'round': None, 'location': None,
                            'meeting_link': 'https://teams.microsoft.com/meet/111222333444555?p=test'},
                 summary='Client interview scheduled.', reason='Explicit candidate interview schedule.')
    return value


@pytest.mark.parametrize('body', [BODY, BODY.replace('Job Description:', 'JD attached:'), BODY.replace('Job Description:', 'Job details:')])
def test_explicit_scheduled_interview_wins_over_incidental_jd(body):
    context = semantics.classify_context(SUBJECT, body, sender_email='recruiter@example.com')
    assert context['is_promotional_or_job_ad'] is False
    assert context['interview_event'] == 'INTERVIEW_CONFIRMED'


@pytest.mark.parametrize('subject,body', [
    (SUBJECT, JD),
    (SUBJECT, JD + ' ' + MEETING),
    (SUBJECT, 'Your interview is not scheduled. ' + MEETING + ' ' + JD),
    (SUBJECT, "Your Client interview is not scheduled on 16th Sep'26 at 3:30pm. " + MEETING + ' ' + JD),
    (SUBJECT, 'We may schedule your interview on 16th Sep 2026 at 3:30pm. ' + MEETING + ' ' + JD),
    ('Interview preparation webinar', BODY),
    ('Job openings', JD + ' Apply now. Interviews on 16th Sep 2026 at 3:30pm. ' + MEETING),
])
def test_standalone_ads_and_unsupported_invites_are_not_bookable(subject, body):
    context = semantics.classify_context(subject, body, sender_email='recruiter@example.com')
    assert context['is_promotional_or_job_ad'] is True
    assert context['interview_event'] != 'INTERVIEW_CONFIRMED'


@pytest.mark.parametrize('html_only', [False, True])
def test_exact_shape_reaches_real_automatic_persistence_path(monkeypatch, html_only):
    """Only network responses and storage are fake; processing/gates are real."""
    monkeypatch.setenv('AI_INTERVIEW_AUTO_BOOKING_ENABLED', 'true')
    candidate, audits = install_store_fakes(monkeypatch)
    candidate['name'] = 'Nitin'
    writes = []
    monkeypatch.setattr(booking.candidate_store, 'assign_interview_slot', slot_writer('persisted-slot', capture=writes))
    original_schedule = booking.normalized_schedule
    monkeypatch.setattr(booking, 'normalized_schedule', lambda value: original_schedule(
        value, now=datetime(2026, 9, 11, tzinfo=ZoneInfo('Asia/Kolkata'))))
    relevance = {'decision': 'ESTABLISHED', 'message_kind': 'RECIPIENT_HIRING_PROCESS', 'confidence': 100,
                 'evidence': [{'source': 'EMAIL_BODY', 'text': ASSERTION}], 'reason': 'Explicit interview.'}
    outputs = iter([relevance, payload(), payload()])
    monkeypatch.setattr(agent, 'chat_structured', lambda **kw: SimpleNamespace(
        content=json.dumps(deepcopy(next(outputs))), model='corpus-replay', duration_ms=1))
    monkeypatch.setattr(agent, '_publish', lambda *a, **kw: None)
    monkeypatch.setattr(agent.store, 'insert_message', lambda *a: ({'id': 'mail-row'}, True))
    monkeypatch.setattr(agent.store, 'is_duplicate_content', lambda *a: False)
    monkeypatch.setattr(agent.store, 'is_duplicate_offer_attachment', lambda *a: False)
    monkeypatch.setattr(agent.store, 'is_duplicate_thread_status', lambda *a: False)
    monkeypatch.setattr(agent.store, 'mark_message_status', lambda *a, **kw: None)
    monkeypatch.setattr(agent.store, 'record_analysis', lambda *a, **kw: {})
    monkeypatch.setattr(agent.store, 'create_event', lambda cid, mid, value, **kw: {
        'id': 'event', 'mailbox_message_id': mid, 'candidate_id': cid,
        'classification': value['classification'], 'notification': {'id': 'n1'}})
    monkeypatch.setattr('services.recruitment_notifications.notify_detection', lambda *a: None)
    message = {'provider_message_id': 'original-invite', 'provider_thread_id': 'invite-thread',
               'subject': SUBJECT, 'body': '' if html_only else BODY, 'html_body': HTML,
               'sender_email': 'recruiter@example.com', 'recipient_email': 'candidate@test.invalid',
               'message_direction': 'INBOUND', 'sent_at': '2026-09-11T06:38:07Z'}
    event = agent.process_message({'id': 'mb1', 'candidate_id': 'c1', 'email_address': 'candidate@test.invalid'}, message, [])
    assert event and event['auto_booking']['automation_state'] == 'AUTO_BOOKED'
    assert len(writes) == 1
    assert (writes[0]['date'], writes[0]['time'], writes[0]['time_end']) == ('2026-09-16', '15:30', '16:00')
    assert audits[-1]['auto_booked'] is True
    assert audits[-1]['booking_id'] == 'persisted-slot'
