"""A re-invitation after a cancellation is a new interview, not a duplicate.

Real PostgreSQL, required in CI; never production. The thread guard is pure SQL,
so a fake connection would only prove the fake agrees with itself.

Production, 15 Sep 2026: an invitation was auto-booked at 20:29, cancelled at
20:31, and re-sent for a different time at 20:36. Google threaded the
re-invitation with the original and gave the cancellation a thread of its own,
so the guard saw a second INTERVIEW_CONFIRMED in one thread and ignored it. The
candidate went to the interview with no slot recorded.
"""
from contextlib import contextmanager
import os
from pathlib import Path
from urllib.parse import urlparse
import uuid

import pytest

from core import recruitment_mail_store as store

CANDIDATE = "candidate-1"
THREAD = "thread-invite"
CONFIRMED = "INTERVIEW_CONFIRMED"


def table(migration: str, name: str) -> str:
    """The real CREATE TABLE, however the migration happens to be laid out."""
    ddl = (Path(__file__).resolve().parents[1] / "core" / "migrations" / migration).read_text()
    start = ddl.index(f"CREATE TABLE IF NOT EXISTS {name}")
    depth = 0
    for index in range(start, len(ddl)):
        depth += (ddl[index] == "(") - (ddl[index] == ")")
        if depth == 0 and ddl[index] == ")":
            return ddl[start:index + 1] + ";"
    raise AssertionError(f"{name} is never closed in {migration}")


@pytest.fixture
def thread_db(monkeypatch):
    url = os.getenv("AUDIT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL tests run in CI")
    target = urlparse(url)
    assert target.hostname in {"localhost", "127.0.0.1"} and target.path == "/business_ci"
    import psycopg2
    from psycopg2 import sql
    schema = "thread_test_" + uuid.uuid4().hex

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
            for name in ("candidate_mailboxes", "mailbox_messages", "ai_recruitment_events"):
                cur.execute(table("001_recruitment_mail_tracking.sql", name))
            cur.execute("CREATE TABLE interview_mail_analyses(id text PRIMARY KEY)")
            cur.execute(table("008_recruitment_mail_auto_booking.sql", "interview_auto_booking_audit"))
            cur.execute("""INSERT INTO candidate_mailboxes(id,candidate_id,provider,email_address,connection_type)
              VALUES('mailbox-1',%s,'gmail','candidate@test.invalid','OAUTH')""", (CANDIDATE,))
        monkeypatch.setattr(store, "get_connection", connect)
        yield connect
    finally:
        assert schema.startswith("thread_test_") and len(schema) == 44
        with psycopg2.connect(url) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def mail(connect, message_id: str, provider_id: str, *, thread: str | None = THREAD) -> str:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO mailbox_messages(id,mailbox_id,candidate_id,provider_message_id,provider_thread_id,subject)
          VALUES(%s,'mailbox-1',%s,%s,%s,'Round 1 Interview')""", (message_id, CANDIDATE, provider_id, thread))
    return message_id


def event(connect, message_id: str, *, status: str = CONFIRMED, review_status: str = "AUTOMATED") -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO ai_recruitment_events(id,candidate_id,mailbox_message_id,primary_status,confidence,structured_result,review_status)
          VALUES(%s,%s,%s,%s,0.9,'{}'::jsonb,%s)""", ("event-" + message_id, CANDIDATE, message_id, status, review_status))


def booking_audit(connect, provider_id: str, *, booking_id: str, status: str, minute: int) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO interview_auto_booking_audit
          (id,booking_id,gmail_message_id,candidate_id,classification,auto_booked,validation_status,booking_status,created_at)
          VALUES(%s,%s,%s,%s,%s,true,'PASSED',%s,%s)""",
          (f"audit-{provider_id}-{status}", booking_id, provider_id, CANDIDATE,
           "interview_cancelled" if status == "Cancelled" else "interview_confirmed", status,
           f"2026-09-15 20:{minute:02d}:00+05:30"))


def test_reinvitation_after_a_cancelled_booking_is_not_a_duplicate(thread_db):
    mail(thread_db, "invitation", "gmail-invitation")
    event(thread_db, "invitation")
    booking_audit(thread_db, "gmail-invitation", booking_id="slot-1", status="Auto Booked", minute=29)
    booking_audit(thread_db, "gmail-cancellation", booking_id="slot-1", status="Cancelled", minute=31)
    mail(thread_db, "reinvitation", "gmail-reinvitation")

    assert store.is_duplicate_thread_status(CANDIDATE, "reinvitation", CONFIRMED) is False


def test_a_reminder_for_a_booking_that_still_stands_is_still_suppressed(thread_db):
    mail(thread_db, "invitation", "gmail-invitation")
    event(thread_db, "invitation")
    booking_audit(thread_db, "gmail-invitation", booking_id="slot-1", status="Auto Booked", minute=29)
    mail(thread_db, "reminder", "gmail-reminder")

    assert store.is_duplicate_thread_status(CANDIDATE, "reminder", CONFIRMED) is True


def test_a_cancellation_of_a_different_booking_does_not_unsuppress(thread_db):
    """Only the booking this thread produced matters; another one is noise."""
    mail(thread_db, "invitation", "gmail-invitation")
    event(thread_db, "invitation")
    booking_audit(thread_db, "gmail-invitation", booking_id="slot-1", status="Auto Booked", minute=29)
    booking_audit(thread_db, "other-invitation", booking_id="slot-2", status="Auto Booked", minute=10)
    booking_audit(thread_db, "other-cancellation", booking_id="slot-2", status="Cancelled", minute=15)
    mail(thread_db, "reminder", "gmail-reminder")

    assert store.is_duplicate_thread_status(CANDIDATE, "reminder", CONFIRMED) is True


def test_a_cancellation_before_the_booking_does_not_unsuppress(thread_db):
    """A slot id reused after an earlier cancellation is not this one cancelled."""
    mail(thread_db, "invitation", "gmail-invitation")
    event(thread_db, "invitation")
    booking_audit(thread_db, "earlier-cancellation", booking_id="slot-1", status="Cancelled", minute=10)
    booking_audit(thread_db, "gmail-invitation", booking_id="slot-1", status="Auto Booked", minute=29)
    mail(thread_db, "reminder", "gmail-reminder")

    assert store.is_duplicate_thread_status(CANDIDATE, "reminder", CONFIRMED) is True


def test_an_offer_thread_without_any_booking_is_unaffected(thread_db):
    mail(thread_db, "offer", "gmail-offer")
    event(thread_db, "offer", status="OFFER_LETTER_RECEIVED")
    mail(thread_db, "offer-reminder", "gmail-offer-reminder")

    assert store.is_duplicate_thread_status(CANDIDATE, "offer-reminder", "OFFER_LETTER_RECEIVED") is True
