"""The reminder loop keeps its memory in the right order and never fails silently.

1. `_save_sent` trimmed with `sorted(keys)[-500:]`, i.e. by candidate id. Past the
   cap it kept the highest ids and dropped the rest, including the reminder just
   written, which then fired again on the next tick (the slot is still inside its
   10-minute window for two ticks).
2. A failing tick was logged with `logger.debug`, so a reminder loop that failed
   every cycle left nothing in the logs.

Candidate ids and names are invented.
"""
import asyncio
import json
import logging

import pytest

from services import interview_reminder_loop as loop


@pytest.fixture()
def sent_path(tmp_path, monkeypatch):
    path = tmp_path / "sent.json"
    monkeypatch.setattr(loop, "_SENT_PATH", str(path))
    monkeypatch.setattr(loop, "DATA_DIR", str(tmp_path))
    return path


def key(cid, day, hhmm="10:00"):
    return f"{cid}:{day}:{hhmm}"


def test_trimming_drops_the_oldest_slots_not_the_lowest_ids(sent_path, monkeypatch):
    monkeypatch.setattr(loop, "MAX_SENT_KEYS", 5)
    old = [key(f"zz{n}", f"2026-08-0{n}") for n in range(1, 6)]      # high ids, old slots
    fresh = key("00aa", "2026-10-03", "15:00")                        # lowest id, newest slot
    loop._save_sent(set(old) | {fresh})
    kept = set(json.loads(sent_path.read_text()))
    assert fresh in kept, "the reminder just written must survive the trim"
    assert key("zz1", "2026-08-01") not in kept, "the oldest slot is the one dropped"
    assert len(kept) == 5


def test_the_old_ordering_would_have_lost_the_new_reminder():
    keys = {key("zz1", "2026-08-01"), key("00aa", "2026-10-03", "15:00")}
    assert sorted(keys)[-1] == key("zz1", "2026-08-01"), "premise: a plain sort ranks by id"
    assert sorted(keys, key=loop._chronological)[-1] == key("00aa", "2026-10-03", "15:00")


def test_a_reminder_is_not_repeated_after_the_list_passes_its_cap(sent_path, monkeypatch):
    monkeypatch.setattr(loop, "MAX_SENT_KEYS", 3)
    for n in range(1, 5):
        loop._save_sent({key(f"zz{n}", f"2026-08-0{n}")} | loop._load_sent())
    loop._save_sent({key("00aa", "2026-10-03", "15:00")} | loop._load_sent())
    assert key("00aa", "2026-10-03", "15:00") in loop._load_sent()


def test_malformed_keys_do_not_break_the_trim(sent_path):
    loop._save_sent({"garbage", key("a1", "2026-10-01")})
    assert key("a1", "2026-10-01") in loop._load_sent()


def test_a_failing_tick_is_logged_visibly_and_the_loop_keeps_going(monkeypatch, caplog):
    calls = {"n": 0}

    async def tick():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("store unavailable")
        raise asyncio.CancelledError

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(loop, "run_reminder_tick", tick)
    monkeypatch.setattr(loop.asyncio, "sleep", no_sleep)
    with caplog.at_level(logging.WARNING, logger=loop.logger.name):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(loop.interview_reminder_loop())
    assert calls["n"] == 2, "the loop survived the failure and ticked again"
    assert any(r.levelno >= logging.ERROR and "store unavailable" in (r.exc_text or "") for r in caplog.records)
