"""The historical company backfill: what it decides, and that it writes one field and nothing else.

The decisions are pure and are pinned as a table. The writes are driven against a real candidate
store in a temporary directory, so a guard that stops working shows up as a changed record.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from features import candidate_store as cs
from features.ollama_invite_extract import clean_company_name

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "interview_company_backfill.py"
spec = importlib.util.spec_from_file_location("interview_company_backfill", SCRIPT)
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)


def reading(company="Capgemini", *, raw="Interview with Capgemini on 2026-08-01", date="2026-08-01", status="ok", verified=None, **extra):
    return {"sha": "a" * 64, "status": status, "company": company, "grounded": bf.grounded(company, raw) if company else False,
            "verified": verified, "date_raw": date, "failures": 0, **extra}


def row(company="", *shas, date="2026-08-01", n_entries=None):
    shots = [{"sha": sha * 64, "pid": f"p{i}", "primary": i == 0, "uploaded_at": "2026-08-01T10:00:00+00:00"} for i, sha in enumerate(shas)]
    return {"company": company, "date": date, "time": "10:00", "slot_confirmed": True,
            "n_entries": len(shots) if n_entries is None else n_entries, "shots": shots}


def decide(r, results, grounding="required"):
    return bf.decide_row(r, results, cleaner=clean_company_name, grounding=grounding)


class TestTheNameChecks:
    @pytest.mark.parametrize("a, b", [("Capgemini", "CAPGEMINI"), ("Wipro Ltd.", "Wipro"), ("Tech  Mahindra", "TechMahindra"),
                                      ("Capgemini", "Capgemini Technology Services"), ("Infosys Limited", "infosys")])
    def test_the_same_company_in_another_spelling_is_compatible(self, a, b):
        assert bf.compatible(a, b) and bf.compatible(b, a)

    @pytest.mark.parametrize("a, b", [("Capgemini", "Wipro"), ("TCS", "Tata Consultancy Services"), ("", "Wipro"), ("Cap", "Capgemini")])
    def test_different_companies_are_not(self, a, b):
        assert not bf.compatible(a, b)

    def test_grounding_needs_the_name_in_the_readers_own_transcription(self):
        assert bf.grounded("Capgemini", "Subject: L1 | Java | CAPGEMINI India")
        assert bf.grounded("Tech Mahindra", "from TechMahindra recruiting")
        assert not bf.grounded("Capgemini", "Teams meeting, Java L1")
        assert not bf.grounded("Capgemini", "")
        assert not bf.grounded("AB", "AB ab AB")          # too short to mean anything

    def test_date_relation(self):
        assert bf.date_relation("2026-08-01", "2026-08-01") == "match"
        assert bf.date_relation("2026-08-02", "2026-08-01") == "mismatch"
        assert bf.date_relation("2026-09-01", "2026-08-01") == "mismatch"
        # the invite shows no year and the model guessed one: same day and month is the same interview
        assert bf.date_relation("2023-08-01", "2026-08-01") == "match_day_month"
        assert bf.date_relation("2027-08-01", "2026-08-01") == "match_day_month"
        assert bf.date_relation("", "2026-08-01") == "unknown"
        assert bf.date_relation("tomorrow", "2026-08-01") == "unknown"
        assert bf.date_relation("2026-08-01", "") == "unknown"


class TestTheQuoteCheck:
    @pytest.mark.parametrize("answer, ok", [
        ({"found": True, "quote": "Interview with CAPGEMINI India"}, True),
        ({"found": True, "quote": "Capgemini Technology Services"}, True),
        ({"found": True, "quote": ""}, False),                       # it said yes but showed nothing
        ({"found": True, "quote": "Interview with Infosys"}, False),  # it quoted something else
        ({"found": False, "quote": "Capgemini"}, False),
        ({"found": "yes", "quote": "Capgemini"}, False),            # only a real true counts
        (None, False), ("Capgemini", False), ({}, False),
    ])
    def test_the_model_must_quote_words_that_contain_the_name(self, answer, ok):
        assert bf.quote_supports("Capgemini", answer) is ok

    def test_a_very_short_name_is_not_confirmed_by_a_quote(self):
        assert bf.quote_supports("AB", {"found": True, "quote": "AB AB AB"}) is False

class TestCheckpoints:
    def line(self, status, **extra):
        return {"sha": "s1", "status": status, **extra}

    def test_a_success_stands_over_an_earlier_failure(self):
        standing = bf.collapse_results([self.line("failed"), self.line("ok", company="X")])
        assert standing["s1"]["status"] == "ok" and standing["s1"]["failures"] == 1

    def test_failures_are_counted_until_the_file_is_called_unreadable(self):
        standing = bf.collapse_results([self.line("failed"), self.line("failed"), self.line("failed")])
        assert standing["s1"]["failures"] == 3
        kind, _ = bf.evidence_for(row("", "s"), {"sha": "s1"}, standing["s1"])
        assert kind == "unreadable"

    def test_fewer_failures_leave_it_pending_for_another_try(self):
        standing = bf.collapse_results([self.line("failed")])
        assert bf.evidence_for(row("", "s"), {"sha": "s1"}, standing["s1"])[0] == "pending"

    def test_a_late_failure_does_not_undo_a_success(self):
        standing = bf.collapse_results([self.line("ok", company="X"), self.line("failed")])
        assert standing["s1"]["status"] == "ok"


class TestTheDecisionPerRecord:
    def results(self, **by_char):
        return {ch * 64: {**reading(**kw), "sha": ch * 64} for ch, kw in by_char.items()}

    def test_a_missing_company_is_filled_when_the_screenshot_names_one(self):
        out = decide(row("", "a"), self.results(a={}))
        assert (out["action"], out["after"]) == ("fill", "Capgemini")

    def test_an_existing_valid_company_is_kept(self):
        assert decide(row("Capgemini", "a"), self.results(a={}))["action"] == "already_correct"
        assert decide(row("Capgemini Technology Services", "a"), self.results(a={}))["action"] == "already_correct"
        assert decide(row("Wipro", "a"), self.results(a={"company": ""}))["action"] == "kept_existing"

    def test_an_existing_valid_company_that_differs_goes_to_review_and_is_not_overwritten(self):
        out = decide(row("Wipro", "a"), self.results(a={}))
        assert out["action"] == "review_conflict_with_existing" and out["before"] == "Wipro"

    @pytest.mark.parametrize("before", ["HirePro", "N/A", "Microsoft Teams"])
    def test_an_invalid_existing_value_is_corrected_by_clear_evidence(self, before):
        out = decide(row(before, "a"), self.results(a={}))
        assert (out["action"], out["after"]) == ("correct_invalid", "Capgemini")

    def test_an_invalid_existing_value_with_no_evidence_is_left_for_review(self):
        assert decide(row("HirePro", "a"), self.results(a={"company": ""}))["action"] == "invalid_unresolved"

    def test_a_screenshot_that_names_no_company_changes_nothing(self):
        assert decide(row("", "a"), self.results(a={"company": ""}))["action"] == "no_company_visible"

    def test_a_company_nothing_confirms_is_never_written(self):
        out = decide(row("", "a"), self.results(a={"raw": "Teams meeting L1"}))
        assert out["action"] == "review_company_not_confirmed"
        out = decide(row("", "a"), self.results(a={"raw": "", "verified": False}))
        assert out["action"] == "review_company_not_confirmed"
        # Only when grounding is switched to advisory (a deliberate choice, recorded in the plan) does it write.
        assert decide(row("", "a"), self.results(a={"raw": "Teams meeting L1"}), grounding="advisory")["action"] == "fill"

    def test_a_quote_from_the_image_confirms_it_when_the_transcription_is_empty(self):
        """The reader usually leaves its transcription empty; the second read is what supports the name."""
        out = decide(row("", "a"), self.results(a={"raw": "", "verified": True}))
        assert (out["action"], out["after"]) == ("fill", "Capgemini")

    def test_a_screenshot_of_another_date_goes_to_review(self):
        out = decide(row("", "a", date="2026-08-01"), self.results(a={"date": "2026-09-15"}))
        assert out["action"] == "review_date_mismatch"
        assert decide(row("", "a", date="2026-08-01"), self.results(a={"date": "2026-08-03"}))["action"] == "review_date_mismatch"

    def test_a_guessed_year_is_not_a_date_mismatch(self):
        assert decide(row("", "a", date="2026-09-18"), self.results(a={"date": "2023-09-18"}))["action"] == "fill"

    def test_an_unreadable_date_does_not_block_a_grounded_company(self):
        assert decide(row("", "a"), self.results(a={"date": ""}))["action"] == "fill"

    def test_two_screenshots_that_agree_write_the_fuller_name(self):
        out = decide(row("", "a", "b"), self.results(a={"company": "Capgemini"}, b={"company": "Capgemini Technology Services", "raw": "Capgemini Technology Services"}))
        assert (out["action"], out["after"]) == ("fill", "Capgemini Technology Services")

    def test_two_screenshots_that_disagree_write_nothing(self):
        out = decide(row("", "a", "b"), self.results(a={"company": "Capgemini"}, b={"company": "Infosys", "raw": "Infosys"}))
        assert out["action"] == "review_conflict_between_screenshots" and out["after"] == ""

    def test_a_usable_screenshot_next_to_one_naming_nothing_still_writes(self):
        out = decide(row("", "a", "b"), self.results(a={"company": "Capgemini"}, b={"company": ""}))
        assert out["action"] == "fill"

    def test_payment_screenshots_and_non_invites_are_not_evidence(self):
        assert decide(row("", "a"), self.results(a={"company": "", "status": "payment_screenshot"}))["action"] == "not_an_invite"
        assert decide(row("", "a"), self.results(a={"company": "", "status": "not_an_invite"}))["action"] == "not_an_invite"

    def test_a_record_with_no_screenshot_or_no_file_is_reported_not_guessed(self):
        assert decide(row(""), {})["action"] == "no_screenshot"
        assert decide(row("", n_entries=2), {})["action"] == "file_missing"

    def test_a_screenshot_not_read_yet_is_pending_and_never_written(self):
        assert decide(row("", "a"), {})["action"] == "pending"

    def test_the_plan_counts_every_record_once(self):
        inventory = {"rows": {"c1": row("", "a"), "c2": row("Wipro", "a"), "c3": row("")}}
        plan = bf.build_plan(inventory, self.results(a={}), cleaner=clean_company_name)
        assert plan["summary"] == {"fill": 1, "review_conflict_with_existing": 1, "no_screenshot": 1}
        assert len(plan["items"]) == 3


# ---------------------------------------------------------------------------------------------
# Writes, against a real store
# ---------------------------------------------------------------------------------------------

@pytest.fixture()
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)
    ids = {}
    for name in ("Asha Test", "Vikram Test", "Devi Test"):
        made = cs.create_candidate({"name": name, "phone": "9000000" + str(100 + len(ids)), "service_type": "profile_service",
                                    # Within the booking date guard (180 days ahead), whenever the suite runs.
                                    "interview_round": "L1", "date": (datetime.now(timezone.utc).date() + timedelta(days=1 + len(ids))).isoformat(), "time": "10:00",
                                    "time_end": "11:00"})
        ids[name] = made["id"]
    return ids


def write_plan(tmp_path, items):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"items": items}), encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def run(capsys, *argv):
    code = bf.main(list(argv))
    return code, capsys.readouterr().out


def live(cid):
    return next(r for r in cs._load(force=True)["candidates"] if r["id"] == cid)


class TestApply:
    def plan_items(self, store):
        return [{"cid": store["Asha Test"], "action": "fill", "before": "", "after": "Capgemini"},
                {"cid": store["Vikram Test"], "action": "review_conflict_with_existing", "before": "Wipro", "after": "Infosys"}]

    def test_it_refuses_a_plan_that_is_not_the_reviewed_one(self, store, tmp_path, capsys):
        path, _ = write_plan(tmp_path, self.plan_items(store))
        code, out = run(capsys, "apply", "--plan", str(path), "--expect-plan-sha256", "0" * 64, "--confirm-count", "1", "--apply")
        assert code == 2 and "not the one that was reviewed" in out
        assert not live(store["Asha Test"]).get("interview_company")

    def test_it_refuses_when_the_number_of_writes_is_not_the_reviewed_number(self, store, tmp_path, capsys):
        path, digest = write_plan(tmp_path, self.plan_items(store))
        code, out = run(capsys, "apply", "--plan", str(path), "--expect-plan-sha256", digest, "--confirm-count", "2", "--apply")
        assert code == 2 and "--confirm-count" in out
        assert not live(store["Asha Test"]).get("interview_company")

    def test_without_apply_nothing_is_written(self, store, tmp_path, capsys):
        path, digest = write_plan(tmp_path, self.plan_items(store))
        code, out = run(capsys, "apply", "--plan", str(path), "--expect-plan-sha256", digest, "--confirm-count", "1")
        assert code == 0 and '"would_write": 1' in out
        assert not live(store["Asha Test"]).get("interview_company")

    def test_it_writes_the_one_field_on_the_planned_rows_only(self, store, tmp_path, capsys):
        before = {cid: json.loads(json.dumps(live(cid))) for cid in store.values()}
        path, digest = write_plan(tmp_path, self.plan_items(store))
        code, out = run(capsys, "apply", "--plan", str(path), "--expect-plan-sha256", digest, "--confirm-count", "1", "--apply")
        assert code == 0 and '"written": 1' in out
        assert live(store["Asha Test"])["interview_company"] == "Capgemini"
        for name, cid in store.items():
            now = live(cid)
            changed = {k for k in set(before[cid]) | set(now) if before[cid].get(k) != now.get(k)} - bf.BOOKKEEPING
            assert changed == ({"interview_company"} if name == "Asha Test" else set()), name
        # a row whose action is a review is never written, whatever it carries as "after"
        assert not live(store["Vikram Test"]).get("interview_company")

    def test_it_is_idempotent(self, store, tmp_path, capsys):
        path, digest = write_plan(tmp_path, self.plan_items(store))
        args = ("apply", "--plan", str(path), "--expect-plan-sha256", digest, "--confirm-count", "1", "--apply")
        run(capsys, *args)
        code, out = run(capsys, *args)
        assert code == 0 and '"already_set": 1' in out and "written" not in json.loads(out.strip().splitlines()[-1])["counts"]

    def test_it_does_not_overwrite_a_value_that_appeared_after_the_plan_was_made(self, store, tmp_path, capsys):
        path, digest = write_plan(tmp_path, self.plan_items(store))
        cs._patch_row_fields(store["Asha Test"], {"interview_company": "Wipro"})   # someone typed one meanwhile
        code, out = run(capsys, "apply", "--plan", str(path), "--expect-plan-sha256", digest, "--confirm-count", "1", "--apply")
        assert code == 0 and "skipped_changed_since_plan" in out
        assert live(store["Asha Test"])["interview_company"] == "Wipro"

    def test_a_row_that_is_gone_is_skipped_not_created(self, store, tmp_path, capsys):
        items = [{"cid": "does-not-exist", "action": "fill", "before": "", "after": "Capgemini"}]
        path, digest = write_plan(tmp_path, items)
        code, out = run(capsys, "apply", "--plan", str(path), "--expect-plan-sha256", digest, "--confirm-count", "1", "--apply")
        assert code == 0 and "row_gone" in out
        assert len(cs._load(force=True)["candidates"]) == 3


class TestVerify:
    def snapshot_file(self, capsys, tmp_path):
        code, out = run(capsys, "snapshot")
        assert code == 0
        path = tmp_path / "before.jsonl"
        path.write_text(out, encoding="utf-8")
        footer = json.loads(out.strip().splitlines()[-1])
        assert footer["count"] == 3 and footer["_snapshot_footer"]
        return path

    def test_it_confirms_only_the_planned_field_changed(self, store, tmp_path, capsys):
        before = self.snapshot_file(capsys, tmp_path)
        items = [{"cid": store["Asha Test"], "action": "fill", "before": "", "after": "Capgemini"}]
        plan, digest = write_plan(tmp_path, items)
        run(capsys, "apply", "--plan", str(plan), "--expect-plan-sha256", digest, "--confirm-count", "1", "--apply")
        code, out = run(capsys, "verify", "--before", str(before), "--plan", str(plan))
        result = json.loads(out)
        assert result["outcome"] == {"company_written_as_planned": 1, "unchanged": 2, "new_rows_since_snapshot": 0}

    def test_it_reports_a_change_to_anything_else(self, store, tmp_path, capsys):
        before = self.snapshot_file(capsys, tmp_path)
        cs._patch_row_fields(store["Devi Test"], {"notes": "edited by someone"})
        plan, _ = write_plan(tmp_path, [])
        _, out = run(capsys, "verify", "--before", str(before), "--plan", str(plan))
        result = json.loads(out)
        assert result["outcome"]["other_fields_changed"] == 1 and result["other_fields_changed_by_key"] == {"notes": 1}

    def test_it_reports_a_deleted_record(self, store, tmp_path, capsys):
        before = self.snapshot_file(capsys, tmp_path)
        data = cs._load(force=True)
        data["candidates"] = [r for r in data["candidates"] if r["id"] != store["Devi Test"]]
        cs._save(data)
        plan, _ = write_plan(tmp_path, [])
        _, out = run(capsys, "verify", "--before", str(before), "--plan", str(plan))
        assert json.loads(out)["outcome"].get("DELETED") == 1


class TestTheInventory:
    def test_it_lists_every_row_including_ones_without_a_screenshot(self, store, capsys):
        code, out = run(capsys, "inventory")
        inventory = json.loads(out)
        assert code == 0 and len(inventory["rows"]) == 3
        assert all(r["n_entries"] == 0 and r["shots"] == [] for r in inventory["rows"].values())

    def test_a_screenshot_is_found_hashed_and_shared_by_rows_that_attach_the_same_file(self, store, capsys):
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 64
        for name in ("Asha Test", "Vikram Test"):
            cs.attach_public_slot_screenshot(store[name], data=png, original_name="slot-screenshot.png", mime_type="image/png")
        inventory = json.loads(run(capsys, "inventory")[1])
        sha = hashlib.sha256(png).hexdigest()
        assert list(inventory["files"]) == [sha]
        assert inventory["files"][sha]["mime"] == "image/png"
        assert len(inventory["files"][sha]["uploads"]) == 2
        assert inventory["rows"][store["Asha Test"]]["shots"][0]["sha"] == sha
        assert inventory["missing"] == []


class TestTheCourtesyCheck:
    def log(self, tmp_path, minutes_ago, path="/public/slots/extract-invite-ai", ip="203.0.113.7"):
        when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        line = f'{ip} - - [{when:%d/%b/%Y:%H:%M:%S +0000}] "POST {path} HTTP/1.1" 200 12 "-" "x"\n'
        target = tmp_path / "access.log"
        target.write_text("old line\n" + line, encoding="utf-8")
        return (str(target),)

    def test_a_person_reading_an_invite_right_now_is_seen(self, tmp_path):
        assert bf.live_ai_requests_in_last(150, self.log(tmp_path, 1)) == 1

    def test_an_old_request_an_internal_address_or_another_path_is_not(self, tmp_path):
        assert bf.live_ai_requests_in_last(150, self.log(tmp_path, 10)) == 0
        assert bf.live_ai_requests_in_last(150, self.log(tmp_path, 1, ip="172.18.0.4")) == 0
        assert bf.live_ai_requests_in_last(150, self.log(tmp_path, 1, path="/health")) == 0


def test_the_tool_has_no_delete_and_no_other_write_than_the_one_field():
    body = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("DELETE FROM", "os.remove", "os.unlink", "shutil.rmtree", "_save(", "delete_candidate", "docker stop", "docker restart", "docker start"):
        assert forbidden not in body, forbidden
    # Two writes in all: apply's and rollback's, and both are the company field and nothing else.
    calls = re.findall(r"_patch_row_fields\(([^\n]*)\)", body)
    assert len(calls) == 2 and all('{"interview_company": ' in call for call in calls), calls


# ---------------------------------------------------------------------------------------------
# The faster read
# ---------------------------------------------------------------------------------------------

class TestThePlanRechecksEveryClaim:
    """Four early readings said "Unknown company" and a quote check alone confirmed them (the model quoted its own placeholder)."""

    @pytest.mark.parametrize("claim", ["Unknown company", "Not specified in the invite", "HirePro", "Hiring company"])
    def test_a_confirmed_placeholder_is_not_a_company(self, claim):
        results = {"a" * 64: {**reading(claim, verified=True), "sha": "a" * 64}}
        out = decide(row("", "a"), results)
        assert out["action"] == "no_company_visible" and out["after"] == ""

    def test_a_real_company_still_passes_the_same_check(self):
        results = {"a" * 64: {**reading("Capgemini", verified=True, raw=""), "sha": "a" * 64}}
        assert decide(row("", "a"), results)["action"] == "fill"


class TestImagePreparation:
    def png(self, width, height):
        import io
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (width, height), (240, 240, 240)).save(buffer, "PNG")
        return buffer.getvalue()

    def tokens(self, data):
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            import math
            return math.ceil(image.width / 32) * math.ceil(image.height / 32)

    def test_a_large_screenshot_is_scaled_down_to_about_the_budget(self):
        original = self.png(1200, 2400)                     # ~2,850 visual tokens
        data, scale = bf.prepare_image(original)
        assert scale < 1 and data != original
        assert self.tokens(data) <= bf.IMAGE_TOKEN_BUDGET * 1.1

    def test_a_small_screenshot_is_sent_as_it_is_never_scaled_up(self):
        original = self.png(700, 600)
        data, scale = bf.prepare_image(original)
        assert data == original and scale == 1.0

    def test_the_budget_is_the_one_that_was_measured_not_a_smaller_one(self):
        """800 tokens made the model invent companies on 2 of 9 files; 1,200 read like the original."""
        assert bf.IMAGE_TOKEN_BUDGET == 1200


class FakeAsk:
    """A stand-in for ask_nodes. It records every question, the image and the node order it was given."""

    def __init__(self, first=None, second=None, *, first_error="", second_error="", unavailable_at=None, node="rtx4060"):
        self.first, self.second = first, second
        self.first_error, self.second_error, self.unavailable_at, self.node = first_error, second_error, unavailable_at, node
        self.calls = []

    def __call__(self, model, b64, prompt, order):
        self.calls.append({"model": model, "b64": b64, "prompt": prompt, "order": list(order)})
        index = len(self.calls)
        if self.unavailable_at == index:
            return {"answer": None, "node": "", "error": "rtx4060: unreachable", "unavailable": True, "tried": []}
        error = self.first_error if index == 1 else self.second_error
        if error:
            return {"answer": None, "node": self.node, "error": error, "unavailable": False, "tried": []}
        return {"answer": dict(self.first if index == 1 else self.second), "node": self.node, "error": "",
                "unavailable": False, "tried": []}


def read(ask, model="qwen3-vl:8b-instruct", order=("rtx4060", "jagadeesh", "our_machine")):
    return bf.read_stage(TestImagePreparation().png(800, 800), model=model, order=list(order), ask=ask,
                         clean=clean_company_name)


class TestTheRead:
    def test_a_company_the_second_question_confirms_with_a_quote_is_accepted(self):
        ask = FakeAsk({"is_interview_invite": True, "company": "Capgemini", "company_quote": "Interview with Capgemini", "interview_date": "2026-08-01"},
                      {"found": True, "quote": "Capgemini Technology Services", "role": "employer"})
        out = read(ask)
        assert out["status"] == "ok" and out["company"] == "Capgemini" and out["verified"] is True and out["date_raw"] == "2026-08-01"
        assert out["model"] == "qwen3-vl:8b-instruct" and out["node"] == "rtx4060" and out["node2"] == "rtx4060"
        # Two questions about the SAME image bytes: that is what makes the second one cheap on the node.
        assert len(ask.calls) == 2 and ask.calls[0]["b64"] == ask.calls[1]["b64"]
        assert "Capgemini" in ask.calls[1]["prompt"]

    def test_the_second_question_goes_first_to_the_node_that_read_the_image(self):
        ask = FakeAsk({"is_interview_invite": True, "company": "Capgemini"}, {"found": True, "quote": "Capgemini"}, node="jagadeesh")
        read(ask)
        assert ask.calls[0]["order"] == ["rtx4060", "jagadeesh", "our_machine"]
        assert ask.calls[1]["order"] == ["jagadeesh", "rtx4060", "our_machine"]

    def test_the_first_answers_own_quote_does_not_count_only_the_second_question_does(self):
        ask = FakeAsk({"is_interview_invite": True, "company": "Deloitte", "company_quote": "Deloitte"}, {"found": False, "quote": "", "role": "other"})
        out = read(ask)
        assert out["quote1"] is True and out["verified"] is False
        assert decide(row("", "a"), {"a" * 64: {**out, "sha": "a" * 64}})["action"] == "review_company_not_confirmed"

    def test_no_company_means_no_second_question(self):
        ask = FakeAsk({"is_interview_invite": True, "company": "", "company_quote": ""})
        out = read(ask)
        assert out["company"] == "" and out["verified"] is None and len(ask.calls) == 1

    @pytest.mark.parametrize("claim", ["Unknown company", "Contract", "R1 Interview - Asha Rao", "Microsoft Teams"])
    def test_a_claim_that_is_not_a_company_is_cleaned_before_it_can_cost_a_second_question(self, claim):
        out = read(FakeAsk({"is_interview_invite": True, "company": claim, "company_quote": claim}))
        assert out["company"] == "" and out["verified"] is None

    def test_something_that_is_not_an_invite_never_yields_a_company(self):
        """A payment receipt attached by mistake names a bank, not an employer."""
        ask = FakeAsk({"is_interview_invite": False, "company": "HDFC Bank", "company_quote": "HDFC Bank"})
        out = read(ask)
        assert out["status"] == "not_an_invite" and out["company"] == "" and len(ask.calls) == 1

    def test_a_failed_question_fails_the_file_so_it_is_retried_not_guessed(self):
        out = read(FakeAsk({}, first_error="no parseable answer"))
        assert out["status"] == "failed" and out["error"].startswith("read:")
        out = read(FakeAsk({"is_interview_invite": True, "company": "Capgemini", "company_quote": "Capgemini"}, second_error="TimeoutError"))
        assert out["status"] == "failed" and out["error"].startswith("verification:")

    @pytest.mark.parametrize("at", [1, 2])
    def test_an_outage_is_reported_as_one_not_as_a_failed_reading(self, at):
        out = read(FakeAsk({"is_interview_invite": True, "company": "Capgemini"}, {"found": True, "quote": "Capgemini"}, unavailable_at=at))
        assert out["status"] == "unavailable" and "company" not in out

    def test_what_is_kept_carries_no_quote_and_no_transcription(self):
        ask = FakeAsk({"is_interview_invite": True, "company": "Capgemini", "company_quote": "Asha Rao, Capgemini, 9000012345"},
                      {"found": True, "quote": "Asha Rao, Capgemini, 9000012345", "role": "client"})
        out = read(ask)
        assert out["method"] == "slim+quote" and out["scale"] == 1.0 and out["seconds_first"] >= 0 and out["verify_role"] == "client"
        assert out["quote_len"] > 0 and "Asha" not in json.dumps(out) and "9000012345" not in json.dumps(out)

    def test_the_company_is_asked_for_after_the_context_fields(self):
        """Benchmarked: asked first, the model found 12 of 21 companies the original found; after the context fields, 17 of 18."""
        template = bf.SLIM_PROMPT.split("\n")[1]
        assert template.index('"interview_round"') < template.index('"company"') < template.index('"company_quote"')
        assert template.index('"meeting_platform"') < template.index('"company"')

    def test_the_prompt_still_tells_the_model_what_a_company_is_not(self):
        for words in ("NOT the meeting platform", "Do not guess", "email domain", "is_interview_invite: false"):
            assert words in bf.SLIM_PROMPT

    def test_a_hung_node_is_given_up_in_minutes(self):
        """The slowest node reads on the CPU in about 140 s; past this the next node is asked."""
        assert 140 < bf.CALL_TIMEOUT <= 240


class FakeNodes:
    """The pool, as ollama_nodes._request sees it: per node, its models and what /api/chat does."""

    def __init__(self, **nodes):
        self.nodes = nodes
        self.chats = []

    def __call__(self, node, path, *, method="GET", payload=None, timeout=5):
        spec = self.nodes.get(node)
        if spec is None or spec.get("down"):
            raise ConnectionRefusedError(f"{node} is down")
        if path == "/api/tags":
            return {"models": [{"name": name} for name in spec.get("models", [])]}
        self.chats.append((node, payload, timeout))
        behaviour = spec.get("chat")
        if isinstance(behaviour, Exception):
            raise behaviour
        return {"message": {"content": behaviour}}


def ask(nodes, model="qwen2.5vl:7b", order=("jagadeesh", "our_machine", "rtx4060")):
    return bf.ask_nodes(model, "B64DATA", "a question", list(order), request=nodes, parse=json.loads)


class TestTheNodeFailover:
    ANSWER = json.dumps({"found": True, "quote": "Capgemini"})

    def test_the_first_node_in_the_order_that_carries_the_model_answers(self):
        nodes = FakeNodes(jagadeesh={"models": ["qwen2.5vl:7b"], "chat": self.ANSWER},
                          rtx4060={"models": ["qwen2.5vl:7b"], "chat": self.ANSWER})
        out = ask(nodes)
        assert out["answer"] == {"found": True, "quote": "Capgemini"} and out["node"] == "jagadeesh" and out["error"] == ""
        assert [chat[0] for chat in nodes.chats] == ["jagadeesh"]

    def test_a_node_that_is_down_or_lacks_the_model_is_passed_over(self):
        nodes = FakeNodes(jagadeesh={"down": True}, our_machine={"models": ["qwen3-vl:8b-instruct"]},
                          rtx4060={"models": ["qwen2.5vl:7b:latest", "qwen2.5vl:7b"], "chat": self.ANSWER})
        out = ask(nodes)
        assert out["node"] == "rtx4060" and out["unavailable"] is False
        assert [t.split(":")[0] for t in out["tried"]] == ["jagadeesh", "our_machine"]

    def test_a_node_that_fails_mid_answer_is_passed_over_for_the_next(self):
        nodes = FakeNodes(jagadeesh={"models": ["qwen2.5vl:7b"], "chat": TimeoutError("timed out")},
                          our_machine={"models": ["qwen2.5vl:7b"], "chat": self.ANSWER})
        out = ask(nodes)
        assert out["node"] == "our_machine" and out["answer"]["found"] is True
        assert [chat[0] for chat in nodes.chats] == ["jagadeesh", "our_machine"]

    def test_no_node_reachable_is_an_outage(self):
        out = ask(FakeNodes(jagadeesh={"down": True}, our_machine={"down": True}, rtx4060={"models": ["other"]}))
        assert out["unavailable"] is True and out["answer"] is None and "jagadeesh" in out["error"]

    def test_nodes_that_took_the_question_and_all_failed_is_a_failed_reading_not_an_outage(self):
        nodes = FakeNodes(jagadeesh={"models": ["qwen2.5vl:7b"], "chat": RuntimeError("Ollama returned HTTP 500")})
        out = ask(nodes, order=("jagadeesh",))
        assert out["unavailable"] is False and out["answer"] is None and "RuntimeError" in out["error"]

    def test_an_answer_that_is_not_json_is_a_failed_reading_and_no_other_node_is_shopped_for_one(self):
        nodes = FakeNodes(jagadeesh={"models": ["qwen2.5vl:7b"], "chat": "I think it is Capgemini"},
                          our_machine={"models": ["qwen2.5vl:7b"], "chat": self.ANSWER})
        with pytest.raises(json.JSONDecodeError):
            ask(nodes)            # json.loads stands in for the production parser, which returns None instead
        nodes = FakeNodes(jagadeesh={"models": ["qwen2.5vl:7b"], "chat": "not json"},
                          our_machine={"models": ["qwen2.5vl:7b"], "chat": self.ANSWER})
        out = bf.ask_nodes("qwen2.5vl:7b", "x", "q", ["jagadeesh", "our_machine"], request=nodes, parse=lambda text: None)
        assert out["error"] == "no parseable answer" and out["node"] == "jagadeesh" and len(nodes.chats) == 1

    def test_the_request_is_production_shaped(self):
        from core import ai_gateway
        nodes = FakeNodes(jagadeesh={"models": ["qwen2.5vl:7b"], "chat": self.ANSWER}, rtx4060={"models": ["qwen3-vl:8b-instruct"], "chat": self.ANSWER})
        ask(nodes)
        _, payload, timeout = nodes.chats[0]
        assert payload["model"] == "qwen2.5vl:7b" and payload["messages"][0]["images"] == ["B64DATA"] and payload["format"] == "json"
        # production's own context window: a model already loaded for live reads is not reloaded for a different one
        assert payload["options"]["num_ctx"] == ai_gateway.resolved_num_ctx()
        assert "think" not in payload and timeout == bf.CALL_TIMEOUT          # only models that know the option are sent it
        ask(nodes, model="qwen3-vl:8b-instruct", order=("rtx4060",))
        assert nodes.chats[-1][1]["think"] is False

    def test_the_audit_model_is_kept_off_the_node_that_serves_live_invites_while_another_can_serve_it(self):
        assert bf.AUDIT_ORDER[-1] == "rtx4060" and bf.AUDIT_MODEL != "qwen3-vl:8b-instruct"


# ---------------------------------------------------------------------------------------------
# The second-model audit
# ---------------------------------------------------------------------------------------------

def audited(company="Capgemini", *, verified=True, status="ok"):
    return {"sha": "a" * 64, "status": status, "company": company, "grounded": False, "verified": verified, "date_raw": ""}


class TestTheSecondModelMustAgree:
    FIRST = {"a" * 64: {**reading("Capgemini", verified=True, raw=""), "sha": "a" * 64}}

    def plan(self, audit):
        return decide(row("", "a"), self.FIRST) if audit is None else bf.decide_row(row("", "a"), self.FIRST, cleaner=clean_company_name, audit=audit)

    def test_without_an_audit_the_fill_stands_this_is_the_preview(self):
        assert self.plan(None)["action"] == "fill"

    def test_a_second_model_that_names_the_same_company_keeps_the_write(self):
        out = self.plan({"a" * 64: audited("Capgemini Technology Services")})
        assert out["action"] == "fill" and out["audit"] == "confirmed"

    def test_a_second_model_that_names_a_different_company_turns_it_into_a_review(self):
        out = self.plan({"a" * 64: audited("Infosys")})
        assert out["action"] == "review_second_model_disagrees" and out["audit"] == "conflict"

    @pytest.mark.parametrize("second", [None, audited(""), audited("Capgemini", verified=False), audited("Capgemini", status="failed")])
    def test_a_second_model_that_found_nothing_confirmed_or_was_never_asked_is_not_a_confirmation(self, second):
        audit = {} if second is None else {"a" * 64: second}
        out = self.plan(audit)
        assert out["action"] == "review_second_model_not_confirmed" and out["audit"] == "unconfirmed"

    def test_a_placeholder_from_the_second_model_never_confirms_anything(self):
        assert self.plan({"a" * 64: audited("Unknown company")})["action"] == "review_second_model_not_confirmed"

    def test_only_writes_are_audited_everything_else_is_untouched(self):
        keeps = bf.decide_row(row("Capgemini", "a"), self.FIRST, cleaner=clean_company_name, audit={})
        assert keeps["action"] == "already_correct" and "audit" not in keeps
        none = bf.decide_row(row("", "b"), {"b" * 64: {**reading("", raw=""), "sha": "b" * 64}}, cleaner=clean_company_name, audit={})
        assert none["action"] == "no_company_visible"

    def test_the_plan_records_that_it_was_audited(self):
        inventory = {"rows": {"c1": row("", "a")}}
        assert bf.build_plan(inventory, self.FIRST, cleaner=clean_company_name)["audited"] is False
        assert bf.build_plan(inventory, self.FIRST, cleaner=clean_company_name, audit={"a" * 64: audited()})["audited"] is True


class TestAGuessedYearNeedsTheSecondModel:
    """18 of 25 date "mismatches" were the right day and month with the year 3 out. They are accepted only when the second
    model independently reads the same day and month off the same screenshot."""

    def first(self, date):
        return {"a" * 64: {**reading("Capgemini", verified=True, raw="", date=date), "sha": "a" * 64}}

    def audit(self, date, company="Capgemini"):
        return {"a" * 64: {**audited(company), "date_raw": date}}

    def decide(self, first_date, audit_date):
        return bf.decide_row(row("", "a", date="2026-09-18"), self.first(first_date), cleaner=clean_company_name, audit=self.audit(audit_date))

    def test_the_second_model_reading_the_same_day_and_month_confirms_it(self):
        assert self.decide("2023-09-18", "2026-09-18")["action"] == "fill"
        assert self.decide("2023-09-18", "2025-09-18")["action"] == "fill"

    @pytest.mark.parametrize("audit_date", ["2026-09-19", "2026-10-18", "", "next Tuesday"])
    def test_a_different_day_or_no_date_from_the_second_model_is_a_review(self, audit_date):
        assert self.decide("2023-09-18", audit_date)["action"] == "review_year_not_confirmed"

    def test_an_exact_date_needs_no_extra_check(self):
        assert self.decide("2026-09-18", "")["action"] == "fill"

    def test_no_date_on_the_first_read_needs_no_extra_check(self):
        assert self.decide("", "")["action"] == "fill"

    def test_without_the_audit_the_preview_still_shows_it_as_a_fill(self):
        assert decide(row("", "a", date="2026-09-18"), self.first("2023-09-18"))["action"] == "fill"

    def test_the_second_model_must_still_agree_on_the_company_first(self):
        out = bf.decide_row(row("", "a", date="2026-09-18"), self.first("2023-09-18"), cleaner=clean_company_name,
                            audit=self.audit("2026-09-18", company="Infosys"))
        assert out["action"] == "review_second_model_disagrees"



class TestBothModelsMustQuote:
    def test_with_require_quote_a_transcription_alone_is_not_evidence(self):
        grounded_only = {"a" * 64: {**reading("Capgemini", raw="Interview with Capgemini"), "sha": "a" * 64}}
        assert bf.decide_row(row("", "a"), grounded_only, cleaner=clean_company_name)["action"] == "fill"
        assert bf.decide_row(row("", "a"), grounded_only, cleaner=clean_company_name, require_quote=True)["action"] == "review_company_not_confirmed"

    def test_with_require_quote_a_quoted_name_is_evidence(self):
        quoted = {"a" * 64: {**reading("Capgemini", raw="", verified=True), "sha": "a" * 64}}
        assert bf.decide_row(row("", "a"), quoted, cleaner=clean_company_name, require_quote=True)["action"] == "fill"

    def test_the_second_model_must_have_quoted_it_a_bare_claim_does_not_confirm(self):
        quoted = {"a" * 64: {**reading("Capgemini", raw="", verified=True), "sha": "a" * 64}}
        unquoted_audit = {"a" * 64: {**audited("Capgemini", verified=None), "grounded": True}}
        out = bf.decide_row(row("", "a"), quoted, cleaner=clean_company_name, audit=unquoted_audit, require_quote=True)
        assert out["action"] == "review_second_model_not_confirmed"


class TestTheDateBasisIsStatedNotAssumed:
    def basis(self, first_date, booked="2026-09-18"):
        first = {"a" * 64: {**reading("Capgemini", verified=True, raw="", date=first_date), "sha": "a" * 64}}
        return bf.decide_row(row("", "a", date=booked), first, cleaner=clean_company_name)["date_basis"]

    def test_an_exact_date_on_the_screenshot(self):
        assert self.basis("2026-09-18") == "exact_date"

    def test_a_guessed_year_is_never_called_screenshot_verified(self):
        assert self.basis("2023-09-18") == "day_month_year_from_record"

    def test_no_date_read_is_said_plainly(self):
        assert self.basis("") == "no_date_on_screenshot"

    def test_only_writes_carry_a_date_basis(self):
        first = {"a" * 64: {**reading("", raw=""), "sha": "a" * 64}}
        assert "date_basis" not in bf.decide_row(row("", "a"), first, cleaner=clean_company_name)


# ---------------------------------------------------------------------------------------------
# Resume, report and review
# ---------------------------------------------------------------------------------------------

def standing(status="ok", company="Capgemini", verified=True, failures=0):
    return {"status": status, "company": company, "verified": verified, "failures": failures}


class TestWhatIsLeftToRead:
    FILES = [{"sha": "a"}, {"sha": "b"}, {"sha": "c"}]

    def test_every_file_gets_its_first_reading_before_any_retry_or_audit(self):
        tasks = bf.pending_tasks(self.FILES, {"a": standing("failed", failures=1), "b": standing()}, {})
        assert [(f["sha"], stage) for f, stage in tasks] == [("c", "primary"), ("a", "primary"), ("b", "audit")]

    def test_only_a_quoted_company_goes_to_the_second_model(self):
        primary = {"a": standing(), "b": standing(verified=False), "c": standing(company="")}
        assert [(f["sha"], stage) for f, stage in bf.pending_tasks(self.FILES, primary, {})] == [("a", "audit")]

    def test_a_file_that_failed_three_times_is_finished_as_unreadable(self):
        primary = {"a": standing("failed", failures=3), "b": standing(company=""), "c": standing(company="")}
        assert bf.pending_tasks(self.FILES, primary, {}) == []

    def test_nothing_is_left_once_every_file_has_its_outcome(self):
        primary = {"a": standing(), "b": standing(), "c": standing("not_an_invite", company="")}
        audit = {"a": standing(), "b": standing("failed", failures=3)}
        assert bf.pending_tasks(self.FILES, primary, audit) == []

    def test_an_outage_neither_finishes_nor_spends_an_attempt(self, tmp_path):
        path = tmp_path / "results.jsonl"
        lines = [{"sha": "a", "stage": "primary", "status": "unavailable"}] * 5 + [{"sha": "b", "stage": "primary", "status": "failed"}]
        path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        primary = bf._load_results(str(path), bf.PRIMARY)
        assert "a" not in primary and primary["b"]["failures"] == 1
        assert [f["sha"] for f, _ in bf.pending_tasks(self.FILES, primary, {})] == ["a", "c", "b"]

    def test_the_two_stages_are_kept_apart_in_one_checkpoint_file(self, tmp_path):
        path = tmp_path / "results.jsonl"
        lines = [{"sha": "a", "stage": "primary", "status": "ok", "company": "X"},
                 {"sha": "a", "stage": "audit", "status": "failed"},
                 {"sha": "b", "status": "ok", "company": "Y"}]                # an older line without a stage is a primary
        path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
        assert set(bf._load_results(str(path), bf.PRIMARY)) == {"a", "b"}
        assert bf._load_results(str(path), bf.AUDIT)["a"]["failures"] == 1


class TestTheReport:
    def test_every_decision_lands_in_exactly_one_bucket(self):
        actions = [action for actions in bf.BUCKETS.values() for action in actions]
        assert len(actions) == len(set(actions))
        # every action the decision code can return
        source = SCRIPT.read_text(encoding="utf-8")
        returned = set(re.findall(r'"action": "([a-z_]+)"', source)) | set(re.findall(r'\("[a-z_]+", "(review_[a-z_]+)"\)', source))
        returned |= {"review_second_model_disagrees", "review_second_model_not_confirmed", "review_year_not_confirmed", "pending"}
        assert returned <= set(actions), returned - set(actions)

    def test_the_totals(self):
        plan = {"items": [{"action": a} for a in (
            "fill", "correct_invalid", "already_correct", "no_company_visible", "not_an_invite", "review_conflict_with_existing",
            "invalid_unresolved", "unreadable", "pending", "no_screenshot", "file_missing")]}
        assert bf.summarise_plan(plan, written=2) == {
            "records": 11, "eligible_with_screenshot": 9, "scanned": 8, "to_update": 2, "skipped": 3, "review": 2,
            "failed": 1, "pending": 1, "not_eligible": 2, "updated": 2}


class TestThePeopleCheck:
    def test_a_company_that_is_the_candidates_own_name_is_never_written(self):
        inventory = {"rows": {"c1": row("", "a"), "c2": row("", "b")}}
        results = {"a" * 64: {**reading("Asha Rao", verified=True), "sha": "a" * 64},
                   "b" * 64: {**reading("Capgemini", verified=True), "sha": "b" * 64}}
        plan = bf.build_plan(inventory, results, cleaner=clean_company_name, require_quote=True,
                             people={"c1": ("Asha Rao",), "c2": ("Asha Rao",)})
        actions = {item["cid"]: item["action"] for item in plan["items"]}
        assert actions == {"c1": "no_company_visible", "c2": "fill"}

    def test_a_recorded_value_that_is_not_a_company_is_corrected_from_a_confirmed_screenshot(self):
        results = {"a" * 64: {**reading("Virtusa", verified=True), "sha": "a" * 64}}
        out = decide(row("Contract", "a"), results)
        assert out["action"] == "correct_invalid" and out["after"] == "Virtusa"
        # with no screenshot naming one it is left as it is, for a person
        assert decide(row("Contract", "a"), {"a" * 64: {**reading(""), "sha": "a" * 64}})["action"] == "invalid_unresolved"

    def test_a_recorded_company_is_never_replaced_even_by_two_agreeing_models(self):
        results = {"a" * 64: {**reading("Infosys", verified=True), "sha": "a" * 64}}
        audit = {"a" * 64: audited("Infosys")}
        out = bf.decide_row(row("Wipro", "a"), results, cleaner=clean_company_name, audit=audit, require_quote=True)
        assert out["action"] == "review_conflict_with_existing"


class TestTheApplyLogAndRollback:
    def apply(self, store, tmp_path, capsys):
        items = [{"cid": store["Asha Test"], "action": "fill", "before": "", "after": "Capgemini"},
                 {"cid": store["Vikram Test"], "action": "fill", "before": "", "after": "Infosys"}]
        path, digest = write_plan(tmp_path, items)
        code, out = run(capsys, "apply", "--plan", str(path), "--expect-plan-sha256", digest, "--confirm-count", "2", "--apply")
        assert code == 0
        applied = tmp_path / "applied.jsonl"
        applied.write_text(out, encoding="utf-8")
        return applied, path

    def test_each_write_is_logged_with_what_it_replaced(self, store, tmp_path, capsys):
        applied, _ = self.apply(store, tmp_path, capsys)
        written = [json.loads(line) for line in applied.read_text(encoding="utf-8").splitlines() if '"written"' in line and "cid" in line]
        assert {(w["cid"], w["before"], w["after"]) for w in written} == {
            (store["Asha Test"], "", "Capgemini"), (store["Vikram Test"], "", "Infosys")}

    def test_rollback_restores_only_what_nobody_has_changed_since(self, store, tmp_path, capsys):
        applied, _ = self.apply(store, tmp_path, capsys)
        cs._patch_row_fields(store["Vikram Test"], {"interview_company": "Wipro"})     # newer, typed by a person
        code, out = run(capsys, "rollback", "--applied", str(applied))
        assert code == 0 and json.loads(out)["counts"] == {"would_restore": 1, "changed_since_kept": 1}
        assert live(store["Asha Test"])["interview_company"] == "Capgemini"           # a dry run writes nothing
        code, out = run(capsys, "rollback", "--applied", str(applied), "--apply")
        assert code == 0 and json.loads(out)["counts"] == {"restored": 1, "changed_since_kept": 1}
        assert live(store["Asha Test"]).get("interview_company", "") == ""
        assert live(store["Vikram Test"])["interview_company"] == "Wipro"

    def test_the_report_counts_the_logged_writes(self, store, tmp_path, capsys):
        applied, plan = self.apply(store, tmp_path, capsys)
        code, out = run(capsys, "report", "--plan", str(plan), "--applied", str(applied))
        assert code == 0 and json.loads(out)["updated"] == 2 and json.loads(out)["to_update"] == 2


class TestTheReviewList:
    def test_it_names_the_record_the_reason_and_the_screenshot_to_open(self, store, tmp_path, capsys):
        cid = store["Asha Test"]
        inventory = tmp_path / "inventory.json"
        inventory.write_text(json.dumps({"rows": {cid: {"shots": [{"sha": "a" * 64, "pid": "p1"}]}}}), encoding="utf-8")
        plan, _ = write_plan(tmp_path, [
            {"cid": cid, "action": "review_conflict_with_existing", "before": "Wipro", "after": "Infosys",
             "evidence": [{"sha": "aaaaaaaa", "kind": "usable", "company": "Infosys"}]},
            {"cid": store["Vikram Test"], "action": "fill", "before": "", "after": "Capgemini", "evidence": []},
        ])
        code, out = run(capsys, "review", "--plan", str(plan), "--inventory", str(inventory))
        lines = out.strip().splitlines()
        assert code == 0 and len(lines) == 2 and lines[0].startswith("candidate_id\tcandidate")
        fields = lines[1].split("\t")
        assert fields[0] == cid and fields[1] == "Asha Test" and fields[4] == "Wipro" and fields[5] == "Infosys"
        assert "different company" in fields[7] and fields[8] == f"/candidates/{cid}/attachments/slot_screenshot_proof/p1"
