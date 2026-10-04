"""A calendar service carrying an employer's invitation may book it. Nothing else may.

The organiser-alignment rule refuses an invitation that did not come from its
organiser, which is how a forwarded one is kept out. A calendar service breaks
that by construction: Zoho sends from `noreply@zohocalendar.com` whoever made
the invitation. Three real interviews in September were refused for it, read by
nobody, and never booked -- the candidate was offered the job before anyone
knew the interviews existed.

Replayed over the 431 stored invitations before the change: 206 book today, 10
fail only this gate, 8 of those come from a relay, and 5 of the 8 also name an
organiser at a company address. Those 5 are what this admits. The other 3 carry
no ORGANIZER at all and still do not book.
"""
from __future__ import annotations

import pytest

from services.calendar_invite_parser import relayed_by_a_calendar_service, trusted_interview_result

INVITE = """BEGIN:VCALENDAR
METHOD:REQUEST
BEGIN:VEVENT
UID:000111222333444555@zohocalendar.com
DTSTART;TZID=Asia/Kolkata:20990918T150000
DTEND;TZID=Asia/Kolkata:20990918T153000
ORGANIZER;CN=Recruiter:mailto:{organizer}
ATTENDEE;CN=Candidate:mailto:candidate@example.invalid
SUMMARY:L1 Interview - Java Developer - Candidate Name
END:VEVENT
END:VCALENDAR
"""


def mail(sender="noreply@zohocalendar.com", organizer="recruiter@employer.example",
         subject="Invitation: L1 Interview - Java Developer - Candidate Name"):
    decoded = {
        "sender_email": sender,
        "recipient_email": "candidate@example.invalid",
        "subject": subject,
        "authentication_results": f"mx.google.com; dkim=pass header.d={sender.split('@')[-1]}; "
                                  f"spf=pass; dmarc=pass header.from={sender.split('@')[-1]}",
        "body": "You are invited to the interview.",
    }
    attachments = [{"filename": "invite.ics", "text": INVITE.format(organizer=organizer)}]
    return decoded, attachments


def books(**kwargs):
    decoded, attachments = mail(**kwargs)
    return trusted_interview_result(decoded, attachments)


# ── the case that went missing ──────────────────────────────────────────────

def test_a_relayed_invitation_from_an_employer_books():
    result = books()

    assert result, "a signed Zoho invitation for a real interview must not be refused"
    assert result["classification"] == "interview_confirmed"
    assert result["interview"]["date"] == "2099-09-18"


@pytest.mark.parametrize("relay", [
    "noreply@zohocalendar.com", "admin@hirepro.in", "notification@risebird.io",
    "interviewassistant.employer.com@viazohorecruit.com",
])
def test_each_relay_we_name(relay):
    assert books(sender=relay)


# ── and what it still refuses ───────────────────────────────────────────────

def test_a_relay_carrying_a_personal_organiser_does_not_book():
    # September's "are you ready to submit your original certificates"
    # invitation was organised from a gmail address. A relay will carry
    # anybody's invitation; this is the line that keeps it out.
    assert books(organizer="someone@gmail.com") is None


def test_an_invitation_that_names_no_organiser_does_not_book():
    # Three RiseBird invitations in the store have no ORGANIZER at all. An
    # invitation attributable to nobody stays on Needs reading.
    decoded, attachments = mail()
    attachments[0]["text"] = attachments[0]["text"].replace(
        "ORGANIZER;CN=Recruiter:mailto:recruiter@employer.example\n", "")

    assert trusted_interview_result(decoded, attachments) is None


def test_a_stranger_forwarding_an_invitation_still_does_not_book():
    # The rule this relaxes: the mail must come from the organiser. Anyone not
    # on the relay list is unchanged.
    assert books(sender="someone.else@forwarder.example") is None


def test_an_unauthenticated_relay_does_not_book():
    decoded, attachments = mail()
    decoded["authentication_results"] = "mx.google.com; dkim=fail; spf=fail; dmarc=fail"

    assert trusted_interview_result(decoded, attachments) is None


def test_the_relay_cannot_stand_in_for_the_employer():
    """An invitation is an employer's because the ORGANISER is an outside
    organisation -- never because the postman is.

    Without this the relay's own domain satisfied "an outside organisation
    inviting this candidate", and any small meeting it carried booked an
    interview: a party, or as here the candidate's own diary entry.
    """
    decoded, attachments = mail(organizer="candidate@example.invalid",
                                subject="Invitation: Dentist")
    attachments[0]["text"] = attachments[0]["text"].replace(
        "SUMMARY:L1 Interview - Java Developer - Candidate Name", "SUMMARY:Dentist")

    assert trusted_interview_result(decoded, attachments) is None


def test_a_relayed_meeting_with_no_interview_word_still_needs_an_outside_organiser():
    # The keyword path is untouched: an employer's invitation books whether or
    # not it says "interview", exactly as a direct one does.
    decoded, attachments = mail(subject="Invitation: Discussion")
    attachments[0]["text"] = attachments[0]["text"].replace(
        "SUMMARY:L1 Interview - Java Developer - Candidate Name", "SUMMARY:Discussion")

    assert trusted_interview_result(decoded, attachments)


def test_the_recipient_must_still_be_on_the_invitation():
    decoded, attachments = mail()
    attachments[0]["text"] = attachments[0]["text"].replace(
        "candidate@example.invalid", "somebody.else@example.invalid")

    assert trusted_interview_result(decoded, attachments) is None


# ── the rule itself ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(("organizer", "sender", "relayed"), [
    ("recruiter@employer.example", "noreply@zohocalendar.com", True),
    ("recruiter@employer.example", "recruiter@employer.example", False),   # aligned already
    ("recruiter@employer.example", "hr@employer.example", False),          # same domain already
    ("recruiter@gmail.com", "noreply@zohocalendar.com", False),            # personal organiser
    ("", "noreply@zohocalendar.com", False),                               # nobody organised it
    ("recruiter@employer.example", "", False),
    ("recruiter@employer.example", "calendar-notification@google.com", False),  # not on the list
    ("recruiter@employer.example", "someone@forwarder.example", False),
])
def test_relayed_by_a_calendar_service(organizer, sender, relayed):
    assert relayed_by_a_calendar_service(organizer, sender) is relayed
