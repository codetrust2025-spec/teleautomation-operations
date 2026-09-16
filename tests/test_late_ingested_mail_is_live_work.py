"""Mail we have only just received is new work, whatever its `sent_at` says.

Konduru Srinivas's HCLTech interview was never booked. The invitation was real
-- "HCLTech | Interview Schedule - TP1" from recruiting@hcltech.com, a Teams
link, 2026-09-11 at 12:00 IST for 45 minutes -- and it was sitting in the queue
untouched, with no analysis and no event, the evening before the interview.

Nothing in the classifier was involved, because the model never saw it. The
invitation was *sent* on 2026-09-08 and *ingested* on 2026-09-10 at 06:05, and
`_TIER_SQL` tiered on `COALESCE(sent_at,created_at)` alone. So a mail this
pipeline had owned for four minutes was filed as history and sorted to rank
2279 of the 2456 claimable messages, behind a backlog reaching back to June. At
the measured 54 analyses/hour the model would have reached it about 33 hours
later -- roughly a day after the interview it was announcing had finished.

Late arrival is ordinary here, not exotic. Gmail OAuth stays in Testing mode, so
refresh tokens expire every seven days and every reconnect backfills whatever
arrived while the mailbox was disconnected. Every one of those mails is stamped
with the sender's `sent_at` and every one of them is unread work.

So a tier now asks when the mail reached us, bounded by how stale it already was
on arrival. The bound is what keeps the fix from becoming the previous outage:
importing an old mailbox also lands with a recent `created_at`, and promoting
thousands of dead mails ahead of live ones is exactly the plain-FIFO stall of
2026-09-09 that the tiering was built to prevent.

Measured against the production queue on 2026-09-10 before deploying, with
2,456 mails claimable:

    HCLTech invitations   tier 3 -> tier 2, rank 2279 -> 24
    promoted             49 mails, draining in about 1.2 hours
    left in history   2,405 mails, including the 116 ingested that same
                              day whose `sent_at` ran back to 2026-08-27
"""

from __future__ import annotations

import inspect

import pytest

from core import recruitment_mail_store as store


def _claim_sql() -> str:
    return inspect.getsource(store.claim_ai_messages)


def _arrival_free(sql: str) -> str:
    """The tier expression with every arrival sub-expression removed."""
    return sql.replace(store._QUEUE_ARRIVAL_SQL, "<arrival>")


class TestArrivalIsWhenTheMailReachedUs:
    def test_a_mail_fresh_on_arrival_is_tiered_by_when_we_received_it(self):
        """`GREATEST` is the whole fix: ingest time wins when it is later."""
        assert "GREATEST(COALESCE(sent_at,created_at),created_at)" in store._QUEUE_ARRIVAL_SQL

    def test_a_mail_already_stale_on_arrival_keeps_its_own_age(self):
        assert store._QUEUE_ARRIVAL_SQL.strip().endswith("ELSE COALESCE(sent_at,created_at) END")

    def test_both_live_tiers_use_arrival_not_sent_at(self):
        """Every tier reads arrival, including the time-critical one.

        Promoting into tier 1 while tier 2 still tiered on `sent_at` would
        have left the HCLTech mail exactly where it was."""
        stripped = _arrival_free(store._TIER_SQL)
        assert stripped.count("<arrival>") == 3
        assert "COALESCE(sent_at,created_at)" not in stripped

    def test_a_null_sent_at_still_cannot_jump_the_queue(self):
        assert "sent_at" not in store._TIER_SQL.replace("COALESCE(sent_at,created_at)", "")


class TestTheBoundThatKeepsAnArchiveSweepOut:
    def test_promotion_is_bounded_by_staleness_on_arrival(self):
        assert "created_at-(%s||' days')::interval" in store._QUEUE_ARRIVAL_SQL

    def test_the_bound_is_measured_from_ingest_not_from_now(self):
        """Anchoring to `created_at` makes promotion a fixed property of the
        row. Anchored to `now()` a mail would drift between tiers as the clock
        moved, and a mail that had already waited days in the queue would keep
        claiming to be new."""
        head = store._QUEUE_ARRIVAL_SQL.split("THEN")[0]
        assert "created_at-(%s" in head
        assert "now()" not in head

    def test_three_days_by_default(self, monkeypatch):
        monkeypatch.delenv("AI_MAIL_INGEST_FRESHNESS_DAYS", raising=False)
        assert store._ingest_freshness_days() == 3

    def test_it_can_be_widened_after_a_long_outage(self, monkeypatch):
        monkeypatch.setenv("AI_MAIL_INGEST_FRESHNESS_DAYS", "7")
        assert store._ingest_freshness_days() == 7

    def test_zero_turns_the_promotion_off_entirely(self, monkeypatch):
        """The escape hatch: a bulk import can restore `sent_at` tiering
        without a deploy."""
        monkeypatch.setenv("AI_MAIL_INGEST_FRESHNESS_DAYS", "0")
        assert store._ingest_freshness_days() == 0

    @pytest.mark.parametrize("raw,expected", [
        ("-5", 0), ("999", 30), ("", 3), ("three", 3), ("  4  ", 4),
    ])
    def test_a_bad_setting_never_widens_the_window_without_limit(self, monkeypatch, raw, expected):
        monkeypatch.setenv("AI_MAIL_INGEST_FRESHNESS_DAYS", raw)
        assert store._ingest_freshness_days() == expected


class RecordingCursor:
    def __init__(self):
        self.calls: list[tuple[str, tuple | None]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RecordingConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def recorded(monkeypatch):
    """Drive the real `claim_ai_messages`, recording the SQL it issues.

    A tier expression can be perfectly correct and still mis-tier everything if
    its parameters arrive in the wrong order -- the placeholders are all `%s`,
    so Postgres would raise nothing and simply compare a day count against an
    hour count. This exercises the real entry point rather than the constant.
    """
    cursor = RecordingCursor()
    monkeypatch.setattr(store, "get_connection", lambda: RecordingConnection(cursor))
    monkeypatch.delenv("AI_MAIL_INGEST_FRESHNESS_DAYS", raising=False)
    monkeypatch.delenv("AI_MAIL_LIVE_WINDOW_HOURS", raising=False)

    def claim(*, backlog_turn: bool):
        monkeypatch.setattr(store, "_claim_prefers_backlog", lambda: backlog_turn)
        cursor.calls.clear()
        assert store.claim_ai_messages(limit=1) == []
        select = [call for call in cursor.calls if call[0].lstrip().startswith("SELECT id")]
        assert len(select) == 1, cursor.calls
        return select[0]

    return claim


class TestTheClaimBindsTheTierCorrectly:
    def test_an_ordinary_turn_binds_days_days_days_hours_days_zone_zone(self, recorded):
        """Tier 0 added a fourth arrival and its own day bound, so the order is
        now: arrival days, the time-critical window, arrival days, the live
        window in hours, arrival days, and the operator zone twice."""
        _, params = recorded(backlog_turn=False)
        assert params[:-1] == (3, 3, 3, 2, 3, "Asia/Kolkata", "Asia/Kolkata")

    def test_every_placeholder_in_the_claim_is_bound(self, recorded):
        sql, params = recorded(backlog_turn=False)
        assert sql.count("%s") == len(params)

    def test_a_backlog_turn_binds_the_tier_three_times_over(self, recorded):
        """`(tier = 0) DESC, (tier = 3) DESC, tier ASC` repeats the expression
        three times, so the parameter list has to repeat with it."""
        sql, params = recorded(backlog_turn=True)
        assert sql.count("%s") == len(params)
        assert params[:-1] == (3, 3, 3, 2, 3, "Asia/Kolkata", "Asia/Kolkata") * 3

    def test_the_day_bound_is_a_day_count_and_the_live_window_is_hours(self, recorded):
        """The failure this guards is silent: swap them and every tier is
        wrong, with no error from Postgres."""
        _, params = recorded(backlog_turn=False)
        days, critical_days, hours = params[0], params[1], params[3]
        assert (days, critical_days, hours) == (
            store._ingest_freshness_days(), store._time_critical_days(), store._live_mail_window_hours())

    def test_the_arrival_expression_reaches_the_database(self, recorded):
        sql, _ = recorded(backlog_turn=False)
        assert store._QUEUE_ARRIVAL_SQL in sql


class TestTheSeptemberNinthStallCannotReturn:
    def test_the_promotion_did_not_reintroduce_plain_fifo(self):
        assert "ORDER BY sent_at ASC,id" not in _claim_sql()

    def test_fifo_survives_inside_every_tier(self):
        assert "sent_at ASC,id" in _claim_sql()

    def test_no_tier_is_filtered_out_of_the_claim(self):
        """History must still be reachable, or the backlog never drains."""
        sql = _claim_sql()
        assert "processing_status IN ('AI_QUEUED','AI_RETRY_PENDING')" in sql
        for excluded in ("AND sent_at >", "AND created_at >", "AND tier"):
            assert excluded not in sql

    def test_history_keeps_its_guaranteed_share(self):
        assert "_claim_prefers_backlog()" in _claim_sql()
