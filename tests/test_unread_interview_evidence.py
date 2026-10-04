"""What counts as "this mail describes a real interview", without asking a model.

The bar is a specific meeting: a calendar event, a meeting room the booking
code already recognises, or a Google Meet room. Never a subject line, a company
name or a date on its own -- those arrive by the thousand from job boards, and
a queue full of them is the same as no queue.

The three shapes that went missing in September are all here: a Zoho invitation
relayed on the recruiter's behalf, a Teams link in the body, a HirePro room.
"""
from __future__ import annotations

import pytest

from services.unread_interview_mail import cancels_an_interview, interview_evidence

ICS = """BEGIN:VCALENDAR
METHOD:REQUEST
BEGIN:VEVENT
UID:000111222333444@zohocalendar.com
DTSTART;TZID=Asia/Kolkata:20990918T150000
ORGANIZER;CN=Recruiter:mailto:recruiter@example.invalid
ATTENDEE:mailto:candidate@example.invalid
SUMMARY:L1_Java Developer
END:VEVENT
END:VCALENDAR
"""
NO_UID = ICS.replace("UID:000111222333444@zohocalendar.com\n", "")


def mail(**overrides):
    return {"subject": "Invitation", "body_text": "", "html_body_text": "", **overrides}


def ics_attachment(text=ICS, filename="invite.ics"):
    return [{"filename": filename, "text": text}]


# ── a specific meeting ──────────────────────────────────────────────────────

def test_a_calendar_invitation_counts_and_says_when():
    found = interview_evidence(mail(), ics_attachment())

    assert found and "calendar invitation" in found
    assert "18 Sep" in found


def test_a_calendar_invitation_relayed_by_the_calendar_service_still_counts():
    # The exact shape the booking path refuses: Zoho sends it, the recruiter
    # organises it. Refusing to book it is one thing; saying nothing is not.
    found = interview_evidence(
        mail(sender_email="noreply@zohocalendar.com"), ics_attachment())

    assert found


def test_a_teams_meeting_in_the_body_counts():
    body = 'Join <a href="https://teams.microsoft.com/l/meetup-join/19%3ameeting_000111222%40thread.v2/0">here</a>'

    assert "Teams meeting" in interview_evidence(mail(html_body_text=body), [])


def test_a_hirepro_interview_room_counts():
    body = ("Start your interview: https://ams.hirepro.in/v2/interview/home/"
            "eyJsdCI6IlRrbjowMDAwMDAwMC0xMTExLTQyMjItODMzMy00NDQ0NDQ0NDQ0NDQifQ==")

    found = interview_evidence(mail(body_text=body), [])

    assert found and "HirePro" in found


def test_a_google_meet_room_counts():
    found = interview_evidence(mail(body_text="Meet: https://meet.google.com/ryf-ipbr-tow"), [])

    assert found == "a Google Meet room link"


# ── and nothing weaker ──────────────────────────────────────────────────────

@pytest.mark.parametrize(("what", "message", "attachments"), [
    ("a subject that says interview", mail(subject="Interview Invitation: apply now"), []),
    ("a date with no meeting", mail(body_text="Your interview is on 18 September at 3 PM."), []),
    ("a company name", mail(body_text="Tekenlight Solutions is hiring Java developers."), []),
    ("a careers link", mail(body_text="See https://careers.example.invalid/jobs/123"), []),
    ("a Teams help page", mail(body_text="https://teams.microsoft.com/help"), []),
    ("a calendar file with no event id", mail(), ics_attachment(NO_UID)),
    ("a PDF that is not a calendar", mail(), [{"filename": "jd.pdf", "text": "Java Developer"}]),
    ("an empty mail", mail(), []),
])
def test_what_does_not_count(what, message, attachments):
    assert interview_evidence(message, attachments) is None, what


def test_a_google_meet_link_needs_a_real_room_code():
    # meet.google.com/ alone, or a marketing path, is not a room.
    assert interview_evidence(mail(body_text="https://meet.google.com/"), []) is None
    assert interview_evidence(mail(body_text="https://meet.google.com/landing"), []) is None


# ── an interview called off reads as one ────────────────────────────────────

def test_a_cancelled_invitation_is_recognised_as_a_cancellation():
    # It is still worth reading -- a booking may need cancelling -- but telling
    # somebody to "book the interview by hand" is the opposite of the truth.
    cancel = ICS.replace("METHOD:REQUEST", "METHOD:CANCEL")

    assert cancels_an_interview(mail(), ics_attachment(cancel)) is True
    assert interview_evidence(mail(), ics_attachment(cancel)), "and it still surfaces"


def test_a_cancelled_status_counts_too():
    cancel = ICS.replace("SUMMARY:L1_Java Developer", "STATUS:CANCELLED\nSUMMARY:L1_Java Developer")

    assert cancels_an_interview(mail(), ics_attachment(cancel)) is True


@pytest.mark.parametrize("subject", ["Canceled: L1 Interview", "Cancelled: L1 Interview",
                                     "  canceled: l1 interview"])
def test_the_subject_alone_says_it_too(subject):
    # Some cancellations arrive with no calendar file at all.
    assert cancels_an_interview(mail(subject=subject, body_text="https://meet.google.com/ryf-ipbr-tow"), []) is True


@pytest.mark.parametrize("subject", ["Invitation: L1 Interview", "Rescheduled: L1 Interview",
                                     "Your interview was not cancelled"])
def test_an_invitation_is_not_a_cancellation(subject):
    assert cancels_an_interview(mail(subject=subject), ics_attachment()) is False


# ── and the alert tells the reader what to do with it ───────────────────────

class _Cursor:
    def __init__(self, sink):
        self.sink = sink
        self.description = []

    def execute(self, sql, params=None):
        self.sink.append(params)

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Connection(_Cursor):
    def cursor(self):
        return _Cursor(self.sink)


def _alert_text(monkeypatch, *, cancelling):
    from core import recruitment_mail_store as store

    sink = []
    monkeypatch.setattr(store, "get_connection", lambda: _Connection(sink))
    monkeypatch.setattr(store, "_candidate_snapshot", lambda *_a, **_k: ("Invented Person", ""))
    store.record_unread_interview_alert(
        {"provider_message_id": "m-1", "candidate_id": "c-1", "subject": "x"},
        evidence="a Teams meeting", cancelling=cancelling,
    )
    params = sink[-1]
    return params[14], params[16]          # ai_summary, recommended_action


def test_an_unread_cancellation_asks_for_a_cancellation(monkeypatch):
    summary, action = _alert_text(monkeypatch, cancelling=True)
    assert "calls an interview off" in summary and "cancel the booking" in summary
    assert "cancel the booking by hand" in action
    assert "book the interview" not in summary + action


def test_an_unread_invitation_still_asks_for_a_booking(monkeypatch):
    summary, action = _alert_text(monkeypatch, cancelling=False)
    assert "book the interview by hand" in summary and "book the interview by hand" in action
