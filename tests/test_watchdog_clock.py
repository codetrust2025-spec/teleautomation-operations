"""The off-server watchdog runs every 30 minutes, not when GitHub gets round to it (11 Oct).

Its cron ("every 30 minutes") ran 3-6 times a day in the private watch
repository, so alerts reached the owner hours late. A clock in this public
repository now waits 30 minutes on an environment wait timer (no runner held,
no minutes billed), starts the watchdog by pushing its `tick` branch with a
deploy key scoped to that one repository, and dispatches its own next run.
"""
from __future__ import annotations

import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOCK = os.path.join(ROOT, ".github", "workflows", "watchdog-clock.yml")
WATCH = os.path.join(ROOT, "deploy", "ops", "watch", ".github", "workflows", "watchdog.yml")
GITHUB_ED25519 = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"


def load(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    data = yaml.safe_load(text)
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 reads the key `on` as True
    return data, text


def test_the_clock_waits_on_the_environment_and_keeps_itself_going():
    data, text = load(CLOCK)
    assert set(data["on"]) == {"workflow_dispatch", "schedule"}
    assert data["permissions"] == {"actions": "write", "contents": "read"}
    assert data["concurrency"] == {"group": "watchdog-clock", "cancel-in-progress": False}
    job = data["jobs"]["tick"]
    assert job["environment"] == "watchdog-clock"
    steps = {step["name"]: step for step in job["steps"]}
    nxt = steps["Schedule the next tick"]
    assert nxt["if"] == "always()" and "gh workflow run watchdog-clock.yml" in nxt["run"]
    assert steps["Remove the key"]["if"] == "always()"


def test_the_clock_starts_the_watchdog_through_its_tick_branch_only():
    data, text = load(CLOCK)
    start = next(s for s in data["jobs"]["tick"]["steps"] if s["name"] == "Start the private watchdog")
    assert start["env"] == {"TICK_KEY": "${{ secrets.WATCHDOG_TICK_DEPLOY_KEY }}"}
    assert "git@github.com:codetrust2025-spec/teleautomation-ops-watch.git" in start["run"]
    assert "git push -q -f origin tick" in start["run"]
    assert "git push -q -f origin main" not in start["run"]
    assert GITHUB_ED25519 in start["run"] and "StrictHostKeyChecking=yes" in start["run"]
    assert "ssh-keyscan" not in text


def test_the_watchdog_runs_on_each_tick_and_keeps_a_fallback_schedule():
    data, _ = load(WATCH)
    assert data["on"]["push"] == {"branches": ["tick"]}
    assert data["on"]["schedule"] == [{"cron": "7 */3 * * *"}]
    assert "workflow_dispatch" in data["on"]
    assert data["permissions"] == {"issues": "write", "contents": "read"}
