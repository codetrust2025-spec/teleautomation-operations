"""A verdict about the mail is not a failure to read it, and must not be retried like one.

Real PostgreSQL, required in CI; never production. The budget is a CASE inside
the retry UPDATE and a second sweep inside the claim, so a fake connection would
only prove the fake agrees with itself.

Measured on the production queue, 2026-09-17: 408 messages carried
EVIDENCE_DOES_NOT_ENTAIL_TRANSITION at an average of 5.7 attempts and exactly
one had ever come back from it; 156 carried RECRUITMENT_RELEVANCE_UNRESOLVED,
average 6.4, and none had. About 3,200 model runs spent and thousands more
scheduled, at a recovery rate of one in 565, against a pipeline that manages
roughly 900 runs a day.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from urllib.parse import urlparse
import uuid

import pytest

from core import recruitment_mail_store as store

NOW = datetime.now(timezone.utc)
EVIDENCE = "EVIDENCE_DOES_NOT_ENTAIL_TRANSITION"
RELEVANCE = "RECRUITMENT_RELEVANCE_UNRESOLVED"
TRANSIENT = "OLLAMA_QUEUE_TIMEOUT"


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
    schema = "verdict_test_" + uuid.uuid4().hex

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
            # The claim joins attachments, so the schema has to carry the same
            # four tables the queue tests build; two of them is a fixture that
            # passes every test that never claims.
            for name in ("candidate_mailboxes", "mailbox_messages",
                         "mailbox_attachments", "mailbox_attachment_cache"):
                cur.execute(table("001_recruitment_mail_tracking.sql", name))
            for column, kind in (("ai_retry_after", "timestamptz"),
                                 ("ai_retry_count", "integer NOT NULL DEFAULT 0"),
                                 ("ai_lease_expires_at", "timestamptz"),
                                 ("ai_last_error_code", "text"),
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
        assert schema.startswith("verdict_test_") and len(schema) == len("verdict_test_") + 32
        with psycopg2.connect(url) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def queue(connect, message_id, *, code=None, retries=0, status="AI_RETRY_PENDING"):
    arrived = NOW - timedelta(minutes=5)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO mailbox_messages(id,mailbox_id,candidate_id,provider_message_id,
                 provider_thread_id,sender_email,subject,sent_at,created_at,
                 processing_status,ai_retry_count,ai_last_error_code)
               VALUES(%s,'mailbox-1','candidate-1',%s,%s,'recruiter@example.com',
                      'Interview scheduled',%s,%s,%s,%s,%s)""",
            (message_id, message_id, message_id, arrived, arrived, status, retries, code),
        )


def row(connect, message_id):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT processing_status, ai_retry_count, ai_last_error_code, ai_retry_after
                 FROM mailbox_messages WHERE id=%s""", (message_id,))
        status, retries, code, retry_after = cur.fetchone()
    return {"status": status, "retries": retries, "code": code, "retry_after": retry_after}


class TestAVerdictStopsEarly:
    @pytest.mark.parametrize("code", [EVIDENCE, RELEVANCE])
    def test_a_second_attempt_is_the_last_one(self, queue_db, code):
        queue(queue_db, "m1", code=code, retries=1)

        store.schedule_ai_retry("m1", succeeded=False)

        parked = row(queue_db, "m1")
        assert parked["status"] == "AI_PROCESSING_FAILED"
        assert parked["retry_after"] is None

    @pytest.mark.parametrize("code", [EVIDENCE, RELEVANCE])
    def test_the_first_failure_still_gets_its_second_look(self, queue_db, code):
        """A node answers deterministically for one model load; a different node
        can legitimately differ, so one retry is kept."""
        queue(queue_db, "m2", code=code, retries=0)

        store.schedule_ai_retry("m2", succeeded=False)

        assert row(queue_db, "m2")["status"] == "AI_RETRY_PENDING"

    def test_parking_keeps_the_reason_rather_than_replacing_it(self, queue_db):
        """"The evidence did not entail the transition" is what makes the parked
        set readable; AI_ATTEMPTS_EXHAUSTED would say only that it gave up."""
        queue(queue_db, "m3", code=EVIDENCE, retries=1)

        store.schedule_ai_retry("m3", succeeded=False)

        assert row(queue_db, "m3")["code"] == EVIDENCE


class TestATransientFailureIsUnchanged:
    def test_it_keeps_the_full_budget(self, queue_db):
        queue(queue_db, "m4", code=TRANSIENT, retries=5)

        store.schedule_ai_retry("m4", succeeded=False)

        pending = row(queue_db, "m4")
        assert pending["status"] == "AI_RETRY_PENDING"
        assert pending["retry_after"] is not None

    def test_it_still_parks_at_the_cap_as_exhausted(self, queue_db):
        queue(queue_db, "m5", code=TRANSIENT, retries=store._max_ai_attempts() - 1)

        store.schedule_ai_retry("m5", succeeded=False)

        parked = row(queue_db, "m5")
        assert parked["status"] == "AI_PROCESSING_FAILED"
        assert parked["code"] == "AI_ATTEMPTS_EXHAUSTED"

    def test_a_message_with_no_code_is_treated_as_transient(self, queue_db):
        queue(queue_db, "m6", code=None, retries=2)

        store.schedule_ai_retry("m6", succeeded=False)

        assert row(queue_db, "m6")["status"] == "AI_RETRY_PENDING"

    def test_success_still_clears_everything(self, queue_db):
        queue(queue_db, "m7", code=EVIDENCE, retries=1)

        store.schedule_ai_retry("m7", succeeded=True)

        cleared = row(queue_db, "m7")
        assert cleared["code"] is None and cleared["retry_after"] is None


class TestTheBacklogIsNotMadeToLearnItAgain:
    def test_claiming_parks_what_is_already_past_the_smaller_budget(self, queue_db):
        """Otherwise each of the 564 already over it spends one more model run
        to be told what it was told the first two times."""
        queue(queue_db, "m8", code=EVIDENCE, retries=7, status="AI_QUEUED")

        store.claim_ai_messages(limit=1)

        parked = row(queue_db, "m8")
        assert parked["status"] == "AI_PROCESSING_FAILED"
        assert parked["code"] == EVIDENCE

    def test_it_does_not_park_a_transient_failure_of_the_same_age(self, queue_db):
        queue(queue_db, "m9", code=TRANSIENT, retries=7, status="AI_QUEUED")

        store.claim_ai_messages(limit=1)

        assert row(queue_db, "m9")["status"] in {"AI_RUNNING", "AI_QUEUED"}

    def test_a_verdict_within_budget_is_still_claimable(self, queue_db):
        queue(queue_db, "m10", code=EVIDENCE, retries=1, status="AI_QUEUED")

        claimed = store.claim_ai_messages(limit=1)

        assert [message["id"] for message in claimed] == ["m10"]
        assert row(queue_db, "m10")["status"] == "AI_RUNNING"


def test_the_budgets_are_what_the_measurements_said():
    assert store.DETERMINISTIC_AI_FAILURE_CODES == {EVIDENCE, RELEVANCE}
    assert store._deterministic_ai_attempts() == 2
    assert store._max_ai_attempts() == 12
