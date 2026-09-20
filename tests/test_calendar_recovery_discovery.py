import pytest

from core import recruitment_mail_store as store

from datetime import datetime, timezone
from services.calendar_recovery_discovery import DISCOVERY_LABELS as LABELS, classify_records

# One event, spelled out once: the tests differ only in method, uid and day.
ICS = chr(10).join([
    'BEGIN:VCALENDAR', 'METHOD:{method}', 'BEGIN:VEVENT', 'UID:{uid}', 'SEQUENCE:0',
    'DTSTART:{date}T090000Z', 'DTEND:{date}T100000Z', 'END:VEVENT', 'END:VCALENDAR',
])


def classify(*, slots=(), audits=(), extra=(), date='20260911', method='REQUEST', **fields):
    row = dict(mailbox_message_id='m', mailbox_id='box', canonical_candidate_id='alias', **fields)
    cal = dict(row, extracted_text=f'BEGIN:VCALENDAR\nMETHOD:{method}\nBEGIN:VEVENT\nUID:uid\nSEQUENCE:0\nDTSTART:{date}T090000Z\nDTEND:{date}T100000Z\nEND:VEVENT\nEND:VCALENDAR')
    return classify_records([row], calendars=[cal, *extra], slots=slots, audits=audits,
                            links={'alias': 'person'}, now=datetime(2026, 9, 10, 12, tzinfo=timezone.utc))['records'][0]


def test_audit_alone_does_not_prove_a_persisted_booking():
    row = classify(audits=[dict(booking_id='missing', candidate_id='person', auto_booked=True)])
    assert row['discovery_state'] == 'RECOVERY_CANDIDATE'
    assert row['canonical_candidate_id'] == 'person'
    assert row['start_ist'] == '2026-09-11T14:30:00+05:30'


def test_persisted_slot_must_match_uid_and_person():
    slots = [dict(id='s', slot_confirmed=True, interview_calendar_uid='uid', date='2026-09-11', time='14:30', time_end='15:30')]
    assert classify(slots=slots, audits=[dict(booking_id='s', candidate_id='alias')])['discovery_state'] == 'ALREADY_REPRESENTED'
    assert classify(slots=slots, audits=[dict(booking_id='s', candidate_id='other')])['discovery_state'] == 'RECOVERY_CANDIDATE'


def test_matching_uid_but_wrong_persisted_schedule_is_not_already_represented():
    slots = [dict(id='s', slot_confirmed=True, interview_calendar_uid='uid', date='2026-09-11', time='12:00', time_end='13:00')]
    assert classify(slots=slots, audits=[dict(booking_id='s', candidate_id='alias')])['discovery_state'] == 'RECOVERY_CANDIDATE'


def test_past_source_calendar_is_not_a_recovery_candidate():
    assert classify(date='20260909')['discovery_state'] == 'PAST_NEVER_BOOKED'


def test_unprocessed_source_cancellation_supersedes_ignored_confirmation():
    cancellation = dict(mailbox_message_id='cancel', mailbox_id='box', extracted_text='BEGIN:VCALENDAR\nMETHOD:CANCEL\nBEGIN:VEVENT\nUID:uid\nSEQUENCE:1\nEND:VEVENT\nEND:VCALENDAR')
    assert classify(extra=[cancellation])['discovery_state'] == 'CANCELLED_OR_SUPERSEDED'
    assert classify(method='CANCEL')['discovery_state'] == 'CANCELLED_OR_SUPERSEDED'


def test_other_mailbox_cancellation_does_not_supersede_persons_invite():
    cancellation = dict(mailbox_message_id='cancel', mailbox_id='other-box', extracted_text='BEGIN:VCALENDAR\nMETHOD:CANCEL\nBEGIN:VEVENT\nUID:uid\nSEQUENCE:1\nEND:VEVENT\nEND:VCALENDAR')
    assert classify(extra=[cancellation])['discovery_state'] == 'RECOVERY_CANDIDATE'


def test_calendar_recovery_discovery_is_read_only_without_postgres(monkeypatch):
    monkeypatch.setattr(store, "use_postgres", lambda: False)

    report = store.calendar_invite_recovery_discovery()

    assert report == {
        "summary": {
            "total": 0,
            "recovery_candidates": 0,
            "already_represented": 0,
            "cancelled_or_superseded": 0,
            "past_never_booked": 0,
            "already_assessed": 0,
        },
        "records": [],
    }
def test_a_past_invite_with_no_booking_is_its_own_state():
    """Filed with the cancellations, it read as history working correctly. It
    may be an interview that happened and was never recorded."""
    row = classify(date='20260909', ignore_reason='IGNORED_LOW_CONFIDENCE',
                   sent_at='2026-09-08T10:00:00+00:00', candidate_name='Synthetic',
                   company_name='Example')

    assert row['discovery_state'] == 'PAST_NEVER_BOOKED'
    assert row['label'] == LABELS['PAST_NEVER_BOOKED']
    # Who, where, when it was due, when the mail came, and why nothing holds it.
    assert (row['candidate_name'], row['company']) == ('Synthetic', 'Example')
    assert row['start_ist'].startswith('2026-09-09T14:30')
    assert row['sent_at'] == '2026-09-08T10:00:00+00:00'
    assert row['reason'] == ('Interview start is in the past and no confirmed slot holds it; '
                             'the mail was set aside as IGNORED_LOW_CONFIDENCE.')


def test_a_past_invite_says_what_it_can_when_the_mail_gave_no_reason():
    assert classify(date='20260909')['reason'] == (
        'Interview start is in the past and no confirmed slot holds it.'
    )


def test_a_candidate_with_no_alert_is_named_from_the_roster():
    row = classify(date='20260909', slots=[dict(id='person', name='Roster Only')])
    assert row['candidate_name'] == 'Roster Only'


def test_a_cancelled_invite_stays_separate_even_when_its_time_has_passed():
    """The two buckets must not collapse back into one: a cancellation that is
    also old is still a cancellation."""
    row = classify(date='20260909', method='CANCEL')
    assert row['discovery_state'] == 'CANCELLED_OR_SUPERSEDED'
    assert row['label'] == LABELS['CANCELLED_OR_SUPERSEDED']


def test_the_summary_counts_the_two_kinds_apart():
    def record(name, date, method='REQUEST'):
        row = dict(mailbox_message_id=name, mailbox_id='box', canonical_candidate_id='alias')
        ics = ICS.format(method=method, uid=name, date=date)
        return row, dict(row, extracted_text=ics)

    rows, cals = zip(record('past', '20260909'), record('gone', '20260909', 'CANCEL'),
                     record('ahead', '20260911'))
    report = classify_records(list(rows), calendars=list(cals), slots=[], audits=[],
                              links={'alias': 'person'},
                              now=datetime(2026, 9, 10, 12, tzinfo=timezone.utc))

    assert report['summary'] == {
        'total': 3, 'recovery_candidates': 1, 'already_represented': 0,
        'cancelled_or_superseded': 1, 'past_never_booked': 1,
        'already_assessed': 0, 'unresolved': 0,
    }


# ── A booking made by hand still represents the invite ──────────────────────

def hand_booked(**overrides):
    """A slot as Daily Ops writes it: no calendar id of its own."""
    return dict({"id": "person", "slot_confirmed": True, "date": "2026-09-11",
                 "time": "14:30", "time_end": "15:30"}, **overrides)


def test_a_hand_booked_slot_represents_the_invite_it_matches_exactly():
    """A production interview was reported as having no booking the day before
    it, while the booking sat in Daily Ops: it had been typed in, so it carries
    no calendar id and the UID could never match."""
    row = classify(slots=[hand_booked()])

    assert row["discovery_state"] == "ALREADY_REPRESENTED"
    assert row["booking_ids"] == ["person"]
    assert row["matched_by"] == "exact schedule"
    assert "no calendar id of its own" in row["reason"]


def test_a_slot_claiming_another_calendar_event_never_matches_on_the_hour():
    """Two meetings can share an hour. A row that names a different event is
    not this invite, however well the clock lines up."""
    assert classify(slots=[hand_booked(interview_calendar_uid="other-uid")],
                    audits=[dict(booking_id="person", candidate_id="alias")]
                    )["discovery_state"] == "RECOVERY_CANDIDATE"


@pytest.mark.parametrize("difference", [
    dict(time="14:00"), dict(time_end="15:00"), dict(date="2026-09-12"),
    dict(slot_confirmed=False),
])
def test_anything_less_than_the_exact_schedule_is_not_a_match(difference):
    assert classify(slots=[hand_booked(**difference)])["discovery_state"] == "RECOVERY_CANDIDATE"


def test_another_persons_slot_at_the_same_time_is_not_a_match():
    assert classify(slots=[hand_booked(id="someone-else")])["discovery_state"] == "RECOVERY_CANDIDATE"


def test_the_calendar_event_is_still_the_first_way_to_match():
    row = classify(slots=[hand_booked(id="s", interview_calendar_uid="uid")],
                   audits=[dict(booking_id="s", candidate_id="alias")])

    assert row["discovery_state"] == "ALREADY_REPRESENTED"
    assert row["matched_by"] == "calendar event"
