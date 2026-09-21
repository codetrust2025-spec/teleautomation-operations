"""A reminder for a booked interview is that interview, not a second one.

On 18 Sep a reminder -- "Reminder: Upcoming interview | ..." -- restated an
interview already booked from its invite, under its own subject and with its
own end time (15:00-15:45 against the invite's 15:00-15:30). It carried the
same Teams meeting, but the duplicate guard also asked for the same subject
and the same end time, so the reminder became a second booking and both rows
were marked attended.

The same person, on the same date, at the same start, in the exact same Teams
meeting is one interview. Nothing looser: another start, another date,
another meeting or no meeting at all can still be a second interview, and a
calendar event on both sides is still decided by its own identity.
"""

import pytest

from services import interview_auto_booking as booking
from tests.test_interview_auto_booking import install_store_fakes, result, slot_writer

MEETING = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_reminded%40thread.v2/0?context=invite"
OTHER_MEETING = MEETING.replace("meeting_reminded", "meeting_elsewhere")
INVITE = {
    "subject": "L1 Interview for Engineer || Rahul with Example Co",
    "html_body_text": f'<a href="{MEETING}">Join the meeting now</a>',
    "sent_at": "2026-09-17T12:58:00Z",
}
BOOKED = {
    "id": "booked-from-invite", "name": "Rahul", "slot_confirmed": True,
    "date": "2099-09-18", "time": "15:00", "time_end": "15:30",
    "interview_calendar_uid": "", "interview_source_message_id": "invite-message",
    "interview_source_thread_id": "invite-thread",
}
REMINDER = {
    "provider_message_id": "reminder-message", "provider_thread_id": "reminder-thread",
    "subject": "Reminder: Upcoming interview | Example Co | Sep 18",
    "html_body": f'<a href="{MEETING}">Join</a>', "sent_at": "2026-09-18T08:31:00Z",
}


def book(monkeypatch, message, *, rows=(BOOKED,), calendar=None, **interview):
    """Run the reminder through the real executor against one stored booking."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    _, audits = install_store_fakes(monkeypatch, rows=[dict(row) for row in rows])
    monkeypatch.setattr(booking.mail_store, "booking_source_message",
                        lambda source_id: INVITE if source_id == "invite-message" else None)
    writes = []
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot",
                        slot_writer("second-booking", capture=writes))
    value = result(**{"date": "2099-09-18", "time": "03:00 PM", "end_time": "03:45 PM", **interview})
    if calendar:
        value["calendar"] = calendar
    outcome = booking.execute_auto_booking(
        mailbox={"id": "mb1", "candidate_id": "c1"}, message=message,
        event={"mailbox_message_id": "reminder-row", "notification": {"id": "n1"}},
        result=value,
    )
    return outcome, writes, audits


def test_a_reminder_with_its_own_subject_and_end_time_is_the_booked_interview(monkeypatch):
    outcome, writes, audits = book(monkeypatch, REMINDER)

    assert writes == [], "the reminder was booked as a second interview"
    assert outcome["failure_code"] == "DUPLICATE_BOOKING"
    assert outcome["status"] == "Duplicate Ignored"
    # "Already booked" names the booking it duplicates, so it can be followed.
    assert audits[-1]["booking_id"] == "booked-from-invite"
    assert audits[-1]["auto_booked"] is False
    assert outcome["notification"]["booking_id"] == "booked-from-invite"


@pytest.mark.parametrize(("subject", "end_time"), [
    (REMINDER["subject"], "03:30 PM"),  # its own subject, the invite's end time
    (INVITE["subject"], "03:45 PM"),    # the invite's subject, its own end time
])
def test_either_difference_alone_is_still_the_same_interview(monkeypatch, subject, end_time):
    outcome, writes, _ = book(monkeypatch, {**REMINDER, "subject": subject}, end_time=end_time)

    assert writes == []
    assert outcome["failure_code"] == "DUPLICATE_BOOKING"


@pytest.mark.parametrize("change", [
    "another_start", "another_date", "another_meeting", "no_meeting", "only_a_teams_help_link",
])
def test_anything_short_of_the_same_meeting_at_the_same_start_is_booked(monkeypatch, change):
    message, interview = dict(REMINDER), {}
    if change == "another_start":
        interview = {"time": "03:15 PM", "end_time": "03:45 PM"}
    if change == "another_date":
        interview = {"date": "2099-09-19"}
    if change == "another_meeting":
        message["html_body"] = f'<a href="{OTHER_MEETING}">Join</a>'
    if change == "no_meeting":
        message["html_body"] = "See you at the interview."
    if change == "only_a_teams_help_link":
        message["html_body"] = "https://teams.microsoft.com/help"

    outcome, writes, _ = book(monkeypatch, message, **interview)

    assert outcome["status"] == "Auto Booked"
    assert len(writes) == 1


def test_the_same_meeting_in_another_persons_booking_does_not_count(monkeypatch):
    someone_else = {**BOOKED, "id": "another-persons-booking", "name": "Another Person"}

    outcome, writes, _ = book(monkeypatch, REMINDER, rows=(someone_else,))

    assert outcome["status"] == "Auto Booked"
    assert len(writes) == 1


def test_two_calendar_events_are_still_decided_by_their_own_identity(monkeypatch):
    # Unchanged: when both mails carry a calendar event, the event ids decide
    # before the meeting link is looked at.
    booked = {**BOOKED, "interview_calendar_uid": "syntheticuid-invite-event"}

    outcome, writes, _ = book(monkeypatch, REMINDER, rows=(booked,),
                              calendar={"uid": "syntheticuid-reminder-event", "sequence": 0})

    assert outcome["status"] == "Auto Booked"
    assert len(writes) == 1


def test_a_booking_with_no_source_mail_is_not_matched_by_the_meeting(monkeypatch):
    # Nothing to compare the meeting with; hand bookings have their own rule
    # (exact date, start and end), which a different end time does not meet.
    by_hand = {**BOOKED, "interview_source_message_id": "", "interview_source_thread_id": ""}

    outcome, writes, _ = book(monkeypatch, REMINDER, rows=(by_hand,))

    assert outcome["status"] == "Auto Booked"
    assert len(writes) == 1
