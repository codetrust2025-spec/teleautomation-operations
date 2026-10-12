from types import SimpleNamespace

from core import recruitment_mail_store as store


class _Cursor:
    def __init__(self):
        self.description = []
        self.rows = []
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, _params=()):
        compact = " ".join(sql.split())
        self.queries.append(compact)
        if "FROM candidate_mailboxes m" in compact:
            names = [
                "id", "candidate_id", "canonical_candidate_id", "email_address",
                "connection_status", "monitoring_enabled", "last_error_code",
                "last_error_message", "last_successful_sync_at", "updated_at",
            ]
            self.rows = [
                ("mailbox-1", "candidate-1", "candidate-1", "one@example.com",
                 "CONNECTED", True, None, None, None, "2026-08-31T00:00:00Z"),
                ("mailbox-2", "candidate-2", "candidate-2", "two@example.com",
                 "CONNECTED", True, None, None, None, "2026-08-31T00:00:00Z"),
            ]
        elif "JOIN mailbox_messages m" in compact:
            names = [
                "mailbox_id", "important_emails", "selection_events",
                "offer_events", "offer_letters",
            ]
            self.rows = [("mailbox-1", 4, 1, 2, 1)]
        elif "FROM mail_monitoring_notifications" in compact:
            names = ["mailbox_id", "pending_reviews"]
            self.rows = [("mailbox-1", 2)]
        elif "FROM mailbox_sync_jobs" in compact:
            names = [
                "mailbox_id", "id", "status", "job_type", "created_at",
                "started_at", "completed_at", "messages_fetched",
                "messages_processed", "events_detected", "error_message",
            ]
            self.rows = [
                ("mailbox-1", "job-1", "COMPLETED", "INCREMENTAL_SYNC",
                 None, None, None, 5, 4, 1, None),
            ]
        else:  # pragma: no cover - new queries must be modelled explicitly
            raise AssertionError(f"Unexpected overview query: {compact}")
        self.description = [SimpleNamespace(name=name) for name in names]

    def fetchall(self):
        return list(self.rows)


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def test_mailbox_overview_uses_one_connection_and_set_queries(monkeypatch):
    cursor = _Cursor()
    connections = []

    def connection():
        connections.append(True)
        return _Connection(cursor)

    monkeypatch.setattr(store, "get_connection", connection)
    rows = store.mailbox_overview_rows()

    assert len(connections) == 1
    assert len(cursor.queries) == 4
    assert rows[0]["stats"] == {
        "important_emails": 4,
        "selection_events": 1,
        "offer_events": 2,
        "offer_letters": 1,
        "pending_reviews": 2,
        "latest_sync_job_id": "job-1",
        "latest_sync_status": "COMPLETED",
        "latest_sync_job_type": "INCREMENTAL_SYNC",
        "latest_sync_created_at": None,
        "latest_sync_started_at": None,
        "latest_sync_completed_at": None,
        "latest_sync_messages_fetched": 5,
        "latest_sync_messages_processed": 4,
        "latest_sync_events_detected": 1,
        "latest_sync_error": None,
    }
    assert rows[1]["stats"] == {
        "important_emails": 0,
        "selection_events": 0,
        "offer_events": 0,
        "offer_letters": 0,
        "pending_reviews": 0,
    }
