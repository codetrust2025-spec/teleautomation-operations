#!/usr/bin/env python3
"""Off-server watchdog, run by GitHub Actions every 30 minutes in the private
teleautomation-ops-watch repository (source of truth: deploy/ops/watch in
teleautomation-operations).

1. probes the public /health from GitHub's network;
2. reads the production monitor's status through a command-restricted SSH key
   (it can run `status` and nothing else);
3. turns that into one GitHub issue per alert. GitHub emails the repository
   owner when an issue is opened (the body @-mentions them) and closed:
     - new alert           -> one issue
     - still failing       -> at most one reminder comment per 24 h
     - recovered           -> one "resolved" comment, issue closed
   so a problem produces two emails plus one a day while it lasts, never one per run.

It also alerts on what the server cannot report about itself: the host
unreachable, the public site down, or the monitor no longer updating.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

LABEL = "ops-alert"
TITLE_PREFIX = "[ops-alert] "
REMIND_AFTER_SEC = 24 * 3600
MONITOR_STALE_SEC = 20 * 60


def parse_time(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def watch_alerts(*, public_ok: bool, status: dict | None, now: float) -> list[dict[str, Any]]:
    """The full set of alerts that are active right now, keyed and de-duplicated."""
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    active: list[dict[str, Any]] = []
    if not public_ok:
        active.append({"key": "watch:public_down", "severity": "critical", "since": stamp,
                       "summary": "Production site is not answering /health from outside"})
    if status is None:
        active.append({"key": "watch:host_unreachable", "severity": "critical", "since": stamp,
                       "summary": "Production server cannot be reached over SSH from outside"})
        return active
    generated = parse_time(status.get("generated_at"))
    if generated is None or now - generated > MONITOR_STALE_SEC:
        age = "never" if generated is None else f"{int((now - generated) // 60)} min ago"
        active.append({"key": "watch:monitor_stale", "severity": "critical", "since": stamp,
                       "summary": f"Production monitor last reported {age}; its alerts may be out of date"})
    seen = {a["key"] for a in active}
    for alert in status.get("active_alerts") or []:
        if alert.get("key") and alert["key"] not in seen:
            seen.add(alert["key"])
            active.append({k: alert.get(k) for k in ("key", "severity", "summary", "since")})
    return active


def issue_key(issue: dict) -> str | None:
    title = issue.get("title") or ""
    if not title.startswith(TITLE_PREFIX):
        return None
    return title[len(TITLE_PREFIX):].split(" ", 1)[0]


def plan_actions(active: list[dict], open_issues: list[dict], *, now: float, mention: str) -> list[dict]:
    by_key = {issue_key(i): i for i in open_issues if issue_key(i)}
    actions: list[dict] = []
    for alert in active:
        issue = by_key.get(alert["key"])
        if issue is None:
            actions.append({
                "op": "create",
                "title": f"{TITLE_PREFIX}{alert['key']} — {alert['summary']}"[:250],
                "body": (f"{mention} **{alert['severity'].upper()}**: {alert['summary']}\n\n"
                         f"- check: `{alert['key']}`\n- failing since: {alert.get('since') or 'unknown'}\n\n"
                         "This issue closes itself when the check recovers."),
            })
            continue
        updated = parse_time(issue.get("updated_at")) or 0
        if now - updated >= REMIND_AFTER_SEC:
            actions.append({"op": "comment", "number": issue["number"],
                            "body": f"{mention} still failing: {alert['summary']}"})
    active_keys = {a["key"] for a in active}
    for key, issue in by_key.items():
        if key not in active_keys:
            actions.append({"op": "close", "number": issue["number"],
                            "body": "Resolved: the check has recovered."})
    return actions


# --- I/O ----------------------------------------------------------------------------------

def probe_public(url: str, attempts: int = 3) -> bool:
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        if i + 1 < attempts:
            time.sleep(20)
    return False


def read_status(ssh_target: str, attempts: int = 3) -> dict | None:
    for i in range(attempts):
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", ssh_target, "status"],
                           capture_output=True, timeout=90)
        if r.returncode == 0:
            try:
                return json.loads(r.stdout.decode() or "{}")
            except ValueError:
                return {}
        if i + 1 < attempts:
            time.sleep(20)
    return None


def gh(*args: str, payload: dict | None = None) -> Any:
    cmd = ["gh", "api", *args]
    r = subprocess.run(cmd + (["--input", "-"] if payload is not None else []),
                       input=json.dumps(payload).encode() if payload is not None else None,
                       capture_output=True, check=True, timeout=60)
    return json.loads(r.stdout.decode() or "null")


def execute(repo: str, actions: list[dict]) -> None:
    for a in actions:
        if a["op"] == "create":
            gh(f"repos/{repo}/issues", "--method", "POST", payload={"title": a["title"], "body": a["body"], "labels": [LABEL]})
        elif a["op"] == "comment":
            gh(f"repos/{repo}/issues/{a['number']}/comments", "--method", "POST", payload={"body": a["body"]})
        elif a["op"] == "close":
            gh(f"repos/{repo}/issues/{a['number']}/comments", "--method", "POST", payload={"body": a["body"]})
            gh(f"repos/{repo}/issues/{a['number']}", "--method", "PATCH", payload={"state": "closed"})


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    mention = "@" + os.environ.get("GITHUB_REPOSITORY_OWNER", "").strip()
    now = time.time()
    active = watch_alerts(public_ok=probe_public(os.environ["PUBLIC_HEALTH_URL"]),
                          status=read_status(os.environ.get("SSH_TARGET", "prod")), now=now)
    open_issues = gh(f"repos/{repo}/issues?state=open&labels={LABEL}&per_page=100")
    actions = plan_actions(active, open_issues, now=now, mention=mention)
    execute(repo, actions)
    print(json.dumps({"active": [a["key"] for a in active], "actions": [(a["op"], a.get("number") or a.get("title")) for a in actions]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
