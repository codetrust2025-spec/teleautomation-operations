"""An interview mail the reader gave up on reaches Mail Alerts, on the real schema.

Three of one candidate's September interviews were signed Zoho invitations
carrying calendar events, and Operations never saw one of them: the trusted
calendar path refuses an invitation relayed by a calendar service (its
organizer is not its sender), the model then failed to read them, and a parked
mail writes no alert. The offer arrived before anyone noticed.

These build the two tables from their migrations, park a message exactly as
`claim_ai_messages` does, and check that the surfacing pass files it where a
person will see it -- and that it stays quiet for everything else.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from urllib.parse import urlparse
import uuid

import pytest

from core import recruitment_mail_store as store
from services import unread_interview_mail

MIGRATIONS = Path(__file__).resolve().parents[1] / "core" / "migrations"
NOW = datetime.now(timezone.utc)

ICS = """BEGIN:VCALENDAR
METHOD:REQUEST
BEGIN:VEVENT
UID:000111222333444@zohocalendar.com
DTSTART;TZID=Asia/Kolkata:20990918T150000
DTEND;TZID=Asia/Kolkata:20990918T153000
ORGANIZER;CN=Recruiter:mailto:recruiter@example.invalid
ATTENDEE;CN=Candidate:mailto:candidate@example.invalid
SUMMARY:L1_Java Developer
END:VEVENT
END:VCALENDAR
"""


#: A foreign key and whatever it does on delete. `mailbox_messages` points at a
#: table these tests do not build, and dropping the reference while leaving its
#: `ON DELETE CASCADE` behind is a syntax error, not a table.
FOREIGN_KEY = re.compile(r"\s+REFERENCES\s+\w+\s*\(\w+\)(?:\s+ON\s+(?:DELETE|UPDATE)\s+(?:CASCADE|RESTRICT|"
                         r"NO\s+ACTION|SET\s+(?:NULL|DEFAULT)))*", re.I)


def table_ddl(migration: str, table: str) -> list[str]:
    """One CREATE TABLE from its migration, without the foreign keys."""
    ddl = (MIGRATIONS / migration).read_text()
    start = ddl.index(f"CREATE TABLE IF NOT EXISTS {table}")
    depth = 0
    for index in range(start, len(ddl)):
        depth += (ddl[index] == "(") - (ddl[index] == ")")
        if depth == 0 and ddl[index] == ")":
            return [FOREIGN_KEY.sub("", ddl[start:index + 1]) + ";"]
    raise AssertionError(f"{table} is never closed")


def schema_statements() -> list[str]:
    statements = table_ddl("001_recruitment_mail_tracking.sql", "mailbox_messages")
    statements += table_ddl("007_recruitment_mail_notifications.sql", "mail_monitoring_notifications")
    statements += table_ddl("007_recruitment_mail_notifications.sql", "mail_ai_analyses")
    for migration in ("005_recruitment_mail_historical_rescan.sql", "008_recruitment_mail_auto_booking.sql",
                      "010_recruitment_mail_lifecycle_truth.sql", "014_recruitment_mail_ai_queue.sql",
                      "015_recruitment_mail_message_direction.sql",
                      "018_recruitment_mail_booking_block_reason.sql"):
        statements += re.findall(
            r"ALTER TABLE (?:mailbox_messages|mail_monitoring_notifications)[^;]*;",
            (MIGRATIONS / migration).read_text())
    return statements


@pytest.fixture
def db(monkeypatch):
    url = os.getenv("AUDIT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL tests run in CI")
    target = urlparse(url)
    assert target.hostname in {"localhost", "127.0.0.1"} and target.path == "/business_ci"
    import psycopg2
    from psycopg2 import sql
    schema = "unread_mail_" + uuid.uuid4().hex

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
            for statement in schema_statements():
                cur.execute(statement)
        monkeypatch.setattr(store, "get_connection", connect)
        monkeypatch.setattr(store, "_candidate_snapshot", lambda cid, structured: ("Candidate", "candidate@example.invalid"))
        yield connect
    finally:
        assert schema.startswith("unread_mail_")
        with psycopg2.connect(url) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def park(connect, message_id="m1", *, subject="Invitation: L1 - Java Developer",
         body="You are invited.", status="AI_PROCESSING_FAILED", parked_minutes_ago=5,
         direction="INBOUND", error="RECRUITMENT_RELEVANCE_UNRESOLVED"):
    """A message in the state `claim_ai_messages` leaves a spent one in."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO mailbox_messages(id,mailbox_id,candidate_id,provider_message_id,
              provider_thread_id,sender_name,sender_email,recipient_email,subject,sent_at,body_text,
              processing_status,message_direction,ai_last_error_code,ai_retry_count,created_at,updated_at)
            VALUES(%s,'mb1','c1',%s,%s,'Zoho','noreply@zohocalendar.com','candidate@example.invalid',
              %s,now(),%s,%s,%s,%s,2,now(),now() - (%s || ' minutes')::interval)""",
            (message_id, message_id, message_id, subject, body, status, direction, error, parked_minutes_ago))
    return message_id


def alerts(connect):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT gmail_message_id, classification, priority, candidate_status, ai_summary,
                              ai_reason, email_subject FROM mail_monitoring_notifications""")
        return [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]


# ── what reaches a person ───────────────────────────────────────────────────

def test_a_parked_invitation_becomes_an_alert(db, monkeypatch):
    park(db)
    monkeypatch.setattr(store, "attachments_for_message",
                        lambda mid, include_text=False: [{"filename": "invite.ics", "extracted_text": ICS}])

    created = unread_interview_mail.surface_unread_interview_mail()

    assert len(created) == 1
    (alert,) = alerts(db)
    assert alert["classification"] == "interview_needs_reading"
    assert alert["candidate_status"] == "Needs reading"
    assert alert["priority"] == "high"
    assert "calendar invitation" in alert["ai_summary"]
    # The park reason is kept, so the queue says why the reader stopped.
    assert alert["ai_reason"] == "RECRUITMENT_RELEVANCE_UNRESOLVED"


def test_running_twice_writes_one_alert(db, monkeypatch):
    park(db)
    monkeypatch.setattr(store, "attachments_for_message",
                        lambda mid, include_text=False: [{"filename": "invite.ics", "extracted_text": ICS}])

    unread_interview_mail.surface_unread_interview_mail()
    again = unread_interview_mail.surface_unread_interview_mail()

    assert again == []
    assert len(alerts(db)) == 1


def test_a_teams_link_is_enough_without_a_calendar_file(db, monkeypatch):
    park(db, body='Join here https://teams.microsoft.com/l/meetup-join/19%3ameeting_000111222%40thread.v2/0')
    monkeypatch.setattr(store, "attachments_for_message", lambda mid, include_text=False: [])

    assert len(unread_interview_mail.surface_unread_interview_mail()) == 1
    assert "Teams meeting" in alerts(db)[0]["ai_summary"]


# ── what stays quiet ────────────────────────────────────────────────────────

def test_a_mail_with_no_meeting_in_it_is_left_alone(db, monkeypatch):
    # The reader gave up on it, but nothing in it names an interview: 3,855
    # mails are parked this way and a queue of those is not a queue.
    park(db, subject="Interview tips for your job search", body="Read our 10 tips for interviews.")
    monkeypatch.setattr(store, "attachments_for_message", lambda mid, include_text=False: [])

    assert unread_interview_mail.surface_unread_interview_mail() == []
    assert alerts(db) == []


def test_mail_parked_before_the_window_is_not_swept_up(db, monkeypatch):
    # The pass runs every cycle; an old backlog is somebody's decision, not a
    # side effect of deploying this.
    park(db, parked_minutes_ago=60 * 24 * 30)
    monkeypatch.setattr(store, "attachments_for_message",
                        lambda mid, include_text=False: [{"filename": "invite.ics", "extracted_text": ICS}])

    assert unread_interview_mail.surface_unread_interview_mail() == []
    assert alerts(db) == []
    # ...and it is still reachable when somebody asks for it by hand.
    assert len(unread_interview_mail.surface_unread_interview_mail(
        since=NOW - timedelta(days=90))) == 1


def test_a_mail_still_being_retried_is_not_surfaced(db, monkeypatch):
    # AI_RETRY_PENDING means another attempt is coming. Only a spent one
    # (AI_PROCESSING_FAILED) has nobody left to read it.
    park(db, status="AI_RETRY_PENDING")
    monkeypatch.setattr(store, "attachments_for_message",
                        lambda mid, include_text=False: [{"filename": "invite.ics", "extracted_text": ICS}])

    assert unread_interview_mail.surface_unread_interview_mail() == []


def test_our_own_outbound_mail_is_not_an_interview_invitation(db, monkeypatch):
    park(db, direction="OUTBOUND")
    monkeypatch.setattr(store, "attachments_for_message",
                        lambda mid, include_text=False: [{"filename": "invite.ics", "extracted_text": ICS}])

    assert unread_interview_mail.surface_unread_interview_mail() == []


def test_a_mail_that_already_has_an_alert_is_not_doubled(db, monkeypatch):
    message = park(db)
    monkeypatch.setattr(store, "attachments_for_message",
                        lambda mid, include_text=False: [{"filename": "invite.ics", "extracted_text": ICS}])
    with db() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO mail_monitoring_notifications(id,candidate_id,gmail_message_id,
              classification,priority,created_at,updated_at)
            VALUES('n-existing','c1',%s,'interview_confirmed','high',now(),now())""", (message,))

    assert unread_interview_mail.surface_unread_interview_mail() == []
    assert len(alerts(db)) == 1
