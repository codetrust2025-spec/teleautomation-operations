"""A mail may claim a booking only for as long as that booking exists.

Mail Alerts showed "Interview Automatically Booked" for slots Confirmed Slots
and Daily Ops did not have. The write path was not at fault: `_persisted` turns
a store write that did not stick into a block, and `assert_slot_persisted`
re-reads past every cache and refuses to record "Auto Booked" against a row
that lost its date, time or confirmation. Both were doing their job.

The gap was afterwards. `booking_status` was written once and never revisited,
so any later removal of the slot left the alert asserting a booking that was
gone. Cancelling from the candidates screen is the quietest way in: it clears
the row and writes no audit and no notification at all, which is exactly what
production showed for the two visible cases.

Two guarantees, tested here:

  1. every route that removes a slot releases the claims on it, because they
     all go through `cancel_interview_slot`
  2. nothing reports a booked status for a booking the roster does not have,
     whatever removed it
"""

from __future__ import annotations

import pytest

from core import recruitment_mail_store as ms


class TestTheClaimStatusesAreTheBookedOnes:
    def test_only_statuses_that_assert_a_slot(self):
        assert ms.BOOKED_BOOKING_STATUSES == (
            "Auto Booked", "Approved & Booked", "Rescheduled",
        )

    def test_a_released_claim_is_not_a_cancellation(self):
        """Nobody cancelled these. The booking is simply not there."""
        assert ms.RELEASED_BOOKING_STATUS == "Booking Removed"
        assert ms.RELEASED_BOOKING_STATUS not in ms.BOOKED_BOOKING_STATUSES
        assert ms.RELEASED_BOOKING_STATUS != ms.CANCELLED_BOOKING_STATUS

    def test_a_released_claim_is_not_an_ai_retry(self):
        """Nothing ever retries a removed booking, so it must not say it will.

        It used to be stored as AI_RETRY_PENDING, which filled the "AI retry
        pending" tile with bookings people had removed by hand."""
        assert ms.RELEASED_BOOKING_STATUS != "AI_RETRY_PENDING"
        assert ms.LEGACY_RELEASED_BOOKING_STATUS == "AI_RETRY_PENDING"

    def test_a_released_claim_is_not_counted_as_auto_booked(self):
        """The summary counts booking_status='Auto Booked'."""
        assert ms.RELEASED_BOOKING_STATUS != "Auto Booked"


class TestNothingReportsABookingTheRosterLacks:
    """`reconcile_booking_claims` is the read-time guarantee."""

    @pytest.fixture
    def roster(self, monkeypatch):
        """One booked candidate, one whose slot has been taken away."""
        rows = {
            "booked-1": {"id": "booked-1", "date": "2026-09-11", "time": "14:00",
                         "slot_confirmed": True},
            "emptied-1": {"id": "emptied-1", "date": "", "time": "",
                          "slot_confirmed": False},
        }
        import features.candidate_store as cs

        monkeypatch.setattr(cs, "get_candidate", lambda cid: rows.get(str(cid)))
        monkeypatch.setattr(
            cs, "candidate_has_confirmed_slot",
            lambda row: bool(row and row.get("slot_confirmed") and row.get("date")),
        )
        return rows

    @pytest.mark.parametrize("status", ["Auto Booked", "Approved & Booked", "Rescheduled"])
    def test_a_claim_on_a_missing_slot_is_released(self, roster, status):
        rows = [{"id": "n1", "booking_status": status, "booking_id": "emptied-1"}]
        ms.reconcile_booking_claims(rows)
        assert rows[0]["booking_status"] == "Booking Removed"
        assert rows[0]["booking_claim_released"] is True

    @pytest.mark.parametrize("status", ["Auto Booked", "Approved & Booked", "Rescheduled"])
    def test_a_claim_on_a_real_slot_is_left_alone(self, roster, status):
        rows = [{"id": "n1", "booking_status": status, "booking_id": "booked-1"}]
        ms.reconcile_booking_claims(rows)
        assert rows[0]["booking_status"] == status
        assert "booking_claim_released" not in rows[0]

    def test_a_claim_on_a_deleted_candidate_is_released(self, roster):
        rows = [{"id": "n1", "booking_status": "Auto Booked", "booking_id": "gone"}]
        ms.reconcile_booking_claims(rows)
        assert rows[0]["booking_status"] == "Booking Removed"

    @pytest.mark.parametrize("status", ["Cancelled", "Blocked", "Processing Failed", "Booking Removed"])
    def test_statuses_that_claim_nothing_are_untouched(self, roster, status):
        """These do not assert a slot, so there is nothing to release."""
        rows = [{"id": "n1", "booking_status": status, "booking_id": "emptied-1"}]
        ms.reconcile_booking_claims(rows)
        assert rows[0]["booking_status"] == status

    def test_a_claim_released_under_the_old_name_reads_as_removed(self, roster):
        """Production holds alerts released before 5 Oct 2026 as AI_RETRY_PENDING
        with the booking they named. They are shown for what they are, and the
        stored row is not rewritten."""
        stored = {"id": "n1", "booking_status": "AI_RETRY_PENDING", "booking_id": "emptied-1",
                  "candidate_status": "Interview Automatically Booked"}
        rows = [dict(stored)]
        ms.reconcile_booking_claims(rows)
        assert rows[0]["booking_status"] == "Booking Removed"
        assert rows[0]["candidate_status"] == "Booking Removed"
        assert rows[0]["historical_candidate_status"] == "Interview Automatically Booked"

    def test_a_real_ai_retry_is_still_an_ai_retry(self, roster):
        """A retry made no slot, so it names no booking; it is left as it is."""
        rows = [{"id": "n1", "booking_status": "AI_RETRY_PENDING", "booking_id": ""}]
        ms.reconcile_booking_claims(rows)
        assert rows[0]["booking_status"] == "AI_RETRY_PENDING"

    def test_a_claim_with_no_booking_id_is_untouched(self, roster):
        rows = [{"id": "n1", "booking_status": "Auto Booked", "booking_id": ""}]
        ms.reconcile_booking_claims(rows)
        assert rows[0]["booking_status"] == "Auto Booked"

    def test_mixed_rows_are_each_judged_on_their_own(self, roster):
        rows = [
            {"id": "a", "booking_status": "Auto Booked", "booking_id": "booked-1"},
            {"id": "b", "booking_status": "Auto Booked", "booking_id": "emptied-1"},
            {"id": "c", "booking_status": "Cancelled", "booking_id": "emptied-1"},
        ]
        ms.reconcile_booking_claims(rows)
        assert [row["booking_status"] for row in rows] == [
            "Auto Booked", "Booking Removed", "Cancelled",
        ]

    def test_an_empty_list_is_fine(self, roster):
        assert ms.reconcile_booking_claims([]) == []
        assert ms.reconcile_booking_claims(None) is None

    def test_a_failure_leaves_the_rows_readable(self, monkeypatch):
        """A reconciliation that cannot run must not blank the screen."""
        import features.candidate_store as cs

        monkeypatch.setattr(cs, "get_candidate", lambda _cid: (_ for _ in ()).throw(RuntimeError("db down")))
        rows = [{"id": "n1", "booking_status": "Auto Booked", "booking_id": "booked-1"}]
        assert ms.reconcile_booking_claims(rows) is rows
        assert rows[0]["id"] == "n1"


class TestTheRetryTileCountsOnlyRetries:
    def test_removed_bookings_are_not_counted_as_ai_retries(self):
        """On 4 Oct 2026 the tile said 22 and none of them was retrying: eight
        were bookings later taken off the roster. A removed booking -- under
        either name -- names a booking; a real retry never does."""
        import inspect

        source = inspect.getsource(ms.notification_summary)
        tile = source[source.index("ai_retry_pending") - 400:source.index(") ai_retry_pending")]
        assert "NOT (booking_status IN ('AI_RETRY_PENDING','Booking Removed')" in tile
        assert "COALESCE(booking_id,'')<>''" in tile


class TestTheListPathAppliesIt:
    def test_both_return_paths_reconcile(self):
        """A list that skipped it would show exactly the bug being fixed."""
        import inspect

        source = inspect.getsource(ms.list_notifications)
        returns = [
            line for line in source.splitlines()
            if "return" in line and "total" in line and "[], total" not in line
        ]
        assert returns, "list_notifications no longer returns rows the same way"
        for line in returns:
            assert "reconcile_booking_claims" in line, line


class TestRemovingASlotReleasesTheClaims:
    def test_cancel_calls_the_release(self):
        """Every removal route goes through cancel_interview_slot."""
        import inspect

        from features import candidate_store as cs

        source = inspect.getsource(cs.cancel_interview_slot)
        assert "release_booking_claims" in source

    def test_it_releases_after_the_row_is_written(self):
        """Releasing first would drop the claim for a cancellation that then
        failed to save."""
        import inspect

        from features import candidate_store as cs

        source = inspect.getsource(cs.cancel_interview_slot)
        assert source.index("_save(data)") < source.index("release_booking_claims")

    def test_a_release_failure_does_not_undo_the_cancellation(self):
        import inspect

        from features import candidate_store as cs

        source = inspect.getsource(cs.cancel_interview_slot)
        released = source.index("release_booking_claims")
        assert "try:" in source[:released]
        assert "except Exception:" in source[released:]

    def test_every_slot_removal_route_goes_through_it(self):
        """If a route stops using cancel_interview_slot, it stops releasing."""
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[1]
        offenders = []
        for path in list(root.glob("api/**/*.py")) + list(root.glob("services/**/*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            # Writing the slot fields empty by hand, rather than cancelling.
            if re.search(r'\["date"\]\s*=\s*""', text):
                offenders.append(str(path.relative_to(root)))
        assert offenders == [], f"these clear a slot without cancelling: {offenders}"


class TestTheWritePathStillRefusesAnUnsavedBooking:
    """The guards that were already right, pinned so they stay that way."""

    def test_booked_statuses_are_the_ones_re_read(self):
        from services import interview_auto_booking as ab

        assert ab._PERSISTED_BOOKING_STATUSES == {
            "Auto Booked", "Approved & Booked", "Rescheduled",
        }

    def test_the_slot_is_re_read_before_the_audit_is_written(self):
        import inspect

        from services import interview_auto_booking as ab

        # Four audits are written in this function; the success one is the
        # only one carrying auto_booked=True, and that is the one the re-read
        # has to precede.
        source = inspect.getsource(ab._execute_auto_booking)
        success_audit = source.index("auto_booked=True")
        assert source.index("_confirm_slot_still_stored") < success_audit
        assert source.index("record_booking_audit", 0, success_audit) < success_audit

    def test_a_write_that_did_not_stick_blocks_the_booking(self):
        import inspect

        from services import interview_auto_booking as ab

        source = inspect.getsource(ab._persisted)
        assert "SlotNotPersistedError" in source
        assert "_booking_not_saved" in source

    def test_confirming_a_slot_reads_past_the_cache(self):
        import inspect

        from features import candidate_store as cs

        source = inspect.getsource(cs.assert_slot_persisted)
        assert "_load(force=True)" in source
        assert "SlotNotPersistedError" in source
