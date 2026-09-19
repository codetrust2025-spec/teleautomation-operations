"""The Booking result filter: which alerts count as booked, which as blocked.

The query itself runs against Postgres in test_booking_result_filter_pg.py.
These hold the definitions it is built from, the one read-time rule the query
cannot express, and the route that carries the filter from the screen.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import recruitment_mail_api
from core import recruitment_mail_store as store
from core.recruitment_mail_api import install_recruitment_mail_routes

ROOT = Path(__file__).resolve().parents[1]


def test_booked_means_a_booked_status_with_its_booking_or_an_interview_already_booked():
    clause, params = store.booking_result_sql("booked")
    assert "booking_status IN (%s, %s, %s)" in clause
    assert "COALESCE(booking_id,'') <> ''" in clause
    assert params == ["Auto Booked", "Approved & Booked", "Rescheduled", "Duplicate Ignored"]


def test_blocked_means_a_stated_block_that_is_not_a_duplicate():
    clause, params = store.booking_result_sql("blocked")
    # The same test the Reason line uses: a block is shown when either column
    # holds text, so the filter treats an empty string as no block too.
    assert "COALESCE(booking_block_reason_code,'') <> ''" in clause
    assert "COALESCE(booking_block_reason,'') <> ''" in clause
    assert params == ["Duplicate Ignored"]


def test_anything_else_filters_nothing():
    for value in ("", "all", "unknown", "anything"):
        assert store.booking_result_sql(value) == ("", [])


def test_the_duplicate_status_is_the_one_booking_writes():
    """The filter files duplicates by their stored status, so the two must not drift."""
    source = (ROOT / "services" / "interview_auto_booking.py").read_text(encoding="utf-8")
    assert f'"{store.DUPLICATE_IGNORED_STATUS}" if duplicate_ignored' in source


def test_the_booked_statuses_are_the_ones_the_roster_check_trusts():
    # reconcile_booking_claims verifies exactly these against the roster; the
    # filter must not call anything a success that it would not verify.
    assert store.BOOKED_BOOKING_STATUSES == ("Auto Booked", "Approved & Booked", "Rescheduled")


def test_a_booking_released_as_it_is_read_is_not_listed_as_booked():
    rows = [{"id": "kept"}, {"id": "released", "booking_claim_released": True}]
    assert [row["id"] for row in store._without_released_bookings(rows, "booked")] == ["kept"]
    # Every other view keeps it, with the title the release gives it.
    assert store._without_released_bookings(rows, "blocked") == rows
    assert store._without_released_bookings(rows, "") == rows


def test_the_route_carries_the_filter_alongside_the_others(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_OFFER_TRACKING_ENABLED", "true")
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    seen = {}

    def list_notifications(**kwargs):
        seen.update(kwargs)
        return [], 0

    monkeypatch.setattr(recruitment_mail_api.store, "list_notifications", list_notifications)
    app = FastAPI()
    install_recruitment_mail_routes(app)
    response = TestClient(app).get(
        "/api/mail-monitoring/notifications"
        "?booking_result=blocked&candidate_id=c1&classification_group=interview&search=Java",
    )
    assert response.status_code == 200
    filters = seen["filters"]
    assert filters["booking_result"] == "blocked"
    assert filters["candidate_id"] == "c1"
    assert filters["classification_group"] == "interview"
    assert filters["search"] == "Java"
