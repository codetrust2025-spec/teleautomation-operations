"""An invitation re-issued under a new calendar event is still the one interview.

28 Sep, 05:14:35: the recruiter's invitation arrives and books a candidate's
12:45-13:30 consultation, carrying the sender's Outlook event
(040000008200E000...). 05:14:46: Gmail forwards the same invitation on as
"Invitation from an unknown sender: ...", under an event id of its own
(...@google.com). Same candidate, same day, same 12:45-13:30, the exact same
Teams meeting -- and two bookings, because a calendar event on both sides was
decided by the event ids alone and these two differ.

A mail provider re-issuing somebody else's invitation is the one case where
differing event ids do not mean two interviews. It takes the whole schedule to
overrule them: same date, same start, same end, and the exact same meeting.
Two interviews can share a room, or an hour, but not both at once.
"""

import pytest

from services import interview_auto_booking as booking
from tests.test_interview_auto_booking import install_store_fakes, result, slot_writer

MEETING = ("https://teams.microsoft.com/l/meetup-join/"
           "19%3ameeting_000111222333444555666%40thread.v2/0?context=consult")
ANOTHER_MEETING = MEETING.replace("000111222333", "777888999777")
# The two event ids production actually held, shortened: an Outlook
# GlobalObjectId on the booking and Google's own id on the re-issue.
OUTLOOK_EVENT = "04000000820000000000000000000000000000001111111122222222333333334444444455555555"
GOOGLE_EVENT = "syntheticuid000111222333@google.com"

INVITATION = {
    "subject": "Career Consultation - Rahul Example (Career Growth Pathway)",
    "html_body_text": f'Meeting Details: 12:45-13:30 <a href="{MEETING}">Join Meeting</a>',
    "sent_at": "2026-09-28T05:14:35Z",
}
BOOKED = {
    # "Rahul" is the harness's candidate: `install_store_fakes` links a stored
    # row to them by that name, which is how the row lands in the duplicate
    # check at all.
    "id": "booked-from-invitation", "name": "Rahul", "slot_confirmed": True,
    "date": "2099-09-28", "time": "12:45", "time_end": "13:30",
    "interview_calendar_uid": OUTLOOK_EVENT,
    "interview_source_message_id": "invitation-message",
    "interview_source_thread_id": "invitation-thread",
}
# What Gmail sent on eleven seconds later: its own thread, its own subject,
# the same meeting inside.
REISSUE = {
    "provider_message_id": "reissue-message", "provider_thread_id": "reissue-thread",
    "subject": "Invitation from an unknown sender: Career Consultation - Rahul Example",
    "html_body": f'You haven\'t interacted with this sender before. <a href="{MEETING}">Join Meeting</a>',
    "sent_at": "2026-09-28T05:14:46Z",
}


def book(monkeypatch, message=REISSUE, *, rows=(BOOKED,), calendar_uid=GOOGLE_EVENT, **interview):
    """Run the re-issued invitation through the real executor."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    _, audits = install_store_fakes(monkeypatch, rows=[dict(row) for row in rows])
    monkeypatch.setattr(booking.mail_store, "booking_source_message",
                        lambda source_id: INVITATION if source_id == "invitation-message" else None)
    writes = []
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot",
                        slot_writer("second-booking", capture=writes))
    value = result(**{"date": "2099-09-28", "time": "12:45 PM", "end_time": "01:30 PM", **interview})
    if calendar_uid:
        value["calendar"] = {"uid": calendar_uid, "sequence": 0}
    outcome = booking.execute_auto_booking(
        mailbox={"id": "mb1", "candidate_id": "c1"}, message=message,
        event={"mailbox_message_id": "reissue-row", "notification": {"id": "n1"}},
        result=value,
    )
    return outcome, writes, audits


# ── The 28 Sep case ─────────────────────────────────────────────────────────

def test_the_re_issued_invitation_does_not_book_a_second_interview(monkeypatch):
    outcome, writes, audits = book(monkeypatch)

    assert writes == [], "the re-issued invitation was booked as a second interview"
    assert outcome["failure_code"] == "DUPLICATE_BOOKING"
    assert outcome["status"] == "Duplicate Ignored"
    # "Already booked" names the booking it duplicates, so it can be followed.
    assert audits[-1]["booking_id"] == "booked-from-invitation"
    assert audits[-1]["auto_booked"] is False
    assert outcome["notification"]["booking_id"] == "booked-from-invitation"


def test_it_holds_whichever_invitation_arrived_first(monkeypatch):
    """The ids are symmetrical: Google's on the booking, Outlook's incoming."""
    booked = {**BOOKED, "interview_calendar_uid": GOOGLE_EVENT}

    outcome, writes, _ = book(monkeypatch, rows=(booked,), calendar_uid=OUTLOOK_EVENT)

    assert writes == []
    assert outcome["failure_code"] == "DUPLICATE_BOOKING"


# ── What still counts as two interviews ─────────────────────────────────────

@pytest.mark.parametrize(("change", "why"), [
    ("another_meeting", "a different room at the same hour is a different interview"),
    ("another_start", "the same room an hour later is a different interview"),
    ("another_end", "two events, one room, and they do not agree on the schedule"),
    ("no_meeting", "nothing names a meeting, so the event ids are all there is"),
])
def test_differing_event_ids_still_decide_everything_else(monkeypatch, change, why):
    message, interview = dict(REISSUE), {}
    if change == "another_meeting":
        message["html_body"] = f'<a href="{ANOTHER_MEETING}">Join</a>'
    if change == "another_start":
        interview = {"time": "01:45 PM", "end_time": "02:30 PM"}
    if change == "another_end":
        interview = {"end_time": "01:45 PM"}
    if change == "no_meeting":
        message["html_body"] = "Speak to you then."

    outcome, writes, _ = book(monkeypatch, message, **interview)

    assert outcome["status"] == "Auto Booked", why
    assert len(writes) == 1


def test_the_booking_must_have_a_mail_to_compare_the_meeting_with(monkeypatch):
    # An event id and no source mail: nothing proves the meetings are one, and
    # the hand-booked rule deliberately does not apply to a row that carries a
    # calendar event of its own.
    no_mail = {**BOOKED, "interview_source_message_id": "", "interview_source_thread_id": ""}

    outcome, writes, _ = book(monkeypatch, rows=(no_mail,))

    assert outcome["status"] == "Auto Booked"
    assert len(writes) == 1


def test_another_persons_identical_booking_is_untouched(monkeypatch):
    someone_else = {**BOOKED, "id": "another-persons-booking", "name": "Another Person"}

    outcome, writes, _ = book(monkeypatch, rows=(someone_else,))

    assert outcome["status"] == "Auto Booked"
    assert len(writes) == 1


# ── The rules this one sits beside, unchanged ───────────────────────────────

def test_the_same_event_id_is_still_the_same_interview():
    # Asked of the rule directly: through the executor the same event is not a
    # duplicate at all but an update to that booking, which is a different
    # path with its own tests.
    value = result(date="2099-09-28", time="12:45 PM", end_time="01:30 PM")
    value["calendar"] = {"uid": OUTLOOK_EVENT, "sequence": 0}

    assert booking._same_lifecycle_slot(
        BOOKED, result=value, message=REISSUE,
        schedule={"date": "2099-09-28", "time": "12:45", "time_end": "13:30"},
    ) is True


def test_a_mail_with_no_event_of_its_own_still_follows_the_meeting_rule(monkeypatch):
    """No calendar event incoming: same start plus the same meeting is enough,
    end time or no end time -- the reminder rule, untouched by this change."""
    outcome, writes, _ = book(monkeypatch, calendar_uid=None, end_time="02:00 PM")

    assert writes == []
    assert outcome["failure_code"] == "DUPLICATE_BOOKING"
