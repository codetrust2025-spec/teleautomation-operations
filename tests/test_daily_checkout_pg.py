"""One check-out per day, and the first one is the one that counts.

Real PostgreSQL, required in CI; never production. Idempotency here is a
`COALESCE` in an UPDATE against a row the schema already keeps unique per
account per day -- a fake connection would only prove the fake agrees with
itself, and the thing worth proving is that a second press cannot move the
recorded end of someone's working day later.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import os
from pathlib import Path
from urllib.parse import urlparse
import uuid

import pytest

from core.office_network import NetworkVerification
from features import attendance_eligibility as attendance
from features import daily_checkout as checkout

ALLOWED = NetworkVerification(
    allowed=True, source="direct", policy_id="office", reason="OFFICE_NETWORK",
    observed_ip="203.0.113.10")
HANDLER = {"username": "handler-one", "role": "handler", "reference": "Handler One",
           "display_name": "Handler One", "session_id_hash": "hash"}


@pytest.fixture
def checkout_db(monkeypatch):
    url = os.getenv("AUDIT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL tests run in CI")
    target = urlparse(url)
    assert target.hostname in {"localhost", "127.0.0.1"} and target.path == "/business_ci"
    import psycopg2
    from psycopg2 import sql
    schema = "checkout_test_" + uuid.uuid4().hex

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
        root = Path(__file__).resolve().parents[1] / "core" / "migrations"
        with connect() as conn, conn.cursor() as cur:
            for name in ("028_attendance_earnings_eligibility.sql", "036_daily_checkout.sql"):
                cur.execute((root / name).read_text(encoding="utf-8"))
        monkeypatch.setattr(attendance, "get_connection", connect)
        monkeypatch.setattr(checkout, "get_connection", connect)
        monkeypatch.setattr(attendance, "_require_database", lambda: None)
        # The pipeline is not what this file is about.
        from features import candidate_store
        monkeypatch.setattr(candidate_store, "_interview_rows_for_range", lambda *_a, **_k: [])
        monkeypatch.setattr(candidate_store, "_split_pending_interviews_by_slot_phase",
                            lambda rows: ([], []))
        monkeypatch.setattr(candidate_store, "pending_works", lambda **_k: {"works": []})
        yield connect
    finally:
        assert schema.startswith("checkout_test_") and len(schema) == 46
        with psycopg2.connect(url) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def mark(connect, when: datetime):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO operations_attendance_records(
                   id, account_id, username, display_name, attendance_date,
                   marked_at, status, office_network_verified)
               VALUES(%s,'handler:handler-one','handler-one','Handler One',%s,%s,'VERIFIED',TRUE)
               ON CONFLICT(account_id, attendance_date) DO NOTHING""",
            (str(uuid.uuid4()), when.date(), when),
        )


def stored(connect, when: datetime):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT checked_out_at, checkout_network_verification, checkout_metadata
                 FROM operations_attendance_records
                WHERE account_id='handler:handler-one' AND attendance_date=%s""",
            (when.date(),),
        )
        return cur.fetchone()


EVENING = attendance._now().replace(hour=19, minute=5, second=0, microsecond=0)


class TestTheFirstCheckOutIsTheOne:
    def test_it_records_the_moment_the_day_ended(self, checkout_db):
        mark(checkout_db, EVENING)

        result = checkout.check_out(HANDLER, ALLOWED, now=EVENING)

        assert result["status"] == "checked_out"
        assert stored(checkout_db, EVENING)[0] == EVENING

    def test_a_second_press_changes_nothing(self, checkout_db):
        mark(checkout_db, EVENING)
        checkout.check_out(HANDLER, ALLOWED, now=EVENING)

        later = checkout.check_out(HANDLER, ALLOWED, now=EVENING + timedelta(minutes=40))

        assert later["status"] == "already_checked_out"
        assert stored(checkout_db, EVENING)[0] == EVENING

    def test_the_network_verification_is_kept_with_it(self, checkout_db):
        """Same evidence attendance keeps for marking, for the same reason."""
        mark(checkout_db, EVENING)
        checkout.check_out(HANDLER, ALLOWED, now=EVENING)

        _, network, metadata = stored(checkout_db, EVENING)
        assert network["verified"] is True
        assert network["observed_ip"] == "203.0.113.10"
        assert metadata["business_timezone"] == attendance.APP_TIMEZONE

    def test_the_next_day_is_its_own_check_out(self, checkout_db):
        mark(checkout_db, EVENING)
        checkout.check_out(HANDLER, ALLOWED, now=EVENING)
        tomorrow = EVENING + timedelta(days=1)
        mark(checkout_db, tomorrow)

        result = checkout.check_out(HANDLER, ALLOWED, now=tomorrow)

        assert result["status"] == "checked_out"
        assert stored(checkout_db, tomorrow)[0] == tomorrow


class TestItRefusesRatherThanRecordingAHalfDay:
    def test_a_day_never_marked_cannot_be_checked_out(self, checkout_db):
        with pytest.raises(checkout.CheckoutBlocked) as raised:
            checkout.check_out(HANDLER, ALLOWED, now=EVENING)

        assert checkout.ATTENDANCE_NOT_MARKED in [b["kind"] for b in raised.value.blockers]
        assert stored(checkout_db, EVENING) is None

    def test_an_open_interview_leaves_nothing_written(self, checkout_db, monkeypatch):
        from features import candidate_store

        mark(checkout_db, EVENING)
        monkeypatch.setattr(candidate_store, "_split_pending_interviews_by_slot_phase",
                            lambda rows: ([{"id": "c", "name": "Sample"}], []))

        with pytest.raises(checkout.CheckoutBlocked):
            checkout.check_out(HANDLER, ALLOWED, now=EVENING)

        assert stored(checkout_db, EVENING)[0] is None


class TestReadingItWithoutDoingIt:
    def test_status_never_checks_anyone_out(self, checkout_db):
        """The panel polls this every minute; a refresh must not end a day."""
        mark(checkout_db, EVENING)

        reading = checkout.status(HANDLER, ALLOWED, now=EVENING)

        assert reading["can_check_out"] is True
        assert reading["checked_out"] is False
        assert stored(checkout_db, EVENING)[0] is None

    def test_status_reports_the_recorded_time_afterwards(self, checkout_db):
        mark(checkout_db, EVENING)
        checkout.check_out(HANDLER, ALLOWED, now=EVENING)

        reading = checkout.status(HANDLER, ALLOWED, now=EVENING + timedelta(hours=1))

        assert reading["checked_out"] is True
        assert str(EVENING.date()) in str(reading["checked_out_at"])
        assert reading["blockers"] == []

    def test_status_lists_what_is_in_the_way(self, checkout_db):
        reading = checkout.status(HANDLER, ALLOWED, now=EVENING)

        assert reading["can_check_out"] is False
        assert [b["kind"] for b in reading["blockers"]] == [checkout.ATTENDANCE_NOT_MARKED]
