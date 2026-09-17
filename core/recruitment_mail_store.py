"""PostgreSQL persistence for candidate mailbox recruitment tracking."""

from __future__ import annotations

import itertools
import hashlib
import json
import logging
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from datetime import date, datetime, timezone
from typing import Any

from core.db.connection import get_connection, use_postgres

logger = logging.getLogger(__name__)
from core.recruitment_offer_visibility import (
    qualified_event_sql,
    should_show_in_selection_offer_review,
)


CANONICAL_CLASSIFICATIONS = {
    "job_selection_confirmed", "offer_received", "offer_accepted",
    "offer_declined", "offer_revoked", "joining_confirmed",
    "joining_date_updated", "onboarding_started", "background_verification",
    "document_verification", "compensation_confirmation", "interview_update",
    "interview_shortlisted", "interview_confirmed", "interview_rescheduled",
    "interview_cancelled", "candidate_rejected",
    "not_relevant", "ai_retry_pending", "final_round_cleared", "hr_confirmation",
}

# Mail Monitoring Notifications track only auto interview slot booking and
# job confirmed monitoring mails. Other classifications are still processed
# for candidate status and offer tracking but do not produce user-facing
# notifications.
TRACKED_NOTIFICATION_CLASSIFICATIONS = {
    "job_selection_confirmed", "offer_received", "offer_accepted",
    "offer_declined", "offer_revoked", "joining_confirmed",
    "joining_date_updated", "onboarding_started", "background_verification",
    "document_verification", "compensation_confirmation",
    "interview_shortlisted", "interview_confirmed", "interview_rescheduled",
    "interview_cancelled",
    "final_round_cleared", "hr_confirmation",
}
# candidate_rejected is deliberately absent. A rejection is an outcome to
# record, not something to interrupt an administrator for: it needs no action,
# and an alert queue that announces them buries the offers and interviews that
# do. The event, the candidate status and the history are all still written -
# only the notification, the sound and the counters are not.
# The two groups the Mail Alerts filter offers in place of eighteen individual
# classifications. They partition TRACKED_NOTIFICATION_CLASSIFICATIONS exactly:
# every tracked classification belongs to one group and none to both, so
# "Selection Related" plus "Interview Related" shows the same set as no filter
# at all. A test asserts that, because a classification added later would
# otherwise become invisible to both filters without anything failing.
INTERVIEW_RELATED_CLASSIFICATIONS = {
    "interview_shortlisted", "interview_confirmed",
    "interview_rescheduled", "interview_cancelled",
}
SELECTION_RELATED_CLASSIFICATIONS = (
    TRACKED_NOTIFICATION_CLASSIFICATIONS - INTERVIEW_RELATED_CLASSIFICATIONS
)
CLASSIFICATION_GROUPS = {
    "selection": SELECTION_RELATED_CLASSIFICATIONS,
    "interview": INTERVIEW_RELATED_CLASSIFICATIONS,
}

IMPORTANT_ALERT_EVIDENCE_MEANINGS = {
    "SELECTED", "FINAL_SELECTION_CONFIRMED", "JOB_SELECTION_CONFIRMED",
    "FINAL_ROUND_CLEARED", "INTERVIEW_CLEARED",
    "OFFER_INDICATION", "OFFER_IN_PROGRESS", "OFFER_APPROVED",
    "OFFER_LETTER_RECEIVED", "APPOINTMENT_LETTER_RECEIVED",
    "OFFER_RECEIVED", "OFFER_ACCEPTED", "OFFER_DECLINED", "OFFER_REVOKED",
    "JOINING_CONFIRMED", "JOINING_DATE_UPDATED", "POST_SELECTION_ONBOARDING",
    "ONBOARDING_STARTED", "BACKGROUND_VERIFICATION", "DOCUMENT_VERIFICATION",
    "HR_CONFIRMATION", "COMPENSATION_CONFIRMATION", "INTERVIEW_SHORTLISTED",
    "INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED",
    "CANDIDATE_REJECTED",
}

_STATUS_CLASSIFICATION = {
    "SELECTED": "job_selection_confirmed",
    "FINAL_SELECTION_CONFIRMED": "job_selection_confirmed",
    "FINAL_ROUND_CLEARED": "final_round_cleared",
    "OFFER_INDICATION": "offer_received",
    "OFFER_IN_PROGRESS": "offer_received",
    "OFFER_APPROVED": "offer_received",
    "OFFER_LETTER_RECEIVED": "offer_received",
    "APPOINTMENT_LETTER_RECEIVED": "offer_received",
    "OFFER_RECEIVED": "offer_received",
    "OFFER_ACCEPTED": "offer_accepted",
    "OFFER_DECLINED": "offer_declined",
    "OFFER_REVOKED": "offer_revoked",
    "JOINING_CONFIRMED": "joining_confirmed",
    "JOINING_DATE_UPDATED": "joining_confirmed",
    "POST_SELECTION_ONBOARDING": "joining_confirmed",
    "JOINED": "joining_confirmed",
    "BACKGROUND_VERIFICATION": "joining_confirmed",
    "DOCUMENT_VERIFICATION": "hr_confirmation",
    "HR_CONFIRMATION": "hr_confirmation",
    "COMPENSATION_CONFIRMATION": "hr_confirmation",
    "INTERVIEW_UPDATE": "interview_update",
    "INTERVIEW_SHORTLISTED": "interview_shortlisted",
    "ASSESSMENT_INVITED": "assessment_invited",
    "INTERVIEW_CONFIRMED": "interview_confirmed",
    "INTERVIEW_RESCHEDULED": "interview_rescheduled",
    "INTERVIEW_CANCELLED": "interview_cancelled",
    "CANDIDATE_REJECTED": "candidate_rejected",
    "MANUAL_REVIEW_REQUIRED": "ai_retry_pending",
    "IGNORED_LOW_CONFIDENCE": "ai_retry_pending",
    "IGNORED_NOT_OFFER_RELATED": "not_relevant",
    "AI_RETRY_PENDING": "ai_retry_pending",
}

_CLASSIFICATION_STATUS = {
    "job_selection_confirmed": "Selected",
    "offer_received": "Offer Received",
    "offer_accepted": "Offer Accepted",
    "offer_declined": "Offer Declined",
    "offer_revoked": "Offer Revoked",
    "joining_confirmed": "Joining Confirmed",
    "joining_date_updated": "Joining Confirmed",
    "onboarding_started": "Joining Confirmed",
    "background_verification": "Joining Confirmed",
    "document_verification": "HR Confirmation",
    "compensation_confirmation": "HR Confirmation",
    "final_round_cleared": "Final Round Cleared",
    "hr_confirmation": "HR Confirmation",
    "interview_update": "Interview In Progress",
    "interview_shortlisted": "Interview Shortlisted",
    "assessment_invited": "Assessment Pending",
    "interview_confirmed": "Interview Confirmed",
    "interview_rescheduled": "Interview Rescheduled",
    "interview_cancelled": "Interview Cancelled",
    "candidate_rejected": "Rejected",
    "needs_review": "AI Retry Pending",
    "ai_retry_pending": "AI Retry Pending",
    "not_relevant": "Profile Active",
}

_STATUS_RANK = {
    "Profile Active": 10, "Interview In Progress": 20,
    "Assessment Pending": 22,
    "Interview Confirmed": 25, "Interview Rescheduled": 25,
    "Interview Cancelled": 20,
    "Interview Shortlisted": 30, "Final Round Cleared": 35, "Rejected": 35, "Selected": 40,
    "HR Confirmation": 45,
    "Offer Received": 50, "Offer Accepted": 60, "Offer Declined": 65,
    "Offer Revoked": 65, "Joining Confirmed": 70,
    "Onboarding Started": 80, "Joined": 90, "Needs Review": 0,
}


def stage_rank(status: str | None) -> int | None:
    """How far along the hiring process a raw AI status claims the candidate is.

    The same ordering `advance_candidate_status` uses, exposed so a caller can
    compare two readings of one mail without reaching into the private tables.
    `None` means the status has no place in the progression -- a review or
    retry marker, or something unrecognised -- and must not be ranked against
    anything.
    """
    classification = _STATUS_CLASSIFICATION.get(str(status or "").upper())
    if classification in (None, "needs_review", "ai_retry_pending"):
        return None
    return _STATUS_RANK.get(_CLASSIFICATION_STATUS.get(classification, ""))


def _id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_candidate_id(candidate_id: str) -> str:
    """Resolve a recruitment event to one strong, persisted person identity."""
    from services.recruitment_identity import canonical_candidate_id as resolve
    return resolve(candidate_id)


@contextmanager
def candidate_booking_lock(candidate_id: str):
    """Cross-process PostgreSQL lock for one candidate's booking transaction."""
    if not use_postgres():
        yield
        return
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(hashtext(%s))", (f"ai-mail-booking:{candidate_id}",))
        try:
            yield
        finally:
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"ai-mail-booking:{candidate_id}",))


def ensure_schema() -> None:
    # Route installation and startup both call this. Re-executing historical
    # SQL here restored retired review flags and re-queued old messages after
    # the tracked migration runner had already applied the cleanup. Schema and
    # data migrations must share the same checksum ledger on every entry path.
    from core.migrations.runner import apply_migrations
    apply_migrations()


def _rows(cur) -> list[dict[str, Any]]:
    names = [d.name for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def mailbox_for_candidate(candidate_id: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM candidate_mailboxes WHERE candidate_id=%s ORDER BY created_at DESC LIMIT 1", (candidate_id,))
        rows = _rows(cur)
    return rows[0] if rows else None


def mailbox_for_candidates(candidate_ids: list[str]) -> dict[str, Any] | None:
    """Find the best single mailbox across legacy rows for one phone identity (backward-compat)."""
    ids = [str(value) for value in candidate_ids if value]
    if not ids:
        return None
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM candidate_mailboxes WHERE candidate_id=ANY(%s)
               ORDER BY (connection_status='CONNECTED') DESC,
                        monitoring_enabled DESC,
                        (credential_ciphertext IS NOT NULL) DESC,
                        last_successful_sync_at DESC NULLS LAST,
                        updated_at DESC LIMIT 1""",
            (ids,),
        )
        rows = _rows(cur)
    return rows[0] if rows else None


def mailboxes_for_candidates(candidate_ids: list[str]) -> list[dict[str, Any]]:
    """Return ALL mailboxes across identity rows — supports multiple emails per candidate."""
    ids = [str(value) for value in candidate_ids if value]
    if not ids:
        return []
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM candidate_mailboxes WHERE candidate_id=ANY(%s)
               ORDER BY (connection_status='CONNECTED') DESC,
                        monitoring_enabled DESC,
                        (credential_ciphertext IS NOT NULL) DESC,
                        last_successful_sync_at DESC NULLS LAST,
                        updated_at DESC""",
            (ids,),
        )
        rows = _rows(cur)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("email_address") or "").strip().casefold()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(row)
    return unique


#: A candidate at one of these stages has left the pipeline, so their Gmail is
#: history rather than something to monitor, chase or reconnect.
TERMINAL_CANDIDATE_STAGES = frozenset({"completed", "fail", "dropped"})


def terminal_mailbox_ids(rows) -> set[str]:
    """Which of these mailboxes belong to a candidate who has left the pipeline.

    Takes rows that have already been fetched rather than querying: the mailbox
    overview is polled while syncs run and once starved the API worker, so this
    must not add a round trip to it. `rows` need only expose `id`,
    `candidate_id` and `canonical_candidate_id`.

    Stage lives in the candidate store, not in this schema, and a mailbox's
    candidate_id is frequently an alias -- 11 of 21 production mailboxes did not
    resolve through the identity-link column alone and needed
    `canonical_candidate_identity_id`. Every one of those was in_progress.

    So an unresolved mailbox is treated as ACTIVE, never terminal. Guessing the
    other way would silently stop monitoring a live candidate's mail, which is
    far worse than leaving a closed one listed.
    """
    from features import candidate_store

    stages = {
        str(row.get("id")): str(row.get("stage") or "").strip().lower()
        for row in candidate_store.list_candidates(stage="all", month="all")
    }
    terminal: set[str] = set()
    for row in rows:
        mailbox_id = str(row.get("id") or "")
        candidate_id = str(row.get("candidate_id") or "")
        canonical_id = str(row.get("canonical_candidate_id") or "")
        stage = None
        for key in (canonical_id, candidate_id):
            if key and key in stages:
                stage = stages[key]
                break
        if stage is None and candidate_id:
            # The alias column is not enough on its own; this is the resolver
            # the candidates page itself uses.
            try:
                resolved = candidate_store.canonical_candidate_identity_id(candidate_id)
            except Exception:
                resolved = None
            if resolved:
                stage = stages.get(str(resolved))
        if stage in TERMINAL_CANDIDATE_STAGES:
            terminal.add(mailbox_id)
    return terminal


#: Google expires a refresh token seven days after consent while the OAuth app
#: is in Testing mode. Measured across 54 reconnects the median gap is 7.0 days,
#: which is what the countdown counts down to.
GMAIL_GRANT_DAYS = 7


def _mailbox_health_rows(cur) -> list[dict[str, Any]]:
    cur.execute(
        """SELECT m.id,m.candidate_id,
                  COALESCE(l.canonical_candidate_id,m.candidate_id)
                    AS canonical_candidate_id,
                  m.email_address,m.connection_status,
                  m.monitoring_enabled,m.last_error_code,m.last_error_message,
                  m.last_successful_sync_at,m.updated_at,
                  a.authorized_at,
                  -- The moment the grant dies, decided here rather than in the
                  -- browser: a countdown that disagrees with the server about
                  -- when it reaches zero is worse than no countdown.
                  a.authorized_at + make_interval(days => %(grant_days)s)
                    AS grant_expires_at
           FROM candidate_mailboxes m
           LEFT JOIN candidate_identity_links l
             ON l.alias_candidate_id=m.candidate_id
           -- When this mailbox was last authorised, which is the only thing
           -- that predicts when its grant dies. The OAuth app is in Testing
           -- mode, so Google expires refresh tokens seven days after consent:
           -- measured across 54 reconnects the median gap is 7.0 days. There
           -- is no authorised-at column, and the audit log already records it.
           LEFT JOIN LATERAL (
             SELECT max(created_at) AS authorized_at
               FROM recruitment_audit_log
              WHERE source_id=m.id AND action='MAILBOX_CONNECTED'
           ) a ON true
           WHERE m.credential_ciphertext IS NOT NULL
             AND m.connection_status <> 'SUPERSEDED'
           ORDER BY m.updated_at DESC""",
        {"grant_days": GMAIL_GRANT_DAYS},
    )
    rows = _rows(cur)
    # Annotated, not filtered: the mailbox stays listed so its history and
    # Gmail linkage remain visible and reconnectable, but everything that
    # counts active work skips it. Derived from stage on every read, so
    # returning a candidate to in_progress restores monitoring with no
    # migration and no flag to remember to unset.
    terminal = terminal_mailbox_ids(rows)
    for row in rows:
        row["monitoring_excluded"] = str(row.get("id")) in terminal
    return rows


def mailbox_health_rows() -> list[dict[str, Any]]:
    """Return credential-free mailbox state for lightweight health polling."""
    with get_connection() as conn, conn.cursor() as cur:
        return _mailbox_health_rows(cur)


def mailbox_overview_rows() -> list[dict[str, Any]]:
    """Return every active mailbox and its counters without exposing credentials.

    This is intentionally a bulk operation for the dashboard.  The old client
    loaded up to 500 candidates and then called the single-candidate mailbox
    endpoint once per candidate, which made the initial render depend on
    hundreds of HTTP round trips.

    Keep the server-side implementation bulk as well.  Opening one PostgreSQL
    connection and running three queries per mailbox made this endpoint issue
    37 sequential connections for 18 production mailboxes.  The UI polls this
    view while a sync is active, so those requests overlapped and starved the
    single API worker.  Four set-oriented queries on one connection keep the
    cost bounded as the mailbox count grows.
    """
    with get_connection() as conn, conn.cursor() as cur:
        mailboxes = _mailbox_health_rows(cur)
        mailbox_ids = [str(mailbox["id"]) for mailbox in mailboxes]
        if not mailbox_ids:
            return []

        predicate, params = qualified_event_sql("e")
        cur.execute(
            f"""SELECT m.mailbox_id,
              count(*) FILTER(WHERE {predicate}) important_emails,
              count(*) FILTER(WHERE e.primary_status IN('SELECTED','FINAL_SELECTION_CONFIRMED') AND {predicate}) selection_events,
              count(*) FILTER(WHERE e.primary_status IN('OFFER_INDICATION','OFFER_IN_PROGRESS','OFFER_APPROVED','OFFER_LETTER_RECEIVED','APPOINTMENT_LETTER_RECEIVED','OFFER_ACCEPTED') AND {predicate}) offer_events,
              count(*) FILTER(WHERE e.primary_status='OFFER_LETTER_RECEIVED' AND {predicate}) offer_letters
              FROM mailbox_messages m
              LEFT JOIN ai_recruitment_events e ON e.mailbox_message_id=m.id
              WHERE m.mailbox_id=ANY(%s)
              GROUP BY m.mailbox_id""",
            params * 4 + [mailbox_ids],
        )
        event_stats = {str(row["mailbox_id"]): row for row in _rows(cur)}

        cur.execute(
            """SELECT gmail_account_id AS mailbox_id,count(*) AS pending_reviews
              FROM mail_monitoring_notifications
              WHERE gmail_account_id=ANY(%s)
                AND priority='review_required'
                AND NOT is_reviewed
                AND dismissed_at IS NULL
                AND COALESCE(booking_status,'') <> 'Historical Skipped'
              GROUP BY gmail_account_id""",
            (mailbox_ids,),
        )
        pending_reviews = {
            str(row["mailbox_id"]): int(row["pending_reviews"] or 0)
            for row in _rows(cur)
        }

        cur.execute(
            """SELECT DISTINCT ON (mailbox_id)
              mailbox_id,id,status,job_type,created_at,started_at,completed_at,
              messages_fetched,messages_processed,events_detected,error_message
              FROM mailbox_sync_jobs
              WHERE mailbox_id=ANY(%s)
              ORDER BY mailbox_id,created_at DESC""",
            (mailbox_ids,),
        )
        latest_jobs = {str(row["mailbox_id"]): row for row in _rows(cur)}

    result = []
    for mailbox in mailboxes:
        mailbox_id = str(mailbox["id"])
        aggregate = event_stats.get(mailbox_id) or {}
        stats = {
            "important_emails": int(aggregate.get("important_emails") or 0),
            "selection_events": int(aggregate.get("selection_events") or 0),
            "offer_events": int(aggregate.get("offer_events") or 0),
            "offer_letters": int(aggregate.get("offer_letters") or 0),
            "pending_reviews": pending_reviews.get(mailbox_id, 0),
        }
        job = latest_jobs.get(mailbox_id)
        if job:
            stats.update({
                "latest_sync_job_id": job.get("id"),
                "latest_sync_status": job.get("status"),
                "latest_sync_job_type": job.get("job_type") or "INCREMENTAL_SYNC",
                "latest_sync_created_at": job.get("created_at"),
                "latest_sync_started_at": job.get("started_at"),
                "latest_sync_completed_at": job.get("completed_at"),
                "latest_sync_messages_fetched": job.get("messages_fetched") or 0,
                "latest_sync_messages_processed": job.get("messages_processed") or 0,
                "latest_sync_events_detected": job.get("events_detected") or 0,
                "latest_sync_error": job.get("error_message"),
            })
        result.append({"mailbox": mailbox, "stats": stats})
    return result


def mailboxes_due_for_watch_renewal(*, before: datetime, limit: int = 100) -> list[dict[str, Any]]:
    """Connected mailboxes whose Gmail push watch lapses before `before`.

    Unlike `mailbox_health_rows` this deliberately returns the credential
    ciphertext, because renewing a watch means calling Google as that mailbox.

    A NULL expiration sorts first and is treated as due: it means the mailbox
    either predates watch tracking or its registration never completed, and both
    need a watch registered rather than skipped.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM candidate_mailboxes
               WHERE credential_ciphertext IS NOT NULL
                 AND monitoring_enabled=true
                 AND connection_status='CONNECTED'
                 AND (gmail_watch_expiration IS NULL OR gmail_watch_expiration < %s)
               ORDER BY gmail_watch_expiration ASC NULLS FIRST
               LIMIT %s""",
            (before, limit),
        )
        return _rows(cur)


def mailbox_by_id(mailbox_id: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM candidate_mailboxes WHERE id=%s", (mailbox_id,))
        rows = _rows(cur)
    return rows[0] if rows else None


def mailbox_by_email(email_address: str) -> dict[str, Any] | None:
    """Resolve a connected mailbox for a Gmail Pub/Sub emailAddress."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM candidate_mailboxes
               WHERE lower(email_address)=lower(%s) AND monitoring_enabled=true
               ORDER BY (connection_status='CONNECTED') DESC, updated_at DESC LIMIT 1""",
            (str(email_address or "").strip(),),
        )
        rows = _rows(cur)
    return rows[0] if rows else None


def upsert_mailbox(candidate_id: str, email: str, **fields: Any) -> dict[str, Any]:
    mailbox_id = fields.pop("id", None) or _id()
    supplied_identity_ids = fields.pop("identity_ids", None)
    email = str(email or "").strip().lower()
    with get_connection() as conn, conn.cursor() as cur:
        # Reconnecting through a legacy candidate alias must reuse the Gmail
        # mailbox already attached to the same verified person identity.
        cur.execute(
            "SELECT canonical_candidate_id FROM candidate_identity_links WHERE alias_candidate_id=%s",
            (candidate_id,),
        )
        identity = cur.fetchone()
        canonical_id = str(identity[0]) if identity and identity[0] else str(candidate_id)
        cur.execute(
            "SELECT alias_candidate_id FROM candidate_identity_links WHERE canonical_candidate_id=%s",
            (canonical_id,),
        )
        identity_ids = {canonical_id, str(candidate_id), *(str(value[0]) for value in cur.fetchall())}
        if supplied_identity_ids:
            identity_ids.update(str(value) for value in supplied_identity_ids if value)
        cur.execute(
            """SELECT * FROM candidate_mailboxes
               WHERE candidate_id=ANY(%s) AND lower(email_address)=lower(%s)
               ORDER BY (connection_status='CONNECTED') DESC,
                        monitoring_enabled DESC,
                        (credential_ciphertext IS NOT NULL) DESC,
                        last_successful_sync_at DESC NULLS LAST,
                        updated_at DESC LIMIT 1 FOR UPDATE""",
            (list(identity_ids), email),
        )
        existing = _rows(cur)
        if existing:
            cur.execute(
                """UPDATE candidate_mailboxes SET monitoring_enabled=COALESCE(%s,monitoring_enabled),
                     connection_status=%s,
                     credential_ciphertext=COALESCE(%s,credential_ciphertext),
                     updated_at=now() WHERE id=%s RETURNING *""",
                (
                    fields.get("monitoring_enabled"),
                    fields.get("connection_status", "PENDING"),
                    fields.get("credential_ciphertext"),
                    existing[0]["id"],
                ),
            )
            return _rows(cur)[0]
        cur.execute("""
            INSERT INTO candidate_mailboxes(id,candidate_id,provider,email_address,connection_type,monitoring_enabled,connection_status,credential_ciphertext,created_at,updated_at)
            VALUES(%s,%s,'gmail',%s,'oauth2',%s,%s,%s,now(),now())
            ON CONFLICT(candidate_id, lower(email_address)) DO UPDATE SET
              monitoring_enabled=EXCLUDED.monitoring_enabled, connection_status=EXCLUDED.connection_status,
              credential_ciphertext=COALESCE(EXCLUDED.credential_ciphertext,candidate_mailboxes.credential_ciphertext), updated_at=now()
            RETURNING *
        """, (mailbox_id,candidate_id,email,bool(fields.get("monitoring_enabled",False)),fields.get("connection_status","PENDING"),fields.get("credential_ciphertext")))
        row=_rows(cur)[0]
        cur.execute("SELECT candidate_id FROM candidate_mailboxes WHERE lower(email_address)=lower(%s) AND NOT (candidate_id=ANY(%s)) LIMIT 1",(email,list(identity_ids)));duplicate=cur.fetchone()
        if duplicate:
            cur.execute("""INSERT INTO recruitment_review_flags(id,candidate_id,flag_type,severity,details,created_at)
              VALUES(%s,%s,'POSSIBLE_DUPLICATE_CANDIDATE','HIGH',%s::jsonb,now()) ON CONFLICT(candidate_id,event_id,flag_type) DO NOTHING""",(_id(),candidate_id,json.dumps({'matching_candidate_id':duplicate[0],'reason':'same_mailbox_email'})))
        return row


def supersede_duplicate_mailboxes(
    candidate_ids: list[str], email_address: str, keep_mailbox_id: str,
) -> int:
    """Disable stale copies of one mailbox across legacy rows for the same person."""
    ids = [str(value) for value in candidate_ids if value]
    if not ids:
        return 0
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE candidate_mailboxes
               SET monitoring_enabled=false,
                   connection_status='SUPERSEDED',
                   next_sync_at=NULL,
                   last_error_code=NULL,
                   last_error_message=NULL,
                   updated_at=now()
               WHERE candidate_id=ANY(%s)
                 AND lower(email_address)=lower(%s)
                 AND id<>%s""",
            (ids, str(email_address or "").strip(), keep_mailbox_id),
        )
        return int(cur.rowcount or 0)


def update_mailbox(mailbox_id: str, values: dict[str, Any]) -> dict[str, Any]:
    allowed={"monitoring_enabled","connection_status","credential_ciphertext","sync_cursor","provider_history_id","last_sync_attempt_at","last_successful_sync_at","next_sync_at","failed_sync_count","last_error_code","last_error_message","gmail_watch_expiration","gmail_watch_topic","last_push_history_id"}
    clean={k:v for k,v in values.items() if k in allowed}
    if not clean:
        return mailbox_by_id(mailbox_id) or {}
    assignments=", ".join(f"{k}=%s" for k in clean)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE candidate_mailboxes SET {assignments},updated_at=now() WHERE id=%s RETURNING *", (*clean.values(),mailbox_id))
        rows=_rows(cur)
    return rows[0] if rows else {}


def stage_gmail_messages_and_advance_cursor(
    mailbox_id: str, refs: list[dict[str, Any]], cursor: str | None,
) -> int:
    """Atomically persist every discovered Gmail ID, then advance its cursor."""
    unique_ids = list(dict.fromkeys(str(ref.get("id") or "") for ref in refs if ref.get("id")))
    with get_connection() as conn, conn.cursor() as cur:
        inserted = 0
        for provider_message_id in unique_ids:
            cur.execute(
                """INSERT INTO gmail_message_ingestion_queue(
                       id,mailbox_id,provider_message_id,source_history_id,discovery_source,status,discovered_at,updated_at)
                   VALUES(%s,%s,%s,%s,'GMAIL_HISTORY','QUEUED',now(),now())
                   ON CONFLICT(mailbox_id,provider_message_id) DO NOTHING""",
                (_id(), mailbox_id, provider_message_id, cursor),
            )
            inserted += int(cur.rowcount or 0)
        cur.execute(
            """UPDATE candidate_mailboxes SET provider_history_id=%s,sync_cursor=%s,
                 updated_at=now() WHERE id=%s""",
            (cursor, cursor, mailbox_id),
        )
    return inserted


def stage_gmail_messages(
    mailbox_id: str, refs: list[dict[str, Any]], *, discovery_source: str = "RECOVERY_AUDIT",
    source_history_id: str | None = None,
) -> int:
    """Durably stage explicitly selected Gmail IDs without changing the cursor."""
    unique_ids = list(dict.fromkeys(str(ref.get("id") or "") for ref in refs if ref.get("id")))
    with get_connection() as conn, conn.cursor() as cur:
        inserted = 0
        for provider_message_id in unique_ids:
            cur.execute(
                """INSERT INTO gmail_message_ingestion_queue(
                       id,mailbox_id,provider_message_id,source_history_id,discovery_source,status,discovered_at,updated_at)
                   VALUES(%s,%s,%s,%s,%s,'QUEUED',now(),now())
                   ON CONFLICT(mailbox_id,provider_message_id) DO NOTHING""",
                (_id(), mailbox_id, provider_message_id, source_history_id, discovery_source),
            )
            inserted += int(cur.rowcount or 0)
    return inserted


def claim_gmail_ingestion(mailbox_id: str, *, limit: int) -> list[dict[str, Any]]:
    """Claim a bounded durable batch, recovering rows abandoned by a crash."""
    lease_minutes = max(1, min(30, int(os.getenv("AI_MAIL_INGESTION_LEASE_MINUTES", "5"))))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE gmail_message_ingestion_queue SET status='QUEUED',started_at=NULL,updated_at=now(),
                 last_error_message='Recovered after interrupted processing'
               WHERE mailbox_id=%s AND status='RUNNING'
                 AND updated_at<now()-(%s||' minutes')::interval""",
            (mailbox_id, lease_minutes),
        )
        cur.execute(
            """SELECT id FROM gmail_message_ingestion_queue
               WHERE mailbox_id=%s AND status='QUEUED'
               ORDER BY discovered_at,id FOR UPDATE SKIP LOCKED LIMIT %s""",
            (mailbox_id, max(1, min(int(limit), 500))),
        )
        ids = [row[0] for row in cur.fetchall()]
        if not ids:
            return []
        cur.execute(
            """UPDATE gmail_message_ingestion_queue SET status='RUNNING',attempts=attempts+1,
                 started_at=now(),updated_at=now() WHERE id=ANY(%s) RETURNING *""",
            (ids,),
        )
        rows = _rows(cur)
    order = {value: index for index, value in enumerate(ids)}
    return sorted(rows, key=lambda row: order[row["id"]])


def finish_gmail_ingestion(
    ingestion_id: str, *, status: str, error: Exception | None = None, max_attempts: int = 5,
) -> str:
    """Complete, tombstone, or safely requeue one staged Gmail message."""
    requested = str(status).upper()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT attempts FROM gmail_message_ingestion_queue WHERE id=%s FOR UPDATE", (ingestion_id,))
        row = cur.fetchone()
        attempts = int(row[0] if row else max_attempts)
        final = requested
        if requested == "QUEUED" and attempts >= max_attempts:
            final = "DEAD_LETTER"
        cur.execute(
            """UPDATE gmail_message_ingestion_queue SET status=%s,
                 completed_at=CASE WHEN %s IN ('COMPLETED','DELETED','DEAD_LETTER') THEN now() ELSE NULL END,
                 started_at=CASE WHEN %s='QUEUED' THEN NULL ELSE started_at END,
                 last_error_code=%s,last_error_message=%s,updated_at=now() WHERE id=%s""",
            (final, final, final, type(error).__name__ if error else None, str(error)[:400] if error else None, ingestion_id),
        )
    return final


def pending_gmail_ingestion_count(mailbox_id: str) -> int:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM gmail_message_ingestion_queue WHERE mailbox_id=%s AND status IN ('QUEUED','RUNNING')",
            (mailbox_id,),
        )
        row = cur.fetchone()
    return int(row[0] if row else 0)


def record_pubsub_delivery(
    pubsub_message_id: str, *, subscription: str, email_address: str,
    history_id: str, mailbox_id: str | None, status: str,
    error_code: str | None = None,
) -> bool:
    """Persist the push envelope; False means Google retried an existing ID."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO gmail_pubsub_deliveries(pubsub_message_id,subscription,email_address,
                 history_id,mailbox_id,delivery_status,error_code,received_at,processed_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,now(),CASE WHEN %s='QUEUED' THEN now() ELSE NULL END)
               ON CONFLICT(pubsub_message_id) DO NOTHING RETURNING pubsub_message_id""",
            (pubsub_message_id, subscription, email_address, history_id, mailbox_id, status, error_code, status),
        )
        return cur.fetchone() is not None


def enqueue_sync(mailbox_id: str, *, requested_by: str, scheduled_for: datetime | None=None) -> dict[str, Any]:
    job_id=_id()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mailbox_sync_jobs WHERE mailbox_id=%s AND status IN('QUEUED','RUNNING') ORDER BY created_at DESC LIMIT 1 FOR UPDATE",(mailbox_id,))
        existing=_rows(cur) if cur.description else []
        if existing:return existing[0]
        cur.execute("""INSERT INTO mailbox_sync_jobs(id,mailbox_id,status,scheduled_for,requested_by,created_at)
          VALUES(%s,%s,'QUEUED',%s,%s,now()) RETURNING *""",(job_id,mailbox_id,scheduled_for or now(),requested_by))
        return _rows(cur)[0]


def enqueue_historical_rescan(mailbox_id: str, *, requested_by: str, range_start: date, range_end: date) -> dict[str, Any]:
    job_id = _id()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mailbox_sync_jobs WHERE mailbox_id=%s AND job_type='HISTORICAL_RESCAN' AND status IN('QUEUED','RUNNING') ORDER BY created_at DESC LIMIT 1 FOR UPDATE", (mailbox_id,))
        existing = _rows(cur) if cur.description else []
        if existing:
            return existing[0]
        cur.execute("""INSERT INTO mailbox_sync_jobs(id,mailbox_id,status,scheduled_for,requested_by,job_type,range_start,range_end,created_at)
          VALUES(%s,%s,'QUEUED',now(),%s,'HISTORICAL_RESCAN',%s,%s,now()) RETURNING *""",
          (job_id, mailbox_id, requested_by, range_start, range_end))
        return _rows(cur)[0]


def claim_job() -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT j.id FROM mailbox_sync_jobs j WHERE j.status='QUEUED' AND j.scheduled_for<=now()
          AND NOT EXISTS(SELECT 1 FROM mailbox_sync_jobs active WHERE active.mailbox_id=j.mailbox_id AND active.status='RUNNING')
          ORDER BY j.scheduled_for FOR UPDATE OF j SKIP LOCKED LIMIT 1""")
        row=cur.fetchone()
        if not row:return None
        cur.execute("UPDATE mailbox_sync_jobs SET status='RUNNING',started_at=now(),attempts=attempts+1 WHERE id=%s RETURNING *",(row[0],))
        return _rows(cur)[0]


def recover_interrupted_jobs() -> int:
    """Requeue jobs that belonged to a previous backend process."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE mailbox_sync_jobs SET status='QUEUED',scheduled_for=now(),
          started_at=NULL,completed_at=NULL,error_message='Backend restarted; job resumed automatically'
          WHERE status='RUNNING'""")
        return int(cur.rowcount or 0)


def finish_job(job_id: str, *, status: str, counts: dict[str,int]|None=None, error: str|None=None) -> None:
    c=counts or {}
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE mailbox_sync_jobs SET status=%s,completed_at=now(),messages_fetched=%s,messages_processed=%s,
          events_detected=%s,error_message=%s WHERE id=%s""",(status,c.get('fetched',0),c.get('processed',0),c.get('events',0),error,job_id))


def retry_job(job_id:str,*,delay_minutes:int,error:str,max_attempts:int=5)->str:
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("SELECT attempts FROM mailbox_sync_jobs WHERE id=%s FOR UPDATE",(job_id,));row=cur.fetchone();attempts=int(row[0] if row else max_attempts)
        status='DEAD_LETTER' if attempts>=max_attempts else 'QUEUED'
        cur.execute("UPDATE mailbox_sync_jobs SET status=%s,scheduled_for=now()+(%s||' minutes')::interval,completed_at=CASE WHEN %s='DEAD_LETTER' THEN now() ELSE NULL END,error_message=%s WHERE id=%s",(status,delay_minutes,status,error,job_id))
    return status


def insert_message(mailbox: dict[str,Any], message: dict[str,Any], score: float) -> tuple[dict[str,Any],bool]:
    mid=_id()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO mailbox_messages(id,mailbox_id,candidate_id,provider_message_id,provider_thread_id,sender_name,sender_email,
          recipient_email,subject,sent_at,message_hash,body_hash,recruitment_relevance_score,processing_status,body_text,html_body_text,
          authentication_results,received_spf,rfc_message_id,message_direction,gmail_label_ids,to_metadata,
          reply_to_email,return_path_email,created_at,updated_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'FILTERED',%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,now(),now())
          ON CONFLICT(mailbox_id,provider_message_id) DO UPDATE SET
            body_text=COALESCE(NULLIF(EXCLUDED.body_text,''),mailbox_messages.body_text),
            html_body_text=COALESCE(NULLIF(EXCLUDED.html_body_text,''),mailbox_messages.html_body_text),
            body_hash=CASE WHEN NULLIF(EXCLUDED.body_text,'') IS NOT NULL THEN EXCLUDED.body_hash ELSE mailbox_messages.body_hash END,
            authentication_results=COALESCE(EXCLUDED.authentication_results,mailbox_messages.authentication_results),
            received_spf=COALESCE(EXCLUDED.received_spf,mailbox_messages.received_spf),
            reply_to_email=COALESCE(EXCLUDED.reply_to_email,mailbox_messages.reply_to_email),
            return_path_email=COALESCE(EXCLUDED.return_path_email,mailbox_messages.return_path_email),
            rfc_message_id=COALESCE(EXCLUDED.rfc_message_id,mailbox_messages.rfc_message_id),
            message_direction=COALESCE(EXCLUDED.message_direction,mailbox_messages.message_direction),
            gmail_label_ids=CASE WHEN EXCLUDED.gmail_label_ids<>'[]'::jsonb THEN EXCLUDED.gmail_label_ids ELSE mailbox_messages.gmail_label_ids END,
            to_metadata=CASE WHEN EXCLUDED.to_metadata<>'[]'::jsonb THEN EXCLUDED.to_metadata ELSE mailbox_messages.to_metadata END,
            recruitment_relevance_score=GREATEST(COALESCE(mailbox_messages.recruitment_relevance_score,0),EXCLUDED.recruitment_relevance_score),updated_at=now()
          RETURNING *, (xmax = 0) AS was_created""",
          (mid,mailbox['id'],mailbox['candidate_id'],message['provider_message_id'],message.get('provider_thread_id'),message.get('sender_name'),message.get('sender_email'),message.get('recipient_email'),message.get('subject'),message.get('sent_at'),message['message_hash'],message['body_hash'],score,message.get('body'),message.get('html_body'),message.get('authentication_results'),message.get('received_spf'),message.get('rfc_message_id'),message.get('message_direction'),json.dumps(message.get('gmail_label_ids') or []),json.dumps(message.get('to_metadata') or []),message.get('reply_to_email'),message.get('return_path_email')))
        rows=_rows(cur) if cur.description else []
    if not rows:return {},False
    row=rows[0];created=bool(row.pop('was_created',False));return row,created


def mark_reprocessed(message_id: str, previous_status: str | None, new_status: str, reason: str) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE mailbox_messages SET previous_processing_status=%s,processing_status=%s,
          reprocessed_at=now(),reprocessing_reason=%s,reprocessing_prompt_version='recruitment_email_status_extraction_v3',
          semantic_classifier_version='v3',updated_at=now()
          WHERE id=%s""", (previous_status,new_status,reason,message_id))


def stored_message(mailbox_id: str, provider_message_id: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mailbox_messages WHERE mailbox_id=%s AND provider_message_id=%s", (mailbox_id,provider_message_id))
        rows=_rows(cur)
    return rows[0] if rows else None


#: Operations time. "Today" means the operator's day, not UTC's.
_QUEUE_TIMEZONE = "Asia/Kolkata"

#: When a mail became this pipeline's work.
#:
#: Usually that is when it was sent, but mail does not always reach us as it is
#: written. A mailbox reconnects after its token expired, a Gmail history gap is
#: backfilled, a sync catches up -- and a batch lands carrying a `sent_at` from
#: days ago. Such a mail has never been read, never been classified, and may
#: describe something that has not happened yet, so as far as the queue is
#: concerned it arrived when we received it, not when it was written.
#:
#: Bounded by how stale it already was on arrival, because a genuine archive
#: sweep also lands with a recent `created_at`. Without that bound, importing an
#: old mailbox would promote thousands of dead mails ahead of live ones and
#: recreate the exact stall this tiering exists to prevent.
_QUEUE_ARRIVAL_SQL = """CASE
      WHEN COALESCE(sent_at,created_at) >= created_at-(%s||' days')::interval
        THEN GREATEST(COALESCE(sent_at,created_at),created_at)
      ELSE COALESCE(sent_at,created_at) END"""

#: A job board addresses nobody: it blasts vacancies, and its subjects say
#: "interview" as often as a real invitation does. 1,231 of the 2,082 mails
#: queued on 2026-09-16 were this shape. Naming them is what lets the tier below
#: promote an invitation without promoting the catalogue it arrived beside.
_JOB_BOARD_SQL = (
    r"(sender_email ~* '(naukri|hirist|indeed|shine|foundit|monster|timesjobs|jobrapido|"
    "ambitionbox|flexjobs|ziprecruiter|glassdoor|jobalert)'"
    r" OR subject ~* '^(\W*)?job \||job alert|top .*(jobs|roles)|apply now|jobs? (for|picked)')"
)

#: An interview or an assessment has an hour attached to it, so it is worth
#: nothing once that hour passes. Measured on the production queue, mail whose
#: subject names one and whose sender is not a job board averaged 0.59
#: recruitment relevance against 0.01 for the noise -- 144 mails out of 2,082.
_TIME_CRITICAL_SQL = (
    "(subject ~* '(interview|assessment|coding test|online test|walk-?in)' AND NOT "
    + _JOB_BOARD_SQL + ")"
)

#: Tier 0 cannot wait, tier 1 just arrived, tier 2 is the rest of today, tier 3
#: is history. Tier 0 is deliberately narrow: recent, and about this person's
#: own interview or test.
_TIER_SQL = f"""CASE
      WHEN {_QUEUE_ARRIVAL_SQL} >= now()-(%s||' days')::interval AND {_TIME_CRITICAL_SQL} THEN 0
      WHEN {_QUEUE_ARRIVAL_SQL} >= now()-(%s||' hours')::interval THEN 1
      WHEN {_QUEUE_ARRIVAL_SQL} >= (date_trunc('day', now() AT TIME ZONE %s) AT TIME ZONE %s) THEN 2
      ELSE 3 END"""

#: Rotates across claims so one in every `_backlog_share()` goes to history.
#: Process-local, which is enough: the worker is one process, and several
#: workers would each keep their own rotation and still average the same split.
_claim_rotation = itertools.count()


def _live_mail_window_hours() -> int:
    """How recent a mail must be to count as just-arrived (tier 1).

    Two hours. A mail that lands now must reach the model in minutes, and the
    only way to promise that is a tier small enough to be nearly empty: at ~50
    messages/hour of throughput, two hours of arrivals is a handful of rows.
    """
    try:
        return max(1, min(720, int(os.getenv("AI_MAIL_LIVE_WINDOW_HOURS", "2"))))
    except (TypeError, ValueError):
        return 2


def _ingest_freshness_days() -> int:
    """How stale a mail may already be on arrival and still count as new work.

    Three days. Measured against the production queue on 2026-09-10, where
    2,456 mails were claimable: three days promotes 49 of them and leaves 2,405
    in history, including the 116 ingested that same day whose `sent_at` ran
    back to 2026-08-27. It is wide enough to cover a mailbox that reconnects
    after a weekend, and narrow enough that the promoted set drains in about an
    hour at the measured 54 analyses/hour.

    Zero disables the promotion entirely and restores tiering by `sent_at`.
    """
    try:
        return max(0, min(30, int(os.getenv("AI_MAIL_INGEST_FRESHNESS_DAYS", "3"))))
    except (TypeError, ValueError):
        return 3


def _time_critical_days() -> int:
    """How recently an interview or assessment mail must have arrived to lead.

    Three days. An invitation for tomorrow can arrive today, and a reminder for
    today can arrive overnight; beyond that the hour it names has usually gone,
    and promoting it would only push live mail behind history again.
    """
    try:
        return max(1, min(30, int(os.getenv("AI_MAIL_TIME_CRITICAL_DAYS", "3"))))
    except (TypeError, ValueError):
        return 3


def _backlog_share() -> int:
    """One claim in N is spent on history, so it can never starve.

    Five by default -- 80% of capacity to live and today's mail, 20% to the
    backlog. The backlog is never excluded from a claim either: when no live
    mail is waiting the ordinary turns fall through to it as well, so the split
    is a floor on history's share, not a ceiling.
    """
    try:
        return max(2, min(50, int(os.getenv("AI_MAIL_BACKLOG_SHARE", "5"))))
    except (TypeError, ValueError):
        return 5


def _busy_backlog_percent() -> int:
    """History's share while it is deep enough to need one.

    Thirty percent. The backlog cleared at 20% too slowly to matter -- 1,861
    mails at one turn in five -- and every point taken from live mail is a point
    of delay for an interview, so this is a lift rather than a reversal, and it
    lasts only while the backlog is deep.
    """
    try:
        return max(10, min(60, int(os.getenv("AI_MAIL_BACKLOG_BUSY_PERCENT", "30"))))
    except (TypeError, ValueError):
        return 30


def _backlog_relief_below() -> int:
    """How small history has to get before its share returns to normal."""
    try:
        return max(50, min(10000, int(os.getenv("AI_MAIL_BACKLOG_RELIEF_BELOW", "500"))))
    except (TypeError, ValueError):
        return 500


def _backlog_percent(history_waiting: int | None = None) -> int:
    """History's share of claims, lifted only while history is deep.

    The lift is measured, not scheduled: the claim counts what is actually
    waiting in tier 3 and the share follows it, so it returns to normal by
    itself the moment the backlog is down. Nothing has to remember to undo it.
    """
    normal = max(2, round(100 / _backlog_share()))
    if history_waiting is None or history_waiting < _backlog_relief_below():
        return normal
    return max(normal, _busy_backlog_percent())


def _claim_prefers_backlog(history_waiting: int | None = None) -> bool:
    """Whether this turn goes to history first, spread evenly across turns.

    A share of 20% falls on every fifth turn and 30% on turns 3, 6 and 9 of
    every ten -- never bunched, because a clump of history turns is exactly the
    delay live mail cannot afford.
    """
    percent = _backlog_percent(history_waiting)
    turn = next(_claim_rotation)
    return (turn + 1) * percent // 100 > turn * percent // 100


def _max_ai_attempts() -> int:
    """Attempts before a message is parked terminally rather than requeued.

    Twelve. This existed for weeks without a single caller, so nothing ever
    stopped: one mail reached 43 attempts, and on 2026-09-16 the 559 messages
    carrying a retry count had consumed 4,731 model runs between them -- most of
    the pipeline's capacity spent re-reading mail it had already failed to read.
    """
    try:
        return max(3, min(50, int(os.getenv("AI_MAIL_MAX_AI_ATTEMPTS", "12"))))
    except (TypeError, ValueError):
        return 12


#: Outcomes that are a verdict about the mail, not a failure to read it.
#:
#: The evidence guard fires when the quoted evidence does not support the
#: transition the model claimed; relevance-unresolved means the pipeline could
#: not establish the mail is recruitment at all. Neither is transient: the same
#: mail, the same model and the same prompt reach the same answer, so a retry
#: is a re-run of a question already answered.
#:
#: Measured on the production queue, 2026-09-17: 408 messages carried
#: EVIDENCE_DOES_NOT_ENTAIL_TRANSITION at an average of 5.7 attempts each and
#: exactly one had ever come back from it; 156 carried
#: RECRUITMENT_RELEVANCE_UNRESOLVED, average 6.4 attempts, and none had. That is
#: roughly 3,200 model runs spent, with thousands more scheduled, at a recovery
#: rate of one in 565 -- against a pipeline that manages about 900 runs a day.
DETERMINISTIC_AI_FAILURE_CODES = frozenset({
    "EVIDENCE_DOES_NOT_ENTAIL_TRANSITION",
    "RECRUITMENT_RELEVANCE_UNRESOLVED",
})


def _deterministic_ai_attempts() -> int:
    """Attempts allowed for a verdict rather than a failure.

    Two, not one: a node answers deterministically for a given model load, so a
    second attempt can land on a different node and legitimately differ. Two
    keeps that chance and stops there, which is where the measured recovery
    rate said the value had already run out.
    """
    try:
        return max(1, min(12, int(os.getenv("AI_MAIL_DETERMINISTIC_ATTEMPTS", "2"))))
    except (TypeError, ValueError):
        return 2


def claim_ai_messages(limit: int = 1, *, lease_seconds: int = 150) -> list[dict[str, Any]]:
    """Lease queued semantic work so crashes/timeouts cannot lose or duplicate it."""
    with get_connection() as conn, conn.cursor() as cur:
        # No semantic outcome may be abandoned in a terminal manual-review
        # bucket.  Leases are reclaimed as retry work and exponential backoff
        # controls load; retry count is diagnostic, not a reason to lose mail.
        cur.execute("""UPDATE mailbox_messages SET processing_status='AI_QUEUED',ai_lease_expires_at=NULL,
              updated_at=now(),ai_last_error_code='LEASE_EXPIRED'
            WHERE processing_status='AI_RUNNING' AND ai_lease_expires_at<now()""")
        # Mail already past the attempt cap is parked here rather than analysed
        # once more and parked afterwards. When the cap was first enforced, 120
        # of the 2,082 queued messages were already over it; making each of them
        # spend one more model run to learn that would have cost most of a day
        # of capacity that live mail needed.
        cur.execute("""UPDATE mailbox_messages SET processing_status='AI_PROCESSING_FAILED',
              ai_last_error_code='AI_ATTEMPTS_EXHAUSTED',ai_retry_after=NULL,ai_lease_expires_at=NULL,
              updated_at=now()
            WHERE processing_status IN ('AI_QUEUED','AI_RETRY_PENDING') AND ai_retry_count>=%s""",
            (_max_ai_attempts(),))
        # The same argument for the smaller budget: a mail already past it
        # would otherwise spend one more model run to be told again what it was
        # told the first two times. Its own reason is kept -- that is what makes
        # the parked set readable afterwards.
        cur.execute("""UPDATE mailbox_messages SET processing_status='AI_PROCESSING_FAILED',
              ai_retry_after=NULL,ai_lease_expires_at=NULL,updated_at=now()
            WHERE processing_status IN ('AI_QUEUED','AI_RETRY_PENDING')
              AND ai_last_error_code=ANY(%s) AND ai_retry_count>=%s""",
            (sorted(DETERMINISTIC_AI_FAILURE_CODES),
             min(_deterministic_ai_attempts(), _max_ai_attempts())))
        # Live mail first, then the backlog -- each oldest-first within itself.
        #
        # Strict `ORDER BY sent_at ASC` is FIFO over the whole table, so a large
        # historical backlog blocks the head of the queue and today's mail waits
        # behind all of it. On 2026-09-09 that stopped Mail Alerts dead: 3,403
        # messages were queued, 2,871 of them older than two days, and the 117
        # mails that arrived after 11:00 sat untouched behind them. Nothing was
        # broken -- ingestion, classification, notifications and the API were all
        # healthy -- there was simply nothing new to show, and at ~174/hour it
        # would have been about twenty hours before live mail was even reached.
        #
        # Recent mail is therefore claimed first. The backlog still drains,
        # because it is claimed whenever no live mail is waiting, and inbound
        # (~300/day) sits well under capacity. COALESCE guards a null sent_at,
        # which would otherwise make the tier NULL and sort to the very front.
        # Three tiers, and one claim in five reserved for history.
        #
        # Ordering by tier alone would let a large backlog wait forever behind
        # steady arrivals; ordering by age alone is what stalled Mail Alerts for
        # six hours on 2026-09-09. So most turns take the newest tier available
        # and every fifth turn takes history first. Neither side can starve: the
        # backlog has a guaranteed share, and a history turn still falls through
        # to live mail when nothing old is waiting, because no tier is filtered
        # out of the claim.
        #
        # FIFO is preserved inside each tier -- `sent_at ASC` after the tier --
        # so a thread is still processed in the order it arrived.
        #
        # A tier is decided by when the mail reached us, not only by when it
        # was written -- see `_QUEUE_ARRIVAL_SQL`. Konduru Srinivas's HCLTech
        # interview was missed because those two are not the same thing. The
        # invitation was sent on 2026-09-08 and ingested on 2026-09-10; tiering
        # on `sent_at` alone filed a mail we had owned for four minutes as
        # history, at rank 2279 of 2456. At ~60 analyses/hour the model would
        # have reached it about 33 hours later -- a full day after the 12:00
        # interview it was announcing.
        live_hours=_live_mail_window_hours()
        fresh_days=_ingest_freshness_days()
        critical_days=_time_critical_days()
        tier=_TIER_SQL
        tier_params=(fresh_days,critical_days,fresh_days,live_hours,fresh_days,_QUEUE_TIMEZONE,_QUEUE_TIMEZONE)
        # How deep history actually is, read rather than assumed: the share it
        # gets follows this number, so the lift ends by itself once the backlog
        # is down and nothing has to remember to undo it.
        cur.execute(f"""SELECT count(*) FROM mailbox_messages
              WHERE processing_status IN ('AI_QUEUED','AI_RETRY_PENDING')
                AND COALESCE(ai_retry_after,now())<=now()
                AND ({tier})=3""",tier_params)
        history_waiting=int(cur.fetchone()[0] or 0)
        if _claim_prefers_backlog(history_waiting):
            # Even the turn reserved for history yields to an interview or an
            # assessment that has just arrived: the backlog will still be there
            # in an hour, and the interview will not.
            order=f"({tier} = 0) DESC,({tier} = 3) DESC,{tier} ASC"
            order_params=tier_params*3
        else:
            order=f"{tier} ASC"
            order_params=tier_params
        cur.execute(f"""SELECT id FROM mailbox_messages
          WHERE processing_status IN ('AI_QUEUED','AI_RETRY_PENDING')
            AND COALESCE(ai_retry_after,now())<=now()
          ORDER BY {order},sent_at ASC,id
          FOR UPDATE SKIP LOCKED LIMIT %s""",
          order_params+(max(1,min(limit,20)),))
        ids=[row[0] for row in cur.fetchall()]
        if not ids:return []
        cur.execute("""UPDATE mailbox_messages m SET processing_status='AI_RUNNING',
              ai_lease_expires_at=now()+(%s||' seconds')::interval,updated_at=now()
            FROM candidate_mailboxes b WHERE m.id=ANY(%s) AND b.id=m.mailbox_id
            RETURNING m.*,b.email_address,b.candidate_id AS mailbox_candidate_id""",
            (max(30,min(int(lease_seconds),900)),ids))
        rows=_rows(cur)
    for row in rows:
        row['attachments']=[{**item,'text':item.get('extracted_text') or ''} for item in attachments_for_message(row['id'],include_text=True)]
    # What the queue is actually doing, in the log, per claim: the tier taken,
    # how long that mail waited, and the share history is getting. Raising
    # history's share is only safe while the first number stays small for tier
    # 0, and this is what makes that visible without a query.
    for row in rows:
        arrival=row.get('sent_at') or row.get('created_at')
        waited=(datetime.now(timezone.utc)-arrival).total_seconds()/60 if arrival else -1
        logger.info(
            "AI claim message=%s waited=%.1fmin history_waiting=%d backlog_share=%d%%",
            row['id'], waited, history_waiting, _backlog_percent(history_waiting),
        )
    return rows


def retry_pending_messages(limit: int = 20) -> list[dict[str, Any]]:
    """Compatibility read used by diagnostics; workers should claim leases."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT m.*,b.email_address,b.candidate_id AS mailbox_candidate_id
          FROM mailbox_messages m JOIN candidate_mailboxes b ON b.id=m.mailbox_id
          WHERE m.processing_status IN ('AI_QUEUED','AI_RETRY_PENDING')
            AND COALESCE(m.ai_retry_after,now())<=now()
          ORDER BY m.sent_at ASC LIMIT %s""",(max(1,min(limit,100)),))
        rows=_rows(cur)
    for row in rows:
        row['attachments']=[{**item,'text':item.get('extracted_text') or ''} for item in attachments_for_message(row['id'],include_text=True)]
    return rows


def schedule_ai_retry(message_id: str, *, succeeded: bool) -> None:
    deterministic = sorted(DETERMINISTIC_AI_FAILURE_CODES)
    cap = _max_ai_attempts()
    determined_cap = min(_deterministic_ai_attempts(), cap)
    with get_connection() as conn, conn.cursor() as cur:
        if succeeded:
            cur.execute("UPDATE mailbox_messages SET ai_retry_after=NULL,ai_lease_expires_at=NULL,ai_last_error_code=NULL,updated_at=now() WHERE id=%s",(message_id,))
        else:
            # Past the cap the mail is parked rather than requeued: it keeps its
            # row, its analyses and its error code, and stops consuming model
            # runs. `AI_ATTEMPTS_EXHAUSTED` says which of the terminal failures
            # this is, so it can be found and re-opened deliberately.
            #
            # The budget depends on what failed. A verdict about the mail is
            # not a failure to read it, and re-running the same model over the
            # same mail asks a question already answered -- so those codes get
            # `_deterministic_ai_attempts()` and keep their own reason when
            # parked, because "the evidence did not entail the transition" is
            # the useful thing to find later; `AI_ATTEMPTS_EXHAUSTED` would
            # replace it with the fact that it gave up.
            cur.execute("""UPDATE mailbox_messages SET ai_retry_count=ai_retry_count+1,
              processing_status=CASE WHEN ai_retry_count+1>=(CASE WHEN ai_last_error_code=ANY(%s)
                    THEN %s ELSE %s END) THEN 'AI_PROCESSING_FAILED' ELSE 'AI_RETRY_PENDING' END,
              ai_last_error_code=CASE
                WHEN ai_last_error_code=ANY(%s) THEN ai_last_error_code
                WHEN ai_retry_count+1>=%s THEN 'AI_ATTEMPTS_EXHAUSTED'
                ELSE ai_last_error_code END,
              ai_retry_after=CASE WHEN ai_retry_count+1>=(CASE WHEN ai_last_error_code=ANY(%s)
                    THEN %s ELSE %s END) THEN NULL
                ELSE now()+(LEAST(360,POWER(2,LEAST(ai_retry_count+1,8)))||' minutes')::interval END,
              ai_lease_expires_at=NULL,
              updated_at=now() WHERE id=%s""",
              (deterministic, determined_cap, cap,
               deterministic, cap,
               deterministic, determined_cap, cap, message_id))


def is_duplicate_content(candidate_id:str,message_id:str,message_hash:str,body_hash:str,subject:str|None=None)->bool:
    """Has this candidate already had an event from an identical message?

    Two independent tests, and the difference matters:

    ``message_hash`` covers sender + subject + sent_at, so it identifies the
    same message arriving twice. That test is exact and is left alone.

    ``body_hash`` is the looser one, and on its own it is too loose. A recruiter
    who books two different interviews from the same template sends two mails
    whose bodies are byte-identical — the substance lives in the subject and in
    the invitation, not the covering note. That happened: two Sourcebae invites
    for the same candidate shared body hash b039d324…, so the second interview
    was dropped as a resend of the first and never reached booking at all.

    So a body match now also requires the subject to match. A genuine resend
    still has both; two different interviews do not.
    """
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM mailbox_messages m JOIN ai_recruitment_events e ON e.mailbox_message_id=m.id
          WHERE m.candidate_id=%s AND m.id<>%s AND (
                m.message_hash=%s
                OR (m.body_hash=%s AND %s<>%s
                    AND COALESCE(m.subject,'')=COALESCE(%s,''))
          ) LIMIT 1""",(candidate_id,message_id,message_hash,body_hash,body_hash,content_hash_empty(),subject))
        return cur.fetchone() is not None


def content_hash_empty()->str:
    import hashlib
    return hashlib.sha256(b'').hexdigest()


def mark_message_status(message_id:str,status:str,*,reason:str|None=None,cleanup_version:str|None=None,
                        error_code:str|None=None)->None:
    """Set the message's processing state.

    ``error_code`` also writes ``ai_last_error_code``, which is the field the
    queue is diagnosed from — ``ignore_reason`` keeps older text and has already
    misled one investigation. A retry parked without a live code looks like an
    unexplained backlog: 24 messages sat on OLLAMA_SCHEMA_VALIDATION_FAILED with
    a null code and nothing said why.
    """
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("""UPDATE mailbox_messages SET processing_status=%s,ignore_reason=%s,
          ignored_at=CASE WHEN %s LIKE 'IGNORED%%' THEN now() ELSE ignored_at END,
          cleanup_version=COALESCE(%s,cleanup_version),semantic_classifier_version='v3',
          ai_last_error_code=COALESCE(%s,ai_last_error_code),
          ai_lease_expires_at=CASE WHEN %s='AI_RUNNING' THEN ai_lease_expires_at ELSE NULL END,updated_at=now() WHERE id=%s""",
          (status,reason,status,cleanup_version,error_code,status,message_id))
    # Mail-level terminal and retry paths frequently occur before an event is
    # created (for example marketing, an exact Gmail duplicate, or an Ollama
    # outage).  Give those messages the same durable, auditable automated
    # state as an event-backed booking.  This is deliberately after the
    # original status write so the transition layer can be added without
    # weakening ingestion's primary persistence boundary.
    normalized = str(status or "").upper()
    if normalized in {
        "IGNORED_NOT_OFFER_RELATED",
        "DUPLICATE_CONTENT", "DUPLICATE_OFFER_EVENT", "DUPLICATE_OFFER_ATTACHMENT",
    }:
        record_automation_state(
            mailbox_message_id=message_id, event_id=None, state="AUTO_IGNORE",
            reason=reason or normalized, details={"source_status": normalized},
        )
    elif normalized in {"AI_RETRY_PENDING", "VALIDATION_FAILED", "MANUAL_REVIEW_REQUIRED", "IGNORED_LOW_CONFIDENCE"}:
        record_automation_state(
            mailbox_message_id=message_id, event_id=None, state="AI_RETRY_PENDING",
            reason=reason or normalized, details={"source_status": normalized},
        )


_AUTOMATION_STATES = frozenset({
    "AUTO_BOOK", "AUTO_IGNORE", "AI_RETRY_PENDING",
    "AUTO_BOOKED", "AUTO_CANCELLED", "AUTO_RESCHEDULED",
})


def _automation_dedupe_key(
    mailbox_message_id: str, state: str, *, booking_id: str | None, reason: str | None,
) -> str:
    material = "\x1f".join((mailbox_message_id, state, str(booking_id or ""), str(reason or "")))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _record_automation_transition(
    cur: Any, *, mailbox_message_id: str, event_id: str | None, state: str,
    reason: str | None = None, booking_id: str | None = None, details: dict[str, Any] | None = None,
) -> None:
    if state not in _AUTOMATION_STATES:
        raise ValueError(f"Unsupported automation state: {state}")
    dedupe_key = _automation_dedupe_key(
        mailbox_message_id, state, booking_id=booking_id, reason=reason,
    )
    cur.execute(
        """INSERT INTO recruitment_automation_transitions(
              id,mailbox_message_id,ai_recruitment_event_id,automation_state,
              reason,booking_id,dedupe_key,details,created_at)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,now())
           ON CONFLICT(dedupe_key) DO NOTHING""",
        (
            _id(), mailbox_message_id, event_id, state, reason, booking_id,
            dedupe_key, json.dumps(details or {}, default=str),
        ),
    )


def record_automation_state(
    *, mailbox_message_id: str, event_id: str | None, state: str,
    reason: str | None = None, booking_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Atomically project an automation result without erasing raw evidence.

    ``AUTO_BOOKED``/``AUTO_RESCHEDULED`` are called only by the executor after
    its canonical candidate-slot re-read.  Retry states stay durable and are
    reclaimed by the mailbox worker; no state is parked for a human action.
    """
    if not use_postgres():
        return
    if state not in _AUTOMATION_STATES:
        raise ValueError(f"Unsupported automation state: {state}")
    retry = state == "AI_RETRY_PENDING"
    ignored = state == "AUTO_IGNORE"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM mailbox_messages WHERE id=%s FOR UPDATE", (mailbox_message_id,))
        if not cur.fetchone():
            return
        _record_automation_transition(
            cur, mailbox_message_id=mailbox_message_id, event_id=event_id,
            state=state, reason=reason, booking_id=booking_id, details=details,
        )
        cur.execute(
            """UPDATE mailbox_messages SET processing_status=%s,
                 ai_retry_after=CASE WHEN %s THEN now() ELSE NULL END,
                 ai_lease_expires_at=NULL,ignore_reason=COALESCE(%s,ignore_reason),
                 ignored_at=CASE WHEN %s THEN now() ELSE ignored_at END,
                 updated_at=now() WHERE id=%s""",
            (state, retry, reason, ignored, mailbox_message_id),
        )
        if event_id:
            cur.execute(
                """UPDATE ai_recruitment_events SET automation_state=%s,
                     requires_manual_review=false,review_status='AUTOMATED',
                     structured_result=jsonb_set(
                       COALESCE(structured_result,'{}'::jsonb),
                       '{automation_state}',to_jsonb(%s::text),true),updated_at=now()
                   WHERE id=%s""",
                (state, state, event_id),
            )
        cur.execute(
            """INSERT INTO recruitment_audit_log(id,actor,role,action,source_id,new_value,created_at)
               VALUES(%s,'system','system','RECRUITMENT_AUTOMATION_STATE',%s,%s::jsonb,now())""",
            (_id(), event_id or mailbox_message_id, json.dumps({
                "state": state, "reason": reason, "booking_id": booking_id,
            }, default=str)),
        )


def promote_legacy_review_states(limit: int = 50) -> int:
    """Replace legacy human-review rows with automatic retry work.

    The original classifier status remains in ``original_primary_status`` and
    the structured result, so this is a reversible projection rather than a
    destructive rewrite.  Row locks plus transition dedupe make concurrent
    worker passes harmless.
    """
    if not use_postgres():
        return 0
    promoted = 0
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT e.id,e.mailbox_message_id
                 FROM ai_recruitment_events e
                 JOIN mailbox_messages m ON m.id=e.mailbox_message_id
                WHERE COALESCE(e.automation_state,'') NOT IN (
                        'AUTO_BOOK','AUTO_IGNORE','AI_RETRY_PENDING',
                        'AUTO_BOOKED','AUTO_CANCELLED','AUTO_RESCHEDULED')
                  AND (e.requires_manual_review=true OR e.classification='needs_review'
                       OR e.primary_status='MANUAL_REVIEW_REQUIRED'
                       OR e.validation_status IN ('NEEDS_REVIEW','REVIEW_REQUIRED'))
                ORDER BY e.updated_at ASC
                FOR UPDATE OF e,m SKIP LOCKED LIMIT %s""",
            (max(1, min(limit, 500)),),
        )
        rows = cur.fetchall()
        for event_id, message_id in rows:
            reason = "LEGACY_REVIEW_CONVERTED_TO_AUTOMATIC_RETRY"
            _record_automation_transition(
                cur, mailbox_message_id=message_id, event_id=event_id,
                state="AI_RETRY_PENDING", reason=reason,
            )
            cur.execute(
                """UPDATE mailbox_messages SET processing_status='AI_RETRY_PENDING',
                     ai_retry_count=COALESCE(ai_retry_count,0)+1,
                     ai_retry_after=now()+(LEAST(360,POWER(2,LEAST(COALESCE(ai_retry_count,0)+1,8)))||' minutes')::interval,
                     ai_lease_expires_at=NULL,ai_last_error_code=%s,updated_at=now()
                   WHERE id=%s""",
                (reason, message_id),
            )
            cur.execute(
                """UPDATE ai_recruitment_events SET
                     original_primary_status=COALESCE(original_primary_status,primary_status),
                     primary_status='AI_RETRY_PENDING',classification='ai_retry_pending',
                     candidate_status='AI Retry Pending',automation_state='AI_RETRY_PENDING',
                     requires_manual_review=false,review_status='AUTOMATED',
                     validation_status='RETRY_PENDING',ai_status='AI_RETRY_PENDING',
                     structured_result=jsonb_set(
                       jsonb_set(COALESCE(structured_result,'{}'::jsonb),
                         '{automation_decision}','"AI_RETRY_PENDING"'::jsonb,true),
                       '{automation_state}','"AI_RETRY_PENDING"'::jsonb,true),
                     updated_at=now() WHERE id=%s""",
                (event_id,),
            )
            cur.execute(
                """INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,new_value,created_at)
                     SELECT %s,'system','system','LEGACY_REVIEW_AUTO_RETRIED',candidate_id,%s,%s::jsonb,now()
                       FROM ai_recruitment_events WHERE id=%s""",
                (_id(), event_id, json.dumps({"state": "AI_RETRY_PENDING", "reason": reason}), event_id),
            )
            promoted += 1
    return promoted


def promote_ignored_messages(limit: int = 200) -> int:
    """Give non-recruitment/marketing messages an explicit automated outcome."""
    if not use_postgres():
        return 0
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id,ignore_reason FROM mailbox_messages
                WHERE processing_status IN ('IGNORED_NOT_OFFER_RELATED','IGNORED_LOW_CONFIDENCE',
                                            'DUPLICATE_CONTENT','DUPLICATE_OFFER_EVENT')
                ORDER BY updated_at ASC FOR UPDATE SKIP LOCKED LIMIT %s""",
            (max(1, min(limit, 1000)),),
        )
        rows = cur.fetchall()
        for message_id, reason in rows:
            _record_automation_transition(
                cur, mailbox_message_id=message_id, event_id=None,
                state="AUTO_IGNORE", reason=str(reason or "AUTOMATED_IGNORE"),
            )
            cur.execute(
                """UPDATE mailbox_messages SET processing_status='AUTO_IGNORE',
                     ignored_at=COALESCE(ignored_at,now()),updated_at=now() WHERE id=%s""",
                (message_id,),
            )
    return len(rows)


_CALENDAR_RECOVERY_VERSION = "calendar-evidence-v1"
_CALENDAR_FALSE_IGNORE_REASONS = (
    "CALENDAR_INVITE_NOT_A_CANDIDATE_INTERVIEW",
    "EVIDENCE_DOES_NOT_ENTAIL_TRANSITION",
    "CALENDAR_RELEVANCE_CONTRADICTION",
)


def claim_calendar_invite_recovery_messages(
    *, provider_message_ids: list[str], limit: int = 5,
) -> list[dict[str, Any]]:
    """Lease a small, versioned set of previously ignored calendar invites.

    This is intentionally not a historical mailbox replay. Only an explicit,
    bounded set of provider message ids with an `.ics` attachment and a known
    calendar false-ignore reason is eligible. A successful ignore is
    reconsidered only after the recovery rules receive a new version.
    """
    ids = sorted({str(value).strip() for value in provider_message_ids if str(value).strip()})
    if not use_postgres() or not ids:
        return []
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.*,b.email_address,b.candidate_id AS mailbox_candidate_id
                 FROM mailbox_messages m
                 JOIN candidate_mailboxes b ON b.id=m.mailbox_id
                 LEFT JOIN recruitment_calendar_recovery r ON r.mailbox_message_id=m.id
                WHERE m.processing_status='AUTO_IGNORE'
                  AND m.provider_message_id=ANY(%s)
                  AND m.ignore_reason=ANY(%s)
                  AND EXISTS(
                    SELECT 1 FROM mailbox_attachments a
                     WHERE a.mailbox_message_id=m.id
                       AND lower(COALESCE(a.filename,'')) LIKE '%%.ics'
                  )
                  AND (
                    r.mailbox_message_id IS NULL
                    OR r.detection_version<>%s
                    OR (r.state='AI_RETRY_PENDING' AND COALESCE(r.next_attempt_at,now())<=now())
                  )
                ORDER BY m.updated_at ASC
                FOR UPDATE OF m SKIP LOCKED LIMIT %s""",
            (ids, list(_CALENDAR_FALSE_IGNORE_REASONS), _CALENDAR_RECOVERY_VERSION, max(1, min(limit, 50))),
        )
        rows = _rows(cur)
        for row in rows:
            cur.execute(
                """INSERT INTO recruitment_calendar_recovery(
                      mailbox_message_id,detection_version,state,attempts,last_reason,next_attempt_at,updated_at)
                   VALUES(%s,%s,'RUNNING',1,NULL,NULL,now())
                   ON CONFLICT(mailbox_message_id) DO UPDATE SET
                     detection_version=EXCLUDED.detection_version,state='RUNNING',
                     attempts=recruitment_calendar_recovery.attempts+1,
                     next_attempt_at=NULL,updated_at=now()""",
                (row["id"], _CALENDAR_RECOVERY_VERSION),
            )
    for row in rows:
        row["attachments"] = [
            {**item, "text": item.get("extracted_text") or ""}
            for item in attachments_for_message(row["id"], include_text=True)
        ]
    return rows


def complete_calendar_invite_recovery(message_id: str, *, state: str, reason: str | None = None) -> None:
    """Record a recovery outcome; only retryable results are scheduled again."""
    if not use_postgres():
        return
    safe_state = state if state in {
        "AUTO_IGNORE", "AI_RETRY_PENDING", "AUTO_BOOKED", "AUTO_RESCHEDULED", "AUTO_CANCELLED",
    } else "AI_RETRY_PENDING"
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE recruitment_calendar_recovery SET state=%s,last_reason=%s,
                 next_attempt_at=CASE WHEN %s='AI_RETRY_PENDING'
                   THEN now()+interval '15 minutes' ELSE NULL END,
                 updated_at=now() WHERE mailbox_message_id=%s""",
            (safe_state, reason, safe_state, message_id),
        )


def calendar_invite_recovery_discovery(*, limit: int = 500) -> dict[str, Any]:
    """Read-only inventory of historical calendar ignores.

    This deliberately does *not* lease, reprocess, or alter a message.  It is
    the safety valve before expanding a recovery list: an operator can see
    whether an old invite is represented by a persisted slot, was
    cancelled, or is merely a candidate for the narrowly configured recovery
    worker.
    """
    empty = {
        "summary": {
            "total": 0, "recovery_candidates": 0, "already_represented": 0,
            "stale_or_cancelled": 0, "already_assessed": 0,
        },
        "records": [],
    }
    if not use_postgres():
        return empty
    from services.calendar_recovery_discovery import load_report
    return load_report(limit=limit)


# Only genuine offer documents identify a duplicate OFFER. Recurring documents
# such as a resume, or a new interview invite, must never suppress a distinct
# event just because the same file was attached to an earlier email.
_OFFER_DOCUMENT_TYPES = ("OFFER_LETTER", "APPOINTMENT_LETTER", "JOINING_LETTER", "COMPENSATION_BREAKUP")


def is_duplicate_offer_attachment(candidate_id:str,message_id:str)->bool:
    """True when an OFFER-document checksum already belongs to a visible event for
    this candidate (scoped to offer documents so a recurring resume or a new
    interview invitation never suppresses a distinct event)."""
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM mailbox_attachments current_attachment
          JOIN mailbox_attachments previous_attachment ON previous_attachment.checksum=current_attachment.checksum
            AND previous_attachment.mailbox_message_id<>current_attachment.mailbox_message_id
          JOIN ai_recruitment_events e ON e.mailbox_message_id=previous_attachment.mailbox_message_id
          WHERE current_attachment.mailbox_message_id=%s AND e.candidate_id=%s
            AND current_attachment.attachment_type = ANY(%s)
            AND e.review_status NOT IN('FALSE_POSITIVE','DUPLICATE') LIMIT 1""",(message_id,candidate_id,list(_OFFER_DOCUMENT_TYPES)))
        return cur.fetchone() is not None


def is_duplicate_thread_status(candidate_id:str,message_id:str,status:str)->bool:
    """Suppress repeated reminders with the same status in the same Gmail thread.

    A reminder repeats an event that still stands. A fresh invitation sent after
    its booking was cancelled does not: the organiser is rebooking, and the mail
    that says so is a new interview, not a second copy of the old one.

    Only the booking connects the two. Google gives a cancellation its own
    thread, so the cancelled invitation and its replacement sit in the thread of
    the original while the cancellation itself sits outside it -- invisible to a
    query that reads the thread alone. Reading the thread alone is what dropped a
    re-invitation sent five minutes after a cancellation: the candidate kept a
    cancelled slot, gained no new one, and the interview went ahead unbooked.
    """
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM mailbox_messages current_message
          JOIN mailbox_messages previous_message ON previous_message.provider_thread_id=current_message.provider_thread_id
            AND previous_message.id<>current_message.id
          JOIN ai_recruitment_events e ON e.mailbox_message_id=previous_message.id
          WHERE current_message.id=%s AND current_message.provider_thread_id IS NOT NULL
            AND e.candidate_id=%s AND e.primary_status=%s
            AND e.review_status NOT IN('FALSE_POSITIVE','DUPLICATE')
            AND NOT EXISTS(SELECT 1 FROM interview_auto_booking_audit booked
              JOIN interview_auto_booking_audit cancelled ON cancelled.booking_id=booked.booking_id
                AND cancelled.booking_status='Cancelled' AND cancelled.created_at>booked.created_at
              WHERE booked.gmail_message_id=previous_message.provider_message_id
                AND booked.booking_id IS NOT NULL) LIMIT 1""",(message_id,candidate_id,status))
        return cur.fetchone() is not None


def attachment_cache(checksum: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mailbox_attachment_cache WHERE checksum=%s", (checksum,))
        rows = _rows(cur)
    return rows[0] if rows else None


def save_attachment(message_id: str, attachment: dict[str, Any]) -> dict[str, Any]:
    aid = _id()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO mailbox_attachment_cache(checksum,mime_type,size,extracted_text,extraction_method,attachment_type,created_at,updated_at)
          VALUES(%s,%s,%s,%s,%s,%s,now(),now()) ON CONFLICT(checksum) DO UPDATE SET
          extracted_text=CASE WHEN length(COALESCE(EXCLUDED.extracted_text,''))>0 THEN EXCLUDED.extracted_text ELSE mailbox_attachment_cache.extracted_text END,
          extraction_method=CASE WHEN length(COALESCE(EXCLUDED.extracted_text,''))>0 THEN EXCLUDED.extraction_method ELSE mailbox_attachment_cache.extraction_method END,
          attachment_type=CASE WHEN length(COALESCE(EXCLUDED.extracted_text,''))>0 THEN EXCLUDED.attachment_type ELSE mailbox_attachment_cache.attachment_type END,
          updated_at=now()""",
          (attachment['checksum'],attachment.get('mime_type'),attachment.get('size'),attachment.get('text'),attachment.get('extraction_method'),attachment.get('attachment_type')))
        cur.execute("""INSERT INTO mailbox_attachments(id,mailbox_message_id,filename,mime_type,size,checksum,attachment_type,extraction_status,extracted_text_reference,created_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) ON CONFLICT(mailbox_message_id,checksum) DO UPDATE SET
          filename=EXCLUDED.filename,mime_type=EXCLUDED.mime_type,size=EXCLUDED.size,
          attachment_type=EXCLUDED.attachment_type,extraction_status=EXCLUDED.extraction_status RETURNING *""",
          (aid,message_id,attachment.get('filename'),attachment.get('mime_type'),attachment.get('size'),attachment['checksum'],attachment.get('attachment_type'),attachment.get('extraction_status'),attachment['checksum']))
        return _rows(cur)[0]


def attachments_for_message(message_id: str, *, include_text: bool=False) -> list[dict[str,Any]]:
    fields="a.*,c.extraction_method"+(",c.extracted_text" if include_text else "")
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute(f"SELECT {fields} FROM mailbox_attachments a LEFT JOIN mailbox_attachment_cache c ON c.checksum=a.checksum WHERE a.mailbox_message_id=%s ORDER BY a.created_at",(message_id,));return _rows(cur)


_INTERVIEW_CLASSIFICATIONS = {"interview_confirmed", "interview_cancelled", "interview_rescheduled"}

# One interview is described by several mails, so the lookup has to reach back
# far enough to find the first of them without trawling a candidate's history.
_CALENDAR_LOOKBACK_DAYS = 30


def _interview_identity(result: dict[str,Any]) -> dict[str,Any]:
    interview=result.get('interview') or {}; recruiter=result.get('recruiter') or {}; company=result.get('company') or {}
    return {
        'interview_date': interview.get('date'), 'interview_time': interview.get('time'),
        'recruiter_email': recruiter.get('email'), 'company_domain': company.get('domain'),
        'calendar_uid': result.get('calendar_uid'), 'calendar_sequence': result.get('calendar_sequence'),
    }


def existing_interview_event(candidate_id: str, result: dict[str,Any]) -> dict[str,Any] | None:
    """The event this interview mail repeats, if one is already recorded.

    Only interviews are considered. An offer letter and its covering note are a
    different problem with different rules, and collapsing them here would be a
    silent behaviour change to a path nobody asked about.
    """
    from features import interview_event_identity

    if str(result.get('classification') or '').strip().lower() not in _INTERVIEW_CLASSIFICATIONS:
        return None
    incoming=_interview_identity(result)
    # Nothing to match on: no calendar identity and no schedule.
    if not incoming['calendar_uid'] and not (incoming['interview_date'] and incoming['interview_time']):
        return None
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM ai_recruitment_events
          WHERE candidate_id=%s AND created_at > now() - (%s || ' days')::interval
          ORDER BY created_at DESC LIMIT 100""",(candidate_id,str(_CALENDAR_LOOKBACK_DAYS)))
        rows=_rows(cur)
    return interview_event_identity.duplicate_of(rows,incoming)


def attach_calendar_identity(event_id: str, uid: Any, sequence: Any) -> None:
    """Record the calendar's identity on an event that was created without it.

    The covering note is classified by AI and has no UID; the invitation that
    follows does. Writing it onto the existing row is what lets the *next*
    delivery — a resend, or Google's second copy — be recognised by UID rather
    than by schedule.
    """
    if not uid:
        return
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE ai_recruitment_events SET calendar_uid=%s,calendar_sequence=%s,updated_at=now()
          WHERE id=%s AND calendar_uid IS NULL""",(str(uid),sequence,event_id))


_CLOCK_IN_TEXT = re.compile(r"(\d{1,2}):([0-5]\d)(?::([0-5]\d))?\s*([AP]M)?", re.I)


def typed_or_null(value: Any) -> Any:
    """Blank -> NULL for a column Postgres types as date, time or number.

    The model expresses "no value" both ways: sometimes ``null``, sometimes an
    empty string. An empty string reaching a typed column raises
    InvalidDatetimeFormat and aborts the whole INSERT.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def storable_time(value: Any) -> Any:
    """A value Postgres can store in a `time` column, or NULL.

    Nothing is lost by nulling this: the model's exact answer is kept verbatim
    in `structured_result`, and this column is only the projection the roster
    and audit read. What *is* lost by passing a bad value through is the entire
    event — the INSERT aborts, and because a raw psycopg2 error is not an
    AIGatewayError it never reaches the semantic retry path, so no code is
    recorded and the mail fails identically forever.

    Two Production cancellation mails died on `"15:30 - 16:00 IST"`: a range
    with a zone suffix, which Postgres reads as a timezone displacement. The
    start of a stated range is the interview time, so it is taken; anything with
    no readable clock at all becomes NULL.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    hit = _CLOCK_IN_TEXT.search(text)
    if not hit:
        return None
    hour, minute = int(hit.group(1)), hit.group(2)
    second = hit.group(3) or "00"
    meridiem = (hit.group(4) or "").upper()
    if meridiem == "AM" and hour == 12:
        hour = 0
    elif meridiem == "PM" and hour != 12:
        hour += 12
    if not 0 <= hour <= 23:
        return None
    return f"{hour:02d}:{minute}:{second}"


def storable_date(value: Any) -> Any:
    """A value Postgres can store in a `date` column, or NULL.

    Same contract as `storable_time`: the raw answer survives in
    `structured_result`, so a value this cannot represent is dropped from the
    projection rather than allowed to abort the event.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def storable_number(value: Any) -> Any:
    """A value Postgres can store in a `numeric` column, or NULL."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def create_event(candidate_id: str, message_id: str, result: dict[str,Any], *, model: str, duration_ms: int) -> dict[str,Any]:
    # One interview, one event. The recruiter's covering note and the calendar
    # invitation arrive a minute apart describing the same meeting, and neither
    # message_hash nor the subject-scoped body_hash dedupe can relate them —
    # they differ in both. Returning the event already recorded keeps a single
    # row, a single notification and a single booking attempt.
    already=existing_interview_event(candidate_id,result)
    if already:
        attach_calendar_identity(already['id'],result.get('calendar_uid'),result.get('calendar_sequence'))
        return already
    event_id=_id(); interview=result.get('interview') or {}; offer=result.get('offer') or {}; company=result.get('company') or {}; job=result.get('job') or {}; recruiter=result.get('recruiter') or {}
    validation_status=str(result.get('validation_status') or 'RETRY_PENDING').upper()
    review_state='AUTOMATED'
    candidate_event={"primary_status":result.get("primary_status"),"confidence":result.get("confidence"),"structured_result":result,"review_status":review_state,"validation_status":validation_status,"visible_in_offer_review":True}
    visible=should_show_in_selection_offer_review(candidate_event)
    original_status=result.get('primary_status')
    status=original_status if visible else ('IGNORED_LOW_CONFIDENCE' if float(result.get('confidence') or 0)<.8 else 'IGNORED_NOT_OFFER_RELATED')
    review_status=review_state if visible else 'IGNORED'
    ignore_reason=None if visible else (result.get('ignore_reason') or 'LOW_CONFIDENCE_OR_NO_STRONG_OFFER_EVIDENCE')
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO ai_recruitment_events(id,candidate_id,mailbox_message_id,primary_status,confidence,company_name,company_domain,job_title,
          recruiter_name,recruiter_email,interview_date,interview_time,interview_mode,offered_ctc,currency,joining_date,offer_date,offer_expiry_date,
          structured_result,summary,requires_manual_review,review_status,visible_in_offer_review,original_primary_status,ignore_reason,ignored_at,
          ai_model,prompt_name,prompt_version,schema_version,processing_duration_ms,calendar_uid,calendar_sequence,created_at,updated_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,
            CASE WHEN %s THEN NULL ELSE now() END,%s,'recruitment_email_status_extraction_v3','v3','selection_offer_event_v1',%s,%s,%s,now(),now()) RETURNING *""",
          (event_id,candidate_id,message_id,status,result['confidence'],company.get('name'),company.get('domain'),job.get('title'),recruiter.get('name'),recruiter.get('email'),storable_date(interview.get('date')),storable_time(interview.get('time')),interview.get('mode'),storable_number(offer.get('offered_ctc')),offer.get('currency'),storable_date(offer.get('joining_date')),storable_date(offer.get('offer_date')),storable_date(offer.get('offer_expiry_date')),json.dumps(result),result.get('summary'),bool(result.get('requires_manual_review')) if visible else False,review_status,visible,original_status if not visible else None,ignore_reason,visible,model,duration_ms,(str(result.get('calendar_uid')) if result.get('calendar_uid') else None),result.get('calendar_sequence')))
        event=_rows(cur)[0]
        canonical_id=canonical_candidate_id(candidate_id)
        cur.execute("""UPDATE ai_recruitment_events SET canonical_candidate_id=%s,validation_status=%s,
          ai_status=%s,email_intent=%s,document_type=%s,evidence_summary=%s,event_fingerprint=%s
          WHERE id=%s RETURNING *""",(
          canonical_id,validation_status,str(result.get('ai_status') or 'ANALYZED'),
          result.get('email_intent'),result.get('document_type'),result.get('evidence_summary') or result.get('summary'),
          message_id,event_id))
        event=_rows(cur)[0]
        cur.execute("""INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,new_value,created_at)
          VALUES(%s,'system','system','AI_RECRUITMENT_EVENT_CREATED',%s,%s,%s::jsonb,now())""",(_id(),candidate_id,event_id,json.dumps({'primary_status':result['primary_status'],'confidence':result['confidence'],'model':model})))
        # Canonical candidate status and history are applied after the event is
        # committed by finalize_detection(), which enforces confidence and
        # monotonic transition rules in one place.
        from services.recruitment_mail_agent import OFFER_CASE_STATUSES
        if visible and result['primary_status'] in OFFER_CASE_STATUSES:
            cur.execute("""SELECT m.provider_thread_id,a.checksum FROM mailbox_messages m
              LEFT JOIN mailbox_attachments a ON a.mailbox_message_id=m.id WHERE m.id=%s
              ORDER BY CASE WHEN lower(COALESCE(a.filename,'')) ~ '(offer|appointment|joining)' THEN 0 ELSE 1 END LIMIT 1""",(message_id,))
            source=cur.fetchone() or (None,None)
            identity=source[1] or source[0] or '|'.join(str(value or '').strip().lower() for value in (company.get('name'),job.get('title')))
            raw_key='|'.join(str(value or '').strip().lower() for value in (candidate_id,identity))
            # An unknown offer must not collapse every offer for the candidate
            # into one case. PostgreSQL permits multiple NULL values in this
            # unique index, so only deduplicate when a stable identity exists.
            offer_case_key=__import__('hashlib').sha256(raw_key.encode()).hexdigest() if identity else None
            cur.execute("""INSERT INTO offer_verification_cases(id,candidate_id,ai_recruitment_event_id,offer_case_key,company_name,job_title,offered_ctc,currency,offer_date,joining_date,offer_expiry_date,verification_status,confidence,created_at,updated_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING_REVIEW',%s,now(),now())
              ON CONFLICT(offer_case_key) DO UPDATE SET ai_recruitment_event_id=EXCLUDED.ai_recruitment_event_id,
                company_name=COALESCE(EXCLUDED.company_name,offer_verification_cases.company_name),
                job_title=COALESCE(EXCLUDED.job_title,offer_verification_cases.job_title),
                offered_ctc=COALESCE(EXCLUDED.offered_ctc,offer_verification_cases.offered_ctc),
                currency=COALESCE(EXCLUDED.currency,offer_verification_cases.currency),
                offer_date=COALESCE(EXCLUDED.offer_date,offer_verification_cases.offer_date),
                joining_date=COALESCE(EXCLUDED.joining_date,offer_verification_cases.joining_date),
                offer_expiry_date=COALESCE(EXCLUDED.offer_expiry_date,offer_verification_cases.offer_expiry_date),
                confidence=GREATEST(offer_verification_cases.confidence,EXCLUDED.confidence),updated_at=now()""",
              (_id(),candidate_id,event_id,offer_case_key,company.get('name'),job.get('title'),storable_number(offer.get('offered_ctc')),offer.get('currency'),storable_date(offer.get('offer_date')),storable_date(offer.get('joining_date')),storable_date(offer.get('offer_expiry_date')),result['confidence']))
            cur.execute("""INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,new_value,created_at)
              VALUES(%s,'system','system','OFFER_CASE_CREATED',%s,%s,%s::jsonb,now())""",(_id(),candidate_id,event_id,json.dumps({'status':result['primary_status'],'confidence':result['confidence']})))
        cur.execute("SELECT confirmed_status FROM candidate_status_history WHERE candidate_id=%s AND confirmed_status IS NOT NULL ORDER BY reviewed_at DESC LIMIT 1",(candidate_id,));confirmed=cur.fetchone()
        conflict_pairs={('INTERVIEW_CANCELLED','INTERVIEW_RESCHEDULED'),('REJECTED','SELECTED'),('APPLICATION_WITHDRAWN','OFFER_LETTER_RECEIVED')}
        if confirmed and (confirmed[0],result['primary_status']) in conflict_pairs:
            cur.execute("""INSERT INTO recruitment_review_flags(id,candidate_id,event_id,flag_type,severity,details,created_at)
              VALUES(%s,%s,%s,'POTENTIAL_STATUS_CONFLICT','HIGH',%s::jsonb,now()) ON CONFLICT(candidate_id,event_id,flag_type) DO NOTHING""",(_id(),candidate_id,event_id,json.dumps({'confirmed_status':confirmed[0],'detected_status':result['primary_status']})))
        if offer.get('offered_ctc'):
            cur.execute("SELECT offered_ctc FROM offer_verification_cases WHERE candidate_id=%s AND ai_recruitment_event_id<>%s AND offered_ctc IS NOT NULL ORDER BY created_at DESC LIMIT 1",(candidate_id,event_id));previous_offer=cur.fetchone()
            if previous_offer and float(previous_offer[0])!=float(offer['offered_ctc']):
                cur.execute("""INSERT INTO recruitment_review_flags(id,candidate_id,event_id,flag_type,severity,details,created_at)
                  VALUES(%s,%s,%s,'POTENTIAL_OFFER_CONFLICT','HIGH',%s::jsonb,now()) ON CONFLICT(candidate_id,event_id,flag_type) DO NOTHING""",(_id(),candidate_id,event_id,json.dumps({'previous_ctc':float(previous_offer[0]),'detected_ctc':offer['offered_ctc']})))
        cur.execute("""UPDATE mailbox_messages SET processing_status=%s,ignore_reason=%s,
          ignored_at=CASE WHEN %s THEN ignored_at ELSE now() END,semantic_classifier_version='v3',updated_at=now() WHERE id=%s""",
          ('EVENT_CREATED' if visible else status,ignore_reason,visible,message_id))
    return finalize_detection(event, result=result, model=model, duration_ms=duration_ms)


def create_or_reprocess_event(candidate_id: str, message_id: str, result: dict[str,Any], *, model: str, duration_ms: int, reason: str) -> dict[str,Any]:
    """Update the existing event in place, preserving its prior classification in audit."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ai_recruitment_events WHERE mailbox_message_id=%s", (message_id,))
        existing_rows=_rows(cur)
    if not existing_rows:
        event=create_event(candidate_id,message_id,result,model=model,duration_ms=duration_ms)
        audit(actor='system',role='system',action='HISTORICAL_EMAIL_REPROCESSED',candidate_id=candidate_id,source_id=event['id'],previous=None,new={'new_classification':result['primary_status'],'prompt_version':'v3','reason':reason})
        return event
    previous=existing_rows[0];company=result.get('company') or {};job=result.get('job') or {};offer=result.get('offer') or {};recruiter=result.get('recruiter') or {}
    with get_connection() as conn,conn.cursor() as cur:
        validation_status=str(result.get('validation_status') or 'RETRY_PENDING').upper()
        review_state='AUTOMATED'
        cur.execute("""UPDATE ai_recruitment_events SET original_primary_status=COALESCE(original_primary_status,primary_status),
          primary_status=%s,confidence=%s,company_name=%s,company_domain=%s,job_title=%s,recruiter_name=%s,recruiter_email=%s,
          joining_date=%s,structured_result=%s::jsonb,summary=%s,requires_manual_review=%s,review_status=%s,
          visible_in_offer_review=true,ignore_reason=NULL,ignored_at=NULL,cleanup_version=NULL,ai_model=%s,
          prompt_name='recruitment_email_status_extraction_v3',prompt_version='v4',processing_duration_ms=%s,
          canonical_candidate_id=%s,validation_status=%s,ai_status=%s,email_intent=%s,document_type=%s,
          evidence_summary=%s,event_fingerprint=%s,updated_at=now()
          WHERE id=%s RETURNING *""",
          (result['primary_status'],result['confidence'],company.get('name'),company.get('domain'),job.get('title'),recruiter.get('name'),recruiter.get('email'),storable_date(offer.get('joining_date')),json.dumps(result),result.get('summary'),bool(result.get('requires_manual_review')),review_state,model,duration_ms,canonical_candidate_id(candidate_id),validation_status,str(result.get('ai_status') or 'ANALYZED'),result.get('email_intent'),result.get('document_type'),result.get('evidence_summary') or result.get('summary'),message_id,previous['id']))
        event=_rows(cur)[0]
        if result['primary_status'] in __import__('services.recruitment_mail_agent',fromlist=['OFFER_CASE_STATUSES']).OFFER_CASE_STATUSES:
            cur.execute("""INSERT INTO offer_verification_cases(id,candidate_id,ai_recruitment_event_id,company_name,job_title,joining_date,verification_status,confidence,created_at,updated_at)
              VALUES(%s,%s,%s,%s,%s,%s,'PENDING_REVIEW',%s,now(),now())
              ON CONFLICT(ai_recruitment_event_id) DO UPDATE SET company_name=EXCLUDED.company_name,job_title=EXCLUDED.job_title,
                joining_date=EXCLUDED.joining_date,verification_status='PENDING_REVIEW',confidence=EXCLUDED.confidence,updated_at=now()""",
              (_id(),candidate_id,event['id'],company.get('name'),job.get('title'),storable_date(offer.get('joining_date')),result['confidence']))
        cur.execute("""INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,previous_value,new_value,created_at)
          VALUES(%s,'system','system','HISTORICAL_EMAIL_RECLASSIFIED',%s,%s,%s::jsonb,%s::jsonb,now())""",
          (_id(),candidate_id,event['id'],json.dumps({'classification':previous.get('primary_status'),'prompt_version':previous.get('prompt_version')},default=str),json.dumps({'classification':result['primary_status'],'prompt_version':'v3','reason':reason},default=str)))
    return finalize_detection(event, result=result, model=model, duration_ms=duration_ms)


def archive_event_for_message(message_id: str, *, status: str, reason: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Audit-safely remove a historical false positive from every consumer."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ai_recruitment_events WHERE mailbox_message_id=%s FOR UPDATE", (message_id,))
        rows = _rows(cur)
        if not rows:
            return None
        previous = rows[0]
        structured = json.dumps(result) if result is not None else json.dumps(previous.get("structured_result") or {})
        cur.execute("""UPDATE ai_recruitment_events SET
          original_primary_status=COALESCE(original_primary_status,primary_status),primary_status=%s,
          structured_result=%s::jsonb,visible_in_offer_review=false,review_status='IGNORED',
          requires_manual_review=false,ignore_reason=%s,ignored_at=now(),cleanup_version='semantic_v4',
          validation_status='REJECTED',ai_status=COALESCE(ai_status,'ANALYZED'),
          prompt_name='recruitment_email_status_extraction_v3',prompt_version='v3',updated_at=now()
          WHERE id=%s RETURNING *""", (status, structured, reason, previous["id"]))
        archived = _rows(cur)[0]
        cur.execute("UPDATE offer_verification_cases SET verification_status='IGNORED',updated_at=now() WHERE ai_recruitment_event_id=%s", (previous["id"],))
        cur.execute("""UPDATE mail_monitoring_notifications SET dismissed_at=COALESCE(dismissed_at,now()),
          is_reviewed=true,reviewed_at=COALESCE(reviewed_at,now()),reviewed_by=COALESCE(reviewed_by,'system'),
          review_notes=COALESCE(review_notes,%s),is_false_detection=true,updated_at=now()
          WHERE ai_recruitment_event_id=%s""", (f"Automatically archived: {reason}", previous["id"]))
        # Preserve administrator-confirmed history; remove only unconfirmed AI
        # history that was generated by the now-archived false positive.
        cur.execute("DELETE FROM candidate_status_history WHERE source_id=%s AND confirmed_status IS NULL", (previous["id"],))
        cur.execute("""INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,previous_value,new_value,created_at)
          VALUES(%s,'system','system','HISTORICAL_EVENT_ARCHIVED',%s,%s,%s::jsonb,%s::jsonb,now())""",
          (_id(), previous["candidate_id"], previous["id"],
           json.dumps({"classification": previous.get("primary_status"), "visible": previous.get("visible_in_offer_review")}, default=str),
           json.dumps({"classification": status, "visible": False, "reason": reason}, default=str)))
        return archived


def audit(*,actor:str,role:str,action:str,candidate_id:str|None=None,source_id:str|None=None,previous:Any=None,new:Any=None,source_ip:str|None=None)->None:
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,previous_value,new_value,source_ip,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,now())",(_id(),actor,role,action,candidate_id,source_id,json.dumps(previous) if previous is not None else None,json.dumps(new) if new is not None else None,source_ip))


def list_flags(*,status:str|None=None,limit:int=100)->list[dict[str,Any]]:
    where=' WHERE review_status=%s' if status else ''
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute(f"SELECT * FROM recruitment_review_flags{where} ORDER BY created_at DESC LIMIT %s",(([status] if status else [])+[limit]));return _rows(cur)


def candidate_filter_ids(value:str)->set[str]:
    key=(value or '').strip().upper()
    with get_connection() as conn,conn.cursor() as cur:
        if key=='MAILBOX_CONNECTED':cur.execute("SELECT candidate_id FROM candidate_mailboxes WHERE connection_status='CONNECTED'")
        elif key=='MAILBOX_MONITORING_ENABLED':cur.execute("SELECT candidate_id FROM candidate_mailboxes WHERE monitoring_enabled=true")
        elif key=='MAILBOX_SYNC_FAILED':cur.execute("SELECT candidate_id FROM candidate_mailboxes WHERE connection_status='ERROR'")
        elif key=='PENDING_AI_REVIEW':
            predicate,params=qualified_event_sql('e');cur.execute(f"SELECT DISTINCT candidate_id FROM ai_recruitment_events e WHERE e.review_status='PENDING' AND {predicate}",params)
        elif key=='POTENTIAL_STATUS_CONFLICT':cur.execute("SELECT DISTINCT candidate_id FROM recruitment_review_flags WHERE flag_type LIKE 'POTENTIAL%%' AND review_status='PENDING'")
        elif key=='OFFER_VERIFIED':cur.execute("SELECT DISTINCT candidate_id FROM offer_verification_cases WHERE verification_status='VERIFIED'")
        else:cur.execute("SELECT DISTINCT candidate_id FROM ai_recruitment_events WHERE primary_status=%s",(key,))
        return {str(row[0]) for row in cur.fetchall()}


def list_events(*, candidate_id: str|None=None, review_status: str|None=None, limit:int=50, offset:int=0, active_only:bool=True) -> list[dict[str,Any]]:
    where=[]; params=[]
    if active_only:
        predicate,predicate_params=qualified_event_sql('e');where.append(predicate);params.extend(predicate_params)
    if candidate_id:where.append('e.candidate_id=%s');params.append(candidate_id)
    if review_status:where.append('e.review_status=%s');params.append(review_status)
    sql='''SELECT e.*,m.subject,m.sender_name,m.sender_email,m.sent_at AS email_sent_at,
      booking.booking_id,booking.booking_status,booking.failure_code AS booking_failure_code
      FROM ai_recruitment_events e
      LEFT JOIN mailbox_messages m ON m.id=e.mailbox_message_id
      LEFT JOIN LATERAL (
        SELECT a.booking_id,a.booking_status,a.failure_code
        FROM interview_auto_booking_audit a
        WHERE a.gmail_message_id=m.provider_message_id
        ORDER BY a.auto_booked DESC,a.created_at DESC,a.id DESC LIMIT 1
      ) booking ON true'''+((' WHERE '+' AND '.join(where)) if where else '')+' ORDER BY e.created_at DESC LIMIT %s OFFSET %s';params.extend([limit,offset])
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql,params);rows=_rows(cur)
    visible_rows=[row for row in rows if not active_only or should_show_in_selection_offer_review(row)]
    from features import candidate_store
    for row in visible_rows:
        for candidate_key in (row.get('canonical_candidate_id'),row.get('candidate_id')):
            candidate=candidate_store.get_candidate(str(candidate_key)) if candidate_key else None
            if candidate and candidate.get('name'):
                row['candidate_name']=candidate['name']
                break
    return visible_rows


def event_detail(event_id:str,*,include_evidence:bool=False)->dict[str,Any]|None:
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("""SELECT e.*,m.subject,m.sender_name,m.sender_email,m.recipient_email,
          m.sent_at AS email_sent_at,m.provider_message_id,m.provider_thread_id,
          m.body_text,m.html_body_text,m.mailbox_id,b.email_address AS mailbox_email
          FROM ai_recruitment_events e
          LEFT JOIN mailbox_messages m ON m.id=e.mailbox_message_id
          LEFT JOIN candidate_mailboxes b ON b.id=m.mailbox_id
          WHERE e.id=%s""",(event_id,));rows=_rows(cur)
    if not rows:return None
    row=rows[0]
    from services.recruitment_semantics import redact_sensitive_text
    structured=dict(row.get('structured_result') or {})
    structured['evidence']=[{**item,'text':redact_sensitive_text(str(item.get('text') or ''))} for item in structured.get('evidence') or [] if isinstance(item,dict)]
    row['structured_result']=structured
    row['email_body']=row.get('body_text') or row.get('html_body_text') or ''
    received=None
    if row.get('mailbox_id') and row.get('provider_thread_id'):
        with get_connection() as conn,conn.cursor() as cur:
            cur.execute("""SELECT subject,sender_name,sender_email,recipient_email,sent_at,
              body_text,html_body_text
              FROM mailbox_messages
              WHERE mailbox_id=%s AND provider_thread_id=%s
                AND lower(COALESCE(sender_email,''))<>lower(COALESCE(%s,''))
                AND sent_at<=%s
              ORDER BY sent_at DESC LIMIT 1""",
              (row['mailbox_id'],row['provider_thread_id'],row.get('mailbox_email'),row.get('email_sent_at')))
            incoming=cur.fetchone()
            if incoming:
                received={
                    'subject':incoming[0],'sender_name':incoming[1],
                    'sender_email':incoming[2],'recipient_email':incoming[3],
                    'sent_at':incoming[4],
                    'body':incoming[5] or incoming[6] or '',
                    # The same mail as the sender wrote it. `body_text` is the
                    # flattened extraction: for the VHS interview mail that is
                    # 644 characters on one line with no newline anywhere, so
                    # the reading view could show neither the paragraphs nor
                    # the bold, and the Teams URL arrived split across four
                    # fragments by the sending client's 76-column wrap.
                    #
                    # Read-only and additive. `body` keeps exactly the value it
                    # had, so every existing consumer is untouched; the reading
                    # view prefers this when it is present.
                    'body_html':incoming[6] or '',
                }
    row['received_email']=received or {
        'subject':row.get('subject'),'sender_name':row.get('sender_name'),
        'sender_email':row.get('sender_email'),'recipient_email':row.get('recipient_email'),
        'sent_at':row.get('email_sent_at'),'body':row['email_body'],
        'body_html':row.get('html_body_text') or '',
    }
    row.pop('body_text',None);row.pop('html_body_text',None)
    row.pop('mailbox_email',None)
    # Extracted attachment text may contain bank/government identifiers. The
    # UI receives document metadata plus already-redacted evidence summaries.
    row['attachments']=attachments_for_message(row['mailbox_message_id'],include_text=False) if row.get('mailbox_message_id') else []
    return row


def event_reprocess_context(event_id: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT e.id AS event_id,m.*,b.email_address,
          b.candidate_id AS mailbox_candidate_id
          FROM ai_recruitment_events e JOIN mailbox_messages m ON m.id=e.mailbox_message_id
          JOIN candidate_mailboxes b ON b.id=m.mailbox_id WHERE e.id=%s""",(event_id,))
        rows=_rows(cur)
    if not rows:return None
    row=rows[0]
    row['attachments']=[{**item,'text':item.get('extracted_text') or ''} for item in attachments_for_message(row['id'],include_text=True)]
    return row


def edit_event(event_id:str,changes:dict[str,Any],*,reviewer:str,notes:str='')->dict[str,Any]:
    allowed={'primary_status','classification','candidate_status','confidence','company_name','company_domain','job_title','recruiter_name','recruiter_email','interview_date','interview_time','interview_mode','offered_ctc','currency','joining_date','offer_date','offer_expiry_date','summary','requires_manual_review'}
    clean={k:v for k,v in changes.items() if k in allowed}
    if 'primary_status' in clean:
        from services.recruitment_mail_agent import STATUSES
        if clean['primary_status'] not in STATUSES:raise ValueError('Invalid recruitment status')
    if 'confidence' in clean and not 0<=float(clean['confidence'])<=1:raise ValueError('Confidence must be between 0 and 1')
    if 'classification' in clean and clean['classification'] not in CANONICAL_CLASSIFICATIONS:raise ValueError('Unsupported classification')
    if not clean:return event_detail(event_id) or {}
    before=event_detail(event_id) or {};assignments=', '.join(f'{k}=%s' for k in clean)
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute(f"UPDATE ai_recruitment_events SET {assignments},corrected_result=%s::jsonb,review_notes=%s,updated_at=now() WHERE id=%s RETURNING *",(*clean.values(),json.dumps(clean,default=str),notes,event_id));rows=_rows(cur)
        if rows:cur.execute("INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,previous_value,new_value,created_at) VALUES(%s,%s,'admin','EVENT_EDITED',%s,%s,%s::jsonb,%s::jsonb,now())",(_id(),reviewer,rows[0]['candidate_id'],event_id,json.dumps({k:before.get(k) for k in clean},default=str),json.dumps(clean,default=str)))
    return rows[0] if rows else {}


def mailbox_stats(mailbox_id:str)->dict[str,Any]:
    with get_connection() as conn,conn.cursor() as cur:
        predicate,params=qualified_event_sql('e')
        cur.execute(f"""SELECT count(*) FILTER(WHERE {predicate}) important_emails,
          count(*) FILTER(WHERE e.primary_status IN('SELECTED','FINAL_SELECTION_CONFIRMED') AND {predicate}) selection_events,
          count(*) FILTER(WHERE e.primary_status IN('OFFER_INDICATION','OFFER_IN_PROGRESS','OFFER_APPROVED','OFFER_LETTER_RECEIVED','APPOINTMENT_LETTER_RECEIVED','OFFER_ACCEPTED') AND {predicate}) offer_events,
          count(*) FILTER(WHERE e.primary_status='OFFER_LETTER_RECEIVED' AND {predicate}) offer_letters,
          0::bigint pending_reviews
          FROM mailbox_messages m LEFT JOIN ai_recruitment_events e ON e.mailbox_message_id=m.id WHERE m.mailbox_id=%s""",params*4+[mailbox_id]);names=[d.name for d in cur.description];stats=dict(zip(names,cur.fetchone()))
        # The mailbox card and Mail Alerts must use the same review queue.
        # Counting legacy PENDING events here made fully validated and
        # historical emails appear as work even though no alert existed.
        cur.execute("""SELECT count(*) FROM mail_monitoring_notifications
          WHERE gmail_account_id=%s
            AND priority='review_required'
            AND NOT is_reviewed
            AND dismissed_at IS NULL
            AND COALESCE(booking_status,'') <> 'Historical Skipped'""",(mailbox_id,))
        stats['pending_reviews']=int(cur.fetchone()[0])
        cur.execute("""SELECT id,status,job_type,created_at,started_at,completed_at,
          messages_fetched,messages_processed,events_detected,error_message
          FROM mailbox_sync_jobs WHERE mailbox_id=%s ORDER BY created_at DESC LIMIT 1""",(mailbox_id,))
        job_rows=_rows(cur)
        if job_rows:
            job=job_rows[0]
            stats.update({
                'latest_sync_job_id':job.get('id'),
                'latest_sync_status':job.get('status'),
                'latest_sync_job_type':job.get('job_type') or 'INCREMENTAL_SYNC',
                'latest_sync_created_at':job.get('created_at'),
                'latest_sync_started_at':job.get('started_at'),
                'latest_sync_completed_at':job.get('completed_at'),
                'latest_sync_messages_fetched':job.get('messages_fetched') or 0,
                'latest_sync_messages_processed':job.get('messages_processed') or 0,
                'latest_sync_events_detected':job.get('events_detected') or 0,
                'latest_sync_error':job.get('error_message'),
            })
        return stats


def summarize_selection_tracking_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure summary used by the API and regression tests."""
    lifecycle_groups = {
        'selected': ('SELECTED','FINAL_SELECTION_CONFIRMED'),
        'offers_received': ('OFFER_INDICATION','OFFER_IN_PROGRESS','OFFER_APPROVED','OFFER_LETTER_RECEIVED','APPOINTMENT_LETTER_RECEIVED'),
        'offers_accepted': ('OFFER_ACCEPTED',),
        'joining_confirmed': ('JOINING_CONFIRMED',),
        'joined': ('JOINED',),
    }
    truth=[event for event in events if str(event.get('validation_status') or '').upper() in {'AUTO_VALIDATED','APPROVED'} and str(event.get('review_status') or '').upper() not in {'FALSE_POSITIVE','DUPLICATE','REJECTED','IGNORED'}]
    filters: dict[str,list[str]]={}
    for key,statuses in lifecycle_groups.items():
        filters[key]=sorted({str(event.get('canonical_candidate_id') or event.get('candidate_id')) for event in truth if event.get('primary_status') in statuses})
    filters['automation_pending']=sorted({
        str(event.get('id')) for event in events
        if event.get('id')
        and (
            str(event.get('automation_state') or '').upper() == 'AI_RETRY_PENDING'
            or str(event.get('primary_status') or '').upper() == 'AI_RETRY_PENDING'
            or (
                str(event.get('review_status') or '').upper() == 'PENDING'
                and str(event.get('validation_status') or '').upper() in {'NEEDS_REVIEW','RETRY_PENDING'}
            )
            or str(event.get('cleanup_version') or '') == 'manual_content_audit_keep_v1'
        )
    })
    metrics={key:len(value) for key,value in filters.items()}
    # Backward-compatible aliases for older clients; all originate here.
    metrics.update({
        'needs_review':metrics['automation_pending'],
        'pending_reviews':metrics['automation_pending'],
        'selections_detected':metrics['selected'],
        'offers_accepted':metrics['offers_accepted'],
        'joining_confirmations':metrics['joining_confirmed'],
        'candidates_joined':metrics['joined'],
    })
    return {'metrics':metrics,'filters':filters}


def selection_tracking_stats() -> dict[str, Any]:
    """Central source of truth for all Selection & Offer dashboard cards."""
    predicate, params = qualified_event_sql('e')
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"""SELECT e.id,e.candidate_id,e.canonical_candidate_id,e.primary_status,
          e.review_status,e.validation_status,e.cleanup_version,e.automation_state
          FROM ai_recruitment_events e WHERE {predicate}""",params)
        events=_rows(cur)
    return summarize_selection_tracking_events(events)


def review_event(event_id:str, action:str, reviewer:str, notes:str='', changes:dict[str,Any]|None=None)->dict[str,Any]:
    status={'approve':'APPROVED','reject':'REJECTED','false-positive':'FALSE_POSITIVE','duplicate':'DUPLICATE'}.get(action,action.upper())
    validation={'APPROVED':'APPROVED','FALSE_POSITIVE':'FALSE_POSITIVE','DUPLICATE':'REJECTED','REJECTED':'REJECTED'}.get(status,'NEEDS_REVIEW')
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE ai_recruitment_events SET review_status=%s,validation_status=%s,reviewed_by=%s,reviewed_at=now(),review_notes=%s,updated_at=now() WHERE id=%s RETURNING *",(status,validation,reviewer,notes,event_id)); rows=_rows(cur)
        if rows:
            cur.execute("INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,new_value,created_at) VALUES(%s,%s,'admin',%s,%s,%s,%s::jsonb,now())",(_id(),reviewer,'EVENT_'+status,rows[0]['candidate_id'],event_id,json.dumps({'notes':notes,'changes':changes or {}})))
    row=rows[0] if rows else {}
    if row and status=='APPROVED':
        classification=canonical_classification(row.get('structured_result') or {},row.get('primary_status'))
        candidate_status=str(row.get('candidate_status') or _CLASSIFICATION_STATUS[classification])
        apply_candidate_job_status(row,classification,candidate_status,force=True,updated_by=reviewer,review_notes=notes)
    elif row and status in {'REJECTED','FALSE_POSITIVE','DUPLICATE'}:
        rebuild_candidate_job_status(str(row.get('canonical_candidate_id') or row.get('candidate_id')))
    return row


def list_offer_cases(*, status:str|None=None, limit:int=50, offset:int=0)->list[dict[str,Any]]:
    predicate,predicate_params=qualified_event_sql('e')
    where=('c.verification_status=%s AND ' if status else "c.verification_status<>'IGNORED' AND ")+predicate
    params=([status] if status else [])+predicate_params+[limit,offset]
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute(f"SELECT c.* FROM offer_verification_cases c JOIN ai_recruitment_events e ON e.id=c.ai_recruitment_event_id WHERE {where} ORDER BY c.created_at DESC LIMIT %s OFFSET %s",params);return _rows(cur)


def review_offer(case_id:str, action:str, reviewer:str, notes:str='')->dict[str,Any]:
    status={'verify':'VERIFIED','reject':'REJECTED','duplicate':'DUPLICATE','dispute':'CANDIDATE_DISPUTED'}.get(action)
    if not status:raise ValueError('Invalid offer review action')
    with get_connection() as conn,conn.cursor() as cur:
        cur.execute("UPDATE offer_verification_cases SET verification_status=%s,reviewed_by=%s,reviewed_at=now(),notes=%s,updated_at=now() WHERE id=%s RETURNING *",(status,reviewer,notes,case_id));rows=_rows(cur)
        if rows:cur.execute("INSERT INTO recruitment_audit_log(id,actor,role,action,candidate_id,source_id,new_value,created_at) VALUES(%s,%s,'admin',%s,%s,%s,%s::jsonb,now())",(_id(),reviewer,'OFFER_'+status,rows[0]['candidate_id'],case_id,json.dumps({'notes':notes})))
    return rows[0] if rows else {}


def canonical_classification(result: dict[str, Any] | None = None, status: str | None = None) -> str:
    result = result or {}
    explicit = str(result.get("classification") or "").strip().lower()
    if explicit == 'needs_review':
        return 'ai_retry_pending'
    if explicit in CANONICAL_CLASSIFICATIONS:
        return explicit
    return _STATUS_CLASSIFICATION.get(str(status or result.get("primary_status") or result.get("status") or "").upper(), "ai_retry_pending")


def booking_source_message(provider_message_id: str) -> dict[str, Any] | None:
    """Read source proof for an existing slot, never infer from model prose.

    Ambiguous provider IDs cannot establish identity. Database errors propagate
    so an unavailable proof store cannot turn a retry into a second booking.
    """
    if not provider_message_id or not use_postgres():
        return None
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        cur.execute('SELECT subject,body_text,html_body_text,sent_at FROM mailbox_messages WHERE provider_message_id=%s LIMIT 2', (provider_message_id,))
        rows = _rows(cur)
    return rows[0] if len(rows) == 1 else None


def notification_priority(classification: str, *, confidence: float, requires_review: bool = False) -> str:
    if requires_review or confidence < float(__import__('os').getenv('OLLAMA_CONFIDENCE_THRESHOLD', '0.75')):
        return "retry_pending"
    if classification in {"job_selection_confirmed", "offer_received", "joining_confirmed", "offer_accepted", "onboarding_started", "interview_confirmed", "interview_rescheduled", "interview_cancelled", "final_round_cleared"}:
        return "high"
    if classification in {"background_verification", "document_verification", "compensation_confirmation", "joining_date_updated", "hr_confirmation"}:
        return "medium"
    return "informational"


def record_analysis(
    message_id: str,
    candidate_id: str,
    result: dict[str, Any] | None,
    *,
    model: str | None,
    processing_status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    value = dict(result or {})
    trace = dict(value.get("_decision_trace") or {})
    deterministic_context = trace.get("deterministic_context")
    relevance_result = trace.get("recruitment_relevance_result")
    primary_result = trace.get("primary_model_result")
    validator_result = trace.get("validator_model_result")
    reconciled_result = trace.get("reconciled_result")
    backend_result = trace.get("backend_validated_final_result") or value

    def json_value(item: Any) -> str | None:
        return json.dumps(item, default=str) if item is not None else None

    classification = canonical_classification(value)
    confidence = max(0.0, min(1.0, float(value.get("confidence") or 0)))
    candidate_status = str(value.get("candidate_status") or _CLASSIFICATION_STATUS[classification])
    analysis_id = _id()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO mail_ai_analyses(
          id,mailbox_message_id,candidate_id,model_name,model_version,classification,candidate_status,
          confidence,summary,reason,recommended_action,raw_ai_response,validated_response,
          deterministic_context,recruitment_relevance_result,primary_model_result,
          validator_model_result,reconciled_result,backend_validated_final_result,
          processing_status,error_code,error_message,created_at,updated_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,
          %s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,now(),now())
          ON CONFLICT(mailbox_message_id) DO UPDATE SET model_name=EXCLUDED.model_name,
            model_version=EXCLUDED.model_version,classification=EXCLUDED.classification,
            candidate_status=EXCLUDED.candidate_status,confidence=EXCLUDED.confidence,
            summary=EXCLUDED.summary,reason=EXCLUDED.reason,recommended_action=EXCLUDED.recommended_action,
            raw_ai_response=EXCLUDED.raw_ai_response,validated_response=EXCLUDED.validated_response,
            deterministic_context=EXCLUDED.deterministic_context,
            recruitment_relevance_result=EXCLUDED.recruitment_relevance_result,
            primary_model_result=EXCLUDED.primary_model_result,
            validator_model_result=EXCLUDED.validator_model_result,
            reconciled_result=EXCLUDED.reconciled_result,
            backend_validated_final_result=EXCLUDED.backend_validated_final_result,
            processing_status=EXCLUDED.processing_status,error_code=EXCLUDED.error_code,
            error_message=EXCLUDED.error_message,updated_at=now() RETURNING *""",
          (analysis_id,message_id,candidate_id,model,(value.get('schema_version') or 'selection_offer_event_v1'),classification,
           candidate_status,confidence,str(value.get('summary') or '')[:1000],str(value.get('reason') or value.get('ignore_reason') or '')[:1000],
            str(value.get('recommended_action') or '')[:1000],json_value(primary_result or value),json_value(backend_result),
            json_value(deterministic_context),json_value(relevance_result),json_value(primary_result),
            json_value(validator_result),json_value(reconciled_result),json_value(backend_result),
            processing_status,error_code,str(error_message or '')[:400] or None))
        row=_rows(cur)[0]
        cur.execute("""UPDATE mail_ai_analyses SET ai_status=%s,validation_status=%s,
          email_intent=%s,document_type=%s,evidence_summary=%s WHERE id=%s RETURNING *""",(
          value.get('ai_status') or ('RETRY_PENDING' if processing_status=='RETRY_PENDING' else 'ANALYZED'),
          value.get('validation_status') or 'RETRY_PENDING',value.get('email_intent'),value.get('document_type'),
          value.get('evidence_summary') or value.get('summary'),row['id']))
        return _rows(cur)[0]


def _candidate_snapshot(candidate_id: str, structured: dict[str, Any]) -> tuple[str | None, str | None]:
    try:
        from features import candidate_store
        row = candidate_store.get_candidate(candidate_id) or {}
    except Exception:
        row = {}
    candidate = structured.get("candidate") or {}
    return (row.get("name") or candidate.get("name"), candidate.get("email"))


_CLASSIFICATIONS_BY_STATUS_LABEL: dict[str, set[str]] = {}
for _cls, _label in _CLASSIFICATION_STATUS.items():
    _CLASSIFICATIONS_BY_STATUS_LABEL.setdefault(_label, set()).add(_cls)


def _agreeing_candidate_status(result: dict[str, Any], classification: str) -> str:
    """The label shown on the alert, forced to agree with the classification.

    `status` and `candidate_status` are separate fields in the model's answer
    and they can contradict each other. An Innominds "Welcome aboard, please
    complete the pre-onboarding formalities" came back as JOINING_CONFIRMED
    with a candidate_status of "Profile Active" - the label belonging to
    not_relevant - so a Selection Related alert was headed with the words for
    nothing having happened.

    Grouping, filtering and the alert sound all key on the classification, so
    the label has to key on it too. A label that belongs to other
    classifications is replaced; one this map has never heard of is left alone,
    since the model may be more specific than the map.
    """
    proposed = str(result.get("candidate_status") or "").strip()
    canonical = _CLASSIFICATION_STATUS[classification]
    if not proposed or proposed == 'Needs Review':
        return canonical
    owners = _CLASSIFICATIONS_BY_STATUS_LABEL.get(proposed)
    if owners is not None and classification not in owners:
        return canonical
    return proposed


def apply_candidate_job_status(event: dict[str, Any], classification: str, candidate_status: str, *, force: bool = False, updated_by: str = "system", review_notes: str = "") -> bool:
    """Apply only high-confidence, monotonic candidate status transitions."""
    confidence = float(event.get("confidence") or 0)
    threshold = max(0.0, min(1.0, float(__import__('os').getenv('OLLAMA_CONFIDENCE_THRESHOLD', __import__('os').getenv('AI_RECRUITMENT_AUTO_ACCEPT_THRESHOLD', '0.90')))))
    validation=str(event.get('validation_status') or (event.get('structured_result') or {}).get('validation_status') or '').upper()
    if not force and (validation != 'AUTO_VALIDATED' or confidence < threshold or classification in {"needs_review", "not_relevant", "interview_update"}):
        return False
    candidate_id = str(event.get("canonical_candidate_id") or canonical_candidate_id(str(event["candidate_id"])))
    new_rank = _STATUS_RANK.get(candidate_status, 0)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM candidate_job_status WHERE candidate_id=%s FOR UPDATE", (candidate_id,))
        previous_rows = _rows(cur) if cur.description else []
        previous = previous_rows[0] if previous_rows else None
        previous_status = previous.get("status") if previous else None
        previous_rank = int(previous.get("status_rank") or 0) if previous else -1
        valid_terminal = (
            classification in {"offer_declined","offer_revoked"} and previous_status in {"Offer Received","Offer Accepted"}
        ) or (classification == "candidate_rejected" and previous_rank < _STATUS_RANK["Selected"])
        if previous and new_rank < previous_rank and not valid_terminal and not force:
            return False
        cur.execute("SELECT provider_message_id FROM mailbox_messages WHERE id=%s", (event.get("mailbox_message_id"),))
        provider_row = cur.fetchone()
        gmail_message_id = provider_row[0] if provider_row else None
        cur.execute("""INSERT INTO candidate_job_status(candidate_id,status,status_rank,source,source_id,gmail_message_id,classification,confidence,validation_status,updated_at)
          VALUES(%s,%s,%s,'AI Mail Monitoring',%s,%s,%s,%s,%s,now())
          ON CONFLICT(candidate_id) DO UPDATE SET status=EXCLUDED.status,status_rank=EXCLUDED.status_rank,
            source=EXCLUDED.source,source_id=EXCLUDED.source_id,gmail_message_id=EXCLUDED.gmail_message_id,
          classification=EXCLUDED.classification,confidence=EXCLUDED.confidence,
          validation_status=EXCLUDED.validation_status,updated_at=now()""",
          (candidate_id,candidate_status,new_rank,event.get('id'),gmail_message_id,classification,confidence,'APPROVED' if force else validation))
        cur.execute("""INSERT INTO candidate_status_history(id,candidate_id,previous_detected_status,new_detected_status,
          confirmed_status,source_type,source_id,gmail_message_id,ai_classification,confidence,updated_by,
          reviewed_by,reviewed_at,review_notes,created_at)
          VALUES(%s,%s,%s,%s,%s,'AI Mail Monitoring',%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN now() ELSE NULL END,%s,now())""",
          (_id(),candidate_id,previous_status,candidate_status,candidate_status if force else None,event.get('id'),gmail_message_id,
           classification,confidence,updated_by,updated_by if force else None,force,review_notes if force else None))
        cur.execute("UPDATE candidate_status_history SET validation_status=%s WHERE source_id=%s AND validation_status IS NULL",('APPROVED' if force else validation,event.get('id')))
    return previous_status != candidate_status


def rebuild_candidate_job_status(candidate_id: str) -> None:
    """Rebuild one derived current-state row from validated event truth."""
    canonical = canonical_candidate_id(candidate_id)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT e.*,m.provider_message_id FROM ai_recruitment_events e
          LEFT JOIN mailbox_messages m ON m.id=e.mailbox_message_id
          WHERE COALESCE(e.canonical_candidate_id,e.candidate_id)=%s
            AND e.validation_status IN('AUTO_VALIDATED','APPROVED')
            AND e.review_status NOT IN('FALSE_POSITIVE','DUPLICATE','REJECTED','IGNORED')
          ORDER BY CASE e.primary_status
            WHEN 'JOINED' THEN 90 WHEN 'POST_SELECTION_ONBOARDING' THEN 80
            WHEN 'JOINING_CONFIRMED' THEN 70 WHEN 'OFFER_ACCEPTED' THEN 60
            WHEN 'APPOINTMENT_LETTER_RECEIVED' THEN 50 WHEN 'OFFER_LETTER_RECEIVED' THEN 50
            WHEN 'OFFER_APPROVED' THEN 50 WHEN 'OFFER_IN_PROGRESS' THEN 50
            WHEN 'OFFER_INDICATION' THEN 50 WHEN 'FINAL_SELECTION_CONFIRMED' THEN 40
            WHEN 'SELECTED' THEN 40 ELSE 0 END DESC,e.created_at DESC LIMIT 1""",(canonical,))
        rows=_rows(cur)
        if not rows:
            cur.execute("DELETE FROM candidate_job_status WHERE candidate_id=%s AND source='AI Mail Monitoring'",(canonical,))
            return
        event=rows[0]
        classification=canonical_classification(event.get('structured_result') or {},event.get('primary_status'))
        status=str(event.get('candidate_status') or _CLASSIFICATION_STATUS[classification])
        rank=_STATUS_RANK.get(status,0)
        cur.execute("""INSERT INTO candidate_job_status(candidate_id,status,status_rank,source,source_id,gmail_message_id,
          classification,confidence,validation_status,updated_at)
          VALUES(%s,%s,%s,'AI Mail Monitoring',%s,%s,%s,%s,%s,now())
          ON CONFLICT(candidate_id) DO UPDATE SET status=EXCLUDED.status,status_rank=EXCLUDED.status_rank,
          source=EXCLUDED.source,source_id=EXCLUDED.source_id,gmail_message_id=EXCLUDED.gmail_message_id,
          classification=EXCLUDED.classification,confidence=EXCLUDED.confidence,
          validation_status=EXCLUDED.validation_status,updated_at=now()""",
          (canonical,status,rank,event.get('id'),event.get('provider_message_id'),classification,event.get('confidence'),event.get('validation_status')))


def notification_is_tracked(classification: str) -> bool:
    """Return True if the classification belongs to a tracked notification category.

    Mail Monitoring Notifications track only:
    - Auto interview slot booking (interview_confirmed, interview_rescheduled, interview_cancelled)
    - Job confirmed monitoring mails (job_selection_confirmed, offer_received, offer_accepted, joining_confirmed)
    """
    return classification in TRACKED_NOTIFICATION_CLASSIFICATIONS


def should_route_to_mail_alert(
    event: dict[str, Any],
    analysis: dict[str, Any],
    *,
    source: dict[str, Any] | None = None,
    today: date | None = None,
) -> bool:
    """Return whether a persisted event belongs on the Mail Alerts screen.

    A valid tracked classification is now enough. This used to demand that the
    model's evidence `meaning` fields matched a fixed vocabulary whenever the
    event needed review, and that second opinion silently withheld 171 real
    events: flocareer interview reminders, an owlsure L1 discussion, an
    Innominds "complete the pre-onboarding formalities", a digiverifier
    employment-BGV invitation. Each was classified correctly, persisted
    correctly, and never shown to anyone. The failure left no trace, because a
    withheld notification logs nothing.

    Precision belongs upstream, where it can be reasoned about: banking,
    job-board, promotional and service-ad mail is excluded deterministically by
    `routing_decision` and `job_board_notification` before Ollama is called at
    all. Judging the model's own vocabulary after the fact could only ever
    reject work that had already passed those checks.

    Interview alerts still need a current or future schedule - a rescan must not
    reopen last month's interviews - and an invalid or errored classification
    still goes to Needs Review rather than being dropped.
    """
    structured = event.get("structured_result") or {}
    if isinstance(structured, str):
        structured = json.loads(structured)
    if structured.get("_suppress_monitoring_notification"):
        return False

    classification = canonical_classification(
        analysis,
        str(event.get("primary_status") or ""),
    )
    if not notification_is_tracked(classification):
        return False

    # Model-produced lifecycle alerts fail closed. A status is routable only
    # when the separate relevance gate established an actual recipient hiring
    # process and the backend's explicit status validator proved the transition.
    # Trusted RFC calendar sources keep their existing independent path.
    source_name = str(structured.get("classification_source") or "").upper()
    if source_name == "OLLAMA":
        relevance = structured.get("recruitment_relevance_result") or {}
        if str(relevance.get("decision") or "").upper() != "ESTABLISHED":
            return False
        if structured.get("backend_transition_validated") is not True:
            return False

    if classification in {"interview_confirmed", "interview_rescheduled"}:
        # Only scheduled interview transitions have a date proof obligation.
        # A shortlist is the outcome before a schedule exists, so applying this
        # gate to every `interview_*` classification made the tracked
        # interview_shortlisted category impossible to notify.
        interview = structured.get("interview") or {}
        raw_date = str(interview.get("date") or event.get("interview_date") or "").strip()
        try:
            scheduled_date = date.fromisoformat(raw_date)
        except ValueError:
            return False
        if scheduled_date < (today or date.today()):
            return False

    if classification.startswith("interview_"):
        # "Trust the classification" means trust Ollama's. These two sources are
        # what the pipeline writes when Ollama produced nothing: during an
        # outage the fallback derives a status from routing context alone, so a
        # newsletter carrying a date could otherwise become an interview alert
        # with no model having read it. A real Ollama classification never
        # reaches this check.
        if source_name in {"FALLBACK", "FAILURE_REVIEW"}:
            subject = str((source or {}).get("subject") or event.get("subject") or "")
            if not any(
                cue in subject.casefold()
                for cue in ("interview", "technical screening", "screening round")
            ):
                return False

    return True


def create_monitoring_notification(
    event: dict[str, Any],
    analysis: dict[str, Any],
    *,
    silent: bool = False,
) -> dict[str, Any]:
    """Create a user-facing notification only for tracked classifications.

    Only auto interview slot booking and job confirmed monitoring mails produce
    notifications. Other classifications are still processed for candidate status
    updates and offer tracking but do not generate notifications.

    `silent=True` writes the alert without the `notification_created` realtime
    event. The browser turns that event into a sound, and a sound is a claim
    that something just happened. Recovering an alert for mail that arrived
    weeks ago is worth doing -- the alert was genuinely missed and belongs on
    the screen -- but announcing it is not: it would interrupt an operator for
    a month-old email and, done in bulk, would fire once per recovered row.
    Live classification never passes this; only a backfill does.
    """
    message_id = event.get("mailbox_message_id")
    structured = event.get("structured_result") or {}
    if isinstance(structured, str):
        structured = json.loads(structured)
    classification = str(analysis["classification"])
    candidate_status = str(analysis.get("candidate_status") or _CLASSIFICATION_STATUS[classification])

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT m.provider_message_id,m.provider_thread_id,m.subject,m.sender_name,m.sender_email,m.sent_at,m.mailbox_id,m.recipient_email
          FROM mailbox_messages m WHERE m.id=%s""", (message_id,))
        source = cur.fetchone()
        if not source:
            raise ValueError("Mailbox message not found for notification")
        provider_id,thread_id,subject,sender_name,sender_email,sent_at,mailbox_id,recipient_email = source
        source_row = {
            "provider_message_id": provider_id,
            "provider_thread_id": thread_id,
            "subject": subject,
            "sender_name": sender_name,
            "sender_email": sender_email,
            "sent_at": sent_at,
            "mailbox_id": mailbox_id,
            "recipient_email": recipient_email,
        }
        if not should_route_to_mail_alert(event, analysis, source=source_row):
            return {}

        name, email = _candidate_snapshot(str(event["candidate_id"]), structured)
        # Keep source provenance on the event, but use the same stable person
        # as booking/lifecycle for new interview alert projections.
        notification_candidate_id = (
            canonical_candidate_id(str(event['candidate_id']))
            if classification in _INTERVIEW_CLASSIFICATIONS else event['candidate_id']
        )
        notification_id = _id()
        company = structured.get("company") or {}
        job = structured.get("job") or {}
        confidence = float(event.get("confidence") or analysis.get("confidence") or 0)
        priority = notification_priority(classification, confidence=confidence, requires_review=bool(event.get("requires_manual_review")))
        reason = str(structured.get("reason") or structured.get("ignore_reason") or '')[:1000]
        action = str(structured.get("recommended_action") or '')[:1000]
        cur.execute("""INSERT INTO mail_monitoring_notifications(id,candidate_id,candidate_name,candidate_email,
          gmail_account_id,gmail_message_id,gmail_thread_id,email_analysis_id,ai_recruitment_event_id,
          classification,candidate_status,company_name,job_role,email_subject,sender_name,sender_email,
          email_received_at,ai_confidence,ai_summary,ai_reason,recommended_action,priority,created_at,updated_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
          ON CONFLICT(gmail_message_id,classification) DO NOTHING RETURNING *""",
          (notification_id,notification_candidate_id,name,email or recipient_email,mailbox_id,provider_id,thread_id,analysis['id'],event['id'],
           classification,candidate_status,company.get('name') or event.get('company_name'),job.get('title') or event.get('job_title'),
           subject,sender_name,sender_email,sent_at,confidence,str(event.get('summary') or structured.get('summary') or '')[:1000],
           reason,action,priority))
        inserted = _rows(cur)
        if not inserted:
            # A reprocess updates the existing row but is not a second alert.
            # Returning the same distinction to the caller prevents it from
            # publishing another `notification_created` event and sound.
            cur.execute("""UPDATE mail_monitoring_notifications SET updated_at=now()
              WHERE gmail_message_id=%s AND classification=%s RETURNING *""",
              (provider_id, classification))
            return _rows(cur)[0]

        notification = inserted[0]
        # The notification row and its creation event are one transaction. A
        # process exit between persistence and WebSocket fan-out can therefore
        # delay delivery, but can no longer leave a genuine alert with no
        # durable realtime event for replay/tailing.
        realtime_payload = {
            "notification_id": notification["id"],
            "candidate_id": notification_candidate_id,
            "candidate_name": notification.get("candidate_name"),
            "company_name": notification.get("company_name"),
            "classification": classification,
            "status": candidate_status,
            "confidence": round(confidence * 100),
            "priority": priority,
            "provider_message_id": provider_id,
        }
        if silent:
            notification["_created_realtime_event"] = None
            notification["_recovered_silently"] = True
            return notification
        notification["_created_realtime_event"] = _record_realtime_event(
            cur, "notification_created", realtime_payload,
        )
        return notification


def recover_missing_notifications(
    start: str,
    end: str,
    *,
    silent: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """Write the alerts that tracked, already-classified events never produced.

    An event can be classified correctly and still never reach the Mail Alerts
    screen, because routing is decided separately and a routing bug leaves the
    event untouched. 92 August `interview_shortlisted` events are exactly that:
    each one correct, each one invisible, because the gate applied an
    interview-date requirement to a classification that by definition has no
    date yet. Nothing about them needs reclassifying -- re-running the model
    would spend hours to reproduce the answers already on record.

    So this replays only the routing decision, through the same
    `create_monitoring_notification` the live path uses, which re-checks
    `should_route_to_mail_alert` itself and is idempotent on
    (gmail_message_id, classification). Rows that should not be alerts stay out
    on their own merits; rows already alerted are left alone.

    Silent by default: see `create_monitoring_notification`.
    """
    recovered: list[dict[str, Any]] = []
    skipped_existing = 0
    not_routable = 0

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT e.id
                 FROM ai_recruitment_events e
                 JOIN mailbox_messages m ON m.id = e.mailbox_message_id
            LEFT JOIN mail_monitoring_notifications n
                   ON n.ai_recruitment_event_id = e.id
                WHERE m.sent_at >= %s AND m.sent_at < %s
                  AND e.classification = ANY(%s)
                  AND n.id IS NULL
             ORDER BY m.sent_at""",
            (start, end, sorted(TRACKED_NOTIFICATION_CLASSIFICATIONS)),
        )
        event_ids = [row["id"] for row in _rows(cur)]

    if limit is not None:
        event_ids = event_ids[:limit]

    for event_id in event_ids:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM ai_recruitment_events WHERE id=%s", (event_id,))
            rows = _rows(cur)
            if not rows:
                continue
            event = rows[0]
            cur.execute(
                """SELECT * FROM mail_ai_analyses WHERE mailbox_message_id=%s
                   ORDER BY created_at DESC LIMIT 1""",
                (event["mailbox_message_id"],),
            )
            analyses = _rows(cur)
        if not analyses:
            # No recorded analysis means nothing to attribute the alert to, and
            # inventing one would fabricate the evidence the alert rests on.
            not_routable += 1
            continue
        analysis = analyses[0]
        analysis["classification"] = event["classification"]
        analysis["candidate_status"] = event.get("candidate_status")

        notification = create_monitoring_notification(event, analysis, silent=silent)
        if not notification:
            not_routable += 1
            continue
        if notification.get("_recovered_silently") or notification.get("_created_realtime_event"):
            recovered.append({
                "event_id": event_id,
                "notification_id": notification["id"],
                "classification": event["classification"],
                "recovered_now": True,
                "sound_fired": not silent,
            })
        else:
            skipped_existing += 1

    return {
        "candidates_considered": len(event_ids),
        "recovered": recovered,
        "recovered_count": len(recovered),
        "already_alerted": skipped_existing,
        "not_routable": not_routable,
        "sound_fired": not silent,
    }


def finalize_detection(event: dict[str, Any], *, result: dict[str, Any], model: str, duration_ms: int) -> dict[str, Any]:
    """Persist analysis, safe candidate state and notification after the event."""
    classification = canonical_classification(result)
    candidate_status = _agreeing_candidate_status(result, classification)
    result["classification"] = classification
    result["candidate_status"] = candidate_status
    try:
        from features import candidate_store
        mapping_confirmed = candidate_store.get_candidate(str(event["candidate_id"])) is not None
    except Exception:
        mapping_confirmed = False
    if not mapping_confirmed:
        classification="ai_retry_pending";candidate_status="AI Retry Pending"
        result.update(classification=classification,candidate_status=candidate_status,requires_manual_review=False,
                      validation_status='RETRY_PENDING',automation_decision='AI_RETRY_PENDING',
                      reason="Candidate mapping could not be confirmed",risk_flags=list(dict.fromkeys((result.get('risk_flags') or [])+['CANDIDATE_MAPPING_ISSUE'])))
        record_automation_state(mailbox_message_id=event['mailbox_message_id'], event_id=event['id'],
                                state='AI_RETRY_PENDING', reason='CANDIDATE_MAPPING_ISSUE')
    validation_status=str(result.get('validation_status') or event.get('validation_status') or 'RETRY_PENDING').upper()
    processing_status='RETRY_PENDING' if validation_status=='RETRY_PENDING' else 'CLASSIFIED'
    analysis = record_analysis(event["mailbox_message_id"], event["candidate_id"], result, model=model, processing_status=processing_status)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE ai_recruitment_events SET classification=%s,candidate_status=%s,ai_reason=%s,
          recommended_action=%s,original_ai_result=COALESCE(original_ai_result,%s::jsonb),updated_at=now()
          WHERE id=%s RETURNING *""", (classification,candidate_status,str(result.get('reason') or result.get('ignore_reason') or '')[:1000],
          str(result.get('recommended_action') or '')[:1000],json.dumps(result,default=str),event['id']))
        event = _rows(cur)[0]
    event['validation_status']=validation_status
    status_updated = apply_candidate_job_status(event, classification, candidate_status)
    if not status_updated and classification not in {"needs_review","ai_retry_pending","not_relevant","interview_update"}:
        with get_connection() as conn,conn.cursor() as cur:
            cur.execute("SELECT status,status_rank FROM candidate_job_status WHERE candidate_id=%s",(event['candidate_id'],));current=cur.fetchone()
        if current and current[0] != candidate_status and int(current[1] or 0) > _STATUS_RANK.get(candidate_status,0):
            event['requires_manual_review']=False
            event['status_conflict']=True
    notification = create_monitoring_notification(event, analysis)
    event["classification"] = classification
    event["candidate_status"] = candidate_status
    event["notification"] = notification
    event["candidate_status_updated"] = status_updated
    return event


def _record_realtime_event(
    cur: Any, event_type: str, payload: dict[str, Any],
) -> dict[str, Any]:
    event_id = _id()
    safe = dict(payload)
    cur.execute("""INSERT INTO mail_realtime_events(id,event_type,notification_id,candidate_id,payload,created_at)
      VALUES(%s,%s,%s,%s,%s::jsonb,now()) RETURNING *""",
      (event_id,event_type,safe.get('notification_id'),safe.get('candidate_id'),json.dumps(safe,default=str)))
    return _rows(cur)[0]


def record_realtime_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    with get_connection() as conn, conn.cursor() as cur:
        return _record_realtime_event(cur, event_type, payload)


def list_realtime_events(*, after_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if after_id:
        where = (
            "WHERE (created_at,id)>(SELECT created_at,id "
            "FROM mail_realtime_events WHERE id=%s)"
        )
        params.append(after_id)
    params.append(max(1, min(limit, 500)))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM mail_realtime_events {where} ORDER BY created_at ASC, id ASC LIMIT %s", params)
        return _rows(cur)


def latest_realtime_event_id() -> str | None:
    """Return the present end of the durable realtime log.

    ``list_realtime_events(limit=1)`` intentionally returns the *oldest* row so
    replay can run forwards.  It must therefore never be used to bootstrap the
    live tailer: doing so re-emits the whole historical log as live events.
    """
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM mail_realtime_events "
            "ORDER BY created_at DESC, id DESC LIMIT 1"
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def list_notifications(
    *, filters: dict[str, Any] | None = None, limit: int = 50, offset: int = 0,
    group_by_candidate: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    filters = filters or {}
    rows_clause, rows_params = visible_rows_sql()
    where: list[str] = [rows_clause]
    params: list[Any] = list(rows_params)
    exact = {"candidate_id", "candidate_status", "priority"}
    # When no specific classification filter is provided, default to tracked
    # notification classifications (auto interview booking + job confirmed).
    # This ensures non-tracked classifications are excluded by default.
    if filters.get("classification"):
        where.append("classification=%s")
        params.append(filters["classification"])
    elif filters.get("classification_group"):
        # The Mail Alerts screen filters by group rather than by the eighteen
        # individual classifications. Exact `classification` is left untouched
        # above so existing callers behave identically; an unknown group falls
        # through to the tracked default rather than returning nothing, because
        # a filter that silently empties the screen reads as "no alerts".
        group = CLASSIFICATION_GROUPS.get(
            str(filters["classification_group"]).strip().lower()
        )
        if group:
            placeholders = ", ".join("%s" for _ in sorted(group))
            where.append(f"classification IN ({placeholders})")
            params.extend(sorted(group))
        else:
            tracked_clause, tracked_params = tracked_classification_sql()
            where.append(tracked_clause)
            params.extend(tracked_params)
    else:
        tracked_clause, tracked_params = tracked_classification_sql()
        where.append(tracked_clause)
        params.extend(tracked_params)
    # `exact` was assigned here and never read, so candidate_id, candidate_status
    # and priority were accepted by the API, forwarded by the screen, and then
    # silently dropped — every query returned the unfiltered set. Nothing failed:
    # a filter that does nothing looks exactly like a filter matching everything.
    # Found by checking the live totals per candidate rather than trusting that
    # the name in the set meant the value was used.
    for field in sorted(exact):
        value = filters.get(field)
        if value not in (None, ""):
            if field == 'candidate_id':
                from services.recruitment_identity import aliases, load_links
                where.append('(candidate_id=%s OR candidate_id=ANY(%s))')
                params.extend([value, sorted(aliases(str(value), load_links()))])
            elif field == 'priority' and value == 'retry_pending':
                where.append("priority IN ('retry_pending','review_required')")
            else:
                where.append(f"{field}=%s")
                params.append(value)
    for field in ("is_read", "is_reviewed"):
        if filters.get(field) is not None:
            where.append(f"{field}=%s")
            params.append(bool(filters[field]))
    if filters.get("company"):
        where.append("company_name ILIKE %s"); params.append(f"%{filters['company']}%")
    if filters.get("search"):
        where.append("concat_ws(' ',candidate_name,candidate_email,company_name,job_role,email_subject,sender_email,ai_summary) ILIKE %s")
        params.append(f"%{filters['search']}%")
    if filters.get("confidence_min") is not None:
        where.append("ai_confidence>=%s"); params.append(float(filters['confidence_min']))
    if filters.get("confidence_max") is not None:
        where.append("ai_confidence<=%s"); params.append(float(filters['confidence_max']))
    if filters.get("date_from"):
        where.append("created_at::date>=%s"); params.append(filters['date_from'])
    if filters.get("date_to"):
        where.append("created_at::date<=%s"); params.append(filters['date_to'])
    clause = " AND ".join(where)
    order = "ASC" if str(filters.get("sort") or "").lower() == "oldest" else "DESC"
    page = max(1, min(limit, 100))
    skip = max(0, offset)
    with get_connection() as conn, conn.cursor() as cur:
        if group_by_candidate:
            # The page unit becomes the candidate rather than the row.
            #
            # Grouping the twenty rows of an ordinary page in the browser would
            # have been less code, but a candidate whose alerts straddle a page
            # boundary would then be drawn as two separate groups on two pages,
            # which is the exact thing the grouped view exists to stop. So the
            # page of candidates is chosen first and every matching row those
            # candidates hold is returned - no mail is dropped or collapsed,
            # and `total` counts candidates because that is what is paged.
            cur.execute(
                f"SELECT count(DISTINCT candidate_id) FROM mail_monitoring_notifications WHERE {clause}",
                params,
            )
            total = int(cur.fetchone()[0])
            cur.execute(
                f"""SELECT candidate_id FROM mail_monitoring_notifications WHERE {clause}
                     GROUP BY candidate_id ORDER BY max(created_at) {order}, candidate_id
                     LIMIT %s OFFSET %s""",
                params + [page, skip],
            )
            candidate_ids = [row[0] for row in cur.fetchall()]
            if not candidate_ids:
                return [], total
            placeholders = ", ".join("%s" for _ in candidate_ids)
            cur.execute(
                f"""SELECT * FROM mail_monitoring_notifications
                     WHERE {clause} AND candidate_id IN ({placeholders})
                     ORDER BY created_at {order}""",
                params + candidate_ids,
            )
            return reconcile_booking_claims(_rows(cur)), total
        cur.execute(f"SELECT count(*) FROM mail_monitoring_notifications WHERE {clause}", params)
        total = int(cur.fetchone()[0])
        cur.execute(f"SELECT * FROM mail_monitoring_notifications WHERE {clause} ORDER BY created_at {order} LIMIT %s OFFSET %s", params + [page, skip])
        rows = _rows(cur)
    return reconcile_booking_claims(rows), total


def list_notification_candidates() -> list[dict[str, Any]]:
    """Candidates that actually have visible alerts, for the Mail Alerts filter.

    Deliberately sourced from the notifications themselves rather than the
    candidate store: the filter matches on `candidate_id`, so the options have
    to be ids this table really holds, and an operator filtering alerts has no
    use for candidates that have never raised one.

    Mirrors the visibility rules of `list_notifications` — dismissed rows and
    historical skips excluded, tracked classifications only — so every option
    offered returns at least one row when selected.
    """
    placeholders = ", ".join("%s" for _ in TRACKED_NOTIFICATION_CLASSIFICATIONS)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT candidate_id,
                       max(candidate_name) AS candidate_name,
                       count(*) AS alert_count
                  FROM mail_monitoring_notifications
                 WHERE dismissed_at IS NULL
                   AND COALESCE(booking_status,'') <> 'Historical Skipped'
                   AND classification IN ({placeholders})
                   AND COALESCE(candidate_id,'') <> ''
              GROUP BY candidate_id
              ORDER BY lower(coalesce(max(candidate_name), candidate_id))""",
            list(TRACKED_NOTIFICATION_CLASSIFICATIONS),
        )
        return _rows(cur)


# A mail may claim a booking only for as long as that booking exists.
#
# These statuses assert a slot is on the roster. They were written once, when
# the slot really was there and had been re-read to prove it, and then never
# revisited -- so removing the slot afterwards left the alert asserting a
# booking that Confirmed Slots and Daily Ops no longer had. Cancelling from the
# candidates screen is the quietest way in: it clears the row and writes no
# audit and no notification at all.
BOOKED_BOOKING_STATUSES = ("Auto Booked", "Approved & Booked", "Rescheduled")

# Not "Cancelled": nobody cancelled these. The booking they named is simply not
# there any more, and a human has to decide what that means.
RELEASED_BOOKING_STATUS = "AI_RETRY_PENDING"


def _project_released_booking_title(row):
    """Keep the historical title available without presenting it as live truth."""
    if row.get('candidate_status') in {
        'Interview Automatically Booked', 'Interview Manually Approved & Booked',
        'Interview Rescheduled',
    }:
        row.setdefault('historical_candidate_status', row['candidate_status'])
        row['candidate_status'] = 'AI Retry Pending'


def reconcile_booking_claims(rows):
    """Never report a booking the roster does not have.

    The stored column is corrected wherever a slot is removed, but that only
    covers the removals that go through the store. This is the guarantee: a row
    claiming a booking is checked against the roster as it stands right now, so
    Mail Alerts cannot disagree with Confirmed Slots and Daily Ops whatever put
    them out of step.
    """
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        # Compatibility projection only; retain historical source/audit rows.
        if row.get('priority') == 'review_required':
            row['priority'] = 'retry_pending'
        if row.get('classification') == 'needs_review':
            row['classification'] = 'ai_retry_pending'
        if row.get('candidate_status') == 'Needs Review':
            row['candidate_status'] = 'AI Retry Pending'
        if row.get('booking_status') in {'Needs Review', 'MANUAL_REVIEW_REQUIRED'}:
            row['booking_status'] = 'AI_RETRY_PENDING'
        # Some removals already persisted the released status. Those rows no
        # longer enter `claims`, but their old title still painted a green
        # "Automatically Booked" in the list, detail dialog and alert bell.
        if row.get('booking_status') == RELEASED_BOOKING_STATUS:
            _project_released_booking_title(row)
    claims = [
        row for row in (rows or [])
        if isinstance(row, dict)
        and str(row.get("booking_status") or "") in BOOKED_BOOKING_STATUSES
        and str(row.get("booking_id") or "").strip()
    ]
    if not claims:
        return rows
    try:
        from features import candidate_store

        for row in claims:
            booked = candidate_store.get_candidate(str(row["booking_id"]).strip())
            if booked and candidate_store.candidate_has_confirmed_slot(booked):
                continue
            row["booking_status"] = RELEASED_BOOKING_STATUS
            row["booking_claim_released"] = True
            _project_released_booking_title(row)
    except Exception:
        # A reconciliation that cannot run must not blank the screen. The
        # stored column is still the one written under the persistence checks.
        logger.exception("Could not reconcile booking claims against the roster")
    return rows


def release_booking_claims(candidate_id: str, *, reason: str = "slot_removed") -> int:
    """Stop any mail claiming a booking on this candidate, and say how many.

    Called wherever a slot leaves the roster, which is the one choke point every
    removal goes through. Rows already recording a cancellation or a block are
    left alone -- they are not claiming a booking.
    """
    target = str(candidate_id or "").strip()
    if not target:
        return 0
    placeholders = ", ".join("%s" for _ in BOOKED_BOOKING_STATUSES)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""UPDATE mail_monitoring_notifications
                   SET booking_status=%s, updated_at=now()
                 WHERE booking_id=%s
                   AND booking_status IN ({placeholders})
             RETURNING id""",
            (RELEASED_BOOKING_STATUS, target, *BOOKED_BOOKING_STATUSES),
        )
        released = cur.fetchall()
    if released:
        logger.warning(
            "released %d booking claim(s) for candidate=%s reason=%s",
            len(released), target, reason,
        )
    return len(released)


def visible_rows_sql() -> tuple[str, list[Any]]:
    """Not dismissed, and not a historical row the tool deliberately skipped."""
    return (
        "dismissed_at IS NULL AND COALESCE(booking_status,'') <> 'Historical Skipped'",
        [],
    )


def tracked_classification_sql() -> tuple[str, list[Any]]:
    """Only classifications the screen is built to act on."""
    placeholders = ", ".join("%s" for _ in TRACKED_NOTIFICATION_CLASSIFICATIONS)
    return f"classification IN ({placeholders})", list(TRACKED_NOTIFICATION_CLASSIFICATIONS)


def notification_visibility_sql() -> tuple[str, list[Any]]:
    """The rows an operator can actually reach on the Mail Alerts screen.

    Composed from the two pieces below because it used to be written twice.
    `notification_summary` counted every non-dismissed row while
    `list_notifications` also required a tracked classification, so the cards
    advertised 129 / 116 / 122 above a table that could only ever show
    17 / 4 / 10 — and clicking a card produced a number nothing on screen
    explained.

    Both callers build from the same pieces now, so the counts and the rows
    cannot describe different sets again.
    """
    rows_clause, rows_params = visible_rows_sql()
    tracked_clause, tracked_params = tracked_classification_sql()
    return f"{rows_clause} AND {tracked_clause}", rows_params + tracked_params


def notification_summary() -> dict[str, Any]:
    visibility, params = notification_visibility_sql()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT count(*) FILTER(WHERE dismissed_at IS NULL) visible_total,
          count(*) FILTER(WHERE NOT is_read AND dismissed_at IS NULL) unread,
          count(*) FILTER(WHERE classification='offer_received' AND dismissed_at IS NULL) new_offers,
          count(*) FILTER(WHERE classification='job_selection_confirmed' AND dismissed_at IS NULL) selections,
          count(*) FILTER(WHERE classification='joining_confirmed' AND dismissed_at IS NULL) joining_confirmations,
          count(*) FILTER(WHERE classification='interview_confirmed' AND booking_status='Auto Booked' AND dismissed_at IS NULL) auto_booked_interviews,
          count(*) FILTER(WHERE booking_status IN('Blocked','Processing Failed') AND dismissed_at IS NULL) booking_blocked,
          count(*) FILTER(WHERE (priority IN ('review_required','retry_pending') OR booking_status='AI_RETRY_PENDING') AND dismissed_at IS NULL) ai_retry_pending,
          count(*) FILTER(WHERE classification IN ('offer_received','offer_accepted','job_selection_confirmed') AND dismissed_at IS NULL) job_confirmed_count,
          count(*) FILTER(WHERE classification IN ('interview_confirmed','interview_rescheduled','interview_cancelled') AND dismissed_at IS NULL) interview_booking_count
          FROM mail_monitoring_notifications
          WHERE """ + visibility, params)
        names = [d.name for d in cur.description]
        return dict(zip(names, cur.fetchone()))


def clear_notifications(*, reviewer: str) -> int:
    """Dismiss every currently visible notification without deleting evidence."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE mail_monitoring_notifications SET
          dismissed_at=now(),is_read=true,read_at=COALESCE(read_at,now()),updated_at=now()
          WHERE dismissed_at IS NULL RETURNING id""")
        cleared = len(cur.fetchall())
    return cleared


def update_notification(notification_id: str, action: str, *, reviewer: str, notes: str = "", changes: dict[str, Any] | None = None) -> dict[str, Any]:
    if action not in {'read', 'unread', 'dismiss'}:
        raise ValueError('Notification decisions are automated')
    changes = dict(changes or {})
    corrected_event: dict[str, Any] | None = None
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM mail_monitoring_notifications WHERE id=%s FOR UPDATE", (notification_id,))
        rows = _rows(cur)
        if not rows:
            return {}
        before = rows[0]
        assignments: dict[str, Any] = {}
        if action == "read": assignments.update(is_read=True, read_at=now())
        elif action == "unread": assignments.update(is_read=False, read_at=None)
        elif action == "reviewed": assignments.update(is_reviewed=True, reviewed_at=now(), reviewed_by=reviewer, review_notes=notes)
        elif action == "dismiss": assignments.update(dismissed_at=now())
        elif action == "false-detection": assignments.update(is_false_detection=True,is_reviewed=True,reviewed_at=now(),reviewed_by=reviewer,review_notes=notes)
        elif action == "correct":
            classification = str(changes.get('classification') or '').lower()
            if classification not in CANONICAL_CLASSIFICATIONS: raise ValueError('Unsupported classification')
            assignments.update(classification=classification,candidate_status=str(changes.get('candidate_status') or _CLASSIFICATION_STATUS[classification]),is_reviewed=True,reviewed_at=now(),reviewed_by=reviewer,review_notes=notes)
        else: raise ValueError('Unsupported notification action')
        sql = ",".join(f"{key}=%s" for key in assignments)
        cur.execute(f"UPDATE mail_monitoring_notifications SET {sql},updated_at=now() WHERE id=%s RETURNING *", (*assignments.values(),notification_id))
        updated = _rows(cur)[0]
        if action in {'reviewed','false-detection','correct'}:
            cur.execute("""INSERT INTO mail_review_evaluations(id,notification_id,email_analysis_id,original_classification,
              corrected_classification,original_candidate_status,corrected_candidate_status,original_confidence,
              is_false_detection,review_notes,reviewed_by,created_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())""",
              (_id(),notification_id,before.get('email_analysis_id'),before.get('classification'),updated.get('classification'),
               before.get('candidate_status'),updated.get('candidate_status'),before.get('ai_confidence'),action=='false-detection',notes,reviewer))
        if action=='correct' and before.get('ai_recruitment_event_id'):
            correction={'classification':updated.get('classification'),'candidate_status':updated.get('candidate_status'),'review_notes':notes,'reviewed_by':reviewer}
            cur.execute("""UPDATE ai_recruitment_events SET classification=%s,candidate_status=%s,
              corrected_result=%s::jsonb,review_status='APPROVED',reviewed_by=%s,reviewed_at=now(),review_notes=%s,updated_at=now()
              WHERE id=%s RETURNING *""",(updated.get('classification'),updated.get('candidate_status'),json.dumps(correction),reviewer,notes,before.get('ai_recruitment_event_id')))
            event_rows=_rows(cur)
            corrected_event=event_rows[0] if event_rows else None
    if corrected_event:
        apply_candidate_job_status(corrected_event,str(updated['classification']),str(updated['candidate_status']),force=True,updated_by=reviewer,review_notes=notes)
    return updated


def notification_reprocess_context(notification_id: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT n.*,m.id AS mailbox_message_id,m.provider_message_id,m.provider_thread_id,
          m.sender_name,m.sender_email,m.recipient_email,m.subject,m.sent_at,m.body_text,m.html_body_text,
          b.* FROM mail_monitoring_notifications n
          JOIN mailbox_messages m ON m.provider_message_id=n.gmail_message_id AND m.mailbox_id=n.gmail_account_id
          JOIN candidate_mailboxes b ON b.id=m.mailbox_id WHERE n.id=%s""",(notification_id,))
        rows=_rows(cur)
    if not rows:return None
    row=rows[0]
    row['attachments']=[{**item,'text':item.get('extracted_text') or ''} for item in attachments_for_message(row['mailbox_message_id'],include_text=True)]
    return row


def record_interview_analysis(
    *, mailbox_message_id: str, email_analysis_id: str | None,
    mailbox_id: str, gmail_message_id: str, gmail_thread_id: str | None,
    candidate_id: str, result: dict[str, Any], validation_status: str,
    processing_status: str,
) -> dict[str, Any]:
    interview = dict(result.get("interview") or {})
    classification = canonical_classification(result)
    analysis_id = _id()
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO interview_mail_analyses(id,mailbox_message_id,email_analysis_id,
              gmail_account_id,gmail_message_id,gmail_thread_id,candidate_id,classification,
              is_interview_email,company_name,job_role,interview_round,interview_date,
              interview_time,timezone,meeting_link,interview_mode,location,ai_confidence,
              ai_summary,ai_reason,validation_status,processing_status,structured_result,
              created_at,updated_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,now(),now())
              ON CONFLICT(mailbox_message_id) DO UPDATE SET
                email_analysis_id=EXCLUDED.email_analysis_id,classification=EXCLUDED.classification,
                is_interview_email=EXCLUDED.is_interview_email,company_name=EXCLUDED.company_name,
                job_role=EXCLUDED.job_role,interview_round=EXCLUDED.interview_round,
                interview_date=EXCLUDED.interview_date,interview_time=EXCLUDED.interview_time,
                timezone=EXCLUDED.timezone,meeting_link=EXCLUDED.meeting_link,
                interview_mode=EXCLUDED.interview_mode,location=EXCLUDED.location,
                ai_confidence=EXCLUDED.ai_confidence,ai_summary=EXCLUDED.ai_summary,
                ai_reason=EXCLUDED.ai_reason,validation_status=EXCLUDED.validation_status,
                processing_status=EXCLUDED.processing_status,structured_result=EXCLUDED.structured_result,
                updated_at=now() RETURNING *""",
            (
                analysis_id, mailbox_message_id, email_analysis_id, mailbox_id, gmail_message_id,
                gmail_thread_id, candidate_id, classification,
                classification.startswith("interview_"),
                (result.get("company") or {}).get("name"), (result.get("job") or {}).get("title"),
                interview.get("round"), interview.get("date") or None, interview.get("time"),
                interview.get("timezone"), interview.get("meeting_link"), interview.get("mode"),
                interview.get("location"), float(result.get("confidence") or 0),
                result.get("summary"), result.get("reason"), validation_status,
                processing_status, json.dumps(result, default=str),
            ),
        )
        return _rows(cur)[0]


def record_booking_audit(
    *, analysis_id: str | None, candidate_id: str, gmail_message_id: str,
    gmail_thread_id: str | None, classification: str, booking_id: str | None,
    auto_booked: bool, validation_status: str, payment_status: str,
    duplicate_status: str, conflict_status: str, booking_status: str,
    previous_booking: dict[str, Any] | None = None,
    new_booking: dict[str, Any] | None = None, failure_code: str | None = None,
    failure_message: str | None = None, correlation_id: str | None = None,
    source_event_id: str | None = None, lifecycle_transition_key: str | None = None,
    source_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append an immutable outcome; an equivalent retry returns the same fact.

    Candidate aliases, regenerated analyses and worker correlation IDs must not
    rekey an outcome. Source message, action, slot and outcome identify the
    fact; source references/snapshots are fixed by its first committed writer.
    A changed outcome (e.g. failure then success) appends, never erases failure.
    """
    audit_id = _id()
    fact_key = hashlib.sha256(json.dumps([
        'booking-audit-v1', gmail_message_id, classification, booking_id or None,
        bool(auto_booked), booking_status, failure_code or None,
    ], separators=(',', ':')).encode()).hexdigest()
    with get_connection() as conn, conn.cursor() as cur:
        # Serialize legacy-row adoption and concurrent retries in one DB
        # transaction. The unique key is the final race/crash safety boundary.
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                    ('booking-audit:' + gmail_message_id + ':' + classification,))
        cur.execute("""SELECT * FROM interview_auto_booking_audit
          WHERE audit_fact_key=%s OR (audit_fact_key IS NULL
            AND gmail_message_id=%s AND classification=%s
            AND booking_id IS NOT DISTINCT FROM %s AND auto_booked=%s
            AND booking_status=%s AND failure_code IS NOT DISTINCT FROM %s)
          ORDER BY created_at,id LIMIT 1""",
          (fact_key,gmail_message_id,classification,booking_id or None,bool(auto_booked),booking_status,failure_code or None))
        existing = _rows(cur)
        if existing:
            return existing[0]  # Never backfill/repoint a historical row.
        cur.execute(
            """INSERT INTO interview_auto_booking_audit(id,booking_id,source,gmail_message_id,
              gmail_thread_id,email_analysis_id,candidate_id,classification,auto_booked,
              validation_status,payment_validation_status,duplicate_check_status,
              conflict_check_status,booking_status,previous_booking,new_booking,failure_code,
              failure_message,correlation_id,created_at,updated_at,
              audit_fact_key,source_event_id,lifecycle_transition_key,source_snapshot)
              VALUES(%s,%s,'AI Mail Monitoring',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                     %s::jsonb,%s::jsonb,%s,%s,%s,now(),now(),%s,%s,%s,%s::jsonb)
              ON CONFLICT(audit_fact_key) DO NOTHING
              RETURNING *""",
            (audit_id, booking_id, gmail_message_id, gmail_thread_id, analysis_id,
             candidate_id, classification, auto_booked, validation_status, payment_status,
             duplicate_status, conflict_status, booking_status,
             json.dumps(previous_booking or {}, default=str), json.dumps(new_booking or {}, default=str),
             failure_code, str(failure_message or "")[:1000] or None, correlation_id,
             fact_key,source_event_id,lifecycle_transition_key,json.dumps(source_snapshot or {},default=str)),
        )
        inserted = _rows(cur)
        if inserted:
            return inserted[0]
        cur.execute('SELECT * FROM interview_auto_booking_audit WHERE audit_fact_key=%s', (fact_key,))
        return _rows(cur)[0]


def booking_audit_for_message(gmail_message_id: str, classification: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM interview_auto_booking_audit WHERE gmail_message_id=%s AND classification=%s ORDER BY auto_booked DESC,created_at DESC,id DESC LIMIT 1",
            (gmail_message_id, classification),
        )
        rows = _rows(cur)
    return rows[0] if rows else None


def notification_for_event(event_id: str) -> dict[str, Any] | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM mail_monitoring_notifications WHERE ai_recruitment_event_id=%s ORDER BY created_at DESC LIMIT 1",
            (event_id,),
        )
        rows = _rows(cur)
    return rows[0] if rows else None


def attach_booking_to_notification(
    notification_id: str, *, audit_id: str, booking_id: str | None,
    booking_status: str, result: dict[str, Any], priority: str | None = None,
    display_status: str | None = None, detail: str | None = None,
    schedule: dict[str, str] | None = None,
    block_reason: dict[str, str] | None = None,
) -> dict[str, Any]:
    interview = result.get("interview") or {}
    # A successful booking is always stored in the operational Asia/Kolkata
    # timezone. Notifications must display that same normalized schedule,
    # rather than the calendar provider's equivalent source-timezone value.
    display_date = (schedule or {}).get("date") or interview.get("date") or None
    display_time = (schedule or {}).get("time") or interview.get("time")
    display_timezone = "Asia/Kolkata" if schedule else interview.get("timezone")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE mail_monitoring_notifications SET notification_type='interview_booking',
              booking_id=%s,booking_audit_id=%s,booking_status=%s,interview_round=%s,
              interview_date=%s,interview_time=%s,interview_timezone=%s,interview_mode=%s,
              meeting_link=%s,priority=COALESCE(%s,priority),
              candidate_status=COALESCE(%s,candidate_status),
              recommended_action=COALESCE(%s,recommended_action),
              booking_block_reason_code=%s,booking_block_reason=%s,
              booking_failure_code=%s,updated_at=now()
              WHERE id=%s RETURNING *""",
            (booking_id, audit_id, booking_status, interview.get("round"),
             display_date, display_time, display_timezone,
             interview.get("mode"), interview.get("meeting_link"), priority,
             display_status, detail,
             # Written unconditionally, not COALESCEd: a booking that later
             # succeeds must clear the reason it was previously blocked for.
             (block_reason or {}).get("reason_code"),
             (block_reason or {}).get("reason"),
             (block_reason or {}).get("internal_code"),
             notification_id),
        )
        rows = _rows(cur)
    return rows[0] if rows else {}


def list_booking_audit(*, candidate_id: str | None = None, booking_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if candidate_id:
        from services.recruitment_identity import aliases, load_links
        where.append('(candidate_id=%s OR candidate_id=ANY(%s))')
        params.extend([candidate_id, sorted(aliases(candidate_id, load_links()))])
    if booking_id:
        where.append("booking_id=%s"); params.append(booking_id)
    clause = " WHERE " + " AND ".join(where) if where else ""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM interview_auto_booking_audit{clause} ORDER BY created_at DESC LIMIT %s",
            params + [max(1, min(limit, 200))],
        )
        return _rows(cur)
