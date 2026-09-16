"""The queue must stop re-reading mail it cannot read, and let an interview past.

Real PostgreSQL, required in CI; never production. Tiering and the retry cap are
SQL, so a fake connection would only prove the fake agrees with itself.

Measured on the production queue, 2026-09-16: 2,082 mails waiting, the oldest
from 30 July. 559 of them carried a retry count totalling 4,731 model runs --
one mail had been analysed 43 times -- because `_max_ai_attempts()` existed with
no caller and `schedule_ai_retry` requeued unconditionally. Meanwhile an
assessment invitation sat in the history tier behind all of it and was never
read at all.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from urllib.parse import urlparse
import uuid

import pytest

from core import recruitment_mail_store as store

NOW = datetime.now(timezone.utc)


def table(migration: str, name: str) -> str:
    ddl = (Path(__file__).resolve().parents[1] / "core" / "migrations" / migration).read_text()
    start = ddl.index(f"CREATE TABLE IF NOT EXISTS {name}")
    depth = 0
    for index in range(start, len(ddl)):
        depth += (ddl[index] == "(") - (ddl[index] == ")")
        if depth == 0 and ddl[index] == ")":
            return ddl[start:index + 1] + ";"
    raise AssertionError(f"{name} is never closed in {migration}")


@pytest.fixture
def queue_db(monkeypatch):
    url = os.getenv("AUDIT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL tests run in CI")
    target = urlparse(url)
    assert target.hostname in {"localhost", "127.0.0.1"} and target.path == "/business_ci"
    import psycopg2
    from psycopg2 import sql
    schema = "queue_test_" + uuid.uuid4().hex

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
            for name in ("candidate_mailboxes", "mailbox_messages",
                         "mailbox_attachments", "mailbox_attachment_cache"):
                cur.execute(table("001_recruitment_mail_tracking.sql", name))
            for column, kind in (("ai_retry_after", "timestamptz"), ("ai_retry_count", "integer NOT NULL DEFAULT 0"),
                                 ("ai_lease_expires_at", "timestamptz"), ("ai_last_error_code", "text"),
                                 ("body_text", "text"), ("html_body_text", "text"),
                                 ("ignore_reason", "text"), ("message_direction", "text"),
                                 ("gmail_label_ids", "jsonb"), ("to_metadata", "jsonb"),
                                 ("authentication_results", "text"), ("received_spf", "text"),
                                 ("rfc_message_id", "text")):
                cur.execute(f"ALTER TABLE mailbox_messages ADD COLUMN IF NOT EXISTS {column} {kind}")
            cur.execute("""INSERT INTO candidate_mailboxes(id,candidate_id,provider,email_address,connection_type)
              VALUES('mailbox-1','candidate-1','gmail','candidate@test.invalid','OAUTH')""")
        monkeypatch.setattr(store, "get_connection", connect)
        yield connect
    finally:
        assert schema.startswith("queue_test_") and len(schema) == 43
        with psycopg2.connect(url) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def queue(connect, message_id, *, subject, sender="recruiter@valuelabs.com", days_old=0.0,
          retries=0, status="AI_QUEUED"):
    arrived = NOW - timedelta(days=days_old)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO mailbox_messages(id,mailbox_id,candidate_id,provider_message_id,
              provider_thread_id,sender_email,subject,sent_at,created_at,processing_status,ai_retry_count)
            VALUES(%s,'mailbox-1','candidate-1',%s,%s,%s,%s,%s,%s,%s,%s)""",
            (message_id, message_id, message_id, sender, subject, arrived, arrived, status, retries))
    return message_id


def row(connect, message_id):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mailbox_messages WHERE id=%s", (message_id,))
        return store._rows(cur)[0]


def tier_of(connect, message_id):
    params = (store._ingest_freshness_days(), store._time_critical_days(), store._ingest_freshness_days(),
              store._live_mail_window_hours(), store._ingest_freshness_days(),
              store._QUEUE_TIMEZONE, store._QUEUE_TIMEZONE)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {store._TIER_SQL} FROM mailbox_messages WHERE id=%s", params + (message_id,))
        return cur.fetchone()[0]


class TestTheRetryCapIsEnforced:
    def test_the_last_allowed_failure_parks_the_mail(self, queue_db):
        queue(queue_db, "nearly-done", subject="Interview scheduled", retries=store._max_ai_attempts() - 1)

        store.schedule_ai_retry("nearly-done", succeeded=False)

        parked = row(queue_db, "nearly-done")
        assert parked["processing_status"] == "AI_PROCESSING_FAILED"
        assert parked["ai_last_error_code"] == "AI_ATTEMPTS_EXHAUSTED"
        assert parked["ai_retry_after"] is None
        assert parked["ai_retry_count"] == store._max_ai_attempts()

    def test_an_early_failure_still_retries_with_backoff(self, queue_db):
        queue(queue_db, "young", subject="Interview scheduled", retries=2)

        store.schedule_ai_retry("young", succeeded=False)

        retried = row(queue_db, "young")
        assert retried["processing_status"] == "AI_RETRY_PENDING"
        assert retried["ai_retry_count"] == 3
        assert retried["ai_retry_after"] is not None

    def test_success_clears_the_retry_state(self, queue_db):
        queue(queue_db, "ok", subject="Interview scheduled", retries=4)

        store.schedule_ai_retry("ok", succeeded=True)

        done = row(queue_db, "ok")
        assert done["ai_retry_after"] is None and done["ai_last_error_code"] is None

    def test_the_cap_is_configurable_and_bounded(self, monkeypatch):
        monkeypatch.setenv("AI_MAIL_MAX_AI_ATTEMPTS", "6")
        assert store._max_ai_attempts() == 6
        for raw, expected in (("1", 3), ("0", 3), ("999", 50), ("junk", 12), ("", 12)):
            monkeypatch.setenv("AI_MAIL_MAX_AI_ATTEMPTS", raw)
            assert store._max_ai_attempts() == expected


class TestMailAlreadyPastTheCap:
    def test_it_is_parked_without_another_model_run(self, queue_db):
        queue(queue_db, "exhausted", subject="Interview scheduled", retries=store._max_ai_attempts() + 8)

        claimed = store.claim_ai_messages(limit=5)

        assert [item["id"] for item in claimed] == []
        parked = row(queue_db, "exhausted")
        assert parked["processing_status"] == "AI_PROCESSING_FAILED"
        assert parked["ai_last_error_code"] == "AI_ATTEMPTS_EXHAUSTED"

    def test_mail_under_the_cap_is_untouched_by_the_sweep(self, queue_db):
        queue(queue_db, "still-trying", subject="Interview scheduled",
              retries=store._max_ai_attempts() - 1, status="AI_RETRY_PENDING")

        claimed = store.claim_ai_messages(limit=5)

        assert [item["id"] for item in claimed] == ["still-trying"]

    def test_parking_frees_the_turn_for_live_mail(self, queue_db):
        for index in range(3):
            queue(queue_db, f"dead-{index}", subject="Interview scheduled", days_old=20,
                  retries=store._max_ai_attempts() + 1)
        queue(queue_db, "live", subject="Interview scheduled with ValueLabs", days_old=0.01)

        claimed = store.claim_ai_messages(limit=1)

        assert [item["id"] for item in claimed] == ["live"]


class TestFreshInterviewsAndAssessmentsLead:
    def test_an_interview_that_just_arrived_is_tier_zero(self, queue_db):
        queue(queue_db, "interview", subject="Round 1 Interview | Full Stack Developer", days_old=1)
        assert tier_of(queue_db, "interview") == 0

    def test_an_assessment_is_tier_zero_too(self, queue_db):
        queue(queue_db, "assessment", subject="Intelliswift invitation for assessment",
              sender="assistant@glider.ai", days_old=1)
        assert tier_of(queue_db, "assessment") == 0

    def test_a_job_board_blast_naming_an_interview_is_not(self, queue_db):
        queue(queue_db, "blast", subject="✉️ Job | F2F Interview on Sep 19 For Sr. Engineer",
              sender="recruiter@naukri.com", days_old=0.01)
        assert tier_of(queue_db, "blast") != 0

    def test_an_old_interview_does_not_jump_the_queue(self, queue_db):
        queue(queue_db, "stale", subject="Interview scheduled", days_old=30)
        assert tier_of(queue_db, "stale") == 3

    def test_ordinary_mail_keeps_its_own_tier(self, queue_db):
        queue(queue_db, "ordinary", subject="Thank you for applying", days_old=0.01)
        assert tier_of(queue_db, "ordinary") == 1

    def test_it_is_claimed_ahead_of_a_month_of_backlog(self, queue_db):
        for index in range(5):
            queue(queue_db, f"old-{index}", subject="Your application update", days_old=20 + index)
        queue(queue_db, "today-noise", subject="Top IT jobs for you", sender="info@hirist.tech", days_old=0.02)
        queue(queue_db, "assessment", subject="Intelliswift invitation for assessment",
              sender="assistant@glider.ai", days_old=1)

        claimed = store.claim_ai_messages(limit=1)

        assert [item["id"] for item in claimed] == ["assessment"]

    def test_even_the_backlog_turn_yields_to_it(self, queue_db, monkeypatch):
        monkeypatch.setattr(store, "_claim_prefers_backlog", lambda: True)
        queue(queue_db, "ancient", subject="Your application update", days_old=40)
        queue(queue_db, "interview", subject="Interview scheduled with ValueLabs", days_old=0.5)

        claimed = store.claim_ai_messages(limit=1)

        assert [item["id"] for item in claimed] == ["interview"]

    def test_a_backlog_turn_still_drains_history_when_nothing_is_urgent(self, queue_db, monkeypatch):
        monkeypatch.setattr(store, "_claim_prefers_backlog", lambda: True)
        queue(queue_db, "ancient", subject="Your application update", days_old=40)
        queue(queue_db, "today", subject="Thank you for applying", days_old=0.02)

        claimed = store.claim_ai_messages(limit=1)

        assert [item["id"] for item in claimed] == ["ancient"]

    def test_fifo_still_decides_between_two_urgent_mails(self, queue_db):
        queue(queue_db, "earlier", subject="Interview scheduled", days_old=2)
        queue(queue_db, "later", subject="Assessment invitation", days_old=1)

        claimed = store.claim_ai_messages(limit=2)

        assert [item["id"] for item in claimed] == ["earlier", "later"]
