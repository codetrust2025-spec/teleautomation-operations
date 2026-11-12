"""A re-read that settles a mail takes its alert out of AI Retry.

Production, 12 Nov 2026: the Innominds offer mail of 24 Aug was re-read as
offer_received at 1.0, but the reprocess only touched `updated_at` on the alert
its first read had left at `review_required`, so it stayed in AI Retry.
"""
from __future__ import annotations

from core import recruitment_mail_store as store
from tests.test_notification_realtime_atomicity import Cursor, inputs, install


def _reread_update(cursor):
    return [(sql, params) for sql, params in cursor.calls
            if sql.startswith("UPDATE mail_monitoring_notifications")]


class RecordingCursor(Cursor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((" ".join(statement.split()), params))
        super().execute(statement, params)


def test_a_settled_reread_offers_its_priority_to_the_existing_alert(monkeypatch):
    cursor = RecordingCursor(inserted=False)
    install(monkeypatch, cursor)

    store.create_monitoring_notification(*inputs())

    (sql, params), = _reread_update(cursor)
    assert "priority=CASE WHEN priority IN ('retry_pending','review_required')" in sql
    assert params[:2] == ("high", "high")


def test_a_doubtful_reread_offers_retry_which_the_case_refuses(monkeypatch):
    cursor = RecordingCursor(inserted=False)
    install(monkeypatch, cursor)
    event, analysis = inputs()
    event["confidence"] = 0.2

    store.create_monitoring_notification(event, analysis)

    (_sql, params), = _reread_update(cursor)
    assert params[:2] == ("retry_pending", "retry_pending")
