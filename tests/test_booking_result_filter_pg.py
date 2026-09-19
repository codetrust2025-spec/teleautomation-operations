"""The Booking result filter, run as the real query on the real schema.

Mail Alerts pages on the server, so the filter has to be SQL: filtering a page
in the browser would leave the total and the page count describing rows the
screen no longer shows. These tests build `mail_monitoring_notifications`
from the migrations that define it, insert one alert for every kind of outcome
production holds, and call `list_notifications` exactly as the API does.

The kinds, from the alerts stored on 19 Sep 2026: Auto Booked (47 visible),
Rescheduled (3), Duplicate Ignored (3), Blocked (12), Cancelled (6), bookings
later released (7), and alerts that are not booking outcomes at all —
shortlists, cancellations without a booking, job-status updates.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
from urllib.parse import urlparse
import uuid

import pytest

from core import recruitment_mail_store as store
from features import candidate_store
from services import recruitment_identity

MIGRATIONS = Path(__file__).resolve().parents[1] / "core" / "migrations"


def notifications_ddl() -> list[str]:
    """The table as the migrations define it, without its foreign keys.

    The two references point at analysis tables this filter never reads.
    """
    ddl = (MIGRATIONS / "007_recruitment_mail_notifications.sql").read_text()
    start = ddl.index("CREATE TABLE IF NOT EXISTS mail_monitoring_notifications")
    depth = 0
    for index in range(start, len(ddl)):
        depth += (ddl[index] == "(") - (ddl[index] == ")")
        if depth == 0 and ddl[index] == ")":
            create = re.sub(r"\s+REFERENCES\s+\w+\(\w+\)", "", ddl[start:index + 1]) + ";"
            break
    else:  # pragma: no cover - the migration would be broken
        raise AssertionError("mail_monitoring_notifications is never closed")
    alters = []
    for migration in ("008_recruitment_mail_auto_booking.sql", "018_recruitment_mail_booking_block_reason.sql"):
        alters += re.findall(
            r"ALTER TABLE mail_monitoring_notifications[^;]*;", (MIGRATIONS / migration).read_text(),
        )
    assert alters, "the booking and block-reason columns come from these migrations"
    return [create, *alters]


@pytest.fixture
def alerts(monkeypatch):
    url = os.getenv("AUDIT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL tests run in CI")
    target = urlparse(url)
    assert target.hostname in {"localhost", "127.0.0.1"} and target.path == "/business_ci"
    import psycopg2
    from psycopg2 import sql
    schema = "booking_filter_" + uuid.uuid4().hex

    @contextmanager
    def connect():
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        with connect() as conn, conn.cursor() as cur:
            for statement in notifications_ddl():
                cur.execute(statement)
        monkeypatch.setattr(store, "get_connection", connect)
        monkeypatch.setattr(recruitment_identity, "load_links", lambda: {})
        # The roster, as reconcile_booking_claims reads it: every booking but
        # "gone" still holds its confirmed slot.
        monkeypatch.setattr(
            candidate_store, "get_candidate",
            lambda cid: None if cid == "gone" else {"id": cid, "slot_confirmed": True, "date": "2099-01-01"},
        )
        monkeypatch.setattr(candidate_store, "candidate_has_confirmed_slot", lambda row: bool(row))
        yield connect
    finally:
        assert schema.startswith("booking_filter_") and len(schema) == 47
        with psycopg2.connect(url) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def add(connect, alert_id, *, candidate="c1", classification="interview_confirmed", status=None,
        booking_id=None, block=None, subject="Interview", dismissed=False, minutes_ago=0):
    reason_code, internal_code = block or (None, None)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mail_monitoring_notifications(
                   id, candidate_id, gmail_message_id, classification, candidate_status,
                   email_subject, booking_status, booking_id, booking_block_reason_code,
                   booking_block_reason, booking_failure_code, dismissed_at, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       CASE WHEN %s THEN now() END, now() - make_interval(mins => %s))""",
            (alert_id, candidate, "gm-" + alert_id, classification, status, subject, status, booking_id,
             reason_code, "stored sentence" if reason_code else None, internal_code,
             dismissed, minutes_ago),
        )


@pytest.fixture
def production_shapes(alerts):
    add(alerts, "auto", status="Auto Booked", booking_id="b1", subject="L1 Interview Java", minutes_ago=1)
    add(alerts, "resched", candidate="c2", classification="interview_rescheduled",
        status="Rescheduled", booking_id="b2", minutes_ago=2)
    add(alerts, "duplicate", status="Duplicate Ignored",
        block=("DUPLICATE_BOOKING", "DUPLICATE_BOOKING"), minutes_ago=3)
    add(alerts, "blocked", classification="interview_cancelled", status="Blocked",
        block=("ROUND_NOT_FOUND", "BOOKING_AMBIGUOUS"), subject="Canceled: L1 Java", minutes_ago=4)
    add(alerts, "failed", candidate="c2", status="Processing Failed",
        block=("AI_RETRY_PENDING", "KEYERROR"), minutes_ago=5)
    add(alerts, "released", status="AI_RETRY_PENDING", booking_id="b3", minutes_ago=6)
    add(alerts, "cancelled", candidate="c2", classification="interview_cancelled",
        status="Cancelled", booking_id="b4", minutes_ago=7)
    add(alerts, "shortlist", classification="interview_shortlisted", minutes_ago=8)
    add(alerts, "offer", classification="offer_received", minutes_ago=9)
    add(alerts, "vanished", candidate="c2", status="Auto Booked", booking_id="gone", minutes_ago=10)
    add(alerts, "dismissed", status="Blocked", block=("PAST_INTERVIEW_DATE", "PAST_INTERVIEW"),
        dismissed=True, minutes_ago=11)
    return alerts


def listed(**filters):
    rows, total = store.list_notifications(filters=filters, limit=50, offset=0)
    return [row["id"] for row in rows], total


def test_successfully_booked_is_what_the_roster_holds(production_shapes):
    ids, total = listed(booking_result="booked")
    # Auto Booked and Rescheduled with their bookings, plus the invite whose
    # interview was already booked. The claim whose slot has gone is released
    # as it is read, so it is not listed as a success.
    assert ids == ["auto", "resched", "duplicate"]
    assert total == 3


def test_blocked_is_every_attempt_that_created_nothing_and_says_why(production_shapes):
    ids, total = listed(booking_result="blocked")
    assert ids == ["blocked", "failed"]
    assert total == 2


def test_alerts_that_are_not_booking_outcomes_are_in_neither(production_shapes):
    booked, _ = listed(booking_result="booked")
    blocked, _ = listed(booking_result="blocked")
    for other in ("released", "cancelled", "shortlist", "offer", "vanished", "dismissed"):
        assert other not in booked and other not in blocked


def test_no_booking_result_changes_nothing(production_shapes):
    everything, total = listed()
    for value in ("", "   ", "anything-else"):
        assert listed(booking_result=value) == (everything, total)
    assert total == 10  # every visible alert, the vanished claim included


def test_it_combines_with_candidate_alert_type_and_search(production_shapes):
    assert listed(booking_result="booked", candidate_id="c2")[0] == ["resched"]
    assert listed(booking_result="blocked", candidate_id="c1")[0] == ["blocked"]
    assert listed(booking_result="blocked", classification_group="interview")[0] == ["blocked", "failed"]
    # Selection alerts are never booking outcomes.
    assert listed(booking_result="booked", classification_group="selection")[0] == []
    assert listed(booking_result="booked", search="Java")[0] == ["auto"]
    assert listed(booking_result="blocked", search="Java")[0] == ["blocked"]
