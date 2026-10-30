"""The Interview Data purge tool, rehearsed against a safe fixture.

The fixture is a data directory shaped like production's -- a Data Room credentials file with the
legacy `interview_data` key, one older app-made copy of it holding some of the same records, other
credentials copies, an expense store and other unrelated stores -- filled with obviously fake data. The
tests hold the properties that make the purge safe to run: it is read-only until told otherwise, it
refuses on any difference from what it was written for, it writes nothing when it refuses, it removes
only the key, it prints no ids or contents, and a verified pre-purge copy can be put back.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "purge_interview_data.py"

_spec = importlib.util.spec_from_file_location("purge_interview_data", SCRIPT)
purge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(purge)

posix_only = pytest.mark.skipif(os.name == "nt", reason="file modes and owners are POSIX")

SECRET = "FIXTURE-SECRET"
OLDER = "credentials.json.pre-srujan-import-20261005T093705Z"


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(n: int, *, newer: bool = True) -> dict:
    row = {
        "id": f"fx_{SECRET.lower()}_{n:04d}",
        "candidate": f"{SECRET} candidate {n}",
        "category": "interview",
        "details": f"{SECRET} details {n}",
        "event_date": "2026-09-01",
        "import_batch": "whatsapp-import-2026-10-05" if n <= 81 else "srujan-import-2026-10-05",
        "imported_at": "2026-10-05T09:00:00+00:00",
        "production_candidate": f"{SECRET} production candidate {n}" if n % 5 else "",
        "review_reason": "",
        "review_status": ("needs_review", "recorded", "conflict")[n % 3],
        "source_file": f"{SECRET}-chat.txt",
        "source_ref": f"line {n}",
        "source_timestamp": "2026-09-01T10:00:00",
        "source_verified": "yes" if n % 2 else "supplied dataset",
        "summary": f"{SECRET} summary {n}",
    }
    if newer:
        row["provenance_updated_at"] = "2026-10-05T11:00:00+00:00"
    return row


def base_file(records: list[dict]) -> dict:
    data = {
        "site_url": "https://ops.example.test",
        "vps_host": "",
        "admin": {"username": "admin", "password": "FIXTURE-HASH-admin", "role": "admin", "reference": "Full dashboard"},
        "handlers": [{"username": f"h{i}", "password": f"FIXTURE-HASH-{i}", "reference": f"H{i}", "role": "handler"} for i in range(5)],
        "service_accounts": [{"id": "sa1", "label": "fixture service account"}],
        "prompts": [],
        "resources": [{"id": f"r{i}", "label": f"resource {i}"} for i in range(9)],
        "offer_letters": [{"id": f"o{i}", "company": "Fixture Co"} for i in range(5)],
        "updated_at": "2026-10-05T09:57:06+00:00",
        "offer_letters_rows_migrated_v1": True,
    }
    data["interview_data"] = records
    return data


def app_format(data: dict) -> bytes:
    """How the application writes the file: indent 2, non-ASCII kept, no trailing newline."""
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


@pytest.fixture()
def world(tmp_path):
    """A data directory shaped like production's, and the expectations that describe it."""
    data_dir = tmp_path / "data"
    room = data_dir / "data_room"
    (room / "offer_letters_cache").mkdir(parents=True)
    (data_dir / "payment_proofs").mkdir()

    records = [record(n) for n in range(1, 100)]
    live = base_file(records)
    older = base_file([record(n, newer=False) for n in range(1, 82)])
    older.update(handlers=live["handlers"][:2], service_accounts=[], resources=[])
    (room / "credentials.json").write_bytes(app_format(live))
    (room / OLDER).write_bytes(app_format(older))
    earlier = base_file([])
    del earlier["interview_data"]
    (room / "credentials.json.pre-temp-reset-20260901T114252Z").write_bytes(app_format(earlier))
    (room / "offer_letters_cache" / "o0.pdf").write_bytes(b"%PDF-fixture")
    (data_dir / "handler_expenses.json").write_text(json.dumps({"expenses": [{"id": "e1", "amount": 42500, "reference": "Fixture"}]}), encoding="utf-8")
    (data_dir / "company_expenses.json").write_text(json.dumps({"expenses": []}), encoding="utf-8")
    (data_dir / "handler_salaries.json").write_text(json.dumps({"salaries": {"fixture": 40000}}), encoding="utf-8")
    (data_dir / "payment_proofs" / "p1.bin").write_bytes(os.urandom(512))

    expectations = {
        "live": {"name": "credentials.json", "count": 99, "digest": purge.digest(records),
                 "rest_digest": purge.digest({k: v for k, v in live.items() if k != "interview_data"})},
        "older_copy": {"name": OLDER, "count": 81, "digest": purge.digest(older["interview_data"]),
                       "rest_digest": purge.digest({k: v for k, v in older.items() if k != "interview_data"})},
        # A fixture has no application: nothing to stop except what a test sets up. The quiet period is kept.
        "writer": {"container": None, "ports": [], "min_idle_seconds": 120, "settle_seconds": 0},
    }
    expectations_path = tmp_path / "expectations.json"
    expectations_path.write_text(json.dumps(expectations), encoding="utf-8")
    long_ago = time.time() - 4 * 86400  # production's credentials file was last written on 5 Oct
    for path in data_dir.rglob("*"):
        if path.is_file():
            os.utime(path, (long_ago, long_ago))
    return {"dir": data_dir, "room": room, "expect": expectations_path, "live": live, "older": older, "records": records}


def run(world, command, *extra, out=None):
    out = out or io.StringIO()
    code = purge.main([command, "--data-dir", str(world["dir"]), "--expectations", str(world["expect"]), *extra], out=out)
    return code, out.getvalue()


def free_port() -> int:
    """A loopback port nothing is listening on: the application, stopped."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def set_writer(world, **changes):
    """Change the fixture's writer safeguards. Only a fixture can: on production they are pinned in the tool."""
    path = world["expect"]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["writer"].update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")


def apply_args(world, *, also=True, count=99, stopped=True):
    live_sha = sha(world["room"] / "credentials.json")
    args = ["--confirm-count", str(count), "--expect-file-sha256", live_sha]
    if stopped:
        args += ["--writers-stopped"]
    if also:
        args += ["--also", OLDER, "--also-expect-file-sha256", f"{OLDER}={sha(world['room'] / OLDER)}"]
    return args


def snapshot(directory: Path) -> dict:
    return {
        p.relative_to(directory).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns, p.stat().st_mode)
        for p in sorted(directory.rglob("*")) if p.is_file()
    }


# These tests are about the port itself and use the real probe; everywhere else a port nothing listens on is
# reported closed at once (Windows takes about two seconds to refuse a connection to a closed loopback port).
REAL_PORT_TESTS = {
    "test_a_listener_on_the_application_port_stops_it_and_nothing_is_written",
    "test_once_the_application_is_down_the_same_command_goes_through",
    "test_plan_checks_the_writer_only_when_asked",
    "test_the_real_port_check_tells_open_from_closed",
}


@pytest.fixture(autouse=True)
def _no_hook(request, monkeypatch):
    purge._BEFORE_REPLACE = None
    purge._DURING_SETTLE = None
    if request.node.name not in REAL_PORT_TESTS:
        monkeypatch.setattr(purge, "port_state", lambda spec: "closed")
    yield
    purge._BEFORE_REPLACE = None
    purge._DURING_SETTLE = None


# ── plan: read-only, and identifies without revealing ─────────────────────────

class TestPlan:
    def test_changes_nothing(self, world):
        before = snapshot(world["dir"])
        code, _ = run(world, "plan", "--also", OLDER)
        assert code == 0
        assert snapshot(world["dir"]) == before

    def test_says_it_wrote_nothing(self, world):
        _, text = run(world, "plan", "--also", OLDER)
        assert "MODE: PLAN (read-only; nothing is written)" in text
        assert "RESULT: all gates pass. Nothing was written." in text

    def test_identifies_the_records_by_count_and_digest(self, world):
        _, text = run(world, "plan", "--also", OLDER)
        assert "interview_data: 99 records, digest " + world["expect"].read_text().split('"digest": "')[1][:16] in text
        assert "interview_data: 81 records" in text
        assert "by import batch: srujan-import-2026-10-05 x18, whatsapp-import-2026-10-05 x81" in text

    def test_prints_neither_ids_nor_contents_nor_secrets(self, world):
        _, text = run(world, "plan", "--also", OLDER, "--manifest")
        for forbidden in (SECRET, SECRET.lower(), "fx_", "FIXTURE-HASH", "candidate 1", "-chat.txt"):
            assert forbidden not in text

    def test_the_manifest_lists_every_record_as_opaque_tags(self, world):
        _, text = run(world, "plan", "--manifest")
        lines = [ln.strip() for ln in text.splitlines() if ln.startswith("    ") and ":" in ln]
        live_lines = lines[:99]
        assert len(live_lines) == 99
        assert all(len(item.split(":")[0]) == 8 and len(item.split(":")[1]) == 6 for item in live_lines)
        assert len(set(live_lines)) == 99

    def test_predicts_the_file_it_would_write(self, world):
        _, text = run(world, "plan")
        rest = {k: v for k, v in world["live"].items() if k != "interview_data"}
        predicted = hashlib.sha256(app_format(rest)).hexdigest()
        assert predicted in text and "formatting kept: yes" in text

    def test_names_every_file_that_mentions_the_key(self, world):
        _, text = run(world, "plan", "--also", OLDER)
        assert "data_room/credentials.json" in text and f"data_room/{OLDER}" in text

    def test_checks_the_backup_binding_when_given_one(self, world):
        good, _ = run(world, "plan", "--also", OLDER, "--expect-file-sha256", sha(world["room"] / "credentials.json"))
        bad, text = run(world, "plan", "--also", OLDER, "--expect-file-sha256", "0" * 64)
        assert (good, bad) == (0, 2) and "[FAIL] credentials.json: file sha256" in text


class TestPlanStopsWhenTheDataIsNotWhatItExpected:
    def stop(self, world, *args):
        before = snapshot(world["dir"])
        code, text = run(world, "plan", *args)
        assert code == 2 and "[FAIL]" in text and "Nothing was written." in text
        assert snapshot(world["dir"]) == before
        return text

    def edit_live(self, world, change):
        path = world["room"] / "credentials.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        path.write_bytes(app_format(data))

    def test_a_record_missing(self, world):
        self.edit_live(world, lambda d: d["interview_data"].pop())
        assert "98 records (expected 99)" in self.stop(world)

    def test_a_record_added(self, world):
        self.edit_live(world, lambda d: d["interview_data"].append(record(100)))
        assert "100 records (expected 99)" in self.stop(world)

    def test_a_record_changed(self, world):
        self.edit_live(world, lambda d: d["interview_data"][3].update(summary="edited"))
        text = self.stop(world)
        assert "99 records (expected 99)" in text and "[FAIL] credentials.json: records digest" in text

    def test_something_else_in_the_file_changed(self, world):
        self.edit_live(world, lambda d: d["handlers"].append({"username": "new", "password": "x"}))
        assert "[FAIL] credentials.json: everything else in the file digests to" in self.stop(world)

    def test_the_older_copy_holds_a_record_the_live_file_does_not(self, world):
        path = world["room"] / OLDER
        data = json.loads(path.read_text(encoding="utf-8"))
        data["interview_data"][0]["id"] = "fx_unknown_0000"
        path.write_bytes(app_format(data))
        text = self.stop(world, "--also", OLDER)
        assert "every record in it is also in credentials.json (80 of 81)" in text

    def test_another_file_mentions_the_key(self, world):
        (world["room"] / "credentials.json.stray").write_bytes(b'{"interview_data": []}')
        assert "no other file under the data directory mentions the key (found in: data_room/credentials.json.stray)" in self.stop(world, "--also", OLDER)

    def test_the_older_copy_is_left_out_but_still_mentions_the_key(self, world):
        text = self.stop(world)
        assert f"found in: data_room/{OLDER}" in text

    @pytest.mark.parametrize("content, why", [
        (b"{not json", "not valid JSON"),
        (b'["a list"]', "top level is not an object"),
        (b'{"interview_data": "text"}', "interview_data is not a list"),
        (b'{"interview_data": [{"id": "a"}, {"id": "a"}]}', "duplicate ids"),
        (b'{"interview_data": [{"summary": "no id"}]}', "no usable id"),
        (b'{"interview_data": ["not an object"]}', "no usable id"),
    ])
    def test_a_file_that_is_not_shaped_as_expected(self, world, content, why):
        (world["room"] / "credentials.json").write_bytes(content)
        before = snapshot(world["dir"])
        code, text = run(world, "plan")
        assert code == 2 and why in text and snapshot(world["dir"]) == before

    @pytest.mark.parametrize("name", ["handler_expenses.json", "../handler_expenses.json", "credentials.json.other-copy", ""])
    def test_a_file_this_purge_was_not_written_for(self, world, name):
        before = snapshot(world["dir"])
        code, text = run(world, "plan", "--also", name)
        assert code == 2 and "Nothing was written." in text and snapshot(world["dir"]) == before

    def test_a_missing_live_file(self, world):
        (world["room"] / "credentials.json").unlink()
        code, text = run(world, "plan")
        assert code == 2 and "no such file" in text


# ── apply ─────────────────────────────────────────────────────────────────────

class TestApplyRefusesWithoutBeingTold:
    def test_it_needs_the_count_and_the_backup_binding(self, world):
        for missing in (["--expect-file-sha256", "0" * 64], ["--confirm-count", "99"]):
            with pytest.raises(SystemExit) as caught:
                purge.main(["apply", "--data-dir", str(world["dir"]), "--expectations", str(world["expect"]), *missing], out=io.StringIO())
            assert caught.value.code == 2

    def test_a_wrong_count_stops_and_writes_nothing(self, world):
        before = snapshot(world["dir"])
        code, text = run(world, "apply", *apply_args(world, count=98))
        assert code == 2 and "[FAIL] --confirm-count 98" in text and snapshot(world["dir"]) == before

    def test_a_file_that_is_not_the_one_backed_up_stops_and_writes_nothing(self, world):
        before = snapshot(world["dir"])
        args = apply_args(world)
        args[args.index("--expect-file-sha256") + 1] = "f" * 64
        code, text = run(world, "apply", *args)
        assert code == 2 and "[FAIL] credentials.json: file sha256" in text and snapshot(world["dir"]) == before

    def test_the_older_copy_must_be_bound_to_its_backup_too(self, world):
        before = snapshot(world["dir"])
        code, text = run(world, "apply", "--confirm-count", "99", "--expect-file-sha256", sha(world["room"] / "credentials.json"), "--also", OLDER)
        assert code == 2 and f"{OLDER}: no --expect-file-sha256" in text and snapshot(world["dir"]) == before

    @pytest.mark.parametrize("break_it", ["count", "digest", "rest", "stray"])
    def test_any_failed_gate_writes_nothing_anywhere(self, world, break_it):
        path = world["room"] / "credentials.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if break_it == "count":
            data["interview_data"].pop()
        elif break_it == "digest":
            data["interview_data"][0]["summary"] = "x"
        elif break_it == "rest":
            data["handlers"] = []
        else:
            (world["room"] / "credentials.json.stray").write_bytes(b'{"interview_data": []}')
        path.write_bytes(app_format(data))
        before = snapshot(world["dir"])
        code, _ = run(world, "apply", *apply_args(world))
        assert code == 2 and snapshot(world["dir"]) == before


class TestApply:
    def test_removes_the_key_from_both_files_and_only_the_key(self, world):
        code, text = run(world, "apply", *apply_args(world))
        assert code == 0, text
        live = json.loads((world["room"] / "credentials.json").read_text(encoding="utf-8"))
        older = json.loads((world["room"] / OLDER).read_text(encoding="utf-8"))
        assert "interview_data" not in live and "interview_data" not in older
        assert live == {k: v for k, v in world["live"].items() if k != "interview_data"}
        assert older == {k: v for k, v in world["older"].items() if k != "interview_data"}
        assert list(live) == [k for k in world["live"] if k != "interview_data"]

    def test_keeps_the_files_formatting_so_the_key_is_the_only_difference(self, world):
        run(world, "apply", *apply_args(world))
        rest = {k: v for k, v in world["live"].items() if k != "interview_data"}
        assert (world["room"] / "credentials.json").read_bytes() == app_format(rest)

    def test_writes_exactly_the_file_the_plan_predicted(self, world):
        _, plan = run(world, "plan", "--also", OLDER)
        predicted = [line.split("sha256 ")[1].split(";")[0] for line in plan.splitlines() if "if applied" in line]
        run(world, "apply", *apply_args(world))
        assert sha(world["room"] / "credentials.json") in predicted and sha(world["room"] / OLDER) in predicted

    def test_leaves_every_unrelated_file_byte_for_byte_and_untouched(self, world):
        before = snapshot(world["dir"])
        assert run(world, "apply", *apply_args(world))[0] == 0
        after = snapshot(world["dir"])
        changed = {name for name in before if before[name][0] != after[name][0]}
        assert changed == {"data_room/credentials.json", f"data_room/{OLDER}"}
        assert all(before[name] == after[name] for name in before if name not in changed), "an unrelated file was touched (even its mtime)"
        assert set(after) == set(before), "a file appeared or disappeared"

    def test_the_expense_store_and_the_other_stores_are_identical(self, world):
        stores = ["handler_expenses.json", "company_expenses.json", "handler_salaries.json", "data_room/offer_letters_cache/o0.pdf", "payment_proofs/p1.bin"]
        before = {s: sha(world["dir"] / s) for s in stores}
        run(world, "apply", *apply_args(world))
        assert {s: sha(world["dir"] / s) for s in stores} == before

    def test_the_other_credentials_copy_without_the_key_is_untouched(self, world):
        name = "credentials.json.pre-temp-reset-20260901T114252Z"
        before = (sha(world["room"] / name), (world["room"] / name).stat().st_mtime_ns)
        run(world, "apply", *apply_args(world))
        assert (sha(world["room"] / name), (world["room"] / name).stat().st_mtime_ns) == before

    def test_leaves_no_temporary_file_behind(self, world):
        run(world, "apply", *apply_args(world))
        assert not [p for p in world["room"].iterdir() if ".purge-" in p.name]

    def test_leaves_no_file_mentioning_the_key(self, world):
        run(world, "apply", *apply_args(world))
        assert purge.scan_for_key(str(world["dir"])) == []

    def test_prints_neither_contents_nor_ids(self, world):
        _, text = run(world, "apply", *apply_args(world))
        for forbidden in (SECRET, SECRET.lower(), "fx_", "FIXTURE-HASH"):
            assert forbidden not in text

    def test_reports_what_it_did(self, world):
        _, text = run(world, "apply", *apply_args(world))
        assert "purged credentials.json: 99 records removed" in text
        assert f"purged {OLDER}: 81 records removed" in text
        assert "files under the data directory that still mention the key: none" in text
        assert "watched the files for 0s after writing: unchanged" in text
        assert "[PASS] --writers-stopped: you confirm the application is stopped" in text
        assert "RESULT: done." in text

    def test_the_live_file_alone_is_refused_while_the_older_copy_still_holds_the_records(self, world):
        before = snapshot(world["dir"])
        code, text = run(world, "apply", *apply_args(world, also=False))
        assert code == 2 and f"found in: data_room/{OLDER}" in text
        assert snapshot(world["dir"]) == before

    @posix_only
    def test_keeps_each_file_s_mode(self, world):
        os.chmod(world["room"] / "credentials.json", 0o644)
        os.chmod(world["room"] / OLDER, 0o640)
        run(world, "apply", *apply_args(world))
        assert (world["room"] / "credentials.json").stat().st_mode & 0o777 == 0o644
        assert (world["room"] / OLDER).stat().st_mode & 0o777 == 0o640

    def test_a_second_run_is_refused_and_writes_nothing(self, world):
        run(world, "apply", *apply_args(world))
        before = snapshot(world["dir"])
        code, _ = run(world, "apply", "--confirm-count", "99", "--expect-file-sha256", sha(world["room"] / "credentials.json"))
        assert code == 2 and snapshot(world["dir"]) == before

    def test_the_application_reads_the_purged_file_exactly_as_before(self, world, monkeypatch):
        from features import data_room_credentials_store as store

        monkeypatch.setattr(store, "_FILE", str(world["room"] / "credentials.json"))

        def view():
            data = store.get_credentials()
            data.pop("updated_at", None)
            return data

        before = view()
        run(world, "apply", *apply_args(world))
        assert view() == before
        assert set(before) >= {"handlers", "service_accounts", "prompts", "resources", "offer_letters"}


class TestItChecksItsOwnWork:
    def test_a_write_that_changed_anything_but_the_key_is_reported_as_an_error(self, world, monkeypatch):
        """If a bug ever made the payload differ from `the file minus the key`, the check after writing says so."""
        real = purge.Loaded.serialized_without_key

        def buggy(self):
            payload, preserved = real(self)
            data = json.loads(payload.decode("utf-8"))
            data["handlers"] = []
            return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"), preserved

        monkeypatch.setattr(purge.Loaded, "serialized_without_key", buggy)
        code, out = run(world, "apply", *apply_args(world))
        assert code == 1
        assert "did not verify after it was written (something other than the key changed)" in out
        assert "PARTIAL STATE" in out and "Do NOT start the application." in out
        assert "RESULT: done." not in out

class TestAChangeWhileItRuns:
    def test_a_write_to_the_live_file_after_the_checks_is_not_overwritten(self, world, application):
        path = world["room"] / "credentials.json"

        def the_app_writes(target):
            if os.path.basename(target) == "credentials.json":
                data = json.loads(Path(target).read_text(encoding="utf-8"))
                data["updated_at"] = "2026-10-10T00:00:00+00:00"
                Path(target).write_bytes(app_format(data))

        purge._BEFORE_REPLACE = the_app_writes
        code, out = run(world, "apply", *apply_args(world))
        # The older copy was already purged, so this is NOT "nothing changed": exit 1, with the partial state spelled out.
        assert code == 1 and "PARTIAL STATE" in out
        assert "changed while the purge was running" in out
        assert "purged and verified (the key is gone):" in out and OLDER in out.split("purged and verified (the key is gone):")[1].splitlines()[0]
        written_by_the_app = json.loads(path.read_text(encoding="utf-8"))
        assert written_by_the_app["updated_at"] == "2026-10-10T00:00:00+00:00"
        assert len(written_by_the_app["interview_data"]) == 99, "the file the app wrote must survive untouched"
        assert "interview_data" not in (world["room"] / OLDER).read_text(encoding="utf-8")
        assert not [p for p in world["room"].iterdir() if ".purge-" in p.name]

    def test_a_write_to_the_older_copy_stops_before_the_live_file_is_touched(self, world):
        before = snapshot(world["dir"])

        def the_app_writes(target):
            if os.path.basename(target) == OLDER:
                Path(target).write_bytes(Path(target).read_bytes() + b" ")

        purge._BEFORE_REPLACE = the_app_writes
        code, out = run(world, "apply", *apply_args(world))
        # Nothing was replaced by the tool (the only change is the stray write the hook made), so this is a true refusal.
        assert code == 2 and "RESULT: refused. Nothing was written." in out and "PARTIAL STATE" not in out
        assert sha(world["room"] / "credentials.json") == before["data_room/credentials.json"][0]

    def test_when_the_live_file_is_the_one_that_moves_the_older_copy_is_reported_as_purged_and_the_exit_is_1(self, world):
        """The bug: the older copy was purged, the live file then refused, and the tool exited 2 ("nothing changed")."""
        def the_app_writes(target):
            if os.path.basename(target) == "credentials.json":
                Path(target).write_bytes(Path(target).read_bytes() + b" ")

        purge._BEFORE_REPLACE = the_app_writes
        code, out = run(world, "apply", *apply_args(world))
        assert code == 1, out
        assert "PARTIAL STATE: the purge did NOT complete, and the data has CHANGED." in out
        assert "RESULT: PARTIAL (exit status 1)." in out and "RESULT: refused" not in out and "Nothing was written" not in out

# -- the application is the only writer: it must be down ------------------------------------------------------

class TestTheWriterMustBeDown:
    """The application rewrites the whole credentials file (load, change, save). A request that loaded the file
    before the purge can write the records back after it, and nothing this tool checks can prevent that, so the
    tool refuses to run unless the writer is demonstrably stopped."""

    def refused(self, world, *args, text=None):
        before = snapshot(world["dir"])
        code, out = run(world, "apply", *args)
        assert code == 2 and "Nothing was written." in out, out
        assert snapshot(world["dir"]) == before
        if text:
            assert text in out
        return out

    def test_it_must_be_told_the_application_is_stopped(self, world):
        self.refused(world, *apply_args(world, stopped=False), text="[FAIL] --writers-stopped")

    def test_a_listener_on_the_application_port_stops_it_and_nothing_is_written(self, world):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            set_writer(world, ports=[f"127.0.0.1:{port}"])
            self.refused(world, *apply_args(world), text=f"[FAIL] something IS listening on 127.0.0.1:{port}: the application is still running")

    def test_once_the_application_is_down_the_same_command_goes_through(self, world):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            set_writer(world, ports=[f"127.0.0.1:{port}"])
            assert run(world, "apply", *apply_args(world))[0] == 2
        assert run(world, "apply", *apply_args(world))[0] == 0

    def test_an_unreachable_answer_is_not_taken_as_closed(self, world, monkeypatch):
        set_writer(world, ports=["127.0.0.1:8210"])
        monkeypatch.setattr(purge, "port_state", lambda spec: "unknown")
        self.refused(world, *apply_args(world), text="could not confirm that 127.0.0.1:8210 is closed")

    def test_the_real_port_check_tells_open_from_closed(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            assert purge.port_state(f"127.0.0.1:{listener.getsockname()[1]}") == "open"
        assert purge.port_state(f"127.0.0.1:{free_port()}") == "closed"
        assert purge.port_state("127.0.0.1:not-a-port") == "unknown"

    def test_a_file_written_a_moment_ago_means_someone_is_working_in_the_data_room(self, world):
        now = time.time()
        os.utime(world["room"] / "credentials.json", (now, now))
        out = self.refused(world, *apply_args(world))
        assert re.search(r"\[FAIL\] credentials\.json: last written \d+s ago \(needs at least 120s of quiet\)", out)

    def test_plan_checks_the_writer_only_when_asked(self, world):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            set_writer(world, ports=[f"127.0.0.1:{port}"])
            asked, out = run(world, "plan", "--also", OLDER, "--check-writer")
            plain_code, plain = run(world, "plan", "--also", OLDER)
        assert asked == 2 and f"[FAIL] something IS listening on 127.0.0.1:{port}" in out
        assert plain_code == 0 and "writer safeguards: not checked in this run" in plain and "listening" not in plain

    @posix_only
    def test_a_process_with_the_file_open_blocks_it(self, world):
        holder = subprocess.Popen([sys.executable, "-c", "import sys,time; f=open(sys.argv[1],'rb'); print('ready',flush=True); time.sleep(60)",
                                   str(world["room"] / "credentials.json")], stdout=subprocess.PIPE, text=True)
        try:
            assert holder.stdout.readline().strip() == "ready"
            self.refused(world, *apply_args(world), text="these files are open in: ")
        finally:
            holder.kill()
            holder.wait()

    def test_where_open_files_cannot_be_checked_it_says_so_instead_of_passing_silently(self, world, monkeypatch):
        monkeypatch.setattr(purge, "processes_with_open", lambda paths: None)
        code, out = run(world, "apply", *apply_args(world))
        assert code == 0 and "[INFO] open files: not checked here (no /proc)" in out


class TestTheContainerCheck:
    """The application's container must not be running, and the port the tool checks must be one Docker says that
    container publishes: naming the wrong port cannot pass."""

    PORT = "127.0.0.1:8210"

    @pytest.fixture(autouse=True)
    def configured(self, world):
        set_writer(world, container="app-container", ports=[self.PORT])

    def docker(self, monkeypatch, *, state="stopped", published=("127.0.0.1:8210",)):
        monkeypatch.setattr(purge, "container_state", lambda name: state)
        monkeypatch.setattr(purge, "container_host_ports", lambda name: None if published is None else set(published))

    def refused(self, world, text):
        before = snapshot(world["dir"])
        code, out = run(world, "apply", *apply_args(world))
        assert code == 2 and text in out and snapshot(world["dir"]) == before, out

    def test_a_running_container_stops_it(self, world, monkeypatch):
        self.docker(monkeypatch, state="running")
        self.refused(world, "[FAIL] the application container app-container is still RUNNING")

    def test_a_container_that_cannot_be_inspected_is_not_taken_as_stopped(self, world, monkeypatch):
        self.docker(monkeypatch, state="unknown", published=None)
        self.refused(world, "[FAIL] could not confirm that the application container app-container is stopped")

    def test_a_stopped_container_that_publishes_the_pinned_port_goes_through(self, world, monkeypatch):
        self.docker(monkeypatch)
        code, out = run(world, "apply", *apply_args(world))
        assert code == 0, out
        assert "[PASS] the application container app-container is stopped" in out
        assert "[PASS] 127.0.0.1:8210 is the application's published port (Docker's own record)" in out

    def test_naming_a_port_the_application_does_not_publish_is_caught(self, world, monkeypatch):
        self.docker(monkeypatch, published=("127.0.0.1:9999",))
        self.refused(world, "[FAIL] 127.0.0.1:8210 is NOT confirmed as a port the application publishes (Docker says: 127.0.0.1:9999)")

    def test_a_port_record_that_cannot_be_read_is_not_taken_as_a_match(self, world, monkeypatch):
        self.docker(monkeypatch, published=None)
        self.refused(world, "(Docker says: unreadable)")

    def test_docker_s_answers_are_read_as_documented(self, monkeypatch):
        answers = {"{{.State.Running}}": "true", "{{json .HostConfig.PortBindings}}": '{"8000/tcp":[{"HostIp":"127.0.0.1","HostPort":"8210"}]}'}
        monkeypatch.setattr(purge, "docker_inspect", lambda name, template: answers.get(template))
        assert purge.container_state("x") == "running"
        assert purge.container_host_ports("x") == {"127.0.0.1:8210"}
        answers["{{.State.Running}}"] = "false"
        assert purge.container_state("x") == "stopped"
        answers["{{.State.Running}}"] = None
        assert purge.container_state("x") == "unknown"
        answers["{{json .HostConfig.PortBindings}}"] = "not json"
        assert purge.container_host_ports("x") is None

    def test_without_docker_the_answer_is_unknown_not_stopped(self, monkeypatch):
        def no_docker(*args, **kwargs):
            raise FileNotFoundError("docker")

        monkeypatch.setattr(purge.subprocess, "run", no_docker)
        assert purge.docker_inspect("x", "{{.State.Running}}") is None
        assert purge.container_state("x") == "unknown"


class TestTheSafeguardsCannotBeWeakened:
    """Review point: --min-idle-seconds 0 or the wrong --require-port-closed used to pass. The safeguards are now pinned
    in the tool: no option loosens them, and on the production directory the expectations cannot be replaced."""

    @pytest.mark.parametrize("command, option", [
        ("apply", ["--min-idle-seconds", "0"]),
        ("apply", ["--require-port-closed", "127.0.0.1:1"]),
        ("apply", ["--settle-seconds", "0"]),
        ("plan", ["--min-idle-seconds", "0"]),
        ("plan", ["--require-port-closed", "127.0.0.1:1"]),
    ])
    def test_the_options_that_used_to_weaken_them_do_not_exist(self, world, command, option):
        before = snapshot(world["dir"])
        extra = apply_args(world) if command == "apply" else []
        with pytest.raises(SystemExit) as caught:
            purge.main([command, "--data-dir", str(world["dir"]), "--expectations", str(world["expect"]), *extra, *option], out=io.StringIO())
        assert caught.value.code == 2
        assert snapshot(world["dir"]) == before

    def test_on_the_production_directory_an_expectations_file_is_refused(self, world, monkeypatch):
        monkeypatch.setattr(purge, "PRODUCTION_DATA_DIR", str(world["dir"]))
        before = snapshot(world["dir"])
        code, out = run(world, "apply", *apply_args(world))
        assert code == 2 and "cannot be used on the production data directory" in out and "Nothing was written." in out
        assert snapshot(world["dir"]) == before

    def test_that_refusal_applies_to_every_command(self, world, monkeypatch):
        monkeypatch.setattr(purge, "PRODUCTION_DATA_DIR", str(world["dir"]))
        for command, extra in (("plan", []), ("verify", []), ("rollback", ["--file", "credentials.json", "--from", str(world["expect"]), "--expect-source-sha256", "0" * 64])):
            code, out = run(world, command, *extra)
            assert code == 2 and "cannot be used on the production data directory" in out, command

    def test_on_the_production_directory_the_pinned_writer_checks_apply_whatever_the_flags_say(self, world, monkeypatch):
        monkeypatch.setattr(purge, "PRODUCTION_DATA_DIR", str(world["dir"]))
        monkeypatch.setattr(purge, "container_state", lambda name: "running")
        monkeypatch.setattr(purge, "container_host_ports", lambda name: {"127.0.0.1:8210"})
        monkeypatch.setattr(purge, "port_state", lambda spec: "open")
        before = snapshot(world["dir"])
        out = io.StringIO()
        code = purge.main(["apply", "--data-dir", str(world["dir"]), "--confirm-count", "99", "--expect-file-sha256", sha(world["room"] / "credentials.json"),
                           "--writers-stopped"], out=out)
        text = out.getvalue()
        assert code == 2 and snapshot(world["dir"]) == before
        assert "[FAIL] the application container teleautomation-production-operations-api-1 is still RUNNING" in text
        assert "[FAIL] something IS listening on 127.0.0.1:8210: the application is still running" in text

    def test_the_pinned_settings_are_the_observed_production_ones(self):
        assert purge.WRITER == {"container": "teleautomation-production-operations-api-1", "ports": ["127.0.0.1:8210"],
                                "min_idle_seconds": 120, "settle_seconds": 10}
        assert purge.EXPECTED["writer"] is purge.WRITER
        assert purge.PRODUCTION_DATA_DIR == "/var/lib/docker/volumes/teleautomation-production_operations_data/_data"

    def test_production_gets_the_pinned_settings_and_a_fixture_gets_its_own(self, world, monkeypatch):
        monkeypatch.setattr(purge, "PRODUCTION_DATA_DIR", str(world["dir"]))
        assert purge.load_expected(None, str(world["dir"]))["writer"]["settle_seconds"] == 10
        monkeypatch.setattr(purge, "PRODUCTION_DATA_DIR", "/somewhere/else")
        assert purge.load_expected(str(world["expect"]), str(world["dir"]))["writer"]["settle_seconds"] == 0

    def test_a_fixture_expectations_file_without_a_writer_section_gets_no_writer_checks(self, world, monkeypatch, tmp_path):
        data = json.loads(world["expect"].read_text(encoding="utf-8"))
        del data["writer"]
        bare = tmp_path / "bare.json"
        bare.write_text(json.dumps(data), encoding="utf-8")
        assert purge.load_expected(str(bare), str(world["dir"]))["writer"] == purge.FIXTURE_WRITER

    def test_the_confirmation_flag_alone_is_never_enough_on_production(self, world, monkeypatch):
        # --writers-stopped is only the operator's word: the container and the port are checked independently.
        monkeypatch.setattr(purge, "PRODUCTION_DATA_DIR", str(world["dir"]))
        monkeypatch.setattr(purge, "container_state", lambda name: "stopped")
        monkeypatch.setattr(purge, "container_host_ports", lambda name: {"127.0.0.1:8210"})
        monkeypatch.setattr(purge, "port_state", lambda spec: "open")
        out = io.StringIO()
        code = purge.main(["apply", "--data-dir", str(world["dir"]), "--confirm-count", "99", "--expect-file-sha256", "0" * 64, "--writers-stopped"], out=out)
        assert code == 2 and "[PASS] --writers-stopped" in out.getvalue() and "[FAIL] something IS listening on 127.0.0.1:8210" in out.getvalue()


class TestTheWriterIsRecheckedBeforeEveryReplacement:
    """A clean gate check is a moment in time, and the write happens a moment later. The writer checks are repeated immediately
    before each file is replaced, so a writer that appears in between is caught before that file is touched. (Review point:
    "a clean window does not guarantee that a write cannot occur after the check".)"""

    def writer_appears_at(self, monkeypatch, name, *, call, up, down):
        """Patch `name` so the call numbered `call` (1 = the gate check, 2 = before the first file, 3 = before the second) sees `up`."""
        calls = {"n": 0}

        def check(*args, **kwargs):
            calls["n"] += 1
            return up if calls["n"] >= call else down

        monkeypatch.setattr(purge, name, check)
        return calls

    def test_a_listener_before_the_first_replacement_means_nothing_is_touched(self, world, monkeypatch):
        set_writer(world, ports=["127.0.0.1:8210"])
        self.writer_appears_at(monkeypatch, "port_state", call=2, up="open", down="closed")
        before = snapshot(world["dir"])
        code, out = run(world, "apply", *apply_args(world))
        assert code == 2 and "Nothing was written." in out and "PARTIAL" not in out
        assert "something is listening on 127.0.0.1:8210 again" in out and "was NOT replaced (checked immediately before writing it)" in out
        assert snapshot(world["dir"]) == before

    def test_a_listener_between_the_two_replacements_is_a_partial_state_not_a_refusal(self, world, monkeypatch):
        set_writer(world, ports=["127.0.0.1:8210"])
        self.writer_appears_at(monkeypatch, "port_state", call=3, up="open", down="closed")
        live_before = (world["room"] / "credentials.json").read_bytes()
        code, out = run(world, "apply", *apply_args(world))
        assert code == 1 and "PARTIAL STATE" in out and "credentials.json was NOT replaced" in out
        assert re.search(r"purged and verified \(the key is gone\):\s+" + re.escape(OLDER), out)
        assert re.search(r"not touched \(still hold their records\):\s+credentials\.json\b", out)
        assert (world["room"] / "credentials.json").read_bytes() == live_before
        assert "interview_data" not in (world["room"] / OLDER).read_text(encoding="utf-8")

    def test_a_container_started_after_the_gates_stops_the_write(self, world, monkeypatch):
        set_writer(world, container="app-container", ports=[])
        self.writer_appears_at(monkeypatch, "container_state", call=2, up="running", down="stopped")
        before = snapshot(world["dir"])
        code, out = run(world, "apply", *apply_args(world))
        assert code == 2 and "the application container app-container is not stopped any more" in out
        assert snapshot(world["dir"]) == before

    def test_a_container_started_between_the_two_writes_is_a_partial_state(self, world, monkeypatch):
        set_writer(world, container="app-container", ports=[])
        self.writer_appears_at(monkeypatch, "container_state", call=3, up="running", down="stopped")
        code, out = run(world, "apply", *apply_args(world))
        assert code == 1 and "PARTIAL STATE" in out and "is not stopped any more" in out

    def test_a_process_that_opens_the_file_after_the_gates_stops_the_write(self, world, monkeypatch):
        self.writer_appears_at(monkeypatch, "processes_with_open", call=2, up=[(4242, "a-writer")], down=[])
        before = snapshot(world["dir"])
        code, out = run(world, "apply", *apply_args(world))
        assert code == 2 and "the file is open in a-writer (pid 4242)" in out
        assert snapshot(world["dir"]) == before

    def test_the_check_is_made_once_per_file_and_a_quiet_run_is_unaffected(self, world, monkeypatch):
        set_writer(world, ports=["127.0.0.1:8210"])
        calls = self.writer_appears_at(monkeypatch, "port_state", call=99, up="open", down="closed")
        code, out = run(world, "apply", *apply_args(world))
        assert code == 0 and "RESULT: done." in out
        assert calls["n"] == 3, "one gate check, then one immediately before each of the two files"


# -- the delayed concurrent write --------------------------------------------------------------------------

@pytest.fixture()
def application(world, monkeypatch):
    """The real application store, pointed at the fixture: the only writer of this file in production."""
    from features import data_room_credentials_store as store

    monkeypatch.setattr(store, "_FILE", str(world["room"] / "credentials.json"))
    return store


class TestADelayedConcurrentWrite:
    """A write that lands AFTER the purge. This is the failure the tool cannot prevent by itself, so these tests
    pin what it does about it: refuse to start while the writer is up, watch afterwards, and verify again later."""

    def test_a_request_that_loaded_the_file_before_the_purge_brings_the_records_back(self, world, application):
        # An in-flight request: load (the whole dict, records included) ... the purge runs ... save.
        stale = application._load()
        assert len(stale["interview_data"]) == 99
        assert run(world, "apply", *apply_args(world))[0] == 0          # the operator believed the application was stopped
        application._save(stale)                                        # the delayed write
        assert len(json.loads((world["room"] / "credentials.json").read_text(encoding="utf-8"))["interview_data"]) == 99, \
            "this is the hazard: the application's own save restores the records"
        code, out = run(world, "verify", "--also", OLDER)
        assert code == 2 and "[FAIL] credentials.json: the interview_data key is gone" in out

    def test_the_watch_after_writing_catches_a_writer_that_wakes_up_late(self, world, application):
        set_writer(world, settle_seconds=3)
        stale = application._load()
        timer = []

        def late_writer():
            thread = threading.Timer(0.4, lambda: application._save(stale))
            thread.start()
            timer.append(thread)

        purge._DURING_SETTLE = late_writer
        code, out = run(world, "apply", *apply_args(world))
        timer[0].join()
        assert code == 1
        assert "ERROR: credentials.json: the interview_data key came back while watching the files after the purge." in out
        assert "A writer is still running. Stop it" in out and "Do NOT start the application." in out
        assert "RESULT: done." not in out

    def test_a_late_write_of_other_data_is_reported_as_well(self, world, application):
        set_writer(world, settle_seconds=2)

        def late_writer():
            def write():
                data = application._load()
                data["handlers"].append({"username": "late", "password": "x"})
                application._save(data)
            threading.Timer(0.3, write).start()

        purge._DURING_SETTLE = late_writer
        code, out = run(world, "apply", *apply_args(world))
        time.sleep(0.2)
        assert code == 1 and "credentials.json: rewritten by something else" in out

    def test_a_quiet_watch_reports_unchanged_and_succeeds(self, world):
        set_writer(world, settle_seconds=1)
        code, out = run(world, "apply", *apply_args(world))
        assert code == 0 and "watched the files for 1s after writing: unchanged" in out

    def test_verify_can_keep_watching_after_the_restart_and_catches_a_late_writer(self, world, application):
        stale = application._load()
        assert run(world, "apply", *apply_args(world))[0] == 0
        threading.Timer(0.5, lambda: application._save(stale)).start()
        code, out = run(world, "verify", "--also", OLDER, "--watch", "3")
        assert code == 2 and "the key CAME BACK in credentials.json while watching" in out

    def test_verify_watch_is_clean_when_nothing_writes(self, world):
        run(world, "apply", *apply_args(world))
        code, out = run(world, "verify", "--also", OLDER, "--watch", "1")
        assert code == 0 and "watched for 1s: the key did not come back" in out


class TestStopPurgeRestart:
    """The procedure itself, with the real store as the application: stop it, purge, start it, verify again."""

    def test_a_stopped_then_restarted_application_does_not_bring_the_records_back(self, world, application):
        stale = application._load()                      # the old process's in-memory copy, records included
        running = {"up": True}

        def old_process_write():
            if not running["up"]:
                raise RuntimeError("the process is stopped: it cannot write")
            application._save(stale)

        running["up"] = False                             # 1. stop the application
        assert run(world, "apply", *apply_args(world))[0] == 0   # 2. purge while it is down
        assert run(world, "verify", "--also", OLDER)[0] == 0     # 3. verify while it is down
        with pytest.raises(RuntimeError):
            old_process_write()                           # the stale copy died with the process

        # 4. start it: a NEW process reads the file from disk, which no longer has the records, and goes about its business.
        fresh = application._load()
        assert "interview_data" not in fresh
        application.sync_admin_login_copy({"username": "admin"})        # an ordinary write after the restart
        application.set_handler_password_mirror("h1", "FIXTURE-HASH-new")

        code, out = run(world, "verify", "--also", OLDER, "--after-restart", "--watch", "1")   # 5. verify again
        assert code == 0 and "RESULT: clean." in out
        assert "[INFO] credentials.json: other data differs from the purge-time digest" in out
        assert "interview_data" not in (world["room"] / "credentials.json").read_text(encoding="utf-8")

    def test_the_strict_verify_still_flags_other_data_that_moved(self, world, application):
        run(world, "apply", *apply_args(world))
        application.sync_admin_login_copy({"username": "admin"})
        code, out = run(world, "verify", "--also", OLDER)
        assert code == 2 and "[FAIL] credentials.json: everything else is as it was" in out

    def test_after_the_restart_the_key_is_still_a_failure_whatever_else_moved(self, world, application):
        stale = application._load()
        run(world, "apply", *apply_args(world))
        application._save(stale)
        code, out = run(world, "verify", "--also", OLDER, "--after-restart")
        assert code == 2 and "[FAIL] credentials.json: the interview_data key is gone" in out


# -- exit status 2 means nothing was changed, and only that ------------------------------------------------

class TestExitStatusMeansWhatItSays:
    """Review point: if the older copy was purged and the live-file replacement then failed, the tool returned 2 --
    which the runbook treats as proof that nothing changed. Exit 2 is now returned only when no file was replaced;
    anything else that went wrong after a change is exit 1 with the state of every file spelled out."""

    def purged_older_then_live_fails(self, world, monkeypatch, how):
        real = purge.os.replace
        calls = []

        def replace(src, dst):
            calls.append(os.path.basename(dst))
            if how == "oserror" and os.path.basename(dst) == "credentials.json":
                raise OSError(28, "No space left on device")
            return real(src, dst)

        monkeypatch.setattr(purge.os, "replace", replace)
        return run(world, "apply", *apply_args(world)), calls

    def test_the_live_replacement_failing_after_the_older_copy_was_purged_is_exit_1_with_the_partial_state(self, world, monkeypatch):
        live_before = (world["room"] / "credentials.json").read_bytes()
        (code, out), calls = self.purged_older_then_live_fails(world, monkeypatch, "oserror")
        assert calls == [OLDER, "credentials.json"], "the older copy was written first, then the live file failed"
        assert code == 1, out
        assert "PARTIAL STATE: the purge did NOT complete, and the data has CHANGED." in out
        assert re.search(r"purged and verified \(the key is gone\):\s+" + re.escape(OLDER), out)
        assert re.search(r"not touched \(still hold their records\):\s+credentials\.json\b", out)
        assert "OSError: [Errno 28] No space left on device" in out
        assert "Do NOT start the application." in out and "RESULT: PARTIAL (exit status 1)." in out
        assert "Nothing was written" not in out and "refused" not in out
        # ...and the disk agrees with the report.
        assert "interview_data" not in (world["room"] / OLDER).read_text(encoding="utf-8")
        assert (world["room"] / "credentials.json").read_bytes() == live_before
        assert not [p for p in world["room"].iterdir() if ".purge-" in p.name], "the temp file of the failed write is cleaned up"

    def test_the_older_copy_failing_first_changes_nothing_and_says_so(self, world, monkeypatch):
        real = purge.os.replace

        def replace(src, dst):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(purge.os, "replace", replace)
        before = snapshot(world["dir"])
        code, out = run(world, "apply", *apply_args(world))
        assert code == 1 and "No file had been replaced, so nothing was written." in out and "PARTIAL STATE" not in out
        assert snapshot(world["dir"]) == before

    def test_a_file_that_cannot_be_read_back_after_it_was_replaced_is_exit_1_not_2(self, world, monkeypatch):
        real = purge.atomic_replace

        def replace_then_corrupt(path, payload, **kwargs):
            real(path, payload, **kwargs)
            if os.path.basename(path) == OLDER:
                Path(path).write_bytes(b"{not json")

        monkeypatch.setattr(purge, "atomic_replace", replace_then_corrupt)
        code, out = run(world, "apply", *apply_args(world))
        assert code == 1, out
        assert "PARTIAL STATE" in out and "Nothing was written" not in out
        assert re.search(r"replaced but NOT verified:\s+" + re.escape(OLDER) + r" \(what is on disk is not the planned content\) \(not readable as JSON\)", out)
        assert re.search(r"not touched \(still hold their records\):\s+credentials\.json\b", out)

    def test_a_late_writer_after_both_files_were_purged_gets_the_state_report_too(self, world, application):
        set_writer(world, settle_seconds=2)
        stale = application._load()
        purge._DURING_SETTLE = lambda: threading.Timer(0.3, lambda: application._save(stale)).start()
        code, out = run(world, "apply", *apply_args(world))
        time.sleep(0.2)
        assert code == 1 and "PARTIAL STATE" in out
        assert re.search(r"purged and verified \(the key is gone\):\s+" + re.escape(OLDER), out)
        assert re.search(r"changed by something else, not by this tool:\s+credentials\.json \(still holds the records\)", out)

    # The invariant, checked at every stage: exit 2  =>  nothing changed;  something changed  =>  exit 1.
    STAGES = [
        "gate: wrong count", "gate: writer up", "concurrent change on the older copy", "concurrent change on the live file",
        "oserror on the older copy", "oserror on the live file", "corrupt after the older copy", "corrupt after the live file",
        "interrupt on the live file", "late writer", "nothing wrong",
    ]

    @pytest.mark.parametrize("stage", STAGES)
    def test_exit_2_only_ever_means_nothing_was_changed(self, world, application, monkeypatch, stage):
        real_replace, real_atomic = purge.os.replace, purge.atomic_replace
        args = apply_args(world, count=98 if stage == "gate: wrong count" else 99)
        if stage == "gate: writer up":
            monkeypatch.setattr(purge, "processes_with_open", lambda paths: [(1, "a-writer")])
        if stage.startswith("concurrent change"):
            target = OLDER if "older" in stage else "credentials.json"
            purge._BEFORE_REPLACE = lambda path: Path(path).write_bytes(Path(path).read_bytes() + b" ") if os.path.basename(path) == target else None
        if stage.startswith("oserror"):
            target = OLDER if "older" in stage else "credentials.json"

            def replace(src, dst):
                if os.path.basename(dst) == target:
                    raise OSError(5, "I/O error")
                return real_replace(src, dst)
            monkeypatch.setattr(purge.os, "replace", replace)
        if stage.startswith("corrupt"):
            target = OLDER if "older" in stage else "credentials.json"

            def corrupting(path, payload, **kw):
                real_atomic(path, payload, **kw)
                if os.path.basename(path) == target:
                    Path(path).write_bytes(b"{broken")
            monkeypatch.setattr(purge, "atomic_replace", corrupting)
        if stage == "interrupt on the live file":
            def interrupt(path):
                if os.path.basename(path) == "credentials.json":
                    raise KeyboardInterrupt()
            purge._BEFORE_REPLACE = interrupt
        if stage == "late writer":
            set_writer(world, settle_seconds=2)
            stale = application._load()
            purge._DURING_SETTLE = lambda: threading.Timer(0.3, lambda: application._save(stale)).start()

        before = snapshot(world["dir"])
        code, out = run(world, "apply", *args)
        time.sleep(0.4 if stage == "late writer" else 0)
        after = snapshot(world["dir"])
        tool_wrote = bool(purge._WRITES)
        if code == 2:
            assert not tool_wrote, "exit 2 was returned after the tool replaced a file:\n" + out
            assert "Nothing was written." in out
        if tool_wrote:
            assert code != 2, "a file was replaced but the exit status was 2:\n" + out
            if stage != "nothing wrong":
                assert code == 1, f"a file was replaced and the purge did not complete, but the exit status was {code}:\n" + out
                assert "PARTIAL STATE" in out
        if stage == "nothing wrong":
            assert code == 0 and tool_wrote
        if stage in {"gate: wrong count", "gate: writer up", "concurrent change on the older copy"}:
            assert code == 2 and (not tool_wrote)

    def test_the_backstop_turns_a_wrong_2_into_a_1(self, world, monkeypatch):
        """Even if some code path ever returned 2 after replacing a file, main() would not let it out as 2."""
        def lying_apply(args, out):
            purge._WRITES.append(str(world["room"] / "credentials.json"))
            return 2

        monkeypatch.setattr(purge, "cmd_apply", lying_apply)
        out = io.StringIO()
        code = purge.main(["apply", "--data-dir", str(world["dir"]), "--confirm-count", "99", "--expect-file-sha256", "0" * 64], out=out)
        assert code == 1 and "ERROR (internal): a file was replaced, so the exit status is 1, not 2: credentials.json" in out.getvalue()

    def test_the_backstop_also_covers_a_refusal_raised_after_a_replacement(self, world, monkeypatch):
        def apply_that_raises(args, out):
            purge._WRITES.append(str(world["room"] / "credentials.json"))
            raise purge.Refusal("something unexpected after the write")

        monkeypatch.setattr(purge, "cmd_apply", apply_that_raises)
        out = io.StringIO()
        code = purge.main(["apply", "--data-dir", str(world["dir"]), "--confirm-count", "99", "--expect-file-sha256", "0" * 64], out=out)
        text = out.getvalue()
        assert code == 1 and "AFTER a file was replaced" in text and "exit status 1, not 2" in text and "Nothing was written" not in text

    def loaded_pair(self, world):
        live, older = purge.Loaded(str(world["room"] / "credentials.json")), purge.Loaded(str(world["room"] / OLDER))
        ordered = [(older, {}), (live, {})]
        return ordered, {item.path: item.serialized_without_key() for item, _ in ordered}

    def test_the_partial_report_returns_1_on_its_own_not_thanks_to_the_backstop(self, world):
        ordered, payloads = self.loaded_pair(world)
        purge._WRITES[:] = [ordered[0][0].path]
        out = io.StringIO()
        assert purge.partial_report(out, ordered, payloads, [], "a later failure") == 1
        assert "PARTIAL STATE" in out.getvalue()

    def test_a_failure_while_writing_is_1_once_anything_was_replaced_and_2_only_before(self, world):
        ordered, payloads = self.loaded_pair(world)
        purge._WRITES[:] = []
        before = io.StringIO()
        assert purge.failed_while_writing(before, ordered, payloads, [], purge.Refusal("a gate-like refusal")) == 2
        assert "Nothing was written." in before.getvalue()
        purge._WRITES[:] = [ordered[0][0].path]
        after = io.StringIO()
        assert purge.failed_while_writing(after, ordered, payloads, [], purge.Refusal("a gate-like refusal")) == 1
        assert "PARTIAL STATE" in after.getvalue() and "Nothing was written" not in after.getvalue()

    def test_a_refusal_with_no_replacement_is_still_exit_2(self, world):
        code, out = run(world, "apply", *apply_args(world, stopped=False))
        assert code == 2 and "Nothing was written." in out

    def test_rollback_that_cannot_read_the_restored_file_back_is_exit_1(self, world, monkeypatch, tmp_path):
        copy = tmp_path / "pre.json"
        copy.write_bytes((world["room"] / "credentials.json").read_bytes())
        pre_sha = sha(copy)
        assert run(world, "apply", *apply_args(world))[0] == 0
        real = purge.atomic_replace

        def replace_then_corrupt(path, payload, **kwargs):
            real(path, payload, **kwargs)
            Path(path).write_bytes(b"{broken")

        monkeypatch.setattr(purge, "atomic_replace", replace_then_corrupt)
        code, out = run(world, "rollback", "--file", "credentials.json", "--from", str(copy), "--expect-source-sha256", pre_sha)
        assert code == 1 and "was replaced with the restored copy but cannot be read back" in out and "Nothing was written" not in out


# ── verify ────────────────────────────────────────────────────────────────────

class TestVerify:
    def test_fails_before_the_purge(self, world):
        code, text = run(world, "verify", "--also", OLDER)
        assert code == 2 and "[FAIL] credentials.json: the interview_data key is gone" in text

    def test_passes_after_the_purge_and_is_read_only(self, world):
        run(world, "apply", *apply_args(world))
        before = snapshot(world["dir"])
        code, text = run(world, "verify", "--also", OLDER)
        assert code == 0 and "RESULT: clean." in text
        assert snapshot(world["dir"]) == before

    def test_notices_when_something_else_in_the_file_moved(self, world):
        run(world, "apply", *apply_args(world))
        path = world["room"] / "credentials.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["handlers"] = []
        path.write_bytes(app_format(data))
        assert run(world, "verify")[0] == 2


# ── rollback ──────────────────────────────────────────────────────────────────

class TestRollback:
    @pytest.fixture()
    def purged(self, world, tmp_path):
        """A verified pre-purge copy of each file (as a backup restore would give), then the purge."""
        copies = tmp_path / "rollback"
        copies.mkdir()
        for name in ("credentials.json", OLDER):
            (copies / name).write_bytes((world["room"] / name).read_bytes())
        originals = {name: sha(world["room"] / name) for name in ("credentials.json", OLDER)}
        assert run(world, "apply", *apply_args(world))[0] == 0
        return {"copies": copies, "originals": originals, **world}

    def roll(self, purged, name="credentials.json", **override):
        source = override.get("source", purged["copies"] / name)
        return run(purged, "rollback", "--file", name, "--from", str(source),
                   "--expect-source-sha256", override.get("sha", purged["originals"][name]))

    def test_restores_the_live_file_byte_for_byte(self, purged):
        code, text = self.roll(purged)
        assert code == 0, text
        assert sha(purged["room"] / "credentials.json") == purged["originals"]["credentials.json"]
        assert "99 records back" in text

    def test_restores_the_older_copy_too(self, purged):
        code, _ = self.roll(purged, OLDER)
        assert code == 0 and sha(purged["room"] / OLDER) == purged["originals"][OLDER]

    def test_leaves_everything_else_alone(self, purged):
        before = snapshot(purged["dir"])
        self.roll(purged)
        after = snapshot(purged["dir"])
        assert {n for n in before if before[n][0] != after[n][0]} == {"data_room/credentials.json"}

    @posix_only
    def test_keeps_the_mode(self, purged):
        os.chmod(purged["room"] / "credentials.json", 0o640)
        self.roll(purged)
        assert (purged["room"] / "credentials.json").stat().st_mode & 0o777 == 0o640

    def test_a_copy_that_is_not_the_verified_one_is_refused(self, purged):
        before = snapshot(purged["dir"])
        code, text = self.roll(purged, sha="0" * 64)
        assert code == 2 and "[FAIL] the copy to restore is the one verified" in text and snapshot(purged["dir"]) == before

    def test_a_tampered_copy_is_refused_even_if_its_own_hash_is_given(self, purged, tmp_path):
        tampered = tmp_path / "tampered.json"
        data = json.loads((purged["copies"] / "credentials.json").read_text(encoding="utf-8"))
        data["interview_data"][0]["summary"] = "tampered"
        tampered.write_bytes(app_format(data))
        before = snapshot(purged["dir"])
        code, text = self.roll(purged, source=tampered, sha=sha(tampered))
        assert code == 2 and "[FAIL] the copy holds the 99 records" in text and snapshot(purged["dir"]) == before

    def test_it_will_not_overwrite_a_file_that_changed_since_the_purge(self, purged):
        path = purged["room"] / "credentials.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["handlers"].append({"username": "added-after-the-purge", "password": "x"})
        path.write_bytes(app_format(data))
        before = snapshot(purged["dir"])
        code, text = self.roll(purged)
        assert code == 2 and "merge by hand" in text and snapshot(purged["dir"]) == before

    def test_it_will_not_run_when_there_is_nothing_to_roll_back(self, world, tmp_path):
        copy = tmp_path / "copy.json"
        copy.write_bytes((world["room"] / "credentials.json").read_bytes())
        before = snapshot(world["dir"])
        code, text = run(world, "rollback", "--file", "credentials.json", "--from", str(copy), "--expect-source-sha256", sha(copy))
        assert code == 2 and "purge is still in effect" in text and snapshot(world["dir"]) == before

    def test_a_file_that_is_not_part_of_this_purge_is_refused(self, purged):
        code, text = run(purged, "rollback", "--file", "handler_expenses.json", "--from", str(purged["copies"] / "credentials.json"), "--expect-source-sha256", "0" * 64)
        assert code == 2

    def test_a_purge_and_its_rollback_leave_every_file_as_it_was(self, purged):
        # `purged` already applied the purge; roll both files back and compare with the original contents.
        assert self.roll(purged)[0] == 0 and self.roll(purged, OLDER)[0] == 0
        for name, original in purged["originals"].items():
            assert sha(purged["room"] / name) == original
        assert sorted(p.name for p in purged["room"].iterdir()) == sorted([
            "credentials.json", OLDER, "credentials.json.pre-temp-reset-20260901T114252Z", "offer_letters_cache"])


# ── the tool itself ───────────────────────────────────────────────────────────

class TestTheTool:
    def test_it_uses_only_the_standard_library(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported

    def test_the_built_in_expectations_are_the_ones_observed_in_production(self):
        assert purge.EXPECTED["live"] == {"name": "credentials.json", "count": 99, "digest": "d292eec0cf32e5d6", "rest_digest": "33622d20f6231a7e"}
        assert purge.EXPECTED["older_copy"] == {
            "name": "credentials.json.pre-srujan-import-20261005T093705Z", "count": 81,
            "digest": "0b07fefcd1841e85", "rest_digest": "aeba532e0c43b6f1",
        }

    def test_run_as_a_program_the_plan_is_read_only_and_exits_zero(self, world):
        before = snapshot(world["dir"])
        done = subprocess.run([sys.executable, str(SCRIPT), "plan", "--data-dir", str(world["dir"]), "--expectations", str(world["expect"]), "--also", OLDER],
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 0 and "RESULT: all gates pass." in done.stdout
        assert snapshot(world["dir"]) == before

    def test_run_as_a_program_a_refusal_exits_two_and_writes_nothing(self, world):
        before = snapshot(world["dir"])
        done = subprocess.run([sys.executable, str(SCRIPT), "apply", "--data-dir", str(world["dir"]), "--expectations", str(world["expect"]),
                               "--confirm-count", "98", "--expect-file-sha256", "0" * 64],
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 2 and snapshot(world["dir"]) == before

    def test_piped_through_stdin_as_the_runbook_does(self, world):
        done = subprocess.run([sys.executable, "-", "plan", "--data-dir", str(world["dir"]), "--expectations", str(world["expect"]), "--also", OLDER],
                              input=SCRIPT.read_bytes(), capture_output=True, timeout=60)
        assert done.returncode == 0, done.stdout.decode() + done.stderr.decode()

    def test_it_is_plain_ascii_so_no_terminal_or_pipe_can_mangle_it(self):
        SCRIPT.read_bytes().decode("ascii")
