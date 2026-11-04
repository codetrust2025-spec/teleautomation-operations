"""Five-minute Operations -> Daybreak account-list and credential reconciliation.

The account list mirrors Data Room names and email addresses; it never
grants consent or starts Naukri jobs. Credential delivery retains its existing
consent, worker-key and signed-envelope protections.
"""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
from datetime import datetime, timezone

from core.config import DATA_DIR
from core.job_heartbeats import beat

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SEC = 300
STARTUP_DELAY_SEC = 90
MAX_PER_PASS = 10
REDELIVERABLE = frozenset({"changed_since_delivery", "delivery_expired", "delivered_to_other_profile"})
_task: asyncio.Task | None = None


def enabled() -> bool:
    """Opt-in at release time; ordinary deployments must not silently start delivery."""
    return os.getenv("DAYBREAK_CREDENTIAL_AUTO_SYNC_ENABLED", "0").strip().lower() in {"1", "true", "yes"}


def plan(view: dict) -> tuple[list[str], str | None]:
    """Select accounts needing a first or refreshed delivery, without changing anything.

    A delivery awaiting a worker receipt is NOT retried. A failed Naukri login,
    missing consent, duplicate/missing candidate or disabled account needs a
    person, never automatic creation or repeated attempts.
    """
    recipients = [w for w in view.get("workers", [])
                  if w.get("enabled") and w.get("recipient") is not False]
    if not recipients:
        return [], "no_enrolled_recipient"
    if any(not w.get("approved") or w.get("key_problem") for w in recipients):
        return [], "worker_key_not_approved"

    chosen: list[str] = []
    for row in view.get("accounts", []):
        profile = row.get("profile") or {}
        if not (row.get("synced") and profile.get("enabled")):
            continue
        codes = set(row.get("attention_codes") or [])
        if not codes and not row.get("delivery"):
            chosen.append(str(row["account_id"]))
        elif codes and codes <= REDELIVERABLE and row.get("delivery"):
            chosen.append(str(row["account_id"]))
    return chosen[:MAX_PER_PASS], None


def sync_once() -> dict:
    """One bounded pass, serialized across local API worker processes.

    Daybreak validates worker enrollment, matching identity, consent and the
    approved encryption fingerprints again before accepting each envelope.
    """
    from features import daybreak_credential_sync as sync

    os.makedirs(DATA_DIR, exist_ok=True)
    fd = os.open(os.path.join(DATA_DIR, "daybreak_credential_auto_sync.lock"),
                 os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "already_running", "delivered": 0}

        # Send a complete, signed, metadata-only snapshot. The Daybreak
        # endpoint preserves consent, sessions and existing job history.
        source = sync.authoritative_naukri_accounts()
        roster = [{"id": str(row.get("id") or ""),
                   "label": str(row.get("label") or ""),
                   "email": sync.normalise_email(row.get("username")),
                   "enabled": row.get("enabled") is not False,
                   "consentVerified": sync.has_attested_daybreak_consent(row)}
                  for row in source]
        mirror = sync._daybreak("roster_sync",
                                {"accounts": roster, "sourceCount": len(roster)})
        if (mirror.get("status") != "ok"
                or mirror.get("sourceCount") != len(roster)
                or mirror.get("matchedCount") != len(roster)):
            raise sync.SyncError(409, "Daybreak account list did not reconcile")
        sync.refresh(force=True)
        view = sync.status_view()
        selected, blocked = plan(view)
        unmatched = sum(1 for r in view.get("accounts", [])
                        if "missing_in_daybreak" in (r.get("attention_codes") or []))
        if blocked:
            return {"status": blocked, "delivered": 0,
                    "total": len(view.get("accounts", [])), "unmatched": unmatched}
        if not selected:
            return {"status": "up_to_date", "delivered": 0,
                    "total": len(view.get("accounts", [])), "unmatched": unmatched}
        result = sync.deliver(selected, actor="automatic-data-room-sync")
        return {"status": "delivered" if result.get("delivered") else "needs_attention",
                "delivered": len(result.get("delivered") or []),
                "refused": len(result.get("refused") or []),
                "total": len(view.get("accounts", [])), "unmatched": unmatched}
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


async def _loop() -> None:
    await asyncio.sleep(STARTUP_DELAY_SEC)
    while True:
        try:
            result = await asyncio.to_thread(sync_once)
            # Counts only: no emails, usernames, passwords or sealed envelopes.
            logger.info("Daybreak account and credential sync: status=%s source=%s mismatches=%s delivered=%s",
                        result["status"], result.get("total", 0),
                        result.get("unmatched", 0), result.get("delivered", 0))
            beat("daybreak_credential_sync", interval_sec=CHECK_INTERVAL_SEC)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Daybreak credential sync pass failed; next interval will retry")
            beat("daybreak_credential_sync", interval_sec=CHECK_INTERVAL_SEC, ok=False,
                 error="automatic credential sync failed")
        await asyncio.sleep(CHECK_INTERVAL_SEC)


def start_daybreak_credential_sync_loop() -> None:
    global _task
    if enabled() and (_task is None or _task.done()):
        _task = asyncio.create_task(_loop(), name="daybreak-credential-sync")


async def stop_daybreak_credential_sync_loop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
