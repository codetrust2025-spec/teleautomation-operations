"""Every stored attendance status gets counted as itself.

`_interview_attendance_counts` named four statuses in an if/elif chain and
derived Pending by subtracting those four from the row count. `re_service` was
in INTERVIEW_ATTENDANCE_STATUSES but not in the chain, so every Re-Service row
fell through the subtraction and was reported as Pending -- on all five
surfaces that call this, including the Daily Ops tabs.

The counts are the contract the dashboard filters against, so a status that
cannot be counted cannot honestly be filtered either.
"""

from __future__ import annotations

import pytest

from features import candidate_store as cs


def booking(row_id: str, status: str, **overrides) -> dict:
    """A roster row as the range query hands it over: a confirmed slot."""
    return {"id": row_id, "interview_attendance_status": status, "slot_confirmed": True,
            "date": "2099-08-06", "time": "15:30", "time_end": "16:00", **overrides}


def rows(*statuses: str) -> list[dict]:
    return [{"interview_attendance_status": status} for status in statuses]


class TestTheStatusSetIsTheSourceOfTruth:
    def test_the_six_statuses_are_what_the_dashboard_shows(self):
        assert cs.INTERVIEW_ATTENDANCE_STATUSES == frozenset({
            "attended", "not_attended", "cancelled", "rescheduled",
            "released_for_reschedule", "re_service",
        })

    def test_pending_is_the_absence_of_one_not_a_member(self):
        """Pending is derived, never stored -- it must not be in the set."""
        assert "pending" not in cs.INTERVIEW_ATTENDANCE_STATUSES
        assert cs.normalise_interview_attendance_status("pending") == ""
        assert cs.normalise_interview_attendance_status("") == ""

    def test_every_stored_status_has_its_own_counter(self):
        counts = cs._interview_attendance_counts([])
        for status in cs.INTERVIEW_ATTENDANCE_STATUSES:
            assert f"{status}_count" in counts, f"{status} has no counter"
        assert "pending_count" in counts


class TestCountingIsExact:
    def test_each_status_counts_once(self):
        counts = cs._interview_attendance_counts(rows(
            "attended", "attended", "not_attended", "cancelled",
            "rescheduled", "released_for_reschedule", "re_service", "",
        ))
        assert counts == {
            "attended_count": 2,
            "not_attended_count": 1,
            "cancelled_count": 1,
            "rescheduled_count": 1,
            "released_for_reschedule_count": 1,
            "re_service_count": 1,
            "pending_count": 1,
        }

    def test_re_service_is_not_reported_as_pending(self):
        """The defect, stated on its own."""
        counts = cs._interview_attendance_counts(rows("re_service", "re_service"))
        assert counts["re_service_count"] == 2
        assert counts["pending_count"] == 0

    def test_cancelled_is_counted_as_cancelled(self):
        counts = cs._interview_attendance_counts(rows("cancelled", "cancelled", ""))
        assert counts["cancelled_count"] == 2
        assert counts["pending_count"] == 1

    def test_only_rows_without_a_status_are_pending(self):
        counts = cs._interview_attendance_counts(rows("", "", ""))
        assert counts["pending_count"] == 3
        assert sum(v for k, v in counts.items() if k != "pending_count") == 0

    def test_the_counters_add_up_to_the_row_count(self):
        every = rows("attended", "not_attended", "cancelled", "rescheduled",
                     "re_service", "", "attended")
        counts = cs._interview_attendance_counts(every)
        assert sum(counts.values()) == len(every)

    @pytest.mark.parametrize("stored,counted", [
        ("canceled", "cancelled_count"),
        ("reschedule", "rescheduled_count"),
        ("CANCELLED", "cancelled_count"),
    ])
    def test_legacy_spellings_land_on_the_right_counter(self, stored, counted):
        counts = cs._interview_attendance_counts(rows(stored))
        assert counts[counted] == 1
        assert counts["pending_count"] == 0

    def test_an_unknown_status_is_pending_rather_than_lost(self):
        counts = cs._interview_attendance_counts(rows("nonsense"))
        assert counts["pending_count"] == 1
        assert sum(counts.values()) == 1


class TestCancelledIsNeverMappedElsewhere:
    @pytest.mark.parametrize("spelling", ["cancelled", "canceled", "Cancelled", " CANCELED "])
    def test_it_normalises_to_cancelled_and_nothing_else(self, spelling):
        assert cs.normalise_interview_attendance_status(spelling) == "cancelled"

    def test_a_cancelled_row_reads_back_as_cancelled(self):
        assert cs.row_interview_attendance_status(
            {"interview_attendance_status": "cancelled"}
        ) == "cancelled"

    def test_cancelled_survives_a_legacy_attended_flag(self):
        """`interview_attended` is only a fallback for rows with no status."""
        assert cs.row_interview_attendance_status(
            {"interview_attendance_status": "cancelled", "interview_attended": True}
        ) == "cancelled"


class TestTheBreakdownBucketsAgree:
    """by_attendee / by_referrer / by_candidate / by_technology in the global
    summary carried the same four-status chain, with everything else swept into
    "pending" -- so Re-Service was pending there too."""

    def test_a_bucket_has_a_slot_for_every_status(self):
        counts = cs._interview_attendance_counts([])
        # Every counter the summary reports has a matching bucket key.
        bucket_keys = set(cs.INTERVIEW_ATTENDANCE_STATUSES) | {"scheduled", "pending"}
        for key in counts:
            assert key.removesuffix("_count") in bucket_keys

    def test_the_global_summary_breaks_re_service_out(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
        monkeypatch.setattr(cs, "_load_cache", None)
        monkeypatch.setattr(cs, "_load_cache_at", 0.0)
        monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)

        # The dashboard reads these from `interviews`, which is where the
        # counts are spread — it is the object the KPI tabs index into.
        summary = cs.interview_global_summary("2026-09-01", "2026-09-30")
        interviews = summary["interviews"]
        for status in cs.INTERVIEW_ATTENDANCE_STATUSES:
            assert f"{status}_count" in interviews
        assert "pending_count" in interviews
        assert "count" in interviews


class TestEveryStoredStatusCountsAsResolved:
    """The Upcoming tab shows slots nothing has happened to yet."""

    def test_a_re_service_row_is_not_upcoming(self):
        upcoming = cs._filter_upcoming_only_rows([
            booking("a", "re_service"),
            booking("b", ""),
        ])
        assert [row["id"] for row in upcoming] == ["b"]

    def test_a_booking_replaced_by_another_is_not_upcoming(self):
        """It carries no status of its own; the replacement is what ended it."""
        upcoming = cs._filter_upcoming_only_rows([
            booking("a", "", superseded_by_booking_id="b"),
            booking("b", ""),
        ])
        assert [row["id"] for row in upcoming] == ["b"]

    @pytest.mark.parametrize("status", sorted(cs.INTERVIEW_ATTENDANCE_STATUSES))
    def test_no_stored_status_survives_into_upcoming(self, status):
        assert cs._filter_upcoming_only_rows(
            [{"id": "x", "interview_attendance_status": status}]
        ) == []

    def test_a_row_with_no_status_is_upcoming(self):
        rows_in = [booking("x", "")]
        assert cs._filter_upcoming_only_rows(rows_in) == rows_in

    def test_the_schema_note_lists_every_status(self):
        """The docstring is what a reader trusts before reading the set."""
        note = cs.__doc__ or ""
        for status in cs.INTERVIEW_ATTENDANCE_STATUSES:
            assert status in note, f"{status} missing from the schema note"


class TestTheNoteIsKeptForEveryStatusThatDemandsOne:
    """The edit form requires a note on every status change, so no status may
    silently discard it."""

    @pytest.fixture
    def store(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
        monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "proofs"))
        monkeypatch.setattr(cs, "_load_cache", None)
        monkeypatch.setattr(cs, "_load_cache_at", 0.0)
        monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)

    @pytest.mark.parametrize("status", ["cancelled", "rescheduled", "re_service"])
    def test_the_remark_survives(self, store, status):
        row = cs.create_candidate({"name": f"Note {status}", "phone": "9000000001"})
        saved = cs.set_interview_attendance(
            str(row["id"]), status=status, remark=f"note for {status}", by="admin",
        )
        assert saved["interview_attendance_status"] == status
        assert saved["interview_attendance_remark"] == f"note for {status}"

    def test_re_service_still_grants_the_entitlement(self, store):
        """Keeping the note must not disturb what Re-Service is for."""
        row = cs.create_candidate({"name": "Grant Ravi", "phone": "9000000002"})
        saved = cs.set_interview_attendance(
            str(row["id"]), status="re_service", remark="one free repeat", by="admin",
        )
        assert saved["re_service_eligible"] is True
        assert saved["re_service_consumed"] is False

    def test_re_service_records_no_attendee(self, store):
        """Nobody sat it, so it must not claim one."""
        row = cs.create_candidate({"name": "NoOne Sat", "phone": "9000000003"})
        saved = cs.set_interview_attendance(
            str(row["id"]), status="re_service", remark="granted", by="admin",
        )
        assert saved["interview_attended"] is False


class TestTheRosterPayloadCarriesThem:
    def test_daily_roster_exposes_a_counter_per_status(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
        monkeypatch.setattr(cs, "_load_cache", None)
        monkeypatch.setattr(cs, "_load_cache_at", 0.0)
        monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)

        payload = cs.daily_interview_roster("2026-09-07")
        for status in cs.INTERVIEW_ATTENDANCE_STATUSES:
            assert f"{status}_count" in payload
        assert "pending_count" in payload
        assert "count" in payload
