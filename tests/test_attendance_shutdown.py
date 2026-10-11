"""Regression coverage for the append-only attendance shutdown controls."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.office_network import NetworkVerification
from features import attendance_eligibility as attendance
from features import daily_checkout


IST = ZoneInfo("Asia/Kolkata")
HANDLER = {"username": "handler-one", "role": "handler", "reference": "Handler One"}


class _Cursor:
    description = []

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def execute(self, *_args, **_kwargs): return None
    def fetchall(self): return []
    def fetchone(self): return None


class _Connection:
    def cursor(self): return _Cursor()


@contextmanager
def _connection():
    yield _Connection()


def _active(scope="HANDLER"):
    return {"active": True, "scope": scope, "global_active": scope == "GLOBAL",
            "handler_active": scope == "HANDLER", "event": {"action": "SHUTDOWN"}}


def test_shutdown_state_global_overrides_individual_reactivation():
    # Rows are supplied newest first, as the production query orders them.
    state = attendance._shutdown_state_from_events([
        {"scope": "HANDLER", "action": "REACTIVATE", "id": "handler-on"},
        {"scope": "GLOBAL", "action": "SHUTDOWN", "id": "global-off"},
        {"scope": "HANDLER", "action": "SHUTDOWN", "id": "handler-off"},
    ])
    assert state["active"] is True
    assert state["scope"] == "GLOBAL"
    assert state["global_active"] is True
    assert state["handler_active"] is False


def test_shutdown_migration_is_append_only_audit_storage():
    sql = Path("core/migrations/037_attendance_shutdown_audit.sql").read_text("utf-8").lower()
    assert "operations_attendance_shutdown_events" in sql
    assert "check (scope in ('global', 'handler'))" in sql
    assert "check (action in ('shutdown', 'reactivate'))" in sql
    statements = [line.strip() for line in sql.splitlines() if not line.lstrip().startswith("--")]
    assert not any(line.startswith(("insert ", "update ", "delete ")) for line in statements)


def test_shutdown_blocks_marking_before_it_can_change_attendance(monkeypatch):
    monkeypatch.setattr(attendance, "use_postgres", lambda: True)
    monkeypatch.setattr(attendance, "get_connection", _connection)
    monkeypatch.setattr(attendance, "_policy", lambda _conn: {"effective_date": datetime(2026, 9, 1).date()})
    monkeypatch.setattr(attendance, "shutdown_state", lambda *_args: _active("GLOBAL"))
    monkeypatch.setattr(attendance, "_attendance_row", lambda *_args: pytest.fail("attendance was read after shutdown"))

    network = NetworkVerification(True, "direct", "office", "OFFICE_NETWORK", "203.0.113.10")
    with pytest.raises(PermissionError, match="shut down globally"):
        attendance.mark_attendance(HANDLER, network, now=datetime(2026, 9, 2, 10, tzinfo=IST))


def test_shutdown_stops_eligibility_reconciliation_without_changing_history(monkeypatch):
    monkeypatch.setattr(attendance, "use_postgres", lambda: True)
    monkeypatch.setattr(attendance, "get_connection", _connection)
    monkeypatch.setattr(attendance, "_policy", lambda _conn: {"effective_date": datetime(2026, 9, 1).date()})
    monkeypatch.setattr(attendance, "_holidays", lambda *_args: {})
    monkeypatch.setattr(attendance, "_attendance_row", lambda *_args: None)
    monkeypatch.setattr(attendance, "shutdown_state", lambda *_args: _active())
    monkeypatch.setattr(attendance, "_reconcile_eligibility", lambda *_args: pytest.fail("eligibility was reconciled"))

    result = attendance.status(HANDLER, now=datetime(2026, 9, 2, 10, tzinfo=IST))
    assert result["popup"]["reason"] == "ATTENDANCE_SHUTDOWN"
    assert result["eligibility"] is None
    assert result["shutdown"]["active"] is True


def test_shutdown_blocks_checkout_processing(monkeypatch):
    monkeypatch.setattr(daily_checkout.attendance, "_require_database", lambda: None)
    monkeypatch.setattr(daily_checkout, "get_connection", _connection)
    monkeypatch.setattr(daily_checkout, "_checkout_row", lambda *_args: {"id": "existing"})
    monkeypatch.setattr(daily_checkout.attendance, "shutdown_state", lambda *_args: _active())

    result = daily_checkout.status(HANDLER, now=datetime(2026, 9, 2, 19, tzinfo=IST))
    assert result["can_check_out"] is False
    assert result["blockers"][0]["kind"] == daily_checkout.ATTENDANCE_SHUTDOWN
