from core import recruitment_mail_store as store
from datetime import date
from pathlib import Path


def test_every_requested_classification_is_stable():
    # `interview_needs_reading` was added on 29 Sep 2026: an interview mail the
    # reader gave up on, filed for a person instead of vanishing. It is not
    # `ai_retry_pending`, which promises an attempt that is no longer coming.
    assert store.CANONICAL_CLASSIFICATIONS == {
        'job_selection_confirmed','offer_received','offer_accepted','offer_declined',
        'offer_revoked','joining_confirmed','joining_date_updated','onboarding_started',
        'background_verification','document_verification','compensation_confirmation',
        'interview_update','interview_shortlisted','interview_confirmed','interview_rescheduled',
            'interview_cancelled','candidate_rejected','ai_retry_pending','not_relevant',
        'final_round_cleared','hr_confirmation','interview_needs_reading',
    }


def test_legacy_statuses_map_without_breaking_existing_records():
    assert store.canonical_classification(status='OFFER_LETTER_RECEIVED') == 'offer_received'
    assert store.canonical_classification(status='FINAL_SELECTION_CONFIRMED') == 'job_selection_confirmed'
    assert store.canonical_classification(status='FINAL_ROUND_CLEARED') == 'final_round_cleared'
    assert store.canonical_classification(status='HR_CONFIRMATION') == 'hr_confirmation'
    assert store.canonical_classification(status='INTERVIEW_UPDATE') == 'interview_update'
    assert store.canonical_classification(status='INTERVIEW_CONFIRMED') == 'interview_confirmed'


def test_priority_and_review_thresholds(monkeypatch):
    monkeypatch.setenv('OLLAMA_CONFIDENCE_THRESHOLD','0.75')
    assert store.notification_priority('offer_received',confidence=.94)=='high'
    assert store.notification_priority('document_verification',confidence=.90)=='medium'
    assert store.notification_priority('candidate_rejected',confidence=.90)=='informational'
    assert store.notification_priority('offer_received',confidence=.70)=='retry_pending'


def test_auto_booking_migration_is_additive_and_idempotent():
    sql=Path('core/migrations/008_recruitment_mail_auto_booking.sql').read_text(encoding='utf-8').lower()
    for table in ('gmail_pubsub_deliveries','interview_mail_analyses','interview_auto_booking_audit'):
        assert f'create table if not exists {table}' in sql
    assert 'unique(gmail_message_id, classification)' in sql
    assert 'drop table' not in sql and 'delete from' not in sql


def test_required_realtime_event_contract_is_emitted():
    source=Path('services/recruitment_mail_agent.py').read_text(encoding='utf-8')
    worker=Path('workers/recruitment_mail_worker.py').read_text(encoding='utf-8')
    booking=Path('services/interview_auto_booking.py').read_text(encoding='utf-8')
    mail_store=Path('core/recruitment_mail_store.py').read_text(encoding='utf-8')
    for event in ('mail_received','mail_ai_analyzing','interview_detected','auto_booking_started','slot_auto_booked','slot_booking_blocked','interview_rescheduled','interview_cancelled','candidate_status_updated','notification_created','mail_processing_failed'):
        assert event in source or event in worker or event in booking or event in mail_store


def _review_event(classification, *, interview=None, evidence=None, **structured):
    return {
        "primary_status": classification.upper(),
        "classification": classification,
        "validation_status": "NEEDS_REVIEW",
        "requires_manual_review": True,
        "structured_result": {
            "classification": classification,
            "requires_manual_review": True,
            "interview": interview or {},
            "evidence": evidence or [],
            **structured,
        },
    }


def test_mail_alert_routing_keeps_important_review_mail():
    event = _review_event(
        "onboarding_started",
        evidence=[{"meaning": "BACKGROUND_VERIFICATION"}],
    )
    assert store.should_route_to_mail_alert(
        event,
        {"classification": "onboarding_started"},
        source={"subject": "Welcome - BGV documents required"},
        today=date(2026, 7, 24),
    )


def test_mail_alert_routing_rejects_past_and_newsletter_interviews():
    past = _review_event(
        "interview_confirmed",
        interview={"date": "2026-07-23"},
        evidence=[{"meaning": "INTERVIEW_CONFIRMED"}],
        classification_source="FALLBACK",
    )
    assert not store.should_route_to_mail_alert(
        past,
        {"classification": "interview_confirmed"},
        source={"subject": "Interview confirmation"},
        today=date(2026, 7, 24),
    )

    newsletter = _review_event(
        "interview_confirmed",
        interview={"date": "2026-07-25"},
        evidence=[{"meaning": "INTERVIEW_CONFIRMED"}],
        classification_source="FALLBACK",
    )
    assert not store.should_route_to_mail_alert(
        newsletter,
        {"classification": "interview_confirmed"},
        source={"subject": "30 days of GraphoTherapy improved energy"},
        today=date(2026, 7, 24),
    )


def test_mail_alert_routing_keeps_future_interview_and_honours_suppression():
    future = _review_event(
        "interview_confirmed",
        interview={"date": "2026-07-31"},
        evidence=[{"meaning": "INTERVIEW_CONFIRMED"}],
        classification_source="ICALENDAR_VERIFIED",
    )
    assert store.should_route_to_mail_alert(
        future,
        {"classification": "interview_confirmed"},
        source={"subject": "L1 React interview"},
        today=date(2026, 7, 24),
    )
    future["structured_result"]["_suppress_monitoring_notification"] = True
    assert not store.should_route_to_mail_alert(
        future,
        {"classification": "interview_confirmed"},
        source={"subject": "L1 React interview"},
        today=date(2026, 7, 24),
    )


def test_mail_alert_routing_keeps_validated_shortlist_without_schedule():
    shortlist = _review_event(
        "interview_shortlisted",
        evidence=[{
            "source": "EMAIL_BODY",
            "meaning": "INTERVIEW_SHORTLISTED",
            "text": "Your profile got shortlisted for L1 interview",
        }],
        classification_source="OLLAMA",
        recruitment_relevance_result={"decision": "ESTABLISHED"},
        backend_transition_validated=True,
    )

    assert store.should_route_to_mail_alert(
        shortlist,
        {"classification": "interview_shortlisted"},
        source={"subject": "Re: New Job Opportunity Azure Engineer - Remote"},
        today=date(2026, 9, 2),
    )


def test_mail_alert_routing_still_rejects_confirmed_interview_without_schedule():
    confirmed = _review_event(
        "interview_confirmed",
        evidence=[{"meaning": "INTERVIEW_CONFIRMED"}],
        classification_source="OLLAMA",
        recruitment_relevance_result={"decision": "ESTABLISHED"},
        backend_transition_validated=True,
    )

    assert not store.should_route_to_mail_alert(
        confirmed,
        {"classification": "interview_confirmed"},
        source={"subject": "Interview confirmation"},
        today=date(2026, 9, 2),
    )


def test_mailbox_stats_uses_one_qualification_parameter_per_sql_predicate(monkeypatch):
    class Column:
        def __init__(self, name):
            self.name = name

    class Cursor:
        def __init__(self):
            self.calls = []
            self.description = []
            self.step = 0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query, params):
            self.calls.append((query, params))
            self.step += 1
            if self.step == 1:
                self.description = [
                    Column("important_emails"), Column("selection_events"),
                    Column("offer_events"), Column("offer_letters"),
                    Column("pending_reviews"),
                ]
            elif self.step == 3:
                self.description = [Column("id")]

        def fetchone(self):
            return (0, 0, 0, 0, 0) if self.step == 1 else (0,)

        def fetchall(self):
            return []

    class Connection:
        def __init__(self, cursor):
            self.value = cursor

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self):
            return self.value

    cursor = Cursor()
    monkeypatch.setattr(store, "get_connection", lambda: Connection(cursor))
    monkeypatch.setattr(
        store,
        "qualified_event_sql",
        lambda _alias: ("e.primary_status=ANY(%s)", [["STATUS"]]),
    )

    store.mailbox_stats("mailbox-1")

    assert cursor.calls[0][1] == [
        ["STATUS"], ["STATUS"], ["STATUS"], ["STATUS"], "mailbox-1",
    ]
