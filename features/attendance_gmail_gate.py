"""Attended on a Profile-wise L2, HR or Final round needs an actively monitored Gmail.

Owner, 26 Nov 2026: a Profile-wise candidate's later rounds are followed through
their Gmail in AI Mail Review, so such a round may be marked Attended only while
at least one Gmail of that candidate shows "Monitoring Active". One such Gmail is
enough: another of the candidate's Gmail accounts needing reconnection, being
paused or expired does not block. Nothing else is gated: Round-wise bookings,
every other round, and every other attendance outcome are unchanged.

"Monitoring Active" is the Mail tab's own badge (mailboxUiStatus in
dashboard/src/utils/mailboxStatus.js): connected, monitoring switched on, not
needing reconnection, and not ended because the candidate left the pipeline. A
sync that happens to be queued or running does not make a healthy Gmail any less
monitored, so it is not a reason to block.

Matching is by Candidate ID only: the booking's id, plus the ids explicitly linked
to it (identity links, as the booking safety gates resolve them). Never by name.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

GATED_ROUNDS = frozenset({"L2", "HR", "Final"})
GMAIL_MONITORING_REQUIRED = "gmail_monitoring_required"
GMAIL_MONITORING_REQUIRED_MESSAGE = (
    "Attended needs at least one Gmail with Monitoring Active in AI Mail Review "
    "for this candidate. Connect the candidate's Gmail, then mark Attended."
)
GMAIL_MONITORING_UNKNOWN_MESSAGE = (
    "Gmail monitoring could not be checked right now, so Attended was not saved. Try again."
)


def requires_monitored_gmail(row: dict | None, *, status: str) -> bool:
    """Whether marking this booking `status` needs an actively monitored Gmail."""
    from features import candidate_store

    if status != "attended" or not row:
        return False
    if candidate_store._normalise_service_type(row.get("service_type"), row) != "profile_service":
        return False
    return candidate_store.normalise_interview_round(row.get("interview_round")) in GATED_ROUNDS


def mailbox_monitoring_active(mailbox: dict[str, Any] | None) -> bool:
    """The "Monitoring Active" badge, as the Mail tab decides it."""
    from services.gmail_reconnect import needs_reconnect

    if not isinstance(mailbox, dict) or mailbox.get("monitoring_excluded"):
        return False
    if str(mailbox.get("connection_status") or "").upper() != "CONNECTED":
        return False
    if needs_reconnect(mailbox):
        return False
    return bool(mailbox.get("monitoring_enabled"))


def candidate_ids_for(cid: str) -> set[str]:
    """This booking's Candidate ID and the ids explicitly linked to it (no name matching)."""
    from features import candidate_store

    return {str(value) for value in candidate_store.candidate_identity_ids(cid, include_name_matches=False) if value} | {str(cid)}


def _mailbox_rows() -> list[dict[str, Any]]:
    from core import recruitment_mail_store

    return recruitment_mail_store.mailbox_health_rows()


def monitored_gmail_for(
    cid: str,
    *,
    mailboxes: Iterable[dict[str, Any]] | None = None,
    ids_for: Callable[[str], set[str]] = candidate_ids_for,
) -> list[dict[str, Any]]:
    """The candidate's Gmail accounts that show Monitoring Active."""
    ids = ids_for(cid)
    rows = list(_mailbox_rows() if mailboxes is None else mailboxes)
    return [
        mailbox for mailbox in rows
        if mailbox_monitoring_active(mailbox)
        and ({str(mailbox.get("candidate_id") or ""), str(mailbox.get("canonical_candidate_id") or "")} & ids)
    ]


def attended_block(row: dict | None, cid: str, *, status: str, **lookup) -> dict | None:
    """None when this attendance may be saved; otherwise the error the API returns."""
    if not requires_monitored_gmail(row, status=status):
        return None
    try:
        if monitored_gmail_for(cid, **lookup):
            return None
    except Exception:
        return {"status": "error", "code": GMAIL_MONITORING_REQUIRED, "message": GMAIL_MONITORING_UNKNOWN_MESSAGE}
    return {"status": "error", "code": GMAIL_MONITORING_REQUIRED, "message": GMAIL_MONITORING_REQUIRED_MESSAGE}


def gmail_monitoring_summary(row: dict | None, cid: str, **lookup) -> dict:
    """What the Daily Ops edit dialog needs before offering Save for Attended."""
    # `active` is reported whatever the stored round, because the dialog may set
    # the round in the same edit; the server re-checks the saved round anyway.
    required = requires_monitored_gmail(row, status="attended")
    active = monitored_gmail_for(cid, **lookup)
    return {
        "required": required,
        "active": bool(active),
        "monitored": [str(mailbox.get("email_address") or "") for mailbox in active],
    }
