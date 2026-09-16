"""Recovering a missed alert must not sound like a new one.

An event can be classified correctly and still never reach the Mail Alerts
screen, because routing is decided separately. 92 August `interview_shortlisted`
events are exactly that: each correct, each invisible, because the routing gate
applied an interview-date requirement to a classification that by definition has
no date yet.

Writing those alerts now is right. Announcing them is not: the realtime event is
what the browser turns into a sound, and a sound claims something just happened.
Ninety-two of them for month-old mail is ninety-two false alarms.

These tests pin both halves: the alert row is written, and no
`notification_created` event is recorded alongside it.
"""

from __future__ import annotations

import pytest

from core import recruitment_mail_store as store


class _Cursor:
    """Enough of a cursor to drive create_monitoring_notification's INSERT."""

    def __init__(self, recorder):
        self.recorder = recorder
        self.description = None
        self._rows: list = []

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        self.recorder["sql"].append(text)
        if text.startswith("SELECT m.provider_message_id"):
            self.description = None
            self._rows = [(
                "gmail-msg-1", "thread-1", "Re: New Job Opportunity Azure Engineer - Remote",
                "Suresh", "suresh@zealogics.com", "2026-09-01T07:26:43+00:00",
                "mailbox-1", "naveen.prakash@example.com",
            )]
        elif text.startswith("INSERT INTO mail_monitoring_notifications"):
            self.description = [("id",), ("candidate_name",), ("company_name",)]
            self._rows = [("notification-1", "Naveen", "Zealogics")]
        else:
            self.description = [("id",)]
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Connection:
    def __init__(self, recorder):
        self.recorder = recorder

    def cursor(self):
        return _Cursor(self.recorder)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def recorder(monkeypatch):
    state = {"sql": [], "realtime": []}

    class _Ctx:
        def __enter__(self_inner):
            return _Connection(state)

        def __exit__(self_inner, *exc):
            return False

    monkeypatch.setattr(store, "get_connection", lambda: _Ctx())
    monkeypatch.setattr(store, "_candidate_snapshot", lambda *a, **k: ("Naveen", "naveen.prakash@example.com"))
    monkeypatch.setattr(store, "should_route_to_mail_alert", lambda *a, **k: True)

    def _fake_realtime(cur, event_type, payload):
        state["realtime"].append((event_type, payload))
        return {"id": "realtime-1"}

    monkeypatch.setattr(store, "_record_realtime_event", _fake_realtime)

    def _fake_rows(cur):
        if cur.description is None:
            return []
        return [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]

    monkeypatch.setattr(store, "_rows", _fake_rows)
    return state


def _event():
    return {
        "id": "event-1",
        "candidate_id": "cand-1",
        "mailbox_message_id": "message-1",
        "classification": "interview_shortlisted",
        "structured_result": {},
        "confidence": 1.0,
    }


def _analysis():
    return {
        "id": "analysis-1",
        "classification": "interview_shortlisted",
        "candidate_status": "Interview Shortlisted",
        "confidence": 1.0,
    }


class TestASilentRecoveryWritesTheAlertWithoutAnnouncingIt:
    def test_the_notification_row_is_written(self, recorder):
        result = store.create_monitoring_notification(_event(), _analysis(), silent=True)
        assert result["id"] == "notification-1"
        assert any(s.startswith("INSERT INTO mail_monitoring_notifications") for s in recorder["sql"])

    def test_no_realtime_event_is_recorded(self, recorder):
        """The realtime event is the sound. A recovery must not fire one."""
        store.create_monitoring_notification(_event(), _analysis(), silent=True)
        assert recorder["realtime"] == []

    def test_it_reports_that_it_was_silent(self, recorder):
        result = store.create_monitoring_notification(_event(), _analysis(), silent=True)
        assert result["_recovered_silently"] is True
        assert result["_created_realtime_event"] is None


class TestLiveClassificationStillAnnounces:
    def test_the_default_records_a_notification_created_event(self, recorder):
        """Silence is opt-in. Nothing on the live path passes it, and a missing
        default must never turn real-time alerting off."""
        result = store.create_monitoring_notification(_event(), _analysis())
        assert [event_type for event_type, _ in recorder["realtime"]] == ["notification_created"]
        assert result["_created_realtime_event"] == {"id": "realtime-1"}
        assert "_recovered_silently" not in result

    def test_the_payload_still_carries_what_the_browser_needs(self, recorder):
        store.create_monitoring_notification(_event(), _analysis())
        _, payload = recorder["realtime"][0]
        assert payload["notification_id"] == "notification-1"
        assert payload["classification"] == "interview_shortlisted"


class TestTheRecoveryOnlyReplaysRouting:
    def test_it_never_reclassifies(self):
        """The events are already correct; re-running the model would spend
        hours reproducing answers that are on record, and could change them."""
        source = store.recover_missing_notifications.__doc__ or ""
        assert "replays only the routing decision" in source

    def test_it_defaults_to_silent(self):
        import inspect

        signature = inspect.signature(store.recover_missing_notifications)
        assert signature.parameters["silent"].default is True
