"""What has to be true before a working day can be closed.

Attendance says someone arrived. Nothing said they left, so the end of a day was
inferred from the last request a session made -- when a browser tab was closed,
not when the work was finished.

These pin the refusals, because the refusals are the feature: a check-out that
let an interview go un-followed-up would record a finished day that was not.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from core.office_network import NetworkVerification
from features import daily_checkout as checkout

IST = ZoneInfo("Asia/Kolkata")
TODAY = date(2026, 9, 17)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 17, hour, minute, tzinfo=IST)


ALLOWED = NetworkVerification(
    allowed=True, source="direct", policy_id="office", reason="OFFICE_NETWORK",
    observed_ip="203.0.113.10")
REFUSED = NetworkVerification(
    allowed=False, source="direct", policy_id="office", reason="NOT_OFFICE_NETWORK",
    observed_ip="198.51.100.7")
MARKED = {"id": "att-1", "account_id": "handler:one", "attendance_date": TODAY}


@pytest.fixture(autouse=True)
def quiet_pipeline(monkeypatch):
    """No interviews and no open tasks unless a test says otherwise."""
    from features import candidate_store

    monkeypatch.setattr(candidate_store, "_interview_rows_for_range",
                        lambda *_a, **_k: [])
    monkeypatch.setattr(candidate_store, "_split_pending_interviews_by_slot_phase",
                        lambda rows: ([], []))
    monkeypatch.setattr(candidate_store, "pending_works", lambda **_k: {"works": []})


def kinds(blockers):
    return [b["kind"] for b in blockers]


def blockers(now=None, attendance_row=MARKED, network=ALLOWED):
    return checkout.checkout_blockers(
        now=now or at(19), attendance_row=attendance_row, network=network)


class TestTheClock:
    def test_nothing_blocks_a_finished_evening(self):
        assert blockers() == []

    def test_the_afternoon_is_too_early(self):
        assert checkout.BEFORE_WINDOW in kinds(blockers(now=at(17, 30)))

    def test_the_window_opens_at_half_past_six_exactly(self):
        assert checkout.BEFORE_WINDOW in kinds(blockers(now=at(18, 29)))
        assert checkout.BEFORE_WINDOW not in kinds(blockers(now=at(18, 30)))

    def test_the_refusal_says_when_it_opens(self):
        early = [b for b in blockers(now=at(9)) if b["kind"] == checkout.BEFORE_WINDOW]
        assert "6:30 PM" in early[0]["summary"]


class TestTheDayHasToHaveStarted:
    def test_a_day_never_marked_cannot_be_closed(self):
        assert checkout.ATTENDANCE_NOT_MARKED in kinds(blockers(attendance_row=None))

    def test_marked_attendance_clears_it(self):
        assert checkout.ATTENDANCE_NOT_MARKED not in kinds(blockers())


class TestTheSameNetworkRuleAsMarking:
    def test_checking_out_from_elsewhere_is_refused(self):
        assert checkout.OFFICE_NETWORK_REQUIRED in kinds(blockers(network=REFUSED))

    def test_the_refusal_names_the_address_it_judged(self):
        """Same as attendance: "I am in the office and it says I am not" has to
        be answerable without reading a server log."""
        refused = [b for b in blockers(network=REFUSED)
                   if b["kind"] == checkout.OFFICE_NETWORK_REQUIRED]
        assert "198.51.100.7" in refused[0]["summary"]


class TestTheWorkItself:
    def test_an_interview_still_to_run_blocks(self, monkeypatch):
        from features import candidate_store

        row = {"id": "c1", "name": "Sample Candidate", "time": "8:00 PM",
               "time_end": "9:00 PM", "company": "Example Corp"}
        monkeypatch.setattr(candidate_store, "_split_pending_interviews_by_slot_phase",
                            lambda rows: ([row], []))

        found = blockers()
        assert checkout.INTERVIEW_NOT_FINISHED in kinds(found)
        assert found[0]["items"][0]["name"] == "Sample Candidate"
        assert found[0]["items"][0]["time"] == "8:00 PM"

    def test_an_interview_that_ended_without_an_outcome_blocks(self, monkeypatch):
        from features import candidate_store

        row = {"id": "c2", "name": "Another Candidate", "time": "10:00 AM"}
        monkeypatch.setattr(candidate_store, "_split_pending_interviews_by_slot_phase",
                            lambda rows: ([], [row]))

        found = blockers()
        assert checkout.INTERVIEW_OUTCOME_PENDING in kinds(found)
        assert found[0]["count"] == 1

    def test_an_open_operator_task_blocks(self, monkeypatch):
        from features import candidate_store

        monkeypatch.setattr(candidate_store, "pending_works", lambda **_k: {
            "works": [{"kind": "missing_resume", "label": "Resume missing",
                       "candidate_id": "c3", "candidate_name": "Third Candidate"}]})

        found = blockers()
        assert checkout.DAILY_TASKS_PENDING in kinds(found)
        assert found[0]["items"][0]["label"] == "Resume missing"

    def test_the_roster_decides_what_finished_means(self, monkeypatch):
        """The gate calls the same splitter the Upcoming tab is built from, so
        it cannot hold a second opinion about whether a slot has ended."""
        from features import candidate_store

        seen = {}

        def record(start, end, **_kwargs):
            seen.update(start=start, end=end)
            return []

        monkeypatch.setattr(candidate_store, "_interview_rows_for_range", record)
        checkout.interview_blockers(TODAY)
        assert seen == {"start": "2026-09-17", "end": "2026-09-17"}


class TestEverythingAtOnce:
    def test_every_blocker_is_returned_not_just_the_first(self, monkeypatch):
        """An operator deciding whether they can leave needs the whole list."""
        from features import candidate_store

        monkeypatch.setattr(candidate_store, "_split_pending_interviews_by_slot_phase",
                            lambda rows: ([{"id": "a", "name": "A"}], [{"id": "b", "name": "B"}]))
        monkeypatch.setattr(candidate_store, "pending_works", lambda **_k: {
            "works": [{"kind": "missing_phone", "candidate_name": "C"}]})

        found = kinds(blockers(now=at(10), attendance_row=None, network=REFUSED))
        assert found == [
            checkout.BEFORE_WINDOW,
            checkout.ATTENDANCE_NOT_MARKED,
            checkout.OFFICE_NETWORK_REQUIRED,
            checkout.INTERVIEW_NOT_FINISHED,
            checkout.INTERVIEW_OUTCOME_PENDING,
            checkout.DAILY_TASKS_PENDING,
        ]

    def test_the_error_carries_them_for_the_api_to_render(self):
        error = checkout.CheckoutBlocked([
            {"kind": checkout.BEFORE_WINDOW, "summary": "too early"},
            {"kind": checkout.DAILY_TASKS_PENDING, "summary": "tasks open"},
        ])
        assert [b["kind"] for b in error.blockers] == [
            checkout.BEFORE_WINDOW, checkout.DAILY_TASKS_PENDING]
        assert "too early" in str(error) and "tasks open" in str(error)


class TestWhoMayCheckOut:
    def test_an_admin_account_is_not_an_attendance_identity(self):
        with pytest.raises(PermissionError):
            checkout._identity_or_refuse(
                {"username": "operations_admin", "role": "admin", "display_name": "Admin"})

    def test_a_handler_is(self):
        identity = checkout._identity_or_refuse(
            {"username": "handler-one", "role": "handler", "reference": "Handler One"})
        assert identity["account_id"] == "handler:handler-one"


def test_the_window_is_the_evening_not_a_guess():
    assert checkout.CHECKOUT_START == time(18, 30)
    assert checkout.CHECKOUT_START_LABEL == "6:30 PM"
