"""The 7-day Gmail countdown stops once the OAuth app is published (11 Oct audit).

In Testing mode Google expires every grant seven days after consent; up to
11 Oct no mailbox had synced later than 7.0 days after it. Publishing the app
is the owner's step in the Google console. After it, a mailbox that keeps
syncing past its eighth day proves grants no longer expire, and the dashboard
must stop listing working mailboxes as "expiring" every week.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core import recruitment_mail_store as store

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def mailbox(synced_after_days, authorised=T0):
    return {
        "authorized_at": authorised,
        "last_successful_sync_at": authorised + timedelta(days=synced_after_days) if synced_after_days is not None else None,
        "grant_expires_at": authorised + timedelta(days=store.GMAIL_GRANT_DAYS),
    }


def test_testing_mode_keeps_the_deadline():
    rows = [mailbox(6.99), mailbox(7.0), mailbox(None), {"authorized_at": None, "last_successful_sync_at": None,
                                                         "grant_expires_at": None}]
    store._annotate_grant_expiry(rows)
    assert all(row["grant_expires"] is True for row in rows)
    assert rows[0]["grant_expires_at"] == T0 + timedelta(days=7)


def test_one_mailbox_outliving_the_week_by_a_day_means_grants_no_longer_expire():
    rows = [mailbox(8.5), mailbox(2.0)]
    store._annotate_grant_expiry(rows)
    assert [row["grant_expires"] for row in rows] == [False, False]
    assert [row["grant_expires_at"] for row in rows] == [None, None]


def test_a_sync_just_past_the_week_is_not_enough():
    rows = [mailbox(7.9)]
    store._annotate_grant_expiry(rows)
    assert rows[0]["grant_expires"] is True


def test_an_expiry_recorded_before_9_sep_reads_as_one():
    """The one "RuntimeError" left on 11 Oct was an expired grant from 1 Sep."""
    rows = [
        {"last_error_code": "RuntimeError", "last_error_message": "Gmail authorization expired or was revoked. Reconnect Gmail."},
        {"last_error_code": "RuntimeError", "last_error_message": "Unexpected response from Gmail"},
        {"last_error_code": "GmailAuthorizationExpired", "last_error_message": "Gmail authorization has expired"},
    ]
    store._relabel_legacy_expiry_errors(rows)
    assert [r["last_error_code"] for r in rows] == ["GmailAuthorizationExpired", "RuntimeError", "GmailAuthorizationExpired"]
