"""A Cantik Prime reminder is the interview it reminds about, not a second booking.

On 12 Nov 2026 an invite ("your interview ... is booked", 14:00-14:45) and its
reminder ("Reminder: your interview with Cantik Prime", 14:00, read as
14:00-14:30) became two bookings at 14:00 for one candidate. Both mails carry the
same personal join link, assess.cantikprime.com/r/<token>; that token is the
interview. The shared portal login page identifies nothing.
"""

from __future__ import annotations

import pytest

from services import interview_auto_booking as booking
from tests.test_interview_auto_booking import install_store_fakes, result, slot_writer

TOKEN = "yCcxpsMmCdW0v3zW06skkER4950jfZ9x0P8n_qYvIKw"
OTHER = "_cfG_myNqDFzijNf3U25j_mYQcQZ44Q1FFWlC-fSCA0"
PORTAL = "https://assess.cantikprime.com/portal/login"
BOOKED = {
    "id": "booked-from-invite", "name": "Rahul", "slot_confirmed": True,
    "date": "2099-11-12", "time": "14:00", "time_end": "14:45",
    "interview_calendar_uid": "", "interview_source_message_id": "invite-message",
    "interview_source_thread_id": "invite-thread",
}


def join(token=TOKEN):
    return f"https://assess.cantikprime.com/r/{token}"


INVITE = {"subject": "Interview Schedule || Senior Developer || Example", "sent_at": "2099-11-12T05:00:00Z",
          "body_text": f"Your interview is booked. Join the interview {join()} Sign in: {PORTAL}"}


def reminder(link):
    return {"provider_message_id": "reminder-message", "provider_thread_id": "reminder-thread",
            "subject": "Reminder: your interview with Cantik Prime", "sent_at": "2099-11-12T05:20:00Z",
            "body_text": "A reminder of your interview." + (f" Join the interview {link}" if link else "")}


def book(monkeypatch, message):
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    install_store_fakes(monkeypatch, rows=[dict(BOOKED)])
    monkeypatch.setattr(booking.mail_store, "booking_source_message",
                        lambda source_id: INVITE if source_id == "invite-message" else None)
    writes = []
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot",
                        slot_writer("second-booking", capture=writes))
    monkeypatch.setattr(booking.candidate_store, "link_invite_to_slot",
                        lambda cid, **identity: {"id": cid}, raising=False)
    outcome = booking.execute_auto_booking(
        mailbox={"id": "mb1", "candidate_id": "c1"}, message=message,
        event={"mailbox_message_id": "reminder-row", "notification": {"id": "n1"}},
        result=result(date="2099-11-12", time="02:00 PM", end_time="02:30 PM"),
    )
    return outcome, writes


def test_the_reminder_with_the_same_join_link_is_the_booked_interview(monkeypatch):
    outcome, writes = book(monkeypatch, reminder(join()))

    assert writes == []
    assert outcome["failure_code"] == "DUPLICATE_BOOKING"
    assert outcome["notification"]["booking_id"] == "booked-from-invite"


@pytest.mark.parametrize("link", [join(OTHER), PORTAL, None, join("short")],
                         ids=["another-room", "portal-only", "no-link", "not-a-token"])
def test_anything_but_the_same_room_is_booked(monkeypatch, link):
    outcome, writes = book(monkeypatch, reminder(link))

    assert outcome["status"] == "Auto Booked" and len(writes) == 1


def test_the_identity_is_the_join_token():
    assert booking._source_cantik_interviews(INVITE) == {("assess.cantikprime.com", "interview-room", TOKEN)}
