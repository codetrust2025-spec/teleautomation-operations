"""The resolved-company cache on a freshly booted machine.

`time.monotonic()` counts from boot. The cache used 0.0 for "never resolved",
so for the first minute after boot `now - 0.0 < TTL` held and the empty
"never" value was served as fresh: Mail Alerts showed no resolved companies,
and test_an_interview_email_without_a_company_shows_the_one_its_evidence_proves
failed on a newly started CI runner, then passed on re-run (run 43249042695).
"""
from __future__ import annotations

import time

from core import recruitment_mail_store as store


class FakeCursor:
    def __init__(self):
        self.rows = []

    def execute(self, sql, params=None):
        if "current_schema()" in sql and "information_schema" not in sql:
            self.rows = [("public",)]
        elif "information_schema.columns" in sql:
            self.rows = [("id",), ("company_name",), ("company_source",)]
        elif "SELECT sender_email, company_name" in sql:
            self.rows = [("hr@hcltech.com", "HCLTech")]
        else:
            self.rows = [("a1", "Ravi Kumar", "talent@hcltech.com", "", "Interview", None, [], "")]

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


def test_a_cache_never_filled_is_not_fresh_seconds_after_boot(monkeypatch):
    monkeypatch.setattr(store, "_booking_companies", lambda: {})
    monkeypatch.setattr(store, "_visibility_for", lambda prefix: "true")
    monkeypatch.setattr(store, "_visibility_params", lambda: [])
    monkeypatch.setattr(time, "monotonic", lambda: 5.0)  # five seconds after boot
    store.clear_resolved_companies()
    try:
        assert store.resolved_alert_companies(FakeCursor()) == {"a1": ("HCLTech", "sender")}
    finally:
        store.clear_resolved_companies()


def test_a_filled_cache_is_served_within_its_minute(monkeypatch):
    monkeypatch.setattr(store, "_booking_companies", lambda: {})
    monkeypatch.setattr(store, "_visibility_for", lambda prefix: "true")
    monkeypatch.setattr(store, "_visibility_params", lambda: [])
    clock = iter([5.0, 30.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    store.clear_resolved_companies()
    try:
        first = store.resolved_alert_companies(FakeCursor())
        assert store.resolved_alert_companies(None) is first  # served from cache, no query
    finally:
        store.clear_resolved_companies()
