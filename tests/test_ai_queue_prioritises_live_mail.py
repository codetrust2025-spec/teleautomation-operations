"""Today's mail must not wait behind a month of backlog.

On 2026-09-09 Mail Alerts stopped at 11:01 and showed nothing new for six
hours. Nothing was broken: Gmail ingestion stored all 132 mails that arrived
after 11:00, the worker was alive, classification and the notification API were
both healthy, and the API returned exactly the 82 alerts the screen showed.

The queue was the problem. `claim_ai_messages` ordered strictly `sent_at ASC`,
which is FIFO across the whole table, and 3,403 messages were queued with 2,871
of them older than two days. Every new mail joined the back of a queue whose
head was 2026-08-17, so at ~174/hour it would have been about twenty hours
before live mail was reached at all.

Recent mail is now claimed first, oldest-first within each tier, so the backlog
still drains whenever nothing live is waiting.
"""

from __future__ import annotations

import inspect
import itertools

import pytest

from core import recruitment_mail_store as store


def _claim_sql() -> str:
    return inspect.getsource(store.claim_ai_messages)


class TestTheThreeTiers:
    def test_just_arrived_today_and_history_are_separated(self):
        sql = store._TIER_SQL
        assert "THEN 1" in sql and "THEN 2" in sql and "ELSE 3" in sql

    def test_tier_one_is_the_last_couple_of_hours(self):
        assert "now()-(%s||' hours')::interval THEN 1" in store._TIER_SQL

    def test_tier_two_is_the_operator_day_not_utc(self):
        assert "date_trunc('day', now() AT TIME ZONE %s) AT TIME ZONE %s) THEN 2" in store._TIER_SQL
        assert store._QUEUE_TIMEZONE == "Asia/Kolkata"

    def test_a_null_sent_at_cannot_jump_the_queue(self):
        """A bare `sent_at` makes the tier NULL, which sorts to the very front.

        Asserted as an invariant rather than a count, so the tier expression can
        grow without the guard quietly ceasing to check anything.
        """
        assert "sent_at" not in store._TIER_SQL.replace("COALESCE(sent_at,created_at)", "")

    def test_fifo_survives_inside_every_tier(self):
        sql = _claim_sql()
        assert "sent_at ASC,id" in sql

    def test_it_is_no_longer_plain_fifo_over_everything(self):
        """The exact ordering that stalled Mail Alerts for six hours."""
        assert "ORDER BY sent_at ASC,id" not in _claim_sql()

    def test_no_tier_is_filtered_out_of_the_claim(self):
        """Ordering decides who goes first; nothing is excluded, so a turn
        always finds work even when its preferred tier is empty."""
        sql = _claim_sql()
        assert "processing_status IN ('AI_QUEUED','AI_RETRY_PENDING')" in sql
        for excluded in ("AND sent_at >", "AND created_at >", "AND tier"):
            assert excluded not in sql


class TestTheWeightedRotation:
    def test_one_turn_in_five_goes_to_history(self, monkeypatch):
        monkeypatch.delenv("AI_MAIL_BACKLOG_SHARE", raising=False)
        monkeypatch.setattr(store, "_claim_rotation", itertools.count())
        turns = [store._claim_prefers_backlog() for _ in range(20)]
        assert sum(turns) == 4          # 20% of capacity
        assert len(turns) - sum(turns) == 16   # 80% to live and today

    def test_the_split_is_evenly_spaced_not_bursty(self, monkeypatch):
        """A backlog turn every fifth claim, so history advances steadily
        rather than in a clump that delays live mail."""
        monkeypatch.setattr(store, "_claim_rotation", itertools.count())
        turns = [store._claim_prefers_backlog() for _ in range(15)]
        assert [i for i, backlog in enumerate(turns) if backlog] == [4, 9, 14]

    def test_the_share_is_configurable_and_bounded(self, monkeypatch):
        monkeypatch.setenv("AI_MAIL_BACKLOG_SHARE", "4")
        assert store._backlog_share() == 4
        for raw, expected in (("1", 2), ("0", 2), ("999", 50), ("junk", 5), ("", 5)):
            monkeypatch.setenv("AI_MAIL_BACKLOG_SHARE", raw)
            assert store._backlog_share() == expected

    def test_a_history_turn_puts_history_first(self):
        assert "(%s = 3) DESC" not in _claim_sql()   # built from the tier SQL
        assert "= 3) DESC" in _claim_sql()

    def test_a_history_turn_still_falls_through_to_live_mail(self):
        """`(tier = 3) DESC` then `tier ASC`: with no history waiting the same
        turn takes tier 1, so reserving a share never wastes capacity."""
        sql = _claim_sql()
        marker = sql.index("= 3) DESC")
        assert "ASC" in sql[marker:marker + 80]


class TestTheLiveWindow:
    def test_tier_one_defaults_to_two_hours(self, monkeypatch):
        """New mail must reach the model in minutes, which needs a tier small
        enough to be nearly empty."""
        monkeypatch.delenv("AI_MAIL_LIVE_WINDOW_HOURS", raising=False)
        assert store._live_mail_window_hours() == 2

    def test_a_host_can_change_it(self, monkeypatch):
        monkeypatch.setenv("AI_MAIL_LIVE_WINDOW_HOURS", "6")
        assert store._live_mail_window_hours() == 6

    @pytest.mark.parametrize("raw,expected", [
        ("0", 1), ("-5", 1), ("100000", 720), ("nonsense", 2), ("", 2),
    ])
    def test_it_stays_within_sane_bounds(self, monkeypatch, raw, expected):
        monkeypatch.setenv("AI_MAIL_LIVE_WINDOW_HOURS", raw)
        assert store._live_mail_window_hours() == expected

    def test_the_window_is_passed_to_the_query(self):
        assert "_live_mail_window_hours()" in _claim_sql()


class TestNothingElseAboutTheQueueChanged:
    def test_exhausted_retries_remain_claimable_with_backoff(self):
        """No mail is terminally abandoned just because a model was down or
        returned an uncertain result repeatedly.  The retry timestamp governs
        capacity; the attempt count remains diagnostic only."""
        sql = _claim_sql()
        assert "AI_FAILED_TERMINAL" not in sql
        assert "processing_status IN ('AI_QUEUED','AI_RETRY_PENDING')" in sql
        assert "ai_retry_after" in sql

    def test_expired_leases_are_still_returned_to_the_queue(self):
        sql = _claim_sql()
        assert "LEASE_EXPIRED" in sql
        assert "processing_status='AI_QUEUED'" in sql

    def test_the_batch_is_still_capped(self):
        assert "max(1,min(limit,20))" in _claim_sql()


class TestHistoryGetsMoreWhileItIsDeep:
    """A backlog of 1,861 clears too slowly at one turn in five, and the share
    has to come back down by itself -- a temporary setting nobody remembers to
    undo is a permanent one."""

    def test_a_deep_backlog_lifts_history_to_thirty_percent(self):
        assert store._backlog_percent(1861) == 30

    def test_it_returns_to_twenty_once_the_backlog_is_small(self):
        assert store._backlog_percent(499) == 20

    def test_the_threshold_is_five_hundred(self):
        assert store._backlog_percent(500) == 30
        assert store._backlog_percent(499) == 20

    def test_an_unknown_depth_is_treated_as_normal(self):
        """The claim measures it; every other caller gets the ordinary share."""
        assert store._backlog_percent() == 20

    def test_thirty_percent_is_three_turns_in_ten_evenly_spread(self, monkeypatch):
        monkeypatch.setattr(store, "_claim_rotation", itertools.count())
        turns = [store._claim_prefers_backlog(history_waiting=1861) for _ in range(20)]
        assert sum(turns) == 6
        assert [i for i, backlog in enumerate(turns) if backlog] == [3, 6, 9, 13, 16, 19]

    def test_twenty_percent_still_falls_every_fifth_turn(self, monkeypatch):
        monkeypatch.setattr(store, "_claim_rotation", itertools.count())
        turns = [store._claim_prefers_backlog(history_waiting=10) for _ in range(15)]
        assert [i for i, backlog in enumerate(turns) if backlog] == [4, 9, 14]

    def test_the_lift_never_lowers_the_ordinary_share(self, monkeypatch):
        monkeypatch.setenv("AI_MAIL_BACKLOG_SHARE", "2")      # 50% normally
        monkeypatch.setenv("AI_MAIL_BACKLOG_BUSY_PERCENT", "30")
        assert store._backlog_percent(5000) == 50

    def test_both_knobs_are_configurable_and_bounded(self, monkeypatch):
        monkeypatch.setenv("AI_MAIL_BACKLOG_BUSY_PERCENT", "45")
        assert store._busy_backlog_percent() == 45
        for raw, expected in (("1", 10), ("99", 60), ("junk", 30), ("", 30)):
            monkeypatch.setenv("AI_MAIL_BACKLOG_BUSY_PERCENT", raw)
            assert store._busy_backlog_percent() == expected
        monkeypatch.setenv("AI_MAIL_BACKLOG_RELIEF_BELOW", "800")
        assert store._backlog_relief_below() == 800
        for raw, expected in (("1", 50), ("999999", 10000), ("junk", 500), ("", 500)):
            monkeypatch.setenv("AI_MAIL_BACKLOG_RELIEF_BELOW", raw)
            assert store._backlog_relief_below() == expected

    def test_tier_zero_still_leads_a_history_turn(self):
        """The share decides how often history goes first, never whether an
        interview does."""
        sql = _claim_sql()
        marker = sql.index("= 0) DESC")
        assert marker < sql.index("= 3) DESC")
