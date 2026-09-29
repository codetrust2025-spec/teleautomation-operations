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

from services.unread_interview_mail import interview_evidence

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
