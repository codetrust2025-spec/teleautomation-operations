"""T-30min web push reminders for confirmed interview slots."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timedelta

from core.config import DATA_DIR
from core.job_heartbeats import beat
from core.ist_time import IST, ist_now

logger = logging.getLogger(__name__)

_SENT_PATH = os.path.join(DATA_DIR, "interview_reminders_sent.json")
_lock = threading.Lock()
_task: asyncio.Task | None = None
REMINDER_MINUTES = 30
CHECK_INTERVAL_SEC = 300
MAX_SENT_KEYS = 500


def _load_sent() -> set[str]:
    if not os.path.isfile(_SENT_PATH):
        return set()
    try:
        with open(_SENT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(str(x) for x in data)
    except Exception:
        pass
    return set()


def _chronological(key: str) -> tuple[str, str]:
    """Sort key for "<id>:<YYYY-MM-DD>:<HH:MM>": by slot, oldest first.

    The list used to be trimmed with a plain sort, which orders by candidate id.
    Once it passed the cap that kept the keys with the highest ids and dropped
    the rest, including a reminder written a moment earlier, which then fired
    again on the next tick while the slot was still inside its window."""
    parts = key.split(":", 2)
    return (parts[1], parts[2]) if len(parts) == 3 else ("", key)


def _save_sent(keys: set[str]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    trimmed = sorted(keys, key=_chronological)[-MAX_SENT_KEYS:]
    tmp = _SENT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, indent=2)
    os.replace(tmp, _SENT_PATH)


def _slot_start_dt(row: dict) -> datetime | None:
    day = (row.get("date") or "").strip()[:10]
    start = (row.get("time") or "").strip()[:5]
    if len(day) != 10 or len(start) < 4:
        return None
    try:
        y, m, d = int(day[:4]), int(day[5:7]), int(day[8:10])
        sh, sm = map(int, start.split(":")[:2])
    except ValueError:
        return None
    return datetime(y, m, d, sh, sm, tzinfo=IST)


def _reminder_key(row: dict) -> str:
    cid = str(row.get("id") or "")
    day = (row.get("date") or "")[:10]
    start = (row.get("time") or "").strip()[:5]
    return f"{cid}:{day}:{start}"


async def run_reminder_tick(*, minutes: int = REMINDER_MINUTES) -> int:
    from features import candidate_store as cs
    from services.messaging_client import send_notification
    from services.slot_booking_notify import format_slot_reminder

    now = ist_now()
    window_start = now + timedelta(minutes=minutes - 5)
    window_end = now + timedelta(minutes=minutes + 5)
    snap = cs.interview_upcoming(days=2, phase="scheduled")
    rows = snap.get("interviews") or []
    sent = _load_sent()
    fired = 0
    for row in rows:
        start_dt = _slot_start_dt(row)
        if start_dt is None:
            continue
        if not (window_start <= start_dt <= window_end):
            continue
        key = _reminder_key(row)
        if key in sent:
            continue
        title, body = format_slot_reminder(row, minutes=minutes)
        tag = f"interview-reminder:{key}"
        await send_notification(title=title, body=body, tag=tag)
        sent.add(key)
        fired += 1
    if fired:
        with _lock:
            _save_sent(sent)
    return fired


async def interview_reminder_loop() -> None:
    await asyncio.sleep(45.0)
    while True:
        try:
            n = await run_reminder_tick()
            if n:
                logger.info("Interview reminders sent: %d", n)
            beat("interview_reminders", interval_sec=CHECK_INTERVAL_SEC)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            beat("interview_reminders", interval_sec=CHECK_INTERVAL_SEC, ok=False, error=repr(exc))
            # Was logger.debug: a tick that fails every time (a store error, the
            # outbox unavailable) left no trace and every reminder silently stopped.
            logger.exception("Interview reminder tick failed; will retry in %ss", CHECK_INTERVAL_SEC)
        await asyncio.sleep(CHECK_INTERVAL_SEC)


def start_interview_reminder_loop() -> None:
    global _task
    if os.environ.get("INTERVIEW_REMINDERS_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(interview_reminder_loop())


async def stop_interview_reminder_loop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
