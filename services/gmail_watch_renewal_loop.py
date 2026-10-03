"""Background renewal of Gmail push watches.

Gmail expires a watch seven days after registration.  Every call site that
registered one was a request handler - connecting a mailbox, updating it, or an
operator pressing renew - so in a steady state nothing re-registered them and
push delivery stopped a week after each connect.

That failure is silent by design: `core.gmail_watch` keeps the polling fallback
running, so mail still arrives and no alarm fires.  This loop is what stops the
system from quietly degrading to polling-only.
"""

from __future__ import annotations

import asyncio

from core.job_heartbeats import beat
import logging
import os

from core.gmail_watch import configured_topic, renew_due_watches

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None

# A watch lives for seven days and is renewed 24h early, so an hourly tick gets
# roughly 24 attempts per mailbox before the deadline.  Cheap: the query returns
# nothing at all until a watch is actually close to lapsing.
CHECK_INTERVAL_SEC = 3600
STARTUP_DELAY_SEC = 60.0


def _disabled() -> bool:
    return os.environ.get("GMAIL_WATCH_RENEWAL_ENABLED", "1").strip().lower() in (
        "0",
        "false",
        "no",
    )


async def run_renewal_tick() -> dict[str, int]:
    """One renewal pass.  Separated from the loop so tests can call it directly."""
    return await asyncio.to_thread(renew_due_watches)


async def gmail_watch_renewal_loop() -> None:
    # Let migrations, schema checks and the mail worker settle before touching
    # the database or Google.
    await asyncio.sleep(STARTUP_DELAY_SEC)
    while True:
        try:
            result = await run_renewal_tick()
            if result.get("renewed") or result.get("failed"):
                logger.info(
                    "Gmail watch renewal: due=%d renewed=%d failed=%d",
                    result.get("due", 0),
                    result.get("renewed", 0),
                    result.get("failed", 0),
                )
            beat("gmail_watch_renewal", interval_sec=CHECK_INTERVAL_SEC)
        except Exception as exc:
            # Never let a bad tick kill the loop - the next one may succeed.
            logger.exception("Gmail watch renewal tick failed")
            beat("gmail_watch_renewal", interval_sec=CHECK_INTERVAL_SEC, ok=False, error=repr(exc))
        await asyncio.sleep(CHECK_INTERVAL_SEC)


def start_gmail_watch_renewal_loop() -> None:
    global _task
    if _disabled():
        return
    if not configured_topic():
        # No topic means push was never wired up; renewing nothing, quietly.
        logger.info("Gmail watch renewal disabled: GMAIL_PUBSUB_TOPIC is not set")
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(gmail_watch_renewal_loop())


async def stop_gmail_watch_renewal_loop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
