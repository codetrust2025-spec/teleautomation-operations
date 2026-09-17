"""Checking out of a working day, and what has to be true first.

Marking attendance says a person arrived. Nothing said they left, so the end of
a day was only ever inferred from the last request a session made -- which says
when a browser tab was closed, not when the work was finished.

Check-out records the end explicitly, and refuses while the day is visibly
unfinished: an interview still to run, an interview that ran and whose outcome
nobody wrote down, or an operator to-do still open. Each refusal names what is
in the way, because a button that says only "not yet" is a button people learn
to ignore.

The checks read the same functions the Daily Ops screens read, so the checklist
cannot disagree with the roster it is derived from.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time
from typing import Any

from core.db.connection import get_connection
from core.office_network import NetworkVerification
from features import attendance_eligibility as attendance

#: Check-out opens in the evening. Before it, the day is simply not over: an
#: early check-out would be a worse record than none, because payroll and the
#: admin overview both read it as the end of the working day.
CHECKOUT_START = time(18, 30)

#: The same moment, written the way the refusal says it. `%-I` is not
#: portable, and a check-out window is not worth a platform branch.
CHECKOUT_START_LABEL = "6:30 PM"

#: Reason codes. The UI renders the text; these are what tests and audit rows
#: pin, so they are part of the interface and are not reworded lightly.
BEFORE_WINDOW = "BEFORE_CHECKOUT_WINDOW"
ATTENDANCE_NOT_MARKED = "ATTENDANCE_NOT_MARKED"
OFFICE_NETWORK_REQUIRED = "OFFICE_NETWORK_REQUIRED"
INTERVIEW_NOT_FINISHED = "INTERVIEW_NOT_FINISHED"
INTERVIEW_OUTCOME_PENDING = "INTERVIEW_OUTCOME_PENDING"
DAILY_TASKS_PENDING = "DAILY_TASKS_PENDING"


class CheckoutBlocked(RuntimeError):
    """Raised with every blocker, not the first one found."""

    def __init__(self, blockers: list[dict[str, Any]]) -> None:
        super().__init__("; ".join(b["summary"] for b in blockers) or "not yet")
        self.blockers = blockers


def _blocker(kind: str, summary: str, *, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = items or []
    return {"kind": kind, "summary": summary, "count": len(rows), "items": rows}


def _interview_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": str(row.get("id") or ""),
        "name": str(row.get("name") or "").strip(),
        "company": str(row.get("company") or "").strip(),
        "time": str(row.get("time") or "").strip(),
        "time_end": str(row.get("time_end") or "").strip(),
        "round": str(row.get("interview_round") or "").strip(),
        "booking_type": str(row.get("booking_type") or "Interview").strip(),
    }


def _task_item(work: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": str(work.get("kind") or ""),
        "label": str(work.get("label") or work.get("kind") or ""),
        "candidate_id": str(work.get("candidate_id") or ""),
        "name": str(work.get("candidate_name") or "").strip(),
    }


def interview_blockers(today: date) -> list[dict[str, Any]]:
    """Today's interviews that are not finished, and today's that are finished
    but unexplained.

    Both come from `_split_pending_interviews_by_slot_phase`, which is what the
    Upcoming and Awaiting-status tabs are built from. Reusing it is the point:
    a check-out gate that decided "finished" differently from the roster would
    be a second opinion nobody asked for.
    """
    from features import candidate_store

    stamp = today.isoformat()
    rows = candidate_store._interview_rows_for_range(stamp, stamp)
    scheduled, awaiting = candidate_store._split_pending_interviews_by_slot_phase(rows)
    blockers: list[dict[str, Any]] = []
    if scheduled:
        blockers.append(_blocker(
            INTERVIEW_NOT_FINISHED,
            "An interview today has not finished yet.",
            items=[_interview_item(row) for row in scheduled],
        ))
    if awaiting:
        blockers.append(_blocker(
            INTERVIEW_OUTCOME_PENDING,
            "An interview has ended without its outcome being recorded.",
            items=[_interview_item(row) for row in awaiting],
        ))
    return blockers


def task_blockers() -> list[dict[str, Any]]:
    """Operator to-dos the pipeline has detected and nobody has cleared."""
    from features import candidate_store

    pending = candidate_store.pending_works()
    works = list(pending.get("works") or [])
    if not works:
        return []
    return [_blocker(
        DAILY_TASKS_PENDING,
        "Operations tasks are still open.",
        items=[_task_item(work) for work in works],
    )]


def checkout_blockers(
    *,
    now: datetime,
    attendance_row: dict[str, Any] | None,
    network: NetworkVerification | None,
) -> list[dict[str, Any]]:
    """Everything standing between this account and the end of its day.

    Ordered the way a person would work through it: the clock, then the day's
    own record, then the network, then the work itself. Every blocker is
    returned, not just the first -- an operator deciding whether they can leave
    needs the whole list, not one item at a time.
    """
    blockers: list[dict[str, Any]] = []
    if now.time() < CHECKOUT_START:
        blockers.append(_blocker(
            BEFORE_WINDOW,
            f"Check-out opens at {CHECKOUT_START_LABEL} IST.",
        ))
    if not attendance_row:
        blockers.append(_blocker(
            ATTENDANCE_NOT_MARKED,
            "Attendance for today has not been marked.",
        ))
    if network is not None and not network.allowed:
        seen = str(getattr(network, "observed_ip", "") or "")
        blockers.append(_blocker(
            OFFICE_NETWORK_REQUIRED,
            "Connect to Office Wi-Fi to check out."
            + (f" This device reaches us from {seen}, which is not an approved office network."
               if seen else ""),
        ))
    blockers.extend(interview_blockers(now.date()))
    blockers.extend(task_blockers())
    return blockers


def _checkout_row(conn, account_id: str, on_date: date) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, account_id, username, display_name, attendance_date,
                      marked_at, checked_out_at
               FROM operations_attendance_records
               WHERE account_id=%s AND attendance_date=%s""",
            (account_id, on_date),
        )
        return attendance._as_dict(cur, cur.fetchone())


def _identity_or_refuse(profile: dict[str, Any]) -> dict[str, str]:
    identity = attendance.account_identity(profile)
    if not attendance.is_employee_identity(identity):
        raise PermissionError("Check-out is only available to handler accounts.")
    return identity


def status(
    profile: dict[str, Any],
    network: NetworkVerification | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """What check-out would do right now, without doing it.

    The panel polls this, so it never performs the write: a page left open in
    the evening must not check someone out because it happened to refresh.
    """
    attendance._require_database()
    current = attendance._now(now)
    identity = _identity_or_refuse(profile)
    with get_connection() as conn:
        row = _checkout_row(conn, identity["account_id"], current.date())
    checked_out_at = row.get("checked_out_at") if row else None
    blockers = [] if checked_out_at else checkout_blockers(
        now=current, attendance_row=row, network=network)
    return attendance._json_safe({
        "status": "ok",
        "checkout_date": current.date(),
        "window_opens_at": CHECKOUT_START_LABEL,
        "checked_out": checked_out_at is not None,
        "checked_out_at": checked_out_at,
        "attendance_marked": bool(row),
        "can_check_out": checked_out_at is None and not blockers,
        "blockers": blockers,
    })


def check_out(
    profile: dict[str, Any],
    network: NetworkVerification,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record the end of this account's working day, once.

    `COALESCE` is what makes a second press harmless: the first check-out time
    is the one that stays, so a double click, a retried request or a second tab
    all report the same moment rather than moving it later.
    """
    attendance._require_database()
    current = attendance._now(now)
    identity = _identity_or_refuse(profile)
    with get_connection() as conn:
        row = _checkout_row(conn, identity["account_id"], current.date())
        if row and row.get("checked_out_at"):
            return attendance._json_safe({"status": "already_checked_out", "checkout": row})
        blockers = checkout_blockers(now=current, attendance_row=row, network=network)
        if blockers:
            # The caller renders these; raising the first one alone would send
            # an operator back and forth for each in turn.
            raise CheckoutBlocked(blockers)
        audit = {
            "source": "authenticated_operations_session",
            "business_timezone": attendance.APP_TIMEZONE,
            "session_id_hash": str(profile.get("session_id_hash") or ""),
            "checkout_id": str(uuid.uuid4()),
        }
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE operations_attendance_records
                      SET checked_out_at = COALESCE(checked_out_at, %s),
                          checkout_network_verification =
                            COALESCE(checkout_network_verification, %s::jsonb),
                          checkout_metadata = COALESCE(checkout_metadata, %s::jsonb)
                    WHERE account_id=%s AND attendance_date=%s
                RETURNING id, account_id, username, display_name, attendance_date,
                          marked_at, checked_out_at""",
                (current, json.dumps(network.audit_payload()), json.dumps(audit),
                 identity["account_id"], current.date()),
            )
            updated = attendance._as_dict(cur, cur.fetchone())
    if updated is None:  # the attendance row vanished between the two reads
        raise CheckoutBlocked([_blocker(
            ATTENDANCE_NOT_MARKED, "Attendance for today has not been marked.")])
    first = updated.get("checked_out_at")
    settled = "checked_out" if first == current else "already_checked_out"
    return attendance._json_safe({"status": settled, "checkout": updated})
