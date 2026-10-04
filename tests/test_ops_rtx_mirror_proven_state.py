"""The RTX mirror receives only a PROVEN backup state, and falling behind is noticed.

A proven state is the repository as it stood after a nightly backup, its restore
test and `restic check --read-data` all passed. The server records it
(`ta_offhost.py record-proven`, root on the host only) and serves exactly that
list: a failed night, a half-written run or a snapshot whose restore test failed
is never mirrored, and nothing at all is served while a backup is running. The
RTX script checks `config` against the recorded SHA-256 and reports the snapshot
it holds; the monitor alerts when that falls behind the latest proven snapshot.
"""
import hashlib
import io
import json
import os
from datetime import datetime, timezone

import pytest

from tests.test_ops_backup_and_alerts import NOW, OPS, ROOT, monitor, offhost


def h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


SNAP = "a" * 64
PACK = b"pack-bytes"
GOOD = f"data/{h(PACK)[:2]}/{h(PACK)}"


def run(role, command, stdin=b""):
    out = io.BytesIO()
    code = offhost.handle(role, command, io.BytesIO(stdin), out)
    return code, out.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    for d in ("snapshots", "keys", "locks", "index"):
        (repo / d).mkdir(parents=True)
    (repo / "config").write_bytes(b"repo-config")
    (repo / GOOD).parent.mkdir(parents=True)
    (repo / GOOD).write_bytes(PACK)
    (repo / "snapshots" / SNAP).write_bytes(b"snapshot")
    (repo / "locks" / ("f" * 64)).write_bytes(b"lock")
    monkeypatch.setattr(offhost, "REPO", str(repo))
    monkeypatch.setattr(offhost, "PROVEN", str(tmp_path / "proven.json"))
    monkeypatch.setattr(offhost, "TOMBSTONES", str(tmp_path / "stones.json"))
    monkeypatch.setattr(offhost, "backup_running", lambda lock_path=None: False)
    return repo


def prove(repo):
    offhost._save_json(offhost.PROVEN, offhost.record_proven(SNAP, repo=str(repo)))


# --- recording -----------------------------------------------------------------------------

def test_the_proven_state_records_sizes_config_hash_and_the_snapshot(env):
    data = offhost.record_proven(SNAP, repo=str(env))
    assert data["snapshot"] == SNAP and data["config_sha256"] == h(b"repo-config")
    assert set(data["files"]) == {"config", GOOD, f"snapshots/{SNAP}"}, "locks are never part of it"
    assert data["files"][GOOD]["size"] == len(PACK)


def test_nothing_is_recorded_for_a_snapshot_that_is_not_there(env):
    with pytest.raises(SystemExit):
        offhost.record_proven("b" * 64, repo=str(env))


def test_nothing_is_recorded_without_a_config(env):
    os.remove(env / "config")
    with pytest.raises(SystemExit):
        offhost.record_proven(SNAP, repo=str(env))


def test_record_proven_cannot_be_reached_over_ssh(env, monkeypatch):
    for role in ("rtx", "watch"):
        assert run(role, f"record-proven {SNAP}")[0] == 2
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "status")
    monkeypatch.setattr(offhost, "STATUS_FILE", os.path.join(str(env), "no-status.json"))
    assert offhost.main(["ta_offhost.py", "record-proven", SNAP]) == 2, "argv mode is refused under SSH"
    assert not os.path.exists(offhost.PROVEN)


# --- serving -------------------------------------------------------------------------------

def test_nothing_is_served_before_anything_has_been_proven(env):
    code, out = run("rtx", "repo-plan")
    plan = json.loads(out)
    assert code == 0 and plan["fetch"] == [] and "no proven" in plan["reason"] and "snapshot" not in plan
    assert run("rtx", f"repo-fetch {GOOD}")[0] == 2


def test_the_plan_serves_the_proven_state_with_its_snapshot_and_config_hash(env):
    prove(env)
    plan = json.loads(run("rtx", "repo-plan")[1])
    assert plan["snapshot"] == SNAP and plan["config_sha256"] == h(b"repo-config")
    assert set(plan["fetch"]) == {"config", GOOD, f"snapshots/{SNAP}"}
    assert plan["fetch"][-1].startswith("snapshots/"), "a snapshot never arrives before its data"


def test_a_proven_file_is_fetchable_byte_for_byte(env):
    prove(env)
    assert run("rtx", f"repo-fetch {GOOD}") == (0, PACK)


def test_a_later_unproven_run_is_never_served(env):
    """A failed night writes packs and a snapshot after the last proven state."""
    prove(env)
    late = b"written-by-a-failed-run"
    late_path = env / "data" / h(late)[:2] / h(late)
    late_path.parent.mkdir(parents=True, exist_ok=True)
    late_path.write_bytes(late)
    (env / "snapshots" / ("c" * 64)).write_bytes(b"unproven snapshot")
    plan = json.loads(run("rtx", "repo-plan")[1])
    assert f"snapshots/{'c' * 64}" not in plan["fetch"]
    assert f"data/{h(late)[:2]}/{h(late)}" not in plan["fetch"]
    assert run("rtx", f"repo-fetch snapshots/{'c' * 64}")[0] == 2, "not fetchable either"


def test_nothing_is_served_while_a_backup_is_running(env, monkeypatch):
    prove(env)
    monkeypatch.setattr(offhost, "backup_running", lambda lock_path=None: True)
    plan = json.loads(run("rtx", "repo-plan")[1])
    assert plan["busy"] is True and plan["fetch"] == [] and plan["delete"] == []
    assert run("rtx", f"repo-fetch {GOOD}")[0] == 3
    assert not os.path.exists(offhost.TOMBSTONES), "a busy plan changes no deletion state"


def test_the_watch_key_still_reads_status_only(env):
    prove(env)
    for denied in ("repo-plan", f"repo-fetch {GOOD}", "laptop-report"):
        assert run("watch", denied)[0] == 2


@pytest.mark.skipif(os.name == "nt", reason="flock is the Linux host's lock")
def test_the_backup_lock_is_detected_on_the_host(tmp_path):
    import fcntl

    lock = tmp_path / "backup.lock"
    with open(lock, "w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert offhost.backup_running(str(lock)) is True
    assert offhost.backup_running(str(lock)) is False


def test_no_lock_file_means_no_backup_running(tmp_path):
    assert offhost.backup_running(str(tmp_path / "absent.lock")) is False


# --- the backup script only proves a state after every check passed ------------------------

BACKUP = open(os.path.join(OPS, "teleautomation-backup"), encoding="utf-8").read()


def test_the_proven_state_is_recorded_only_after_restore_test_and_read_data_check():
    markers = ('restic restore --quiet "$SNAPSHOT"', 'ta_backup_verify.py" files', "restore_db operations\n",
               "restore_db marketing\n", "restic check --quiet --read-data",
               'ta_offhost.py" record-proven "$SNAPSHOT_ID"', "status ok\n")
    positions = [BACKUP.index(m) for m in markers]
    assert positions == sorted(positions)


def test_the_full_snapshot_id_is_what_gets_proven():
    assert "print(s[-1][\"id\"] if s else \"\")" in BACKUP and 'SNAPSHOT="${SNAPSHOT_ID:0:8}"' in BACKUP


def test_retention_policy_is_unchanged_and_skipped_only_on_explicit_request():
    assert "--keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune" in BACKUP
    assert '"${TA_SKIP_RETENTION:-0}" = "1"' in BACKUP


# --- the monitor notices a mirror that falls behind -----------------------------------------

def probe(tmp_path, monkeypatch, *, mirrored, proven_hours_ago):
    monkeypatch.setattr(monitor, "LAPTOP_REPORT", str(tmp_path / "l.json"))
    monkeypatch.setattr(monitor, "PROVEN", str(tmp_path / "p.json"))
    stamp = datetime.fromtimestamp(NOW - 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "l.json").write_text(json.dumps({
        "received_at": NOW - 300, "mirror_last_success_at": stamp, "mirror_verify_ok": True,
        "disk_free_gb": 200, "mirror_snapshot": mirrored}))
    (tmp_path / "p.json").write_text(json.dumps({"snapshot": "b" * 64, "recorded_at": NOW - proven_hours_ago * 3600}))
    return {c["key"]: c for c in monitor.probe_laptop(NOW)}


def test_a_mirror_holding_the_latest_proven_snapshot_is_ok(tmp_path, monkeypatch):
    assert probe(tmp_path, monkeypatch, mirrored="b" * 64, proven_hours_ago=30)["rtx:mirror_snapshot"]["ok"]


def test_a_new_proven_snapshot_gets_time_to_arrive(tmp_path, monkeypatch):
    assert probe(tmp_path, monkeypatch, mirrored="a" * 64, proven_hours_ago=2)["rtx:mirror_snapshot"]["ok"]


def test_a_mirror_behind_the_latest_good_snapshot_alerts(tmp_path, monkeypatch):
    c = probe(tmp_path, monkeypatch, mirrored="a" * 64, proven_hours_ago=8)["rtx:mirror_snapshot"]
    assert c["ok"] is False and "behind" in c["summary"] and c["min_fail_minutes"] == 0


def test_no_proven_state_yet_means_no_lag_check(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "LAPTOP_REPORT", str(tmp_path / "l.json"))
    monkeypatch.setattr(monitor, "PROVEN", str(tmp_path / "absent.json"))
    (tmp_path / "l.json").write_text(json.dumps({"received_at": NOW - 300}))
    assert "rtx:mirror_snapshot" not in {c["key"] for c in monitor.probe_laptop(NOW)}


# --- the RTX script verifies config and reports what it holds -------------------------------

MIRROR_PS = open(os.path.join(ROOT, "scripts", "rtx-backup", "rtx_backup_mirror.ps1"), encoding="utf-8").read()


def test_the_rtx_script_checks_config_against_the_server_hash():
    assert "$script:ConfigSha = [string]$plan.config_sha256" in MIRROR_PS
    assert "if (-not $script:ConfigSha) { return $false }" in MIRROR_PS, "no recorded hash means config fails"


def test_the_rtx_script_waits_when_the_server_is_busy_or_unproven():
    assert "$plan.busy" in MIRROR_PS and "no proven backup state on the server yet" in MIRROR_PS


def test_the_rtx_script_reports_the_snapshot_only_once_it_is_present():
    assert MIRROR_PS.index("is missing after the run") < MIRROR_PS.index("Set-StateValue $state 'mirror_snapshot'")
