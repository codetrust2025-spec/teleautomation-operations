"""The re-read priority CASE on real PostgreSQL: only a retry flag gives way,
and only to a settled read. CI only; never production."""
from __future__ import annotations

import os
from contextlib import contextmanager
from urllib.parse import urlparse
import uuid

import pytest

from core import recruitment_mail_store as store


# ── the CASE itself, on real PostgreSQL (CI; never production) ─────────────

@pytest.fixture
def pg():
    url = os.getenv("AUDIT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL tests run in CI")
    target = urlparse(url)
    assert target.hostname in {"localhost", "127.0.0.1"} and target.path == "/business_ci"
    import psycopg2
    from psycopg2 import sql
    schema = "reread_test_" + uuid.uuid4().hex
    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        cur.execute("CREATE TABLE mail_monitoring_notifications (id text PRIMARY KEY, priority text)")

    @contextmanager
    def cursor():
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
                yield cur
            conn.commit()
        finally:
            conn.close()
    try:
        yield cursor
    finally:
        assert schema.startswith("reread_test_")
        with psycopg2.connect(url) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.parametrize(("before", "new", "after"), [
    ("review_required", "high", "high"),
    ("retry_pending", "medium", "medium"),
    ("retry_pending", "retry_pending", "retry_pending"),
    ("high", "retry_pending", "high"),
    ("low", "high", "low"),
])
def test_only_a_retry_flag_gives_way_and_only_to_a_settled_read(pg, before, new, after):
    with pg() as cur:
        cur.execute("INSERT INTO mail_monitoring_notifications VALUES('n1',%s)", (before,))
        cur.execute("UPDATE mail_monitoring_notifications SET priority=" + store._REREAD_PRIORITY_SQL
                    + " WHERE id='n1' RETURNING priority", (new, new))
        assert cur.fetchone()[0] == after
