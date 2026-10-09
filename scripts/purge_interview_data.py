#!/usr/bin/env python3
"""Remove the Interview Data records from the Data Room store. One purpose, every step gated.

Interview Data was a section of the Data Room (WhatsApp / dataset imports of candidate interview
history). Its code and schema were removed on 6 Oct 2026; 99 records are still in
data_room/credentials.json under the key "interview_data", and 81 of the same records in one older
app-made copy of that file. This tool removes exactly that key, from exactly those files, and refuses
to do anything if the data is not what it was written for.

    plan      read-only. Identifies the records (counts, digests, salted-hash tags -- never ids or
              contents), checks every gate, and predicts the file that would be written.
    apply     the same checks again, then removes the key. All-or-nothing validation before any write;
              each file is replaced atomically and verified afterwards, then watched for a few seconds.
    verify    read-only. After a purge: the key is gone everywhere and nothing else moved. With
              --after-restart it tolerates the application's own later writes; with --watch it keeps
              looking for a while, to catch a writer that wakes up late.
    rollback  puts a verified pre-purge copy of one file back, but only if nothing else has changed since.

THE APPLICATION MUST BE STOPPED FIRST. The application rewrites the whole credentials file (load, change,
save), so a request that loaded the file before the purge can write the records back after it, and no
check made by this tool can prevent that: the application does not take any lock this tool could respect.
So `apply` refuses unless the writer is demonstrably down -- it is told so (--writers-stopped), nothing
is listening on the application's port (--require-port-closed), no process has the files open, and the
files have been idle (--min-idle-seconds) -- and afterwards it watches the files (--settle-seconds).

It imports nothing from the application, so it runs the same on the host, in the container or on a
fixture, and cannot be changed by a deploy. Standard library only.

    python3 purge_interview_data.py plan  --data-dir DIR [--also NAME] [--manifest]
    python3 purge_interview_data.py apply --data-dir DIR [--also NAME] --confirm-count 99 \\
                                          --expect-file-sha256 SHA [--also-expect-file-sha256 NAME=SHA] \\
                                          --writers-stopped --require-port-closed HOST:PORT \\
                                          [--min-idle-seconds 120] [--settle-seconds 5]
    python3 purge_interview_data.py verify   --data-dir DIR [--also NAME] [--after-restart] [--watch SECONDS]
    python3 purge_interview_data.py rollback --data-dir DIR --file NAME --from COPY --expect-source-sha256 SHA

Exit status: 0 done / all gates pass, 2 refused (a gate failed: NOTHING was written), 1 error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import tempfile
import time
from collections import Counter

KEY = "interview_data"
LIVE_NAME = "credentials.json"
ROOM = "data_room"

# What this purge was written for, observed read-only in production on 9 Oct 2026. `digest` is over the
# records, `rest_digest` over everything else in the same file, so "the unrelated data is untouched" is
# checked against a number, not a feeling. Any difference stops the tool.
EXPECTED = {
    "live": {
        "name": LIVE_NAME,
        "count": 99,
        "digest": "d292eec0cf32e5d6",
        "rest_digest": "33622d20f6231a7e",
    },
    "older_copy": {
        "name": "credentials.json.pre-srujan-import-20261005T093705Z",
        "count": 81,
        "digest": "0b07fefcd1841e85",
        "rest_digest": "aeba532e0c43b6f1",
    },
}

# Files bigger than this are not searched for the key (the data directory holds proof images).
SCAN_LIMIT_BYTES = 50_000_000
SCAN_SKIP_DIRS = {"__pycache__"}

# Test seams. `_BEFORE_REPLACE` is called after the final unchanged-check and before the atomic
# replace; `_DURING_SETTLE` once at the start of the watch that follows the writes.
_BEFORE_REPLACE = None
_DURING_SETTLE = None


class Refusal(Exception):
    """A gate failed or the data is not what it should be. Nothing has been written."""


# -- numbers that identify without revealing -----------------------------------

def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def tag(value) -> str:
    """A short, one-way label. Record ids in this data carry fragments of names, so ids are never printed."""
    return hashlib.sha256(("interview-purge|" + str(value)).encode()).hexdigest()[:8]


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# -- reading a file ------------------------------------------------------------

class Loaded:
    """One file, read once: its bytes, its parse and the numbers that describe it."""

    def __init__(self, path: str):
        self.path = path
        self.name = os.path.basename(path)
        st = os.stat(path)
        with open(path, "rb") as f:
            self.raw = f.read()
        self.size = len(self.raw)
        self.sha256 = sha256_bytes(self.raw)
        self.mode = st.st_mode & 0o7777
        self.uid = getattr(st, "st_uid", None)
        self.gid = getattr(st, "st_gid", None)
        self.mtime_ns = st.st_mtime_ns
        try:
            self.data = json.loads(self.raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Refusal(f"{self.name}: not valid JSON ({type(exc).__name__}); refusing to touch it")
        if not isinstance(self.data, dict):
            raise Refusal(f"{self.name}: top level is not an object; refusing to touch it")
        self.has_key = KEY in self.data
        self.records = self.data.get(KEY) if self.has_key else []
        if self.has_key:
            if not isinstance(self.records, list):
                raise Refusal(f"{self.name}: {KEY} is not a list")
            ids = []
            for item in self.records:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
                    raise Refusal(f"{self.name}: a {KEY} record has no usable id")
                ids.append(item["id"])
            if len(set(ids)) != len(ids):
                raise Refusal(f"{self.name}: {KEY} has duplicate ids")
            self.ids = ids
        else:
            self.ids = []
        self.count = len(self.records)
        self.digest = digest(self.records) if self.has_key else None
        self.rest = {k: v for k, v in self.data.items() if k != KEY}
        self.rest_digest = digest(self.rest)

    def serialized_without_key(self) -> tuple[bytes, bool]:
        """The file as it would be written, and whether its formatting is the file's own.

        The original is re-serialised the way the application writes it (and with a trailing newline);
        if that reproduces the original bytes the output uses the same formatting, so the only change to
        the file is the removed key.
        """
        for indent, newline in ((2, False), (2, True), (4, False), (4, True)):
            text = json.dumps(self.data, ensure_ascii=False, indent=indent) + ("\n" if newline else "")
            if text.encode("utf-8") == self.raw:
                out = json.dumps(self.rest, ensure_ascii=False, indent=indent) + ("\n" if newline else "")
                return out.encode("utf-8"), True
        return (json.dumps(self.rest, ensure_ascii=False, indent=2)).encode("utf-8"), False


def load_expected(path: str | None) -> dict:
    if not path:
        return EXPECTED
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def room_dir(data_dir: str) -> str:
    return os.path.join(data_dir, ROOM)


def locate(data_dir: str, name: str) -> str:
    if os.path.basename(name) != name or name in {"", ".", ".."}:
        raise Refusal(f"{name!r} is not a plain file name")
    if name != LIVE_NAME and not name.startswith(LIVE_NAME + "."):
        raise Refusal(f"{name}: only {LIVE_NAME} and its app-made copies ({LIVE_NAME}.*) can be targeted")
    path = os.path.join(room_dir(data_dir), name)
    if not os.path.isfile(path):
        raise Refusal(f"{name}: no such file in {room_dir(data_dir)}")
    return path


def scan_for_key(data_dir: str) -> list[str]:
    """Every file under the data directory whose bytes mention the key. Names only."""
    needle = KEY.encode()
    found = []
    for base, dirs, files in os.walk(data_dir):
        dirs[:] = sorted(d for d in dirs if d not in SCAN_SKIP_DIRS)
        for name in sorted(files):
            path = os.path.join(base, name)
            try:
                if os.path.getsize(path) > SCAN_LIMIT_BYTES:
                    continue
                with open(path, "rb") as f:
                    if needle in f.read():
                        found.append(os.path.relpath(path, data_dir).replace(os.sep, "/"))
            except OSError:
                continue
    return found


# -- the gates -----------------------------------------------------------------

class Gates:
    def __init__(self):
        self.rows: list[tuple[bool, str]] = []

    def check(self, ok: bool, text: str) -> bool:
        self.rows.append((bool(ok), text))
        return bool(ok)

    def info(self, text: str) -> None:
        self.rows.append((None, text))

    @property
    def failed(self) -> list[str]:
        return [text for ok, text in self.rows if ok is False]

    def print(self, out) -> None:
        for ok, text in self.rows:
            print(f"  [{'INFO' if ok is None else 'PASS' if ok else 'FAIL'}] {text}", file=out)


def build_plan(args, expected: dict):
    """Read everything, decide nothing is written, and return (targets, gates, scan)."""
    data_dir = args.data_dir
    gates = Gates()
    targets: list[tuple[Loaded, dict]] = []

    live_expect = expected["live"]
    live = Loaded(locate(data_dir, live_expect["name"]))
    targets.append((live, live_expect))
    gates.check(live.has_key, f"{live.name}: holds the {KEY} key")
    gates.check(live.count == live_expect["count"], f"{live.name}: {live.count} records (expected {live_expect['count']})")
    gates.check(live.digest == live_expect["digest"], f"{live.name}: records digest {live.digest} (expected {live_expect['digest']})")
    gates.check(live.rest_digest == live_expect["rest_digest"],
                f"{live.name}: everything else in the file digests to {live.rest_digest} (expected {live_expect['rest_digest']})")

    for name in args.also or []:
        older_expect = expected.get("older_copy") or {}
        if name != older_expect.get("name"):
            raise Refusal(f"{name}: not a file this purge was written for (expected {older_expect.get('name')!r})")
        older = Loaded(locate(data_dir, name))
        targets.append((older, older_expect))
        gates.check(older.has_key, f"{older.name}: holds the {KEY} key")
        gates.check(older.count == older_expect["count"], f"{older.name}: {older.count} records (expected {older_expect['count']})")
        gates.check(older.digest == older_expect["digest"], f"{older.name}: records digest {older.digest} (expected {older_expect['digest']})")
        gates.check(older.rest_digest == older_expect["rest_digest"],
                    f"{older.name}: everything else in the file digests to {older.rest_digest} (expected {older_expect['rest_digest']})")
        extra = sorted(set(older.ids) - set(live.ids))
        gates.check(not extra, f"{older.name}: every record in it is also in {live.name} ({len(older.ids) - len(extra)} of {len(older.ids)})")

    scan = scan_for_key(data_dir)
    listed = {os.path.relpath(t.path, data_dir).replace(os.sep, "/") for t, _ in targets}
    unexpected = sorted(set(scan) - listed)
    gates.check(not unexpected, "no other file under the data directory mentions the key" + (f" (found in: {', '.join(unexpected)})" if unexpected else ""))
    return targets, gates, scan


def bound_file_gates(args, targets, gates: Gates, *, required: bool) -> None:
    """The backup is bound to the exact bytes being purged: the sha256 verified against a snapshot."""
    wanted = {LIVE_NAME: getattr(args, "expect_file_sha256", None)}
    for item in getattr(args, "also_expect_file_sha256", None) or []:
        name, _, sha = item.partition("=")
        wanted[name] = sha
    for loaded, _ in targets:
        sha = wanted.get(loaded.name)
        if sha:
            gates.check(loaded.sha256 == sha, f"{loaded.name}: file sha256 is the one verified against the backup ({loaded.sha256[:12]}... vs {sha[:12]}...)")
        elif required:
            gates.check(False, f"{loaded.name}: no --expect-file-sha256 given, so the backup is not tied to this file")


# -- is the writer really down? ------------------------------------------------

def port_state(spec: str) -> str:
    """'open' if something accepts connections, 'closed' if the connection is refused, else 'unknown'."""
    host, _, port = spec.rpartition(":")
    try:
        with socket.create_connection((host or "127.0.0.1", int(port)), timeout=3):
            return "open"
    except ConnectionRefusedError:
        return "closed"
    except (OSError, ValueError):
        return "unknown"


def processes_with_open(paths: list[str]):
    """(pid, command name) of every other process holding one of `paths` open, or None where /proc is absent."""
    if not os.path.isdir("/proc"):
        return None
    wanted = {os.path.realpath(p) for p in paths}
    me = os.getpid()
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            fds = os.listdir(f"/proc/{entry}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(f"/proc/{entry}/fd/{fd}")
            except OSError:
                continue
            if link in wanted:
                try:
                    with open(f"/proc/{entry}/comm", encoding="utf-8", errors="replace") as f:
                        name = f.read().strip()
                except OSError:
                    name = "?"
                found.append((int(entry), name))
                break
    return found


def writer_gates(args, targets, gates: Gates, *, required: bool) -> None:
    """Checks that the application, the only writer of these files, is not running.

    They cannot prove it, but each failure mode they catch is a real one: the operator forgot to stop it,
    something restarted it, a request is mid-flight, or someone was just editing the Data Room.
    """
    ports = list(getattr(args, "require_port_closed", None) or [])
    idle = getattr(args, "min_idle_seconds", None)
    if required:
        gates.check(bool(getattr(args, "writers_stopped", False)), "--writers-stopped: the application has been stopped before this run")
        gates.check(bool(ports), "--require-port-closed names the application's port, so a running application is detected")
    if not (required or ports or idle is not None):
        return
    for spec in ports:
        state = port_state(spec)
        gates.check(state == "closed",
                    f"nothing is listening on {spec}" if state == "closed"
                    else (f"something IS listening on {spec}: the application is still running" if state == "open"
                          else f"could not confirm that {spec} is closed"))
    if idle is not None:
        for loaded, _ in targets:
            age = time.time() - loaded.mtime_ns / 1e9
            gates.check(age >= idle, f"{loaded.name}: last written {int(age)}s ago (needs at least {idle}s of quiet)")
    holders = processes_with_open([loaded.path for loaded, _ in targets])
    if holders is None:
        gates.info("open files: not checked here (no /proc)")
    else:
        gates.check(not holders, "no process has these files open" if not holders
                    else "these files are open in: " + ", ".join(f"{name} (pid {pid})" for pid, name in holders))


def settle(written: dict[str, str], seconds: float) -> list[str]:
    """Watch the files just written. Returns what went wrong (empty when they stayed as written)."""
    if _DURING_SETTLE is not None:
        _DURING_SETTLE()
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        problems = []
        for path, sha in written.items():
            try:
                now = Loaded(path)
            except Refusal as exc:
                problems.append(f"{os.path.basename(path)}: unreadable while watching ({exc})")
                continue
            if now.has_key:
                problems.append(f"{os.path.basename(path)}: the {KEY} key came back")
            elif now.sha256 != sha:
                problems.append(f"{os.path.basename(path)}: rewritten by something else")
        if problems or time.monotonic() >= deadline:
            return problems
        time.sleep(0.25)


# -- writing -------------------------------------------------------------------

def _fsync_dir(directory: str) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_replace(path: str, payload: bytes, *, like: Loaded, unchanged_since: Loaded) -> None:
    """Write `payload` to `path` atomically, keeping the file's mode and owner.

    Refuses if the file is no longer the one that was read: the application writes it too.
    """
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".purge-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, like.mode)
        if like.uid is not None and hasattr(os, "chown"):
            try:
                os.chown(tmp, like.uid, like.gid)
            except OSError:
                pass
        if _BEFORE_REPLACE is not None:
            _BEFORE_REPLACE(path)
        now = os.stat(path)
        with open(path, "rb") as f:
            current = f.read()
        if sha256_bytes(current) != unchanged_since.sha256 or now.st_mtime_ns != unchanged_since.mtime_ns or len(current) != unchanged_since.size:
            raise Refusal(f"{os.path.basename(path)} changed while the purge was running; it was NOT replaced. Nothing was written to it")
        os.replace(tmp, path)
        tmp = None
        _fsync_dir(directory)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


# -- commands ------------------------------------------------------------------

def describe(loaded: Loaded, out, *, manifest: bool) -> None:
    print(f"file: {loaded.name}", file=out)
    print(f"  size {loaded.size} bytes, mode {oct(loaded.mode)}, owner {loaded.uid}:{loaded.gid}, sha256 {loaded.sha256}", file=out)
    print(f"  top-level keys: {', '.join(loaded.data.keys())}", file=out)
    if not loaded.has_key:
        return
    print(f"  {KEY}: {loaded.count} records, digest {loaded.digest}", file=out)
    batches = Counter(str(r.get("import_batch")) for r in loaded.records)
    status = Counter(str(r.get("review_status")) for r in loaded.records)
    print("  by import batch: " + ", ".join(f"{k} x{v}" for k, v in sorted(batches.items())), file=out)
    print("  by review status: " + ", ".join(f"{k} x{v}" for k, v in sorted(status.items())), file=out)
    tags = sorted(f"{tag(r['id'])}:{digest(r)[:6]}" for r in loaded.records)
    print(f"  identity list digest {digest(tags)} (record tag:content tag, {len(tags)} entries; ids and contents are never printed)", file=out)
    if manifest:
        for line in tags:
            print(f"    {line}", file=out)
    print(f"  everything else in the file: {len(loaded.rest)} keys, digest {loaded.rest_digest}", file=out)


def cmd_plan(args, out=sys.stdout) -> int:
    expected = load_expected(args.expectations)
    print("MODE: PLAN (read-only; nothing is written)", file=out)
    targets, gates, scan = build_plan(args, expected)
    for loaded, _ in targets:
        describe(loaded, out, manifest=args.manifest)
        payload, preserved = loaded.serialized_without_key()
        print(f"  if applied: the file would become {len(payload)} bytes, sha256 {sha256_bytes(payload)}; formatting kept: {'yes' if preserved else 'no (re-indented)'}", file=out)
    bound_file_gates(args, targets, gates, required=False)
    writer_gates(args, targets, gates, required=False)
    print("files under the data directory that mention the key: " + (", ".join(scan) if scan else "none"), file=out)
    print("GATES:", file=out)
    gates.print(out)
    failed = gates.failed
    if failed:
        print(f"RESULT: STOP. {len(failed)} gate(s) failed. Nothing was written.", file=out)
        return 2
    print("RESULT: all gates pass. Nothing was written.", file=out)
    return 0


def cmd_apply(args, out=sys.stdout) -> int:
    expected = load_expected(args.expectations)
    print("MODE: APPLY", file=out)
    targets, gates, scan = build_plan(args, expected)
    bound_file_gates(args, targets, gates, required=True)
    writer_gates(args, targets, gates, required=True)
    live_expect = expected["live"]
    gates.check(args.confirm_count == live_expect["count"],
                f"--confirm-count {args.confirm_count} matches the {live_expect['count']} records to be deleted")
    for loaded, _ in targets:
        describe(loaded, out, manifest=False)
    print("GATES:", file=out)
    gates.print(out)
    if gates.failed:
        print(f"RESULT: STOP. {len(gates.failed)} gate(s) failed. Nothing was written.", file=out)
        return 2

    # Everything is validated. Write the least important file first so a refusal on the live
    # file (it is the one the application writes) leaves the live data as it was.
    ordered = sorted(targets, key=lambda item: item[0].name == LIVE_NAME)
    payloads = {loaded.path: loaded.serialized_without_key() for loaded, _ in ordered}
    done = []
    for loaded, _ in ordered:
        payload, preserved = payloads[loaded.path]
        try:
            atomic_replace(loaded.path, payload, like=loaded, unchanged_since=loaded)
        except Refusal as exc:
            print(f"STOP: {exc}", file=out)
            print(f"RESULT: refused. Already purged before this: {', '.join(done) if done else 'nothing'}.", file=out)
            return 2
        after = Loaded(loaded.path)
        problems = []
        if after.sha256 != sha256_bytes(payload):
            problems.append("the file on disk is not the planned bytes")
        if after.has_key:
            problems.append("the key is still present")
        if after.rest_digest != loaded.rest_digest:
            problems.append("something other than the key changed")
        if after.mode != loaded.mode:
            problems.append("the file mode changed")
        if loaded.uid is not None and (after.uid, after.gid) != (loaded.uid, loaded.gid):
            problems.append("the owner changed")
        if problems:
            print(f"ERROR: {loaded.name}: {'; '.join(problems)}. Roll back with the pre-purge copy.", file=out)
            return 1
        done.append(loaded.name)
        print(f"purged {loaded.name}: {loaded.count} records removed; {loaded.size} -> {after.size} bytes; "
              f"sha256 {after.sha256}; the rest of the file is unchanged ({after.rest_digest}); mode and owner kept", file=out)
    written = {loaded.path: sha256_bytes(payloads[loaded.path][0]) for loaded, _ in ordered}
    problems = settle(written, args.settle_seconds)
    if problems:
        for problem in problems:
            print(f"ERROR: {problem} while watching the files after the purge.", file=out)
        print("A writer is still running. Stop it, run verify, and if the records are back run apply again with the application stopped. "
              "Do NOT start the application.", file=out)
        return 1
    print(f"watched the files for {args.settle_seconds:g}s after writing: unchanged", file=out)
    remaining = scan_for_key(args.data_dir)
    print("files under the data directory that still mention the key: " + (", ".join(remaining) if remaining else "none"), file=out)
    print("RESULT: done. " + ", ".join(done), file=out)
    return 0 if not remaining else 1


def cmd_verify(args, out=sys.stdout) -> int:
    expected = load_expected(args.expectations)
    print("MODE: VERIFY (read-only)" + (", after the application was restarted" if args.after_restart else ""), file=out)
    gates = Gates()
    names = [expected["live"]["name"]] + list(args.also or [])
    paths = {}
    for name in names:
        info = expected["live"] if name == expected["live"]["name"] else expected.get("older_copy", {})
        loaded = Loaded(locate(args.data_dir, name))
        paths[loaded.path] = loaded.name
        gates.check(not loaded.has_key, f"{loaded.name}: the {KEY} key is gone")
        same = loaded.rest_digest == info.get("rest_digest")
        if args.after_restart and not same:
            # The application has been running again and may legitimately have written other data.
            gates.info(f"{loaded.name}: other data differs from the purge-time digest ({loaded.rest_digest}); expected once the application has written")
        else:
            gates.check(same, f"{loaded.name}: everything else is as it was ({loaded.rest_digest}, expected {info.get('rest_digest')})")
        print(f"file: {loaded.name} size {loaded.size} sha256 {loaded.sha256}", file=out)
    if args.watch:
        deadline = time.monotonic() + args.watch
        back = []
        while time.monotonic() < deadline and not back:
            time.sleep(1.0)
            for path, name in paths.items():
                try:
                    if Loaded(path).has_key:
                        back.append(name)
                except Refusal:
                    continue
        gates.check(not back, f"watched for {args.watch:g}s: the key did not come back" if not back
                    else f"the key CAME BACK in {', '.join(back)} while watching")
    scan = scan_for_key(args.data_dir)
    gates.check(not scan, "no file under the data directory mentions the key" + (f" (found in: {', '.join(scan)})" if scan else ""))
    print("GATES:", file=out)
    gates.print(out)
    if gates.failed:
        print("RESULT: NOT CLEAN.", file=out)
        return 2
    print("RESULT: clean.", file=out)
    return 0


def cmd_rollback(args, out=sys.stdout) -> int:
    expected = load_expected(args.expectations)
    print("MODE: ROLLBACK", file=out)
    info = None
    for entry in expected.values():
        if entry.get("name") == args.file:
            info = entry
    if info is None:
        raise Refusal(f"{args.file}: not a file this purge was written for")
    gates = Gates()
    with open(args.source, "rb") as f:
        source_raw = f.read()
    gates.check(sha256_bytes(source_raw) == args.expect_source_sha256,
                f"the copy to restore is the one verified before the purge (sha256 {sha256_bytes(source_raw)[:12]}...)")
    try:
        copy_data = json.loads(source_raw.decode("utf-8"))
        copy_records = copy_data.get(KEY) if isinstance(copy_data, dict) else None
        gates.check(isinstance(copy_records, list) and len(copy_records) == info["count"] and digest(copy_records) == info["digest"],
                    f"the copy holds the {info['count']} records ({KEY} digest {info['digest']})")
    except (UnicodeDecodeError, json.JSONDecodeError):
        gates.check(False, "the copy is valid JSON")
    path = locate(args.data_dir, args.file)
    current = Loaded(path)
    gates.check(not current.has_key, f"{current.name}: currently has no {KEY} (the purge is still in effect)")
    gates.check(current.rest_digest == info["rest_digest"],
                f"{current.name}: nothing else has changed since the purge ({current.rest_digest}, expected {info['rest_digest']})")
    print("GATES:", file=out)
    gates.print(out)
    if gates.failed:
        print("RESULT: STOP. Nothing was written. If the file changed since the purge, merge by hand instead of restoring over it.", file=out)
        return 2
    atomic_replace(path, source_raw, like=current, unchanged_since=current)
    after = Loaded(path)
    print(f"restored {args.file}: {current.size} -> {after.size} bytes; sha256 {after.sha256}; {after.count} records back", file=out)
    print("RESULT: restored.", file=out)
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, also=True):
        sp.add_argument("--data-dir", required=True, help="the application data directory (the one holding data_room/)")
        sp.add_argument("--expectations", help="JSON overriding the built-in expectations (for fixtures only)")
        if also:
            sp.add_argument("--also", action="append", help="also purge this older app-made copy of the file (named exactly)")

    sp = sub.add_parser("plan", help="read-only dry run")
    common(sp)
    sp.add_argument("--manifest", action="store_true", help="list every record as tag:tag (no ids, no contents)")
    sp.add_argument("--expect-file-sha256", help="check the live file's sha256 (the one verified against the backup)")
    sp.add_argument("--also-expect-file-sha256", action="append", metavar="NAME=SHA")
    sp.add_argument("--require-port-closed", action="append", metavar="HOST:PORT", help="check that the application is not listening here")
    sp.add_argument("--min-idle-seconds", type=int, help="check that the files have been quiet this long")
    sp.set_defaults(run=cmd_plan)

    sp = sub.add_parser("apply", help="remove the key")
    common(sp)
    sp.add_argument("--confirm-count", type=int, required=True, help="the number of records that will be deleted")
    sp.add_argument("--expect-file-sha256", required=True, help="the live file's sha256 as verified against the backup")
    sp.add_argument("--also-expect-file-sha256", action="append", metavar="NAME=SHA")
    sp.add_argument("--writers-stopped", action="store_true", help="the application has been stopped (required)")
    sp.add_argument("--require-port-closed", action="append", metavar="HOST:PORT", help="the application's port; must refuse connections (required)")
    sp.add_argument("--min-idle-seconds", type=int, default=120, help="the files must have been quiet this long (default 120)")
    sp.add_argument("--settle-seconds", type=float, default=5.0, help="watch the files this long after writing (default 5)")
    sp.set_defaults(run=cmd_apply)

    sp = sub.add_parser("verify", help="read-only post-check")
    common(sp)
    sp.add_argument("--after-restart", action="store_true", help="the application has run again: its own writes to other data are not a failure")
    sp.add_argument("--watch", type=float, default=0.0, help="keep checking for this many seconds, to catch a writer that wakes up late")
    sp.set_defaults(run=cmd_verify)

    sp = sub.add_parser("rollback", help="restore a verified pre-purge copy of one file")
    common(sp, also=False)
    sp.add_argument("--file", required=True)
    sp.add_argument("--from", dest="source", required=True)
    sp.add_argument("--expect-source-sha256", required=True)
    sp.set_defaults(run=cmd_rollback)
    return p


def main(argv=None, out=sys.stdout) -> int:
    args = parser().parse_args(argv)
    try:
        return args.run(args, out)
    except Refusal as exc:
        print(f"STOP: {exc}", file=out)
        print("RESULT: refused. Nothing was written.", file=out)
        return 2
    except OSError as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=out)
        return 1


if __name__ == "__main__":
    sys.exit(main())
