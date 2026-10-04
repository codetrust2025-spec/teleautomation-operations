"""Stale booking titles are corrected on read, never by rewriting history."""
from copy import deepcopy

import pytest

from core import recruitment_mail_store as store
from features import candidate_store


@pytest.mark.parametrize('title', [
    'Interview Automatically Booked', 'Interview Manually Approved & Booked',
    'Interview Rescheduled',
])
@pytest.mark.parametrize('status', ['Auto Booked', 'AI_RETRY_PENDING'])
def test_released_claim_title_matches_automation_state(monkeypatch, title, status):
    stored = {'id': 'historical-alert', 'booking_id': 'inactive-slot',
              'booking_status': status, 'candidate_status': title}
    original = deepcopy(stored)
    monkeypatch.setattr(candidate_store, 'get_candidate', lambda _: None)
    def forbid_writes():
        raise AssertionError('Read projection must not write notification/audit history')
    monkeypatch.setattr(store, 'get_connection', forbid_writes)
    projected = store.reconcile_booking_claims([deepcopy(stored)])[0]
    assert projected['candidate_status'] == 'Booking Removed'
    assert projected['booking_status'] == 'Booking Removed'
    assert projected['historical_candidate_status'] == title
    assert store.reconcile_booking_claims([projected])[0] == projected
    assert stored == original


def test_valid_booking_title_and_cancellation_are_preserved(monkeypatch):
    monkeypatch.setattr(candidate_store, 'get_candidate', lambda _: {
        'id': 'slot', 'slot_confirmed': True, 'date': '2026-09-16', 'time': '15:30',
    })
    rows = [
        {'booking_id': 'slot', 'booking_status': 'Auto Booked',
         'candidate_status': 'Interview Automatically Booked'},
        {'booking_id': 'cancelled', 'booking_status': 'Cancelled',
         'candidate_status': 'Interview Cancelled'},
        {'booking_status': 'AI_RETRY_PENDING', 'candidate_status': 'HR Confirmation'},
    ]
    original = deepcopy(rows)
    assert store.reconcile_booking_claims(rows) == original
