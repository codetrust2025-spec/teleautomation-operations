"""The database refuses a second automatic booking of one interview (real PostgreSQL, CI only)."""
from contextlib import contextmanager
import os
from pathlib import Path
from urllib.parse import urlparse
import uuid

import pytest

from core import recruitment_mail_store as store

MIGRATION = Path(__file__).resolve().parents[1] / "core" / "migrations" / "039_interview_booking_claims.sql"


@pytest.fixture
def claims_db(monkeypatch):
    url = os.getenv("AUDIT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL tests run in CI")
    target = urlparse(url)
    assert target.hostname in {"localhost", "127.0.0.1"} and target.path == "/business_ci"
    import psycopg2
    from psycopg2 import sql
    schema = "claims_test_" + uuid.uuid4().hex

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
            cur.execute(MIGRATION.read_text())
            cur.execute(MIGRATION.read_text())  # additive and re-runnable
        monkeypatch.setattr(store, "get_connection", connect)
        monkeypatch.setattr(store, "use_postgres", lambda: True)
        yield connect
    finally:
        assert schema.startswith("claims_test_") and len(schema) == 44
        with psycopg2.connect(url) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def rows(connect):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT claim_key, source_message_id, booking_id FROM interview_booking_claims ORDER BY claim_key")
        return cur.fetchall()


KEYS = ["mail:c1:invite", "room:c1:2099-11-12:14:00:assess.cantikprime.com|interview-room|token"]


def test_a_standing_booking_holds_its_keys(claims_db):
    store.claim_booking_keys(KEYS, candidate_id="c1", source_message_id="invite", booking_stands=lambda _id: True)
    store.settle_booking_claims(KEYS, booking_id="slot-1")

    with pytest.raises(store.BookingClaimTaken) as taken:
        store.claim_booking_keys(["mail:c1:reminder", KEYS[1]], candidate_id="c1",
                                 source_message_id="reminder", booking_stands=lambda _id: True)

    assert taken.value.booking_id == "slot-1"
    # The refused claim left nothing behind: its own new key was rolled back.
    assert [key for key, *_ in rows(claims_db)] == KEYS


def test_a_key_whose_booking_is_gone_is_taken_over(claims_db):
    store.claim_booking_keys(KEYS, candidate_id="c1", source_message_id="invite", booking_stands=lambda _id: True)
    store.settle_booking_claims(KEYS, booking_id="slot-1")

    store.claim_booking_keys([KEYS[1]], candidate_id="c1", source_message_id="rebooked",
                             booking_stands=lambda _id: False)

    assert rows(claims_db)[1] == (KEYS[1], "rebooked", None)


def test_a_claim_left_by_a_crash_before_the_write_does_not_block(claims_db):
    store.claim_booking_keys(KEYS, candidate_id="c1", source_message_id="invite", booking_stands=lambda _id: True)

    store.claim_booking_keys(KEYS, candidate_id="c1", source_message_id="invite", booking_stands=lambda _id: True)

    assert [booking for *_, booking in rows(claims_db)] == [None, None]


def test_the_key_is_unique_in_the_database_itself(claims_db):
    import psycopg2
    with claims_db() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO interview_booking_claims(claim_key,candidate_id,source_message_id) VALUES('k','c1','m')")
    with pytest.raises(psycopg2.IntegrityError):
        with claims_db() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO interview_booking_claims(claim_key,candidate_id,source_message_id) VALUES('k','c1','m2')")
