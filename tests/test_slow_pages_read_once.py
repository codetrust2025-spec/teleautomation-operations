"""The slow pages of 12 Oct: each read of the store does its work once.

Measured on production data (286 rows, 24 mailboxes) before the fix:

* every row's computed fields asked how many interviews that person already had
  booked, and each ask walked the whole store -- 21 s for the booking form's
  name list, 5 s for the Candidates stats, 1.5 s for the Candidates list;
* the mailbox health read worked out each unresolved mailbox's candidate by
  recomputing the whole candidate list, one mailbox at a time -- 16-19 s per
  read, and the mail scheduler did the same every few seconds;
* those reads ran on the API's only event loop, so every other page waited.

These pin the fixes without pinning the timings: the work is done once per
read, the answers are the same as before, and the loop stays free.
Names are invented.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time

import pytest

from features import candidate_store as cs

DAY = "2099-09-1{}"


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "candidate-proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr(cs, "INTERVIEW_DATE_MAX_DAYS_AHEAD", 10 ** 6)
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    return tmp_path


def _seed(people: int = 8) -> list[dict]:
    """Profile candidates with standing and ended slot rows, plus round-wise rows."""
    created = []
    for i in range(people):
        created.append(cs.create_candidate({
            "name": f"Seeded Person {i}", "phone": f"90000001{i:02d}", "service_type": "profile_service",
            "technology": "Java", "reference": "Owner", "stage": "in_progress",
            "payment": 5000 * (i % 5), "expected_payment": 20000, "logged_date": "2099-09-01",
        }))
    created.append(cs.create_candidate({
        "name": "Round Person", "phone": "9000000299", "service_type": "round_wise", "technology": "Python",
        "reference": "Owner", "stage": "in_progress", "payment": 2000, "expected_payment": 2000,
        "logged_date": "2099-09-01",
    }))
    data = cs._load(force=True)
    rows = data["candidates"]
    for i, person in enumerate(created[:people]):
        base = next(r for r in rows if r["id"] == person["id"])
        for j in range(i % 3):
            clone = json.loads(json.dumps(base))
            clone.update({
                "id": f"{person['id']}-slot{j}", "date": DAY.format(j), "time": "10:00", "time_end": "10:30",
                "slot_confirmed": True, "slots_group_posted": True, "interview_round": f"L{j + 1}",
                "interview_attendance_status": "cancelled" if (i + j) % 4 == 3 else "",
            })
            rows.append(clone)
    cs._save(data)
    cs._load_cache = None
    return created


def _without_memo(monkeypatch, fn):
    with monkeypatch.context() as m:
        m.setattr(cs, "_snapshot_memo", lambda _name, _build: None)
        cs._load_cache = None
        return fn()


def _same(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class TestTheAnswersAreUnchanged:
    @pytest.mark.parametrize("read", [
        lambda: cs.list_candidates(),
        lambda: cs.list_candidates(month="2099-09"),
        lambda: cs.list_candidates(stage="in_progress", month="all"),
        lambda: cs.stats(),
        lambda: cs.stats(month="2099-09"),
        lambda: cs.pending_works(),
        lambda: cs.interview_slot_picker_rows(),
        lambda: cs.public_booked_interview_slots(days=10 ** 5),
    ])
    def test_shared_reads_answer_exactly_what_row_by_row_reads_did(self, store, monkeypatch, read):
        _seed()
        before = _same(_without_memo(monkeypatch, read))
        cs._load_cache = None
        assert _same(read()) == before

    def test_the_booking_lock_still_counts_standing_slots(self, store):
        people = _seed()
        # Person 2 has two slot rows, one of them cancelled ((2+1) % 4 == 3).
        name = people[2]["name"]
        assert cs.profile_booked_slot_count(name) == 1
        with cs.shared_read_snapshot():
            assert cs.profile_booked_slot_count(name) == 1
            assert cs.profile_booked_slot_count(people[0]["name"]) == 0


class TestTheWorkIsDoneOnce:
    def test_listing_no_longer_walks_the_store_once_per_row(self, store, monkeypatch):
        _seed(people=12)
        rows = len(cs._load(force=True)["candidates"])
        calls = {"n": 0}
        real = cs.slot_still_stands

        def counting(row):
            calls["n"] += 1
            return real(row)

        monkeypatch.setattr(cs, "slot_still_stands", counting)
        cs.list_candidates()
        # Row by row this was at least 2 x rows x rows (two asks per row, each
        # walking every row); once per read it grows with the rows.
        assert calls["n"] < rows * 6
        assert calls["n"] < 2 * rows * rows

    def test_a_stored_row_is_computed_once_per_read(self, store, monkeypatch):
        _seed()
        cs._load_cache = None
        snapshot = cs._load()
        stored = {id(row) for row in snapshot["candidates"]}
        calls = {"stored": 0}
        real = cs._compute_row

        def counting(row):
            if id(row) in stored:
                calls["stored"] += 1
            return real(row)

        monkeypatch.setattr(cs, "_compute_row", counting)
        monkeypatch.setattr(cs, "_load", lambda force=False: snapshot)
        cs.stats()
        once = calls["stored"]
        calls["stored"] = 0
        with monkeypatch.context() as m:
            m.setattr(cs, "_snapshot_memo", lambda _name, _build: None)
            cs.stats()
        row_by_row = calls["stored"]
        # Merged profile rows are new objects and always computed fresh; every
        # stored row is computed once however many passes the stats take.
        assert once <= len(stored)
        assert row_by_row >= 3 * once

    def test_each_caller_gets_its_own_copy(self, store):
        _seed()
        with cs.shared_read_snapshot():
            first = cs.list_candidates()
            first[0]["annotated_by_a_caller"] = True
            first[0]["payment_proofs"] = ["changed"]
            second = cs.list_candidates()
        assert "annotated_by_a_caller" not in second[0]
        assert second[0]["payment_proofs"] != ["changed"]

    def test_a_save_inside_a_read_is_seen_by_the_next_question(self, store):
        people = _seed()
        name = people[0]["name"]
        with cs.shared_read_snapshot():
            assert cs.profile_booked_slot_count(name) == 0
            cs.assign_interview_slot(
                candidate_id=people[0]["id"], date="2099-10-01", time="11:00", time_end="11:30",
                interview_company="Fresh Client",
            )
            assert cs.profile_booked_slot_count(name) == 1

    def test_nothing_is_kept_between_reads(self, store):
        _seed()
        with cs.shared_read_snapshot():
            cs.list_candidates()
        assert cs._READ_SNAPSHOT.get() is None


class TestMailboxStagesAreResolvedInBulk:
    def test_unresolved_mailboxes_cost_one_lookup_not_one_list_each(self, store, monkeypatch):
        from core import recruitment_mail_store as mail

        people = _seed()
        monkeypatch.setattr(mail, "_TERMINAL_MEMO", None)
        cs.update_candidate(people[3]["id"], {"stage": "completed"})
        bulk_calls = []
        real_bulk = cs.canonical_candidate_identity_ids

        def bulk(ids):
            bulk_calls.append(list(ids))
            return {cid: people[3]["id"] for cid in ids}

        def scalar(_cid):
            raise AssertionError("resolved one mailbox at a time")

        monkeypatch.setattr(cs, "canonical_candidate_identity_ids", bulk)
        monkeypatch.setattr(cs, "canonical_candidate_identity_id", scalar)
        rows = [
            {"id": "mb-known", "candidate_id": people[0]["id"], "canonical_candidate_id": people[0]["id"]},
            {"id": "mb-alias-1", "candidate_id": "legacy-a", "canonical_candidate_id": "legacy-a"},
            {"id": "mb-alias-2", "candidate_id": "legacy-b", "canonical_candidate_id": "legacy-b"},
        ]
        terminal = mail.terminal_mailbox_ids(rows)
        assert bulk_calls == [["legacy-a", "legacy-b"]]
        assert terminal == {"mb-alias-1", "mb-alias-2"}
        assert real_bulk is not bulk

    def test_an_unresolvable_mailbox_stays_monitored(self, store, monkeypatch):
        from core import recruitment_mail_store as mail

        _seed()
        monkeypatch.setattr(mail, "_TERMINAL_MEMO", None)

        def broken(_ids):
            raise RuntimeError("identity links unavailable")

        monkeypatch.setattr(cs, "canonical_candidate_identity_ids", broken)
        rows = [{"id": "mb-x", "candidate_id": "legacy-x", "canonical_candidate_id": "legacy-x"}]
        assert mail.terminal_mailbox_ids(rows) == set()

    def test_the_answer_is_reused_until_the_store_changes(self, store, monkeypatch):
        from core import recruitment_mail_store as mail

        people = _seed()
        monkeypatch.setattr(mail, "_TERMINAL_MEMO", None)
        rows = [{"id": "mb-1", "candidate_id": people[3]["id"], "canonical_candidate_id": people[3]["id"]}]
        lists = {"n": 0}
        real = cs.list_candidates

        def counting(*a, **k):
            lists["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(cs, "list_candidates", counting)
        assert mail.terminal_mailbox_ids(rows) == set()
        assert mail.terminal_mailbox_ids(rows) == set()
        assert lists["n"] == 1
        cs.update_candidate(people[3]["id"], {"stage": "dropped"})
        assert mail.terminal_mailbox_ids(rows) == {"mb-1"}


class TestTheSchedulerIdlesCheaply:
    def test_nothing_due_means_nothing_worked_out(self, monkeypatch):
        from workers import recruitment_mail_worker as module

        statements = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def execute(self, sql, params=None):
                statements.append(" ".join(sql.split()))

            def fetchone(self):
                return None

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def cursor(self):
                return Cursor()

        def no_terminal(_cur):
            raise AssertionError("worked out terminal mailboxes with nothing due")

        monkeypatch.setattr("core.db.connection.get_connection", lambda: Connection())
        monkeypatch.setattr(module.RecruitmentMailWorker, "_terminal_mailbox_ids", staticmethod(no_terminal))
        module.RecruitmentMailWorker().schedule_due()
        assert len(statements) == 1
        assert statements[0].startswith("SELECT 1 FROM candidate_mailboxes")
        assert "COALESCE(next_sync_at,now())<=now()" in statements[0]


class TestTheEventLoopStaysFree:
    @pytest.mark.parametrize("route, store_fn, kwargs", [
        ("candidates_list", "list_candidates", dict(stage=None, task=None, search=None, month=None,
                                                    pending_only=False, reference=None, service_type=None,
                                                    ai_filter=None)),
        ("candidates_stats", "stats", dict(month=None, reference=None, service_type=None)),
    ])
    def test_a_slow_read_does_not_stall_other_requests(self, monkeypatch, route, store_fn, kwargs):
        from api.routers import candidates as routes

        monkeypatch.setattr("core.dashboard_access.handler_reference_scope", lambda _r, ref: ref)
        monkeypatch.setattr("core.dashboard_access.handler_payout_reference_scope", lambda _r, ref=None: ref)

        def slow(*_a, **_k):
            time.sleep(0.4)
            return [] if store_fn == "list_candidates" else {}

        monkeypatch.setattr(cs, store_fn, slow)

        async def scenario():
            ticks = []

            async def other_request():
                for _ in range(6):
                    ticks.append(time.monotonic())
                    await asyncio.sleep(0.02)

            started = time.monotonic()
            await asyncio.gather(getattr(routes, route)(object(), **kwargs), other_request())
            return started, ticks

        started, ticks = asyncio.run(scenario())
        # Every tick of the other request lands while the slow read is running.
        assert ticks[-1] - started < 0.3

    def test_every_whole_store_read_route_leaves_the_loop(self):
        from api.routers import candidates as routes
        from core import public_slot_api

        source = inspect.getsource(routes)
        for fn in ("list_candidates", "stats", "pending_works", "daily_interview_roster", "interview_monitor",
                   "interview_upcoming", "interview_global_summary", "active_roster", "bootstrap_data"):
            assert f"candidate_store.{fn}(" not in source, fn
            assert f"candidate_store.{fn}," in source, fn
        public = inspect.getsource(public_slot_api)
        assert "to_thread(\n                cs.interview_slot_picker_rows" in public
        assert "to_thread(cs.public_booked_interview_slots" in public
