"""An alert stays in AI Retry only while its mail can still be retried.

Real PostgreSQL, required in CI; never production. On 12 Nov 2026 seventeen of
the twenty-one alerts in AI Retry were classified at 0.99-1.0 and belonged to
mail that was parked, ignored or never queued: nothing would retry them, so the
tab could never empty.
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from core import recruitment_mail_store as store
from tests.test_deterministic_ai_failures_pg import queue, queue_db, table  # noqa: F401

NOW = datetime.now(timezone.utc)


@pytest.fixture
def alerts_db(queue_db):
    with queue_db() as conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE mail_monitoring_notifications (
            id uuid PRIMARY KEY, gmail_message_id text, classification text,
            priority text, booking_status text, booking_failure_code text, dismissed_at timestamptz,
            updated_at timestamptz)""")
    return queue_db


def alert(connect, message_id, *, classification="interview_cancelled",
          booking_status="Blocked", priority="retry_pending", failure="BOOKING_NOT_FOUND"):
    alert_id = str(uuid.uuid4())
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO mail_monitoring_notifications
            (id,gmail_message_id,classification,priority,booking_status,booking_failure_code,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,now())""",
            (alert_id, message_id, classification, priority, booking_status, failure))
    return alert_id


def priority(connect, alert_id):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT priority FROM mail_monitoring_notifications WHERE id=%s", (alert_id,))
        return cur.fetchone()[0]


def sweep(connect):
    with connect() as conn, conn.cursor() as cur:
        return store.release_parked_retry_flags(cur)


@pytest.mark.parametrize("status", ["AI_PROCESSING_FAILED", "AUTO_IGNORE", "EVENT_CREATED"])
def test_a_blocked_alert_on_finished_mail_leaves_ai_retry(alerts_db, status):
    queue(alerts_db, "m1", status=status, code="AI_ATTEMPTS_EXHAUSTED", retries=12)
    alert_id = alert(alerts_db, "m1")

    assert sweep(alerts_db) == [alert_id]
    assert priority(alerts_db, alert_id) == "high"


@pytest.mark.parametrize("status", ["AI_QUEUED", "AI_RETRY_PENDING", "AI_RUNNING"])
def test_mail_still_being_retried_keeps_its_flag(alerts_db, status):
    queue(alerts_db, "m1", status=status)
    alert_id = alert(alerts_db, "m1")

    assert sweep(alerts_db) == []
    assert priority(alerts_db, alert_id) == "retry_pending"


@pytest.mark.parametrize("classification", ["ai_retry_pending", "needs_review", None])
def test_mail_the_ai_never_classified_keeps_its_flag(alerts_db, classification):
    queue(alerts_db, "m1", status="AI_PROCESSING_FAILED")
    alert_id = alert(alerts_db, "m1", classification=classification)

    assert sweep(alerts_db) == []
    assert priority(alerts_db, alert_id) == "retry_pending"


def test_an_alert_without_a_blocked_booking_is_left_alone(alerts_db):
    queue(alerts_db, "m1", status="AI_PROCESSING_FAILED")
    alert_id = alert(alerts_db, "m1", classification="offer_received", booking_status=None)

    assert sweep(alerts_db) == []
    assert priority(alerts_db, alert_id) == "retry_pending"


def test_a_lower_ranked_classification_becomes_medium(alerts_db):
    queue(alerts_db, "m1", status="AUTO_IGNORE")
    alert_id = alert(alerts_db, "m1", classification="hr_confirmation")

    sweep(alerts_db)

    assert priority(alerts_db, alert_id) == "medium"


def test_the_claim_runs_the_sweep(alerts_db):
    queue(alerts_db, "m1", status="AI_PROCESSING_FAILED")
    alert_id = alert(alerts_db, "m1")

    store.claim_ai_messages(1)

    assert priority(alerts_db, alert_id) == "high"


def test_the_legacy_review_flag_is_released_too(alerts_db):
    queue(alerts_db, "m1", status="AUTO_IGNORE")
    alert_id = alert(alerts_db, "m1", priority="review_required", failure="PAST_INTERVIEW")

    assert sweep(alerts_db) == [alert_id]
    assert priority(alerts_db, alert_id) == "high"


@pytest.mark.parametrize("failure", ["AI_REQUIRES_REVIEW", "LOW_CONFIDENCE"])
def test_a_block_that_is_the_ais_own_doubt_stays(alerts_db, failure):
    """Altimetrik, 9 Sep 2026: blocked because the AI's evidence did not entail
    the invite. That is the AI's work, so the alert stays in AI Retry."""
    queue(alerts_db, "m1", status="AI_PROCESSING_FAILED")
    alert_id = alert(alerts_db, "m1", classification="interview_confirmed", failure=failure)

    assert sweep(alerts_db) == []
