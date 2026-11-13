"""A visible mail alert and its creation event are one durable fact."""

from __future__ import annotations

from contextlib import nullcontext

from core import recruitment_mail_store as store


class Column:
    def __init__(self, name):
        self.name = name


class Cursor:
    def __init__(self, *, inserted=True):
        self.inserted = inserted
        self.description = []
        self.rows = []
        self.source = None
        self.sql = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        normalized = " ".join(statement.split())
        self.sql.append(normalized)
        self.source = None
        if "FROM mailbox_messages m WHERE m.id=" in normalized:
            self.source = (
                "provider-1", "thread-1", "Interview invitation", "Recruiter",
                "recruiter@example.com", "2026-09-02T10:00:00Z", "mailbox-1",
                "candidate@example.com",
            )
        elif normalized.startswith("INSERT INTO mail_monitoring_notifications"):
            self.description = [Column(name) for name in (
                "id", "candidate_id", "candidate_name", "company_name",
                "classification", "candidate_status", "priority",
            )]
            self.rows = [(
                params[0], params[1], params[2], params[11], params[9], params[10], params[21],
            )] if self.inserted else []
        elif normalized.startswith("UPDATE mail_monitoring_notifications"):
            self.description = [Column(name) for name in (
                "id", "candidate_id", "candidate_name", "company_name",
                "classification", "candidate_status", "priority",
            )]
            self.rows = [(
                "existing-notification", "candidate-1", "Candidate", "Company",
                "interview_confirmed", "Interview Confirmed", "high",
            )]
        elif normalized.startswith("INSERT INTO mail_realtime_events"):
            self.description = [Column(name) for name in (
                "id", "event_type", "notification_id", "candidate_id", "payload", "created_at",
            )]
            self.rows = [(params[0], params[1], params[2], params[3], {}, "now")]

    def fetchone(self):
        return self.source

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return nullcontext(self._cursor)


def inputs():
    event = {
        "id": "event-1", "mailbox_message_id": "message-1", "candidate_id": "candidate-1",
        "confidence": 0.99, "structured_result": {
            "company": {"name": "Company"}, "job": {"title": "Engineer"},
            "classification_source": "CALENDAR_INVITE",
            # A new interview alert needs this mail's own Prompt 12 verdict.
            "prompt12": {"verdict": "SCHEDULED"},
        },
    }
    analysis = {"id": "analysis-1", "classification": "interview_confirmed", "candidate_status": "Interview Confirmed"}
    return event, analysis


def install(monkeypatch, cursor):
    monkeypatch.setattr(store, "get_connection", lambda: Connection(cursor))
    monkeypatch.setattr(store, "_candidate_snapshot", lambda *_args: ("Candidate", "candidate@example.com"))
    monkeypatch.setattr(store, "should_route_to_mail_alert", lambda *_args, **_kwargs: True)


def test_new_notification_and_creation_event_share_one_transaction(monkeypatch):
    cursor = Cursor(inserted=True)
    install(monkeypatch, cursor)
    notification = store.create_monitoring_notification(*inputs())

    realtime = notification.pop("_created_realtime_event")
    assert realtime["event_type"] == "notification_created"
    assert realtime["notification_id"] == notification["id"]
    assert cursor.sql.index(next(sql for sql in cursor.sql if sql.startswith("INSERT INTO mail_monitoring_notifications"))) < cursor.sql.index(next(sql for sql in cursor.sql if sql.startswith("INSERT INTO mail_realtime_events")))


def test_reprocess_updates_existing_row_without_a_second_creation_event(monkeypatch):
    cursor = Cursor(inserted=False)
    install(monkeypatch, cursor)
    notification = store.create_monitoring_notification(*inputs())

    assert notification["id"] == "existing-notification"
    assert "_created_realtime_event" not in notification
    assert not any(sql.startswith("INSERT INTO mail_realtime_events") for sql in cursor.sql)


def test_interview_alert_and_realtime_use_booking_canonical_person(monkeypatch):
    cursor = Cursor(inserted=True)
    install(monkeypatch, cursor)
    monkeypatch.setattr(store, 'canonical_candidate_id', lambda _source: 'canonical-person')
    event, analysis = inputs()
    notification = store.create_monitoring_notification(event, analysis)
    assert notification['candidate_id'] == 'canonical-person'
    assert notification['_created_realtime_event']['candidate_id'] == 'canonical-person'
    assert event['candidate_id'] == 'candidate-1'  # immutable source provenance
