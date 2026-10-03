"""Proof of life for the background loops.

Every loop started in main.py runs as an asyncio task. When one dies, or fails on
every tick, nothing on any screen changes: the interview-reminder loop logged its
failures at DEBUG for months, and nothing would have shown a dispatcher that had
stopped. Each loop now calls `beat()` once per pass, and the external monitor
(deploy/ops/teleautomation-monitor) reads the file this writes.

The file is written atomically and at most every WRITE_EVERY_SEC per process,
except when a loop's outcome changes (ok -> failing or back), which is written at
once so a failure is never hidden behind the throttle.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from core.config import DATA_DIR

FILE = os.path.join(DATA_DIR, "job_heartbeats.json")
WRITE_EVERY_SEC = 30.0

_lock = threading.Lock()
_state: dict[str, dict[str, Any]] = {}
_last_write = 0.0
_started_at = time.time()


def beat(name: str, *, interval_sec: float, ok: bool = True, error: str = "") -> None:
    """Record one pass of loop `name`, which normally repeats every `interval_sec`."""
    global _last_write
    now = time.time()
    with _lock:
        entry = _state.setdefault(name, {"consecutive_failures": 0, "last_ok_at": None, "last_error": ""})
        was_failing = entry["consecutive_failures"] > 0
        entry["interval_sec"] = float(interval_sec)
        entry["last_beat_at"] = now
        if ok:
            entry["consecutive_failures"] = 0
            entry["last_ok_at"] = now
        else:
            entry["consecutive_failures"] += 1
            entry["last_error"] = str(error)[:300]
            entry["last_error_at"] = now
        changed = was_failing != (entry["consecutive_failures"] > 0)
        if changed or now - _last_write >= WRITE_EVERY_SEC:
            _write_locked(now)
            _last_write = now


def _write_locked(now: float) -> None:
    payload = {"written_at": now, "process_started_at": _started_at, "pid": os.getpid(), "jobs": _state}
    try:
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        tmp = f"{FILE}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
        os.replace(tmp, FILE)
    except OSError:
        # A full disk is reported by the monitor's own disk check; a heartbeat
        # must never take a loop down with it.
        pass


def snapshot() -> dict[str, Any]:
    with _lock:
        return {"process_started_at": _started_at, "jobs": json.loads(json.dumps(_state))}


def _reset_for_tests() -> None:
    global _last_write, _started_at
    with _lock:
        _state.clear()
        _last_write = 0.0
        _started_at = time.time()
