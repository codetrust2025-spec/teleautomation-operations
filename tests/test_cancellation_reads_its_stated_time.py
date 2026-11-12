"""A cancellation is matched to the booking at the time the mail itself states.

On 12 Nov 2026 "Cancelled: Interview Schedule || ..." said "your interview ...
has been cancelled Thursday, 12 November 2026 at 2:00 pm IST", and on 14 Oct a
Google Calendar "Canceled event: ... @ Thu Oct 15, 2026 5pm - 5:30pm (IST)" did
the same, but their readings kept no date or time, so every booking the
candidate held scored zero and both were refused as matching no booking.
"""

from __future__ import annotations

import pytest

from services import interview_auto_booking as booking
from tests.test_interview_auto_booking import install_store_fakes, result

OTHER = {"id": "slot-other", "name": "Rahul", "slot_confirmed": True, "date": "2099-11-13", "time": "11:00"}
TARGET = {"id": "slot-target", "name": "Rahul", "slot_confirmed": True, "date": "2099-11-12", "time": "14:00"}


def cancel(monkeypatch, message, rows=(OTHER, TARGET)):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch, rows=[dict(row) for row in rows])
    cancelled = []
    monkeypatch.setattr(booking.candidate_store, "cancel_interview_slot",
                        lambda **kwargs: cancelled.append(kwargs["candidate_id"]) or {"id": kwargs["candidate_id"]})
    value = result("interview_cancelled", date=None, time=None, timezone=None, round=None)
    value["company"]["name"] = None
    value["job"]["title"] = None
    outcome = booking.execute_auto_booking(
        mailbox={"id": "mb1", "candidate_id": "c1"},
        message={"provider_message_id": "cancel-1", "provider_thread_id": "cancel-thread",
                 "sent_at": "2099-11-12T07:42:08Z", **message},
        event={"mailbox_message_id": "mm-cancel", "notification": {"id": "n1"}}, result=value,
    )
    return outcome, cancelled


@pytest.mark.parametrize("message", [
    {"subject": "Cancelled: Interview Schedule || Senior Developer || Example",
     "body_text": "Hi, your interview for Senior Developer has been cancelled‌ Thursday, "
                  "12 November 2099 at 2:00 pm IST 45 minutes · video call"},
    {"subject": "Canceled event: PM Round | RAHUL @ Thu Nov 12, 2099 2pm - 2:30pm (IST)", "body_text": ""},
    {"subject": "Interview cancelled", "body_text": "The interview on 12 Nov 2099 at 08:30 AM UTC is cancelled."},
], ids=["cantik-plain-text", "google-calendar-subject", "utc-converted"])
def test_the_booking_at_the_stated_time_is_cancelled(monkeypatch, message):
    outcome, cancelled = cancel(monkeypatch, message)

    assert outcome["status"] == "Cancelled"
    assert cancelled == ["slot-target"]


@pytest.mark.parametrize("message", [
    {"subject": "Interview cancelled", "body_text": "Your interview on 12 November 2099 at 2:00 pm is cancelled."},
    {"subject": "Interview cancelled", "body_text": "Your interview at 2:00 pm IST is cancelled."},
    {"subject": "Interview cancelled", "body_text": "Your interview on 12 November 2099 IST is cancelled."},
], ids=["no-zone", "no-date", "no-clock"])
def test_a_partial_statement_matches_nothing(monkeypatch, message):
    outcome, cancelled = cancel(monkeypatch, message)

    assert outcome["failure_code"] == "BOOKING_NOT_FOUND"
    assert cancelled == []


def test_two_bookings_at_the_stated_time_are_still_ambiguous(monkeypatch):
    twin = {**TARGET, "id": "slot-twin"}
    outcome, cancelled = cancel(
        monkeypatch, {"subject": "Interview cancelled",
                      "body_text": "Your interview on 12 November 2099 at 2:00 pm IST is cancelled."},
        rows=(OTHER, TARGET, twin))

    assert outcome["failure_code"] == "BOOKING_AMBIGUOUS"
    assert cancelled == []
