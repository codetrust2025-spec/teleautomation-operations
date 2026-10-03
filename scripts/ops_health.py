"""Application health checks for the external monitor. Read-only.

Run inside the operations-api container:

    python -m scripts.ops_health

It prints one JSON document: {"generated_at": ..., "checks": [...]}. Each check is

    {"key", "ok", "severity": "critical"|"warning", "summary", "min_fail_minutes",
     "business_hours_only"}

`summary` never names a candidate or carries an address, a phone or a payment
reference: it travels to the alert channel. The host monitor
(deploy/ops/teleautomation-monitor) decides when a failing check becomes an
alert, de-duplicates it and announces recovery; this file only measures.

The evaluation functions are pure (inputs in, checks out) so tests can drive them
without a database, a Gmail account or an AI node.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

# How often each loop normally beats, how long after start-up it may stay
# silent, and the switch that disables it on purpose. A disabled loop is not
# "dead".
JOBS: dict[str, dict[str, Any]] = {
    "outbox_dispatcher": {"interval": 30, "startup": 60, "enabled": lambda env: True},
    "mail_worker": {"interval": 5, "startup": 60, "enabled": lambda env: True},
    "interview_reminders": {
        "interval": 300, "startup": 90,
        "enabled": lambda env: env.get("INTERVIEW_REMINDERS_ENABLED", "1").strip().lower() not in ("0", "false", "no"),
    },
    "gmail_reconnect": {
        "interval": 600, "startup": 120,
        "enabled": lambda env: env.get("GMAIL_RECONNECT_REMINDERS_ENABLED", "1").strip().lower() not in ("0", "false", "no"),
    },
    "gmail_watch_renewal": {
        "interval": 3600, "startup": 90,
        "enabled": lambda env: env.get("GMAIL_WATCH_RENEWAL_ENABLED", "1").strip().lower() not in ("0", "false", "no")
        and bool((env.get("GMAIL_PUBSUB_TOPIC") or "").strip()),
    },
}
FAILING_PASSES = 3          # this many failed passes in a row is an outage, not a blip
STALE_FACTOR = 3            # silent for 3 intervals (+ slack) means the loop is gone
STALE_SLACK_SEC = 120


def check(key: str, ok: bool, summary: str, *, severity: str = "critical",
          min_fail_minutes: int = 10, business_hours_only: bool = False) -> dict[str, Any]:
    return {"key": key, "ok": bool(ok), "severity": severity, "summary": summary,
            "min_fail_minutes": min_fail_minutes, "business_hours_only": business_hours_only}


# --- pure evaluations -------------------------------------------------------------

def evaluate_jobs(heartbeats: dict[str, Any] | None, *, now: float, env: dict[str, str],
                  process_started_at: float | None = None) -> list[dict[str, Any]]:
    """`process_started_at` is the service's real start, used when no heartbeat
    file exists yet: without it a service whose heartbeats never appear would be
    reported as "starting" for ever."""
    out = []
    heartbeats = heartbeats or {}
    started = float(heartbeats.get("process_started_at") or 0) or float(process_started_at or 0) or now
    uptime = now - started
    jobs = heartbeats.get("jobs") or {}
    for name, spec in JOBS.items():
        key = f"job:{name}"
        if not spec["enabled"](env):
            continue
        entry = jobs.get(name)
        if entry is None:
            if uptime < spec["startup"] + 2 * spec["interval"] + STALE_SLACK_SEC:
                out.append(check(key, True, f"{name} starting"))
            else:
                out.append(check(key, False, f"Background job {name} has not run since the service started"))
            continue
        age = now - float(entry.get("last_beat_at") or 0)
        limit = STALE_FACTOR * float(entry.get("interval_sec") or spec["interval"]) + STALE_SLACK_SEC
        failures = int(entry.get("consecutive_failures") or 0)
        if age > limit:
            out.append(check(key, False, f"Background job {name} stopped: no pass for {int(age // 60)} min"))
        elif failures >= FAILING_PASSES:
            out.append(check(key, False, f"Background job {name} failing: {failures} passes in a row"))
        else:
            out.append(check(key, True, f"{name} ok"))
    return out


def evaluate_outbox(rows: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    def when(value):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None

    stuck = [r for r in rows if r.get("status") == "pending"
             and (when(r.get("created_at")) or now) < now - timedelta(minutes=30)]
    recent_dead = [r for r in rows if r.get("status") == "dead"
                   and (when(r.get("created_at")) or now) > now - timedelta(days=2)]
    return [
        check("outbox:stuck", not stuck,
              f"{len(stuck)} notification(s) undelivered for over 30 min" if stuck else "outbox flowing"),
        check("outbox:dead", not recent_dead,
              f"{len(recent_dead)} notification(s) permanently failed in the last 2 days" if recent_dead else "no failed notifications",
              severity="warning", min_fail_minutes=0),
    ]


def evaluate_mailboxes(rows: list[dict[str, Any]], *, now: datetime,
                       needs_reconnect: Callable[[dict], bool]) -> list[dict[str, Any]]:
    active = [r for r in rows if not r.get("monitoring_excluded")]
    broken = [r for r in active if needs_reconnect(r)]
    stale = []
    for r in active:
        if r in broken or str(r.get("connection_status") or "").upper() != "CONNECTED":
            continue
        last = r.get("last_successful_sync_at")
        if isinstance(last, str):
            try:
                last = datetime.fromisoformat(last.replace("Z", "+00:00"))
            except ValueError:
                last = None
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is None or last < now - timedelta(hours=6):
            stale.append(r)
    return [
        check("gmail:disconnected", not broken,
              f"{len(broken)} of {len(active)} Gmail accounts need reconnecting; their mail is not being monitored"
              if broken else f"all {len(active)} Gmail accounts connected",
              severity="critical" if active and len(broken) == len(active) else "warning", min_fail_minutes=0),
        check("gmail:sync_stale", not stale,
              f"{len(stale)} connected Gmail account(s) have not synced for over 6 hours" if stale else "Gmail sync current",
              severity="warning", min_fail_minutes=30),
    ]


def evaluate_ai(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Payment screenshots and interview invites are read by the vision model."""
    ready = [n for n in nodes if n.get("ready")]
    return [check(
        "ai:vision", bool(ready),
        f"{len(ready)} of {len(nodes)} AI node(s) can read payment screenshots" if ready
        else f"No AI node can read payment screenshots or invites ({len(nodes)} configured, all offline or missing the model)",
        severity="critical", min_fail_minutes=60, business_hours_only=True,
    )]


# --- gathering (the only part that touches anything) ------------------------------

def _guarded(key: str, fn: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - one broken probe must not hide the others
        return [check(key, False, f"Health probe {key} could not run: {type(exc).__name__}")]


def gather() -> dict[str, Any]:
    from core.config import DATA_DIR

    now_ts = time.time()
    now = datetime.now(timezone.utc)
    env = dict(os.environ)
    checks: list[dict[str, Any]] = []

    def jobs():
        path = os.path.join(DATA_DIR, "job_heartbeats.json")
        data = json.load(open(path, encoding="utf-8")) if os.path.isfile(path) else {}
        try:
            # PID 1 of the container is the service; its /proc entry dates its start.
            started = os.stat("/proc/1").st_ctime
        except OSError:
            started = None
        if data and float(data.get("process_started_at") or 0) < float(started or 0) - 5:
            # A file left by the previous container: its loops are not this one's.
            data = {}
        return evaluate_jobs(data, now=now_ts, env=env, process_started_at=started)

    def candidates():
        from features import candidate_store as cs

        rows = cs._load(force=True).get("candidates") or []
        return [check("data:candidates", bool(rows), f"candidate store readable ({len(rows)} rows)" if rows
                      else "Candidate store returned no rows")]

    def ledger():
        path = os.path.join(DATA_DIR, "payment_verification_ledger.json")
        data = json.load(open(path, encoding="utf-8"))
        ok = isinstance(data, dict) and "payments" in data
        return [check("data:payment_ledger", ok, "payment ledger readable" if ok else "Payment ledger is not in the expected shape")]

    def outbox():
        from services import cross_project_outbox

        return evaluate_outbox(cross_project_outbox._load(), now=now)

    def mailboxes():
        from core import recruitment_mail_store as store
        from services.gmail_reconnect import needs_reconnect

        return evaluate_mailboxes(store.mailbox_health_rows(), now=now, needs_reconnect=needs_reconnect)

    def ai():
        from core import ollama_nodes
        from core.ai_gateway import configured_models

        vision = configured_models()["vision"]
        nodes = []
        for item in ollama_nodes.configured_nodes():
            status = ollama_nodes.node_health(item["id"], model=vision, timeout=5, deep=False)
            nodes.append({"id": item["id"], "ready": bool(status.get("endpoint_reachable")) and bool(status.get("model_available"))})
        return evaluate_ai(nodes)

    for key, fn in (("jobs", jobs), ("data:candidates", candidates), ("data:payment_ledger", ledger),
                    ("outbox", outbox), ("gmail", mailboxes), ("ai:vision", ai)):
        checks.extend(_guarded(key, fn))
    return {"generated_at": now.isoformat(), "checks": checks}


if __name__ == "__main__":
    json.dump(gather(), sys.stdout)
    sys.stdout.write("\n")
