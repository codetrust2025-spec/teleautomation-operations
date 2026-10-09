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
import subprocess
import sys
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
    }
    expectations_path = tmp_path / "expectations.json"
    expectations_path.write_text(json.dumps(expectations), encoding="utf-8")
    return {"dir": data_dir, "room": room, "expect": expectations_path, "live": live, "older": older, "records": records}


def run(world, command, *extra, out=None):
    out = out or io.StringIO()
    code = purge.main([command, "--data-dir", str(world["dir"]), "--expectations", str(world["expect"]), *extra], out=out)
    return code, out.getvalue()


def apply_args(world, *, also=True, count=99):
    live_sha = sha(world["room"] / "credentials.json")
    args = ["--confirm-count", str(count), "--expect-file-sha256", live_sha]
    if also:
        args += ["--also", OLDER, "--also-expect-file-sha256", f"{OLDER}={sha(world['room'] / OLDER)}"]
    return args


def snapshot(directory: Path) -> dict:
    return {
        p.relative_to(directory).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns, p.stat().st_mode)
        for p in sorted(directory.rglob("*")) if p.is_file()
    }


@pytest.fixture(autouse=True)
def _no_hook():
    purge._BEFORE_REPLACE = None
    yield
    purge._BEFORE_REPLACE = None


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
        code, text = run(world, "apply", *apply_args(world))
        assert code == 1
        assert "something other than the key changed" in text and "Roll back with the pre-purge copy" in text
        assert "RESULT: done." not in text


class TestAChangeWhileItRuns:
    def test_a_write_to_the_live_file_after_the_checks_is_not_overwritten(self, world):
        path = world["room"] / "credentials.json"

        def the_app_writes(target):
            if os.path.basename(target) == "credentials.json":
                data = json.loads(Path(target).read_text(encoding="utf-8"))
                data["updated_at"] = "2026-10-10T00:00:00+00:00"
                Path(target).write_bytes(app_format(data))

        purge._BEFORE_REPLACE = the_app_writes
        code, text = run(world, "apply", *apply_args(world))
        assert code == 2 and "changed while the purge was running" in text
        written_by_the_app = json.loads(path.read_text(encoding="utf-8"))
        assert written_by_the_app["updated_at"] == "2026-10-10T00:00:00+00:00"
        assert len(written_by_the_app["interview_data"]) == 99, "the file the app wrote must survive untouched"
        assert not [p for p in world["room"].iterdir() if ".purge-" in p.name]

    def test_a_write_to_the_older_copy_stops_before_the_live_file_is_touched(self, world):
        before_live = (world["room"] / "credentials.json").read_bytes()

        def the_app_writes(target):
            if os.path.basename(target) == OLDER:
                Path(target).write_bytes(Path(target).read_bytes() + b" ")

        purge._BEFORE_REPLACE = the_app_writes
        code, text = run(world, "apply", *apply_args(world))
        assert code == 2 and "Already purged before this: nothing" in text
        assert (world["room"] / "credentials.json").read_bytes() == before_live

    def test_when_the_live_file_is_the_one_that_moves_the_older_copy_is_reported_as_done(self, world):
        def the_app_writes(target):
            if os.path.basename(target) == "credentials.json":
                Path(target).write_bytes(Path(target).read_bytes() + b" ")

        purge._BEFORE_REPLACE = the_app_writes
        code, text = run(world, "apply", *apply_args(world))
        assert code == 2 and f"Already purged before this: {OLDER}" in text


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
        assert purge.EXPECTED["live"] == {"name": "credentials.json", "count": 99, "digest": "d292eec0cf32e5d6", "rest_digest": "e6addf97158d0760"}
        assert purge.EXPECTED["older_copy"] == {
            "name": "credentials.json.pre-srujan-import-20261005T093705Z", "count": 81,
            "digest": "0b07fefcd1841e85", "rest_digest": "4e1489a37b084e54",
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
