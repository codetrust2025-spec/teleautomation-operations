#!/usr/bin/env python3
"""The only thing the off-server keys can run on the production host. Stdlib only.

Each key is pinned in root's authorized_keys as

    restrict,command="/usr/local/lib/teleautomation/ta_offhost.py <role>" ssh-ed25519 ...

so whatever the client asks for arrives in SSH_ORIGINAL_COMMAND and is checked
here against an allow-list. Nothing else (no shell, no port forwarding, no
file transfer) is possible with these keys.

role "watch" (the GitHub Actions watchdog):
    status                      the monitor's status.json

role "rtx" (the RTX 4060 laptop: backup mirror + watchdog):
    status
    laptop-report               stdin: the laptop's JSON report (<= 64 KiB), stored for the monitor
    repo-plan                   stdin: the laptop's inventory "path<TAB>size" lines;
                                stdout: what to fetch and what it may delete
    repo-fetch <path>           one repository file, raw bytes

The repository is restic's: every file except `config` is named by the SHA-256
of its content, so the laptop verifies each file it receives without needing the
repository password, which never leaves this host.

Only a PROVEN state is ever served. The nightly backup records the repository's
file list (`record-proven`, run by root on this host, never reachable over SSH)
only after the backup, its restore test and `restic check --read-data` have all
passed. repo-plan and repo-fetch serve exactly that list: a failed night, a
half-written run or a snapshot whose restore test failed is never mirrored. While
a backup is running nothing is served at all.

Deletions are deferred and capped. A file the server no longer has (pruned) may
be deleted on the laptop only after it has been absent for DELETE_AFTER_DAYS, at
most DELETE_CAP of the laptop's files per run, and never while the server holds
no recent snapshot: an emptied or broken server repository must not empty the
off-server copy along with it.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any

REPO = os.environ.get("TA_RESTIC_REPO", "/var/backups/teleautomation/restic")
STATUS_DIR = os.environ.get("TA_STATUS_DIR", "/var/lib/teleautomation-monitor")
STATUS_FILE = os.path.join(STATUS_DIR, "status.json")
LAPTOP_REPORT = os.path.join(STATUS_DIR, "laptop-report.json")
TOMBSTONES = os.path.join(STATUS_DIR, "mirror-tombstones.json")
PROVEN = os.path.join(STATUS_DIR, "mirror-proven.json")
BACKUP_LOCK = os.path.join(STATUS_DIR, "backup.lock")

DELETE_AFTER_DAYS = 8
DELETE_CAP = 0.2
FRESH_SNAPSHOT_DAYS = 3
MAX_REPORT_BYTES = 64 * 1024

_HEX64 = r"[0-9a-f]{64}"
REPO_PATH = re.compile(rf"^(config|keys/{_HEX64}|index/{_HEX64}|snapshots/{_HEX64}|data/[0-9a-f]{{2}}/{_HEX64})$")
ROLES = {"watch": {"status"}, "rtx": {"status", "laptop-report", "repo-plan", "repo-fetch"}}


def repo_listing(repo: str = REPO) -> dict[str, dict[str, Any]]:
    """{path: {size, mtime}} of every mirrorable file; locks are never mirrored."""
    out: dict[str, dict[str, Any]] = {}
    for base, _dirs, names in os.walk(repo):
        for name in names:
            path = os.path.join(base, name)
            rel = os.path.relpath(path, repo).replace(os.sep, "/")
            if REPO_PATH.match(rel):
                st = os.stat(path)
                out[rel] = {"size": st.st_size, "mtime": st.st_mtime}
    return out


def _sha256(path: str) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def record_proven(snapshot: str, *, repo: str = REPO, now: float | None = None) -> dict[str, Any]:
    """The repository state the mirror may serve: every mirrorable file with its
    size and mtime, the SHA-256 of `config` (the one file not named by its hash)
    and the snapshot that was just proven restorable."""
    files = repo_listing(repo)
    if "config" not in files:
        raise SystemExit("repository has no config; nothing recorded")
    if f"snapshots/{snapshot}" not in files:
        raise SystemExit(f"snapshot {snapshot} is not in the repository; nothing recorded")
    return {
        "snapshot": snapshot,
        "recorded_at": time.time() if now is None else now,
        "config_sha256": _sha256(os.path.join(repo, "config")),
        "files": files,
    }


def backup_running(lock_path: str | None = None) -> bool:
    """True while teleautomation-backup holds its lock (it takes an exclusive flock)."""
    lock_path = lock_path or BACKUP_LOCK
    try:
        import fcntl
    except ImportError:  # not a Linux host (tests on Windows)
        return False
    if not os.path.exists(lock_path):
        return False
    with open(lock_path, "a") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return False


def proven_state() -> dict[str, Any] | None:
    data = _load_json(PROVEN, None)
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict) or "config" not in data["files"]:
        return None
    return data


def parse_inventory(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        if "\t" not in line:
            continue
        path, size = line.rsplit("\t", 1)
        path = path.strip().replace("\\", "/")
        if REPO_PATH.match(path) and size.strip().isdigit():
            out[path] = int(size)
    return out


def plan(remote: dict[str, dict[str, Any]], local: dict[str, int], tombstones: dict[str, float],
         *, now: float) -> tuple[dict[str, Any], dict[str, float]]:
    """What the laptop should fetch and delete, and the updated tombstones."""
    fetch = sorted(p for p, meta in remote.items() if local.get(p) != meta["size"])
    # Snapshots go last: a snapshot must never arrive before the data it names.
    fetch.sort(key=lambda p: (p.startswith("snapshots/"), p.startswith("index/"), p))
    newest_snapshot = max((m["mtime"] for p, m in remote.items() if p.startswith("snapshots/")), default=0.0)
    healthy = bool(remote.get("config")) and newest_snapshot >= now - FRESH_SNAPSHOT_DAYS * 86400

    extra = sorted(p for p in local if p not in remote)
    stones = {p: t for p, t in tombstones.items() if p in extra}   # reappeared files lose their stone
    for p in extra:
        stones.setdefault(p, now)
    due = [p for p in extra if now - stones[p] >= DELETE_AFTER_DAYS * 86400]
    cap = max(5, int(len(local) * DELETE_CAP))
    delete = due[:cap] if healthy else []
    held = len(extra) - len(delete)
    return ({
        "fetch": fetch,
        "fetch_bytes": sum(remote[p]["size"] for p in fetch),
        "delete": delete,
        "held_deletions": held,
        "server_repository_healthy": healthy,
        "remote_files": len(remote),
    }, stones)


def _load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return default


def _save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(data, stream)
    os.replace(tmp, path)


def handle(role: str, command: str, stdin, stdout) -> int:
    allowed = ROLES.get(role, set())
    parts = command.strip().split()
    verb = parts[0] if parts else ""
    if verb not in allowed:
        sys.stderr.write("denied\n")
        return 2
    if verb == "status":
        stdout.write(open(STATUS_FILE, "rb").read() if os.path.isfile(STATUS_FILE) else b"{}")
        return 0
    if verb == "laptop-report":
        raw = stdin.read(MAX_REPORT_BYTES + 1)
        if len(raw) > MAX_REPORT_BYTES:
            sys.stderr.write("report too large\n")
            return 2
        report = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(report, dict):
            return 2
        report["received_at"] = time.time()
        _save_json(LAPTOP_REPORT, report)
        stdout.write(b"ok\n")
        return 0
    if verb == "repo-plan":
        local = parse_inventory(stdin.read().decode("utf-8-sig", "replace"))
        idle = {"fetch": [], "fetch_bytes": 0, "delete": [], "held_deletions": 0}
        if backup_running():
            stdout.write(json.dumps({**idle, "busy": True, "reason": "a backup is running; try later"}).encode())
            return 0
        proven = proven_state()
        if proven is None:
            stdout.write(json.dumps({**idle, "reason": "no proven backup state recorded yet"}).encode())
            return 0
        result, stones = plan(proven["files"], local, _load_json(TOMBSTONES, {}), now=time.time())
        _save_json(TOMBSTONES, stones)
        result.update(snapshot=proven["snapshot"], config_sha256=proven["config_sha256"],
                      proven_at=proven["recorded_at"])
        stdout.write(json.dumps(result).encode())
        return 0
    if verb == "repo-fetch":
        if len(parts) != 2 or not REPO_PATH.match(parts[1]):
            sys.stderr.write("bad path\n")
            return 2
        if backup_running():
            sys.stderr.write("busy: a backup is running\n")
            return 3
        proven = proven_state()
        if proven is None or parts[1] not in proven["files"]:
            sys.stderr.write("not in the proven state\n")
            return 2
        with open(os.path.join(REPO, parts[1]), "rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                stdout.write(block)
        return 0
    return 2


def main(argv: list[str]) -> int:
    # Root on this host only: the backup script records the proven state. An
    # off-server key cannot reach this branch, because its argv is fixed in
    # authorized_keys and it always arrives with SSH_ORIGINAL_COMMAND set.
    if len(argv) == 3 and argv[1] == "record-proven" and "SSH_ORIGINAL_COMMAND" not in os.environ:
        data = record_proven(argv[2])
        _save_json(PROVEN, data)
        print(f"proven state recorded: snapshot {argv[2][:8]}, {len(data['files'])} files")
        return 0
    role = argv[1] if len(argv) > 1 else ""
    return handle(role, os.environ.get("SSH_ORIGINAL_COMMAND", ""), sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
