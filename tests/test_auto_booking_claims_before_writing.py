"""An automatic booking claims its interview in the database before any write,
and a refused booking is filed as a booking failure, not an AI retry."""
from __future__ import annotations

from core import recruitment_mail_store as store
from services import interview_auto_booking as booking
from tests.test_interview_auto_booking import install_store_fakes, result, slot_writer

TOKEN = "yCcxpsMmCdW0v3zW06skkER4950jfZ9x0P8n_qYvIKw"
MESSAGE = {"provider_message_id": "invite", "provider_thread_id": "thread", "sent_at": "2099-11-12T05:00:00Z",
           "body_text": f"Join https://assess.cantikprime.com/r/{TOKEN}"}


def run(monkeypatch, *, held_by=None):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch, rows=[])
    calls = []

    def claim(keys, **kwargs):
        calls.append(("claim", list(keys)))
        if held_by:
            raise store.BookingClaimTaken(keys[0], held_by)

    monkeypatch.setattr(booking.mail_store, "claim_booking_keys", claim)
    monkeypatch.setattr(booking.mail_store, "settle_booking_claims",
                        lambda keys, booking_id: calls.append(("settle", booking_id)))
    writes = []
    write = slot_writer("new-slot", capture=writes)
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot",
                        lambda **kwargs: calls.append(("write", None)) or write(**kwargs))
    outcome = booking.execute_auto_booking(
        mailbox={"id": "mb1", "candidate_id": "c1"}, message=MESSAGE,
        event={"mailbox_message_id": "mm1", "notification": {"id": "n1"}},
        result=result(date="2099-11-12", time="02:00 PM", end_time="02:45 PM"),
    )
    return outcome, calls, writes


def test_the_claim_comes_before_the_write_and_is_settled_after(monkeypatch):
    outcome, calls, writes = run(monkeypatch)

    assert outcome["status"] == "Auto Booked" and len(writes) == 1
    assert [name for name, _ in calls] == ["claim", "write", "settle"]
    assert calls[0][1] == [
        "mail:c1:invite",
        f"room:c1:2099-11-12:14:00:assess.cantikprime.com|interview-room|{TOKEN}",
    ]
    assert calls[2] == ("settle", "new-slot")


def test_a_key_held_by_a_standing_booking_writes_nothing(monkeypatch):
    outcome, calls, writes = run(monkeypatch, held_by="existing-slot")

    assert writes == []
    assert outcome["failure_code"] == "DUPLICATE_BOOKING"
    assert outcome["status"] == "Duplicate Ignored"
    assert outcome["notification"]["booking_id"] == "existing-slot"


def test_booking_failures_are_filed_apart_from_ai_retries(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch, rows=[])
    value = result("interview_cancelled", date=None, time=None, timezone=None, round=None)
    outcome = booking.execute_auto_booking(
        mailbox={"id": "mb1", "candidate_id": "c1"}, message={"provider_message_id": "m", "provider_thread_id": "t"},
        event={"mailbox_message_id": "mm1", "notification": {"id": "n1"}}, result=value,
    )

    assert outcome["failure_code"] == "BOOKING_NOT_FOUND"
    assert outcome["notification"]["priority"] == "booking_failed"


def test_failure_kind_reads_old_and_new_rows_alike():
    assert store.failure_kind_of({"priority": "retry_pending", "booking_status": "Blocked"}) == "booking"
    assert store.failure_kind_of({"priority": "booking_failed", "booking_status": "Blocked"}) == "booking"
    assert store.failure_kind_of({"priority": "retry_pending", "booking_status": "Processing Failed"}) == "booking"
    assert store.failure_kind_of({"priority": "retry_pending", "booking_status": None}) == "ai"
    assert store.failure_kind_of({"priority": "review_required"}) == "ai"
    assert store.failure_kind_of({"priority": "high", "booking_status": "AI_RETRY_PENDING", "booking_id": "x"}) == ""
    assert store.failure_kind_of({"priority": "high", "booking_status": "Auto Booked"}) == ""


def test_the_ai_retry_filter_leaves_booking_failures_out():
    clause, _params, _ = store.notification_filter_sql({"priority": "retry_pending"})
    assert ("priority IN ('retry_pending','review_required') AND NOT "
            "(COALESCE(booking_status,'') IN ('Blocked', 'Processing Failed')") in clause

    clause, params, _ = store.notification_filter_sql({"priority": "booking_failed"})
    assert "(COALESCE(booking_status,'') IN ('Blocked', 'Processing Failed') OR priority = 'booking_failed')" in clause
    assert "booking_failed" not in params
