"""Backups, the off-server copy and alerting (deploy/ops, scripts/ops_health.py).

These pin the parts whose failure would be silent:
  * a backup is only "good" once a restore of it re-hashes identically and every
    table comes back with production's row count;
  * the RTX laptop's mirror fetches what is new, verifies by content hash, and a
    damaged or emptied server can never make it delete its copy;
  * an alert is raised once, reminded at most daily, closed on recovery, and not
    raised for a single blip or (for laptop-dependent AI) outside working hours;
  * a background loop that dies or keeps failing is noticed.
"""
import hashlib
import importlib.util
import io
import json
import os
import time
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = os.path.join(ROOT, "deploy", "ops")


def load(name, path):
    loader = SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


verify = load("ta_backup_verify", os.path.join(OPS, "ta_backup_verify.py"))
offhost = load("ta_offhost", os.path.join(OPS, "ta_offhost.py"))
monitor = load("ta_monitor", os.path.join(OPS, "teleautomation-monitor"))
watchdog = load("ta_watchdog", os.path.join(OPS, "watch", "watchdog.py"))
NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc).timestamp()   # 11:30 IST


# --- backup verification ------------------------------------------------------------------

def stage(tmp_path):
    s = tmp_path / "stage"
    (s / "db").mkdir(parents=True)
    (s / "operations-data" / "candidates_proofs" / "c1").mkdir(parents=True)
    (s / "db" / "operations.sql").write_text("CREATE TABLE t();")
    (s / "db" / "marketing.sql").write_text("-- empty")
    (s / "db" / "operations.counts.tsv").write_text("candidates_store\t257\nmail_realtime_events\t223490\n")
    (s / "db" / "marketing.counts.tsv").write_text("leads\t3\n")
    (s / "operations-data" / "payment_verification_ledger.json").write_text("{}")
    (s / "operations-data" / "candidates_proofs" / "c1" / "p.jpg").write_bytes(b"\xff\xd8image")
    (s / "host-config.tar").write_bytes(b"tar")
    (s / "manifest.json").write_text(json.dumps(verify.build_manifest(str(s))))
    return s


def test_a_faithful_restore_verifies(tmp_path):
    assert verify.verify_files(str(stage(tmp_path))) == []


def test_a_corrupted_proof_after_restore_is_caught(tmp_path):
    s = stage(tmp_path)
    (s / "operations-data" / "candidates_proofs" / "c1" / "p.jpg").write_bytes(b"\xff\xd8IMAGE")
    assert any("differ" in p for p in verify.verify_files(str(s)))


def test_a_file_missing_after_restore_is_caught(tmp_path):
    s = stage(tmp_path)
    os.remove(s / "operations-data" / "payment_verification_ledger.json")
    assert any("missing" in p for p in verify.verify_files(str(s)))


def test_a_backup_without_the_ledger_is_not_a_backup(tmp_path):
    s = stage(tmp_path)
    os.remove(s / "operations-data" / "payment_verification_ledger.json")
    (s / "manifest.json").write_text(json.dumps(verify.build_manifest(str(s))))
    assert any("payment_verification_ledger" in p for p in verify.verify_files(str(s)))


def test_manifest_counts_proofs_and_rows(tmp_path):
    m = json.loads((stage(tmp_path) / "manifest.json").read_text())
    assert m["summary"]["operations_proof_files"] == 1
    assert m["row_counts"]["operations"]["mail_realtime_events"] == 223490


def test_row_counts_tolerate_live_writes_but_not_loss():
    expected = {"candidates_store": 257, "mail_realtime_events": 223490}
    assert verify.compare_counts(expected, {"candidates_store": 257, "mail_realtime_events": 223560}) == []
    assert verify.compare_counts(expected, {"candidates_store": 0, "mail_realtime_events": 223490})
    assert verify.compare_counts(expected, {"candidates_store": 257})          # a table not restored
    assert verify.compare_counts({}, {"x": 1})                                 # nothing recorded is a failure


# --- the off-server mirror ------------------------------------------------------------------

def h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def remote(files, *, snapshot_age_days=0, at=None):
    at = NOW if at is None else at
    out = {"config": {"size": 10, "mtime": at}}
    for path, size in files.items():
        out[path] = {"size": size, "mtime": at - snapshot_age_days * 86400 if path.startswith("snapshots/") else at}
    return out


def test_only_paths_shaped_like_a_restic_repository_are_accepted():
    good = [f"data/ab/{'a' * 64}", f"index/{'b' * 64}", f"snapshots/{'c' * 64}", f"keys/{'d' * 64}", "config"]
    bad = ["../etc/shadow", "locks/" + "e" * 64, f"data/ab/{'a' * 63}", "/config", "data/zz/" + "a" * 64, "config "]
    assert all(offhost.REPO_PATH.match(p) for p in good)
    assert not any(offhost.REPO_PATH.match(p) for p in bad)


def test_fetches_what_is_new_and_snapshots_last():
    snap, data = f"snapshots/{'1' * 64}", f"data/aa/{'2' * 64}"
    result, _ = offhost.plan(remote({snap: 5, data: 9}), {"config": 10}, {}, now=NOW)
    assert result["fetch"] == [data, snap], "a snapshot must never arrive before the data it names"


def test_a_size_mismatch_is_fetched_again():
    data = f"data/aa/{'2' * 64}"
    result, _ = offhost.plan(remote({data: 9}), {"config": 10, data: 4}, {}, now=NOW)
    assert result["fetch"] == [data]


def test_a_pruned_file_is_held_for_eight_days_then_released():
    gone = f"data/aa/{'3' * 64}"
    local = {"config": 10, gone: 7}
    result, stones = offhost.plan(remote({f"snapshots/{'1' * 64}": 5}), local, {}, now=NOW)
    assert result["delete"] == [] and result["held_deletions"] == 1
    later = NOW + 8 * 86400
    result, _ = offhost.plan(remote({f"snapshots/{'1' * 64}": 5}, at=later), local, stones, now=later)
    assert result["delete"] == [gone]


def test_a_broken_server_repository_never_releases_deletions():
    gone = f"data/aa/{'3' * 64}"
    stones = {gone: NOW - 30 * 86400}
    empty = {}                                                       # server repository wiped
    result, _ = offhost.plan(empty, {"config": 10, gone: 7}, stones, now=NOW)
    assert result["delete"] == [] and result["server_repository_healthy"] is False
    stale = remote({f"snapshots/{'1' * 64}": 5}, snapshot_age_days=10)  # no new snapshot for 10 days
    result, _ = offhost.plan(stale, {"config": 10, gone: 7}, stones, now=NOW)
    assert result["delete"] == []


def test_deletions_per_run_are_capped():
    local = {"config": 10, **{f"data/aa/{i:064x}": 1 for i in range(100)}}
    stones = {p: NOW - 30 * 86400 for p in local if p != "config"}
    result, _ = offhost.plan(remote({f"snapshots/{'1' * 64}": 5}), local, stones, now=NOW)
    assert len(result["delete"]) == 20 and result["held_deletions"] == 80


def test_a_file_that_reappears_loses_its_tombstone():
    p = f"data/aa/{'3' * 64}"
    _, stones = offhost.plan(remote({p: 7}), {"config": 10, p: 7}, {p: NOW - 30 * 86400}, now=NOW)
    assert p not in stones


def run_handle(role, command, stdin=b""):
    out = io.BytesIO()
    code = offhost.handle(role, command, io.BytesIO(stdin), out)
    return code, out.getvalue()


def test_the_github_key_can_only_read_status(tmp_path, monkeypatch):
    monkeypatch.setattr(offhost, "STATUS_FILE", str(tmp_path / "status.json"))
    (tmp_path / "status.json").write_text('{"ok": true}')
    assert run_handle("watch", "status") == (0, b'{"ok": true}')
    for denied in ("repo-fetch config", "laptop-report", "repo-plan", "bash", "status; rm -rf /"):
        assert run_handle("watch", denied)[0] == 2


def test_an_unknown_role_can_do_nothing():
    assert run_handle("", "status")[0] == 2
    assert run_handle("root", "status")[0] == 2


def test_fetch_refuses_anything_outside_the_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(offhost, "REPO", str(tmp_path))
    (tmp_path / "config").write_bytes(b"cfg")
    assert run_handle("rtx", "repo-fetch config") == (0, b"cfg")
    for bad in ("repo-fetch ../../etc/shadow", "repo-fetch locks/" + "a" * 64, "repo-fetch", "repo-fetch config extra"):
        assert run_handle("rtx", bad)[0] == 2


def test_a_laptop_report_is_stored_with_its_arrival_time(tmp_path, monkeypatch):
    monkeypatch.setattr(offhost, "LAPTOP_REPORT", str(tmp_path / "laptop.json"))
    code, _ = run_handle("rtx", "laptop-report", b'\xef\xbb\xbf{"mirror_verify_ok": true}')  # PowerShell sends a BOM
    stored = json.loads((tmp_path / "laptop.json").read_text())
    assert code == 0 and stored["mirror_verify_ok"] is True and stored["received_at"] > 0


def test_an_oversized_report_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(offhost, "LAPTOP_REPORT", str(tmp_path / "laptop.json"))
    assert run_handle("rtx", "laptop-report", b"{" + b" " * (70 * 1024) + b"}")[0] == 2


# --- the alert engine -------------------------------------------------------------------------

def c(key="x", ok=False, **kw):
    return monitor.check(key, ok, f"{key} {'ok' if ok else 'failing'}", **kw)


def test_a_single_failed_run_is_not_an_alert():
    active, state = monitor.evaluate([c()], {}, now=NOW)
    assert active == []
    active, _ = monitor.evaluate([c()], state, now=NOW + 300)
    assert active == [], "5 minutes is under the default 10"
    active, _ = monitor.evaluate([c()], state, now=NOW + 600)
    assert [a["key"] for a in active] == ["x"]


def test_an_immediate_check_alerts_at_once():
    active, _ = monitor.evaluate([c(min_fail_minutes=0)], {}, now=NOW)
    assert [a["key"] for a in active] == ["x"]


def test_an_alert_needs_two_passing_runs_to_close():
    _, state = monitor.evaluate([c(min_fail_minutes=0)], {}, now=NOW)
    active, state = monitor.evaluate([c(ok=True)], state, now=NOW + 300)
    assert [a["key"] for a in active] == ["x"], "one good run is not recovery"
    active, state = monitor.evaluate([c(ok=True)], state, now=NOW + 600)
    assert active == []


def test_a_flapping_check_stays_one_alert():
    _, state = monitor.evaluate([c(min_fail_minutes=0)], {}, now=NOW)
    first_since = state["checks"]["x"]["alert_since"]
    for i, ok in enumerate([True, False, True, False], start=1):
        _, state = monitor.evaluate([c(ok=ok, min_fail_minutes=0)], state, now=NOW + i * 300)
    assert state["checks"]["x"]["alerting"] and state["checks"]["x"]["alert_since"] == first_since


def test_working_hours_only_checks_wait_for_the_morning():
    night = datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc).timestamp()   # 22:30 IST
    _, state = monitor.evaluate([c(min_fail_minutes=0, business_hours_only=True)], {}, now=night)
    assert not state["checks"]["x"].get("alerting")
    morning = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc).timestamp()  # 09:30 IST
    active, _ = monitor.evaluate([c(min_fail_minutes=0, business_hours_only=True)], state, now=morning)
    assert [a["key"] for a in active] == ["x"]


def test_a_check_that_stops_being_measured_is_forgotten_quietly():
    _, state = monitor.evaluate([c(min_fail_minutes=0)], {}, now=NOW)
    active, state = monitor.evaluate([], state, now=NOW + 7200)
    assert active == [] and "x" not in state["checks"]


def test_backup_freshness_and_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "BACKUP_STATUS", str(tmp_path / "b.json"))
    keys = lambda checks: {x["key"]: x["ok"] for x in checks}
    assert keys(monitor.probe_backup(NOW)) == {"backup:fresh": False, "backup:last_run": True}
    stamp = lambda hours: datetime.fromtimestamp(NOW - hours * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "b.json").write_text(json.dumps({"last_success_at": stamp(5), "last_result": "ok"}))
    assert keys(monitor.probe_backup(NOW)) == {"backup:fresh": True, "backup:last_run": True}
    (tmp_path / "b.json").write_text(json.dumps({"last_success_at": stamp(30), "last_result": "failed", "last_error": "failed at step: dump"}))
    assert keys(monitor.probe_backup(NOW)) == {"backup:fresh": False, "backup:last_run": False}


def test_the_laptop_copy_is_watched(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "LAPTOP_REPORT", str(tmp_path / "l.json"))
    assert [x["key"] for x in monitor.probe_laptop(NOW)] == ["rtx:report"]
    stamp = lambda hours: datetime.fromtimestamp(NOW - hours * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "l.json").write_text(json.dumps({"received_at": NOW - 600, "mirror_last_success_at": stamp(40),
                                                 "mirror_verify_ok": False, "disk_free_gb": 4}))
    result = {x["key"]: x["ok"] for x in monitor.probe_laptop(NOW)}
    assert result == {"rtx:report": True, "rtx:mirror_fresh": False, "rtx:mirror_verify": False, "rtx:disk": False}


# --- the GitHub watchdog ------------------------------------------------------------------------

STATUS_OK = {"generated_at": datetime.fromtimestamp(NOW - 120, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "active_alerts": []}


def test_a_healthy_status_raises_nothing():
    assert watchdog.watch_alerts(public_ok=True, status=STATUS_OK, now=NOW) == []


def test_what_the_server_cannot_say_about_itself_is_raised():
    keys = lambda a: [x["key"] for x in a]
    assert keys(watchdog.watch_alerts(public_ok=False, status=None, now=NOW)) == ["watch:public_down", "watch:host_unreachable"]
    stale = {**STATUS_OK, "generated_at": "2026-10-04T04:00:00Z"}
    assert keys(watchdog.watch_alerts(public_ok=True, status=stale, now=NOW)) == ["watch:monitor_stale"]


def test_server_alerts_are_passed_through_once_each():
    status = {**STATUS_OK, "active_alerts": [{"key": "backup:fresh", "severity": "critical", "summary": "s", "since": "x"}] * 2}
    assert [a["key"] for a in watchdog.watch_alerts(public_ok=True, status=status, now=NOW)] == ["backup:fresh"]


def issue(number, key, updated_hours_ago=1):
    stamp = datetime.fromtimestamp(NOW - updated_hours_ago * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"number": number, "title": f"[ops-alert] {key} — summary", "updated_at": stamp}


def alert(key):
    return {"key": key, "severity": "critical", "summary": f"{key} broke", "since": "2026-10-04T05:00:00Z"}


def test_a_new_alert_opens_one_issue_that_mentions_the_owner():
    actions = watchdog.plan_actions([alert("backup:fresh")], [], now=NOW, mention="@owner")
    assert [a["op"] for a in actions] == ["create"]
    assert actions[0]["title"].startswith("[ops-alert] backup:fresh ") and "@owner" in actions[0]["body"]


def test_an_ongoing_alert_is_silent_until_a_day_has_passed():
    assert watchdog.plan_actions([alert("x")], [issue(5, "x", 3)], now=NOW, mention="@o") == []
    actions = watchdog.plan_actions([alert("x")], [issue(5, "x", 25)], now=NOW, mention="@o")
    assert [(a["op"], a["number"]) for a in actions] == [("comment", 5)]


def test_a_recovered_alert_closes_its_issue():
    actions = watchdog.plan_actions([], [issue(7, "x")], now=NOW, mention="@o")
    assert [(a["op"], a["number"]) for a in actions] == [("close", 7)]


def test_issues_that_are_not_alerts_are_left_alone():
    other = {"number": 9, "title": "Something else", "updated_at": "2026-01-01T00:00:00Z"}
    assert watchdog.plan_actions([], [other], now=NOW, mention="@o") == []


# --- heartbeats and application checks -------------------------------------------------------------

from core import job_heartbeats  # noqa: E402
from scripts import ops_health  # noqa: E402


def test_a_failure_is_written_at_once_despite_the_throttle(tmp_path, monkeypatch):
    monkeypatch.setattr(job_heartbeats, "FILE", str(tmp_path / "hb.json"))
    job_heartbeats._reset_for_tests()
    job_heartbeats.beat("j", interval_sec=30)
    job_heartbeats.beat("j", interval_sec=30, ok=False, error="boom")
    data = json.loads((tmp_path / "hb.json").read_text())
    assert data["jobs"]["j"]["consecutive_failures"] == 1 and "boom" in data["jobs"]["j"]["last_error"]


def jobs_state(now, **entries):
    return {"process_started_at": now - 86400, "jobs": entries}


def job_checks(state, now, env=None):
    return {c["key"]: c for c in ops_health.evaluate_jobs(state, now=now, env=env or {"GMAIL_PUBSUB_TOPIC": "t"})}


def test_a_loop_that_stopped_is_reported():
    now = time.time()
    state = jobs_state(now, outbox_dispatcher={"last_beat_at": now - 900, "interval_sec": 30, "consecutive_failures": 0})
    assert job_checks(state, now)["job:outbox_dispatcher"]["ok"] is False


def test_a_loop_that_keeps_failing_is_reported():
    now = time.time()
    state = jobs_state(now, interview_reminders={"last_beat_at": now - 10, "interval_sec": 300, "consecutive_failures": 3})
    assert job_checks(state, now)["job:interview_reminders"]["ok"] is False


def test_a_loop_that_never_ran_is_reported_after_start_up_but_not_during_it():
    now = time.time()
    assert job_checks({"process_started_at": now - 30, "jobs": {}}, now)["job:mail_worker"]["ok"] is True
    assert job_checks({"process_started_at": now - 86400, "jobs": {}}, now)["job:mail_worker"]["ok"] is False


def test_a_heartbeat_file_from_a_previous_container_is_not_trusted():
    now = time.time()
    checks = job_checks({}, now)  # no file yet; the real start time decides
    assert all(c["ok"] for c in checks.values())
    old = ops_health.evaluate_jobs({}, now=now, env={}, process_started_at=now - 86400)
    assert not all(c["ok"] for c in old)


def test_a_disabled_loop_is_not_reported_dead():
    now = time.time()
    keys = job_checks({"process_started_at": now - 86400, "jobs": {}}, now, env={"GMAIL_RECONNECT_REMINDERS_ENABLED": "0"})
    assert "job:gmail_reconnect" not in keys and "job:gmail_watch_renewal" not in keys


def test_outbox_stuck_and_dead_notifications():
    now = datetime.now(timezone.utc)
    old = (now.replace(microsecond=0) - __import__("datetime").timedelta(hours=2)).isoformat()
    result = {c["key"]: c["ok"] for c in ops_health.evaluate_outbox(
        [{"status": "pending", "created_at": old}, {"status": "dead", "created_at": old}], now=now)}
    assert result == {"outbox:stuck": False, "outbox:dead": False}


def test_every_mailbox_disconnected_is_critical_and_says_how_many():
    rows = [{"connection_status": "ERROR"}, {"connection_status": "ERROR"}, {"connection_status": "ERROR", "monitoring_excluded": True}]
    checks = ops_health.evaluate_mailboxes(rows, now=datetime.now(timezone.utc), needs_reconnect=lambda r: r["connection_status"] == "ERROR")
    gmail = checks[0]
    assert gmail["ok"] is False and gmail["severity"] == "critical" and gmail["summary"].startswith("2 of 2 ")


def test_no_ai_node_for_payment_screenshots_waits_an_hour_in_working_hours():
    check = ops_health.evaluate_ai([{"id": "a", "ready": False}])[0]
    assert check["ok"] is False and check["min_fail_minutes"] == 60 and check["business_hours_only"] is True


def test_alert_summaries_carry_no_personal_data():
    """Summaries travel to GitHub issues: counts and job names only."""
    rows = [{"connection_status": "ERROR", "email_address": "person@example.test", "candidate_id": "c1"}]
    text = json.dumps(ops_health.evaluate_mailboxes(rows, now=datetime.now(timezone.utc), needs_reconnect=lambda r: True))
    assert "example.test" not in text and "c1" not in text


def test_host_scripts_are_lf_so_they_run_on_linux():
    """A CRLF shell script dies on its first line on the host ("set: pipefail: invalid option")."""
    for base, dirs, names in os.walk(OPS):
        dirs[:] = [d for d in dirs if d != "__pycache__"]   # bytecode the tests above create
        for name in names:
            if name.endswith((".pyc", ".pyo")):
                continue
            with open(os.path.join(base, name), "rb") as stream:
                assert b"\r" not in stream.read(), os.path.join(base, name)
