"""The Mail Alerts list query orders by Mail received, not Tool detected."""
from __future__ import annotations

from core import recruitment_mail_store as store


class Cursor:
    def __init__(self):
        self.executed = []
        self.description = [type("D", (), {"name": "id"})]

    def execute(self, sql, params=None):
        self.executed.append(" ".join(sql.split()))

    def fetchone(self):
        return (0,)

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Conn:
    def __init__(self, cursor):
        self.value = cursor

    def cursor(self):
        return self.value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_the_flat_list_orders_by_received_time_newest_first(monkeypatch):
    cursor = Cursor()
    monkeypatch.setattr(store, "get_connection", lambda: Conn(cursor))
    store.list_notifications(filters={"sort": "newest"})
    assert "ORDER BY COALESCE(email_received_at, created_at) DESC, id DESC LIMIT" in cursor.executed[-1]
    assert "ORDER BY created_at" not in cursor.executed[-1]


def test_the_order_helper():
    assert store.notification_order_sql("DESC", "n") == "COALESCE(n.email_received_at, n.created_at) DESC, n.id DESC"
    assert store.notification_order_sql("ASC") == "COALESCE(email_received_at, created_at) ASC, id ASC"
    assert store.notification_order_sql("anything") == "COALESCE(email_received_at, created_at) DESC, id DESC"
