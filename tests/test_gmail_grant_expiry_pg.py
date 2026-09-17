"""When a Gmail grant dies is the server's answer, not the browser's.

The dashboard used to compute it: `authorized_at` plus seven days, in
JavaScript, against the viewer's clock. Two machines with different clocks
disagreed about whether a mailbox had hours left, and the label only changed on
reload. The countdown now counts down to a timestamp the server sends, so this
has to prove the server sends it -- and that it is null, not "now", for a
mailbox that has never been authorised through a route the audit log recorded.

Real PostgreSQL, required in CI; never production. The expiry is an interval in
SQL, so a fake connection would only prove the fake agrees with itself.
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
def grant_db(monkeypatch):
    url = os.getenv("AUDIT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL tests run in CI")
    target = urlparse(url)
    assert target.hostname in {"localhost", "127.0.0.1"} and target.path == "/business_ci"
    import psycopg2
    from psycopg2 import sql
    schema = "grant_test_" + uuid.uuid4().hex

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
            cur.execute(table("001_recruitment_mail_tracking.sql", "candidate_mailboxes"))
            cur.execute(table("001_recruitment_mail_tracking.sql", "recruitment_audit_log"))
            cur.execute(table("010_recruitment_mail_lifecycle_truth.sql", "candidate_identity_links"))
        # Stage lives in the candidate store, which this test is not about.
        from features import candidate_store
        monkeypatch.setattr(candidate_store, "list_candidates", lambda **_kwargs: [])
        monkeypatch.setattr(store, "get_connection", connect)
        yield connect
    finally:
        assert schema.startswith("grant_test_") and len(schema) == 43
        with psycopg2.connect(url) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def mailbox(connect, mailbox_id, *, authorised_days_ago=None, email="candidate@example.com"):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO candidate_mailboxes(id,candidate_id,provider,email_address,
                 connection_type,credential_ciphertext,connection_status)
               VALUES(%s,%s,'gmail',%s,'OAUTH','cipher','CONNECTED')""",
            (mailbox_id, f"candidate-{mailbox_id}", email),
        )
        if authorised_days_ago is not None:
            cur.execute(
                """INSERT INTO recruitment_audit_log(id,actor,role,action,source_id,created_at)
                   VALUES(%s,'admin','admin','MAILBOX_CONNECTED',%s,%s)""",
                (f"audit-{mailbox_id}", mailbox_id,
                 NOW - timedelta(days=authorised_days_ago)),
            )


def row_for(mailbox_id):
    return next(r for r in store.mailbox_health_rows() if r["id"] == mailbox_id)


class TestTheServerDecidesWhenItDies:
    def test_expiry_is_the_authorisation_plus_the_grant_length(self, grant_db):
        mailbox(grant_db, "mb-1", authorised_days_ago=2)

        row = row_for("mb-1")

        assert row["grant_expires_at"] == row["authorized_at"] + timedelta(
            days=store.GMAIL_GRANT_DAYS)

    def test_a_grant_more_than_seven_days_old_is_already_past(self, grant_db):
        mailbox(grant_db, "mb-2", authorised_days_ago=9)

        assert row_for("mb-2")["grant_expires_at"] < NOW

    def test_a_fresh_grant_has_nearly_the_whole_week_left(self, grant_db):
        mailbox(grant_db, "mb-3", authorised_days_ago=0)

        remaining = row_for("mb-3")["grant_expires_at"] - NOW
        assert timedelta(days=6, hours=23) < remaining <= timedelta(days=7)

    def test_it_is_timezone_aware_so_the_browser_reads_it_unambiguously(self, grant_db):
        mailbox(grant_db, "mb-4", authorised_days_ago=1)

        assert row_for("mb-4")["grant_expires_at"].tzinfo is not None


class TestWhatItWillNotInvent:
    def test_a_mailbox_never_authorised_has_no_expiry_rather_than_now(self, grant_db):
        """A null here makes the dashboard say "unknown". A zero would make it
        say "Expired" about a mailbox that is working."""
        mailbox(grant_db, "mb-5", authorised_days_ago=None)

        assert row_for("mb-5")["grant_expires_at"] is None

    def test_the_latest_authorisation_wins_after_a_reconnect(self, grant_db):
        mailbox(grant_db, "mb-6", authorised_days_ago=6)
        with grant_db() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO recruitment_audit_log(id,actor,role,action,source_id,created_at)
                   VALUES('audit-mb-6-again','admin','admin','MAILBOX_CONNECTED','mb-6',%s)""",
                (NOW - timedelta(hours=1),),
            )

        remaining = row_for("mb-6")["grant_expires_at"] - NOW
        assert remaining > timedelta(days=6)


class TestItCostsTheOverviewNothing:
    def test_the_expiry_adds_no_query(self, grant_db):
        """The overview is polled while syncs run and once starved the API
        worker, so the expiry has to ride the query that was already there."""
        import inspect

        source = inspect.getsource(store._mailbox_health_rows)
        assert source.count("cur.execute") == 1
        assert "grant_expires_at" in source
