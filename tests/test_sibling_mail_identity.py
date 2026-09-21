from datetime import datetime, timezone

import pytest

from services import interview_auto_booking as booking
from tests.test_interview_auto_booking import install_store_fakes, result, slot_writer


LINK = 'https://teams.microsoft.com/l/meetup-join/19%3ameeting_source%40thread.v2/0?context=original'
SOURCE = {'subject': 'HCLTech | Interview Schedule - TP1', 'html_body_text': f'<a href="{LINK}">Join</a>',
          'sent_at': '2026-09-08T08:03:03Z'}
SLOT = {'id': 'slot-1', 'name': 'Rahul', 'slot_confirmed': True, 'date': '2099-09-11', 'time': '12:00', 'time_end': '12:45',
        'interview_calendar_uid': 'original-uid', 'interview_source_message_id': 'original-message',
        'interview_source_thread_id': 'original-thread'}
MESSAGE = {'provider_message_id': 'sibling-message', 'provider_thread_id': 'sibling-thread',
           'subject': SOURCE['subject'], 'html_body': SOURCE['html_body_text'], 'sent_at': '2026-09-08T08:03:20Z'}
SCHEDULE = {'date': SLOT['date'], 'time': SLOT['time'], 'time_end': SLOT['time_end']}


def test_hcl_sibling_reaches_exact_duplicate_guard_in_real_executor(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_AUTO_BOOKING_ENABLED', 'true')
    _, audits = install_store_fakes(monkeypatch, rows=[SLOT])
    monkeypatch.setattr(booking.mail_store, 'booking_source_message', lambda source_id: SOURCE)
    monkeypatch.setattr(booking.candidate_store, 'assign_interview_slot', lambda **kw: pytest.fail('Duplicate slot created'))
    outcome = booking.execute_auto_booking(
        mailbox={'id': 'mb1', 'candidate_id': 'c1'}, message=MESSAGE,
        event={'mailbox_message_id': 'mail-row', 'notification': {'id': 'n1'}},
        result=result(date='2099-09-11', time='12:00 PM', end_time='12:45 PM'))
    assert outcome['failure_code'] == 'DUPLICATE_BOOKING'
    assert audits[-1]['auto_booked'] is False


# A different subject alone no longer makes a second interview: see
# test_reminder_same_meeting_is_one_interview.py.
@pytest.mark.parametrize('change', ['different_uid', 'different_time', 'different_date', 'different_meeting', 'no_meeting'])
def test_different_interviews_are_still_allowed(monkeypatch, change):
    monkeypatch.setattr(booking.mail_store, 'booking_source_message', lambda source_id: SOURCE)
    value, message, schedule = {}, dict(MESSAGE), dict(SCHEDULE)
    if change == 'different_uid': value['calendar'] = {'uid': 'other-uid'}
    if change == 'different_time': schedule['time'] = '12:15'
    if change == 'different_date': schedule['date'] = '2099-09-12'
    if change == 'different_meeting': message['html_body'] = SOURCE['html_body_text'].replace('meeting_source', 'meeting_other')
    if change == 'no_meeting': message['html_body'] = 'https://teams.microsoft.com/help'
    assert not booking._same_lifecycle_slot(SLOT, result=value, message=message, schedule=schedule)


def test_proof_store_outage_cannot_permit_duplicate_booking(monkeypatch):
    def unavailable(*args): raise ConnectionError('proof unavailable')
    monkeypatch.setattr(booking.mail_store, 'booking_source_message', unavailable)
    with pytest.raises(ConnectionError):
        booking._same_lifecycle_slot(SLOT, result={}, message=MESSAGE, schedule=SCHEDULE)


def test_plain_mail_first_calendar_sibling_second_is_also_duplicate(monkeypatch):
    monkeypatch.setattr(booking.mail_store, 'booking_source_message', lambda source_id: SOURCE)
    plain_slot = {**SLOT, 'interview_calendar_uid': ''}
    assert booking._same_lifecycle_slot(plain_slot,
        result={'calendar': {'uid': 'later-ics-uid', 'sequence': 0}},
        message=MESSAGE, schedule=SCHEDULE)


def test_same_name_is_not_canonical_candidate_identity(monkeypatch):
    from core.db import connection
    first = {**SLOT, 'phone': '9000000001', 'service_type': 'profile_service'}
    second = {**SLOT, 'id': 'other-canonical', 'phone': '9000000002', 'service_type': 'profile_service'}
    alias = {**second, 'id': 'same-person-alias'}
    monkeypatch.setattr(connection, 'use_postgres', lambda: False)
    monkeypatch.setattr(booking.candidate_store, '_load', lambda **kwargs: {'candidates': [first, second, alias]})
    monkeypatch.setattr(booking.candidate_store, '_with_computed', lambda row: dict(row))
    # Exercise the real identity resolver, not a fake that conceals its legacy
    # name-only fallback. Phone-linked historical bookings remain visible.
    assert 'slot-1' in booking.candidate_store.candidate_identity_ids('other-canonical')
    assert {r['id'] for r in booking._candidate_slots(second)} == {'other-canonical', 'same-person-alias'}


@pytest.mark.parametrize('calendars', [False, True])
@pytest.mark.parametrize('same_company_and_role', [False, True])
def test_real_executor_persists_distinct_exact_time_interviews(monkeypatch, calendars, same_company_and_role):
    monkeypatch.setenv('AI_INTERVIEW_AUTO_BOOKING_ENABLED', 'true')
    old_slot = {**SLOT, 'interview_calendar_uid': 'old-uid' if calendars else '',
                'interview_company': 'Example', 'interview_role': 'Engineer'}
    _, audits = install_store_fakes(monkeypatch, rows=[old_slot])
    monkeypatch.setattr(booking.mail_store, 'booking_source_message', lambda source_id: SOURCE)
    writes = []
    monkeypatch.setattr(booking.candidate_store, 'assign_interview_slot', slot_writer('new-slot', capture=writes))
    value = result(date=SCHEDULE['date'], time='12:00 PM', end_time='12:45 PM')
    if calendars:
        value['calendar'] = {'uid': 'independent-event', 'sequence': 0}
    if not same_company_and_role:
        value.update(company={'name': 'Different company'}, job={'title': 'Different role'})
    message = {**MESSAGE, 'subject': 'Independent interview',
               'html_body': MESSAGE['html_body'].replace('meeting_source', 'meeting_independent')}
    outcome = booking.execute_auto_booking(mailbox={'id': 'mb1', 'candidate_id': 'c1'},
        message=message, event={'mailbox_message_id': 'new-mail', 'notification': {'id': 'n1'}}, result=value)
    assert outcome['automation_state'] == 'AUTO_BOOKED'
    assert outcome['booking']['id'] == 'new-slot'
    assert len(writes) == 1
    assert audits[-1]['auto_booked'] is True
    assert old_slot == {**SLOT, 'interview_calendar_uid': 'old-uid' if calendars else '',
                       'interview_company': 'Example', 'interview_role': 'Engineer'}


@pytest.mark.parametrize('classification', ['interview_cancelled', 'interview_rescheduled'])
def test_old_no_uid_transition_cannot_modify_newer_booked_source(monkeypatch, classification):
    monkeypatch.setattr(booking.mail_store, 'booking_source_message', lambda source_id: SOURCE)
    with pytest.raises(booking.BookingValidationError) as error:
        booking._resolve_existing_slot([SLOT], result={},
            message={'sent_at': '2026-09-01T06:53:39Z'}, classification=classification)
    assert error.value.code == 'STALE_INTERVIEW_EVENT'


def test_same_uid_sequence_precedence_is_not_replaced_by_send_time(monkeypatch):
    monkeypatch.setattr(booking.mail_store, 'booking_source_message', lambda *a: pytest.fail('UID precedence bypassed'))
    assert booking._resolve_existing_slot([SLOT], result={'calendar': {'uid': 'original-uid'}},
        message={'sent_at': '2026-09-01T06:53:39Z'}, classification='interview_cancelled') == SLOT
