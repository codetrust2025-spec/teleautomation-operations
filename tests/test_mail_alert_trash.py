"""Mail Alerts Trash (migration 038): a removed Interview Related alert stays
removed, and nothing about Selection Related alerts changes."""
from __future__ import annotations

import re
from pathlib import Path

from core import recruitment_mail_store as store

MIGRATION = Path(__file__).resolve().parents[1] / "core" / "migrations" / "038_mail_alert_trash.sql"


def test_trigger_blocks_exactly_the_interview_related_classifications():
    sql = MIGRATION.read_text()
    block = re.search(r"NEW\.classification IN \((.*?)\)", sql, re.S).group(1)
    blocked = set(re.findall(r"'([a-z_]+)'", block))
    assert blocked == store.INTERVIEW_RELATED_CLASSIFICATIONS
    assert not blocked & store.SELECTION_RELATED_CLASSIFICATIONS


def test_trashed_alerts_are_off_the_screen():
    clause, _ = store.visible_rows_sql(prefix="n")
    assert "NOT EXISTS (SELECT 1 FROM mail_alert_trash trashed WHERE trashed.notification_id = n.id)" in clause


class _Cursor:
    def __init__(self, *, trashed: bool, update_rows: list):
        self.trashed = trashed
        self.update_rows = update_rows
        self.sql: list[str] = []
        self.description = []
        self._one = None
        self._rows: list = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, statement, params=None):
        text = " ".join(str(statement).split())
        self.sql.append(text)
        self._one, self._rows = None, []
        if "FROM mailbox_messages m WHERE m.id=" in text:
            self._one = ("gmail-1", "thread-1", "Profile shortlisted", "Recruiter", "r@example.com",
                         "2026-11-01T10:00:00Z", "mailbox-1", "candidate@example.com")
        elif text.startswith("SELECT 1 FROM mail_alert_trash"):
            self._one = (1,) if self.trashed else None
        elif text.startswith("UPDATE mail_monitoring_notifications"):
            self.description = [type("C", (), {"name": "id"})()]
            self._rows = list(self.update_rows)

    def fetchone(self):
        return self._one

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def cursor(self):
        return self._cursor


def _call(monkeypatch, cursor, classification="interview_shortlisted"):
    monkeypatch.setattr(store, "get_connection", lambda: _Conn(cursor))
    monkeypatch.setattr(store, "_candidate_snapshot", lambda *_a: ("Candidate", "candidate@example.com"))
    monkeypatch.setattr(store, "should_route_to_mail_alert", lambda *_a, **_k: True)
    monkeypatch.setattr(store, "canonical_candidate_id", lambda cid: cid)
    monkeypatch.setattr(store, "_selection_alert_filing", lambda *a: a[3])
    event = {"id": "event-1", "candidate_id": "candidate-1", "mailbox_message_id": "message-1",
             "structured_result": {}}
    analysis = {"id": "analysis-1", "classification": classification}
    return store.create_monitoring_notification(event, analysis)


def test_a_trashed_email_raises_no_interview_alert(monkeypatch):
    cursor = _Cursor(trashed=True, update_rows=[])
    assert _call(monkeypatch, cursor) == {}
    assert not any(s.startswith("INSERT INTO mail_monitoring_notifications") for s in cursor.sql)


def test_a_selection_alert_never_consults_the_trash(monkeypatch):
    cursor = _Cursor(trashed=True, update_rows=[])
    _call(monkeypatch, cursor, classification="job_selection_confirmed")
    assert not any("mail_alert_trash" in s for s in cursor.sql)
    assert any(s.startswith("INSERT INTO mail_monitoring_notifications") for s in cursor.sql)


def test_an_insert_the_database_skipped_is_no_alert_not_a_crash(monkeypatch):
    # The trigger can skip a row the Python check did not see (a race, or a
    # path that skips the check): no inserted row and no row to update.
    cursor = _Cursor(trashed=False, update_rows=[])
    assert _call(monkeypatch, cursor) == {}
