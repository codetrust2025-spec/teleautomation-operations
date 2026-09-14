from __future__ import annotations

from services.interview_state_reconciliation import build_reconciliation_report


def report(**kwargs):
    return build_reconciliation_report(**dict(dict(candidates=[], analyses=[], audits=[], notifications=[]), **kwargs))


def test_report_finds_false_auto_booked_claim_without_mutating_inputs():
    candidates = [{"id": "slot-1", "slot_confirmed": True, "date": "2026-09-11", "time": "14:00", "time_end": "15:00"}]
    audits = [{"id": "audit-1", "candidate_id": "candidate-1", "booking_id": "missing-slot", "email_analysis_id": "analysis-1", "auto_booked": True}]
    notifications = [{"candidate_id": "candidate-1", "booking_id": "missing-slot", "booking_audit_id": "audit-1", "booking_status": "Auto Booked"}]
    analyses = [{"id": "analysis-1"}]

    report = build_reconciliation_report(candidates=candidates, audits=audits, notifications=notifications, analyses=analyses)

    assert report["mode"] == "report_only"
    assert {row["code"] for row in report["findings"]} == {
        "AUTO_BOOKED_WITHOUT_PERSISTED_SLOT", "NOTIFICATION_CLAIMS_BOOKED_WITHOUT_SLOT",
    }
    assert candidates[0]["slot_confirmed"] is True
    assert audits[0]["booking_id"] == "missing-slot"


def test_later_failed_replay_does_not_hide_an_immutable_successful_fact():
    result = report(candidates=[{'id': 'slot', 'slot_confirmed': True}], audits=[
        {'id': 'success', 'gmail_message_id': 'mail', 'booking_id': 'slot', 'auto_booked': True,
         'booking_status': 'Auto Booked', 'created_at': '2026-09-01'},
        {'id': 'replay', 'gmail_message_id': 'mail', 'auto_booked': False,
         'booking_status': 'Historical Skipped', 'created_at': '2026-09-13'},
    ], events=[{'id': 'event', 'provider_message_id': 'mail', 'automation_state': 'AUTO_BOOKED'}])
    assert not any(f['code'] == 'EVENT_CLAIMS_BOOKED_WITHOUT_SLOT' for f in result['findings'])


def test_report_finds_ai_slot_without_audit_and_broken_notification_audit_link():
    report = build_reconciliation_report(
        candidates=[{"id": "slot-1", "slot_confirmed": True, "interview_booking_source": "ai_auto_booked", "date": "2026-09-11", "time": "14:00", "time_end": "15:00"}],
        audits=[],
        notifications=[{"candidate_id": "candidate-1", "booking_id": "slot-1", "booking_audit_id": "missing-audit", "booking_status": "Auto Booked"}],
        analyses=[],
    )

    assert {row["code"] for row in report["findings"]} == {
        "PERSISTED_AI_SLOT_WITHOUT_AUDIT", "NOTIFICATION_AUDIT_MISSING",
    }


def test_successful_cancellation_explains_historical_booking_audits_and_alerts():
    result = report(candidates=[{'id': 's', 'slot_confirmed': False}], audits=[
        {'id': 'book', 'booking_id': 's', 'auto_booked': True, 'booking_status': 'Auto Booked', 'updated_at': '2026-09-01'},
        {'id': 'cancel', 'booking_id': 's', 'auto_booked': True, 'booking_status': 'Cancelled', 'updated_at': '2026-09-02'},
    ], notifications=[{'booking_id': 's', 'booking_status': 'Auto Booked'}])
    assert result['findings'] == []
    assert result['summary']['explained_cancellations'] == 1


def test_missing_slot_is_not_excused_by_a_cancellation_audit():
    result = report(audits=[
        {'id': 'book', 'booking_id': 's', 'auto_booked': True, 'updated_at': '2026-09-01'},
        {'id': 'cancel', 'booking_id': 's', 'auto_booked': True, 'booking_status': 'Cancelled', 'updated_at': '2026-09-02'},
    ])
    assert result['findings'][0]['code'] == 'AUTO_BOOKED_WITHOUT_PERSISTED_SLOT'


def test_deactivated_slot_is_reported_once_not_per_historical_audit():
    result = report(candidates=[{'id': 's', 'slot_confirmed': False}],
                    audits=[{'id': str(i), 'booking_id': 's', 'auto_booked': True} for i in range(3)])
    assert len(result['findings']) == 1
    assert result['findings'][0]['code'] == 'BOOKING_DEACTIVATED_WITHOUT_CANCELLATION_EVIDENCE'


def test_time_overlap_does_not_prove_a_duplicate():
    result = report(candidates=[dict(id=key, slot_confirmed=True, date='2026-09-11', time='14:00') for key in ['a', 'b']])
    assert result['findings'] == []


def test_exact_uid_and_person_proves_duplicate_but_other_person_is_allowed():
    slots = [dict(id=key, slot_confirmed=True, interview_calendar_uid='uid') for key in ['a', 'b']]
    assert report(candidates=slots)['findings'] == []
    result = report(candidates=slots, identity_links={'a': 'person', 'b': 'person'})
    assert result['findings'][0]['code'] == 'DUPLICATE_CONFIRMED_INTERVIEW_IDENTITY'


def test_existing_alias_is_not_canonical_drift_and_preserves_source_ids():
    audit = dict(id='audit', candidate_id='alias', gmail_message_id='mail')
    result = report(audits=[audit], events=[dict(id='event', candidate_id='alias', canonical_candidate_id='person', provider_message_id='mail')],
                    identity_links={'alias': 'person'})
    assert result['findings'] == []
    assert audit['candidate_id'] == 'alias'


def test_split_identity_preserves_source_ids_and_exposes_event():
    audit = dict(id='audit', candidate_id='alias', gmail_message_id='mail')
    result = report(audits=[audit], events=[dict(id='event', candidate_id='person', canonical_candidate_id='person', provider_message_id='mail')],
                    identity_links={'alias': 'other-person'})
    finding = result['findings'][0]
    assert finding['candidate_id'] == 'other-person'
    assert finding['event_id'] == 'event'
    assert finding['expected_state'] == 'person'
    assert audit['candidate_id'] == 'alias'


def test_transitive_aliases_resolve_all_references_without_mutating_input():
    import copy
    inputs = dict(audits=[dict(id='a', candidate_id='old', gmail_message_id='m')],
                  events=[dict(id='e', provider_message_id='m', candidate_id='middle', canonical_candidate_id='old')],
                  identity_links={'old': 'middle', 'middle': 'person'})
    before = copy.deepcopy(inputs)
    assert report(**inputs)['findings'] == []
    assert inputs == before


def test_alias_without_event_does_not_require_historical_audit_repair():
    assert report(audits=[dict(candidate_id='old')], identity_links={'old': 'person'})['findings'] == []


def test_missing_or_conflicting_event_canonical_identity_is_not_hidden():
    for canonical in (None, '', 'unlinked-person'):
        result = report(audits=[dict(candidate_id='old', gmail_message_id='m')],
                        events=[dict(provider_message_id='m', candidate_id='person', canonical_candidate_id=canonical)],
                        identity_links={'old': 'person'})
        assert [f['code'] for f in result['findings']] == ['CANONICAL_CANDIDATE_REFERENCE_DRIFT']


def test_equal_contact_details_do_not_prove_identity():
    result = report(candidates=[dict(id=cid, name='Same Name', phone='123', email='same@example.test') for cid in ('a', 'b')],
                    audits=[dict(candidate_id='a', gmail_message_id='m')],
                    events=[dict(provider_message_id='m', candidate_id='b', canonical_candidate_id='b')])
    assert [f['code'] for f in result['findings']] == ['CANONICAL_CANDIDATE_REFERENCE_DRIFT']


def test_partial_inventory_does_not_claim_references_are_missing():
    result = report(complete=False, candidates=[dict(id='s', slot_confirmed=True, interview_booking_source='ai_auto_booked')],
                    notifications=[dict(booking_id='s', booking_status='Auto Booked', booking_audit_id='outside-page')])
    assert result['findings'] == []
    assert result['coverage']['complete'] is False


def test_event_and_applied_lifecycle_must_be_backed_by_slot_evidence():
    result = report(events=[dict(id='e', provider_message_id='m', automation_state='AUTO_BOOKED')],
                    lifecycles=[dict(booking_id='missing', lifecycle_state='BOOKED', transition_status='APPLIED')])
    assert {f['code'] for f in result['findings']} == {'EVENT_CLAIMS_BOOKED_WITHOUT_SLOT', 'APPLIED_LIFECYCLE_WITHOUT_CONFIRMED_SLOT'}


def test_successful_booking_audit_without_booking_id_is_invalid():
    result = report(audits=[dict(id='a', auto_booked=True, gmail_message_id='m')])
    assert result['findings'][0]['code'] == 'AUTO_BOOKED_WITHOUT_PERSISTED_SLOT'
