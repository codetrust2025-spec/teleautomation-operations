"""A recruiter titles the invite with the role, not with the word "interview".

Built from the real message that was lost. Sourcebae invited Lavanya to
"Fullstack Ai || Lavanya" on 11 Aug 2026, 4:15–4:45pm. The invitation was
cryptographically authenticated, the organizer matched the sender, the
candidate was a named attendee, and it carried a valid start and timezone —
and it was discarded because none of the words "interview", "technical round",
"managerial round" or "hr round" appeared anywhere in it.

The keyword is still sufficient. It is no longer necessary.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.calendar_invite_parser import trusted_interview_result

IST = ZoneInfo("Asia/Kolkata")


def ics(
    *,
    summary="Fullstack Ai || Lavanya",
    organizer="neha@sourcebae.com",
    attendees=("lavanya.rathore@example.com", "neha@sourcebae.com"),
    uid="syntheticuid000000000001ab@google.com",
    sequence=0,
    method="REQUEST",
    description="Join https://meet.google.com/tst-fake-mtg",
):
    start = (datetime.now(IST) + timedelta(days=3)).replace(
        hour=16, minute=15, second=0, microsecond=0
    )
    end = start + timedelta(minutes=30)
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"METHOD:{method}", "BEGIN:VEVENT",
        f"UID:{uid}", f"SEQUENCE:{sequence}",
        f"DTSTART;TZID=Asia/Kolkata:{start:%Y%m%dT%H%M%S}",
        f"DTEND;TZID=Asia/Kolkata:{end:%Y%m%dT%H%M%S}",
        f"SUMMARY:{summary}",
        f"ORGANIZER;CN=Neha Mishra:mailto:{organizer}",
    ]
    lines += [f"ATTENDEE;CN=Guest:mailto:{a}" for a in attendees]
    lines += [f"DESCRIPTION:{description}", "END:VEVENT", "END:VCALENDAR", ""]
    return "\r\n".join(lines)


def mail(**changes):
    value = {
        "sender_name": "Neha Mishra",
        "sender_email": "neha@sourcebae.com",
        "recipient_email": "lavanya.rathore@example.com",
        # The real subject: Gmail's unknown-sender wrapper, and still no
        # mention of an interview anywhere in it.
        "subject": (
            "Invitation from an unknown sender: Fullstack Ai || Lavanya "
            "@ Tue Aug 11, 2026 4:15pm - 4:45pm (GMT+5:30) "
            "(lavanya.rathore@example.com)"
        ),
        "authentication_results": (
            "mx.google.com; spf=pass smtp.mailfrom=sourcebae.com; "
            "dmarc=pass header.from=sourcebae.com"
        ),
        "received_spf": "pass (google.com: domain of neha@sourcebae.com designates 203.0.113.4)",
    }
    value.update(changes)
    return value


def attachments(text=None):
    return [{"filename": "invite.ics", "mime_type": "text/calendar", "text": text or ics()}]


# ── the regression ───────────────────────────────────────────────────────────


def test_a_role_titled_invite_is_accepted_without_the_word_interview():
    result = trusted_interview_result(mail(), attachments())

    assert result is not None, "the invitation that was lost must now be accepted"
    assert result["classification"] == "interview_confirmed"
    assert result["interview"]["time"] == "04:15 PM"
    assert result["interview"]["timezone"] == "Asia/Kolkata"
    assert "interview" not in result["job"]["title"].lower()


def test_the_gmail_unknown_sender_wrapper_does_not_block_acceptance():
    """"Invitation from an unknown sender:" is Gmail's own prefix.

    It says Google has not seen this correspondent before. It says nothing
    about whether the invitation is genuine, and the ICS is authenticated
    independently.
    """
    plain = mail(subject="Fullstack Ai || Lavanya")
    wrapped = mail()

    assert trusted_interview_result(plain, attachments()) is not None
    assert trusted_interview_result(wrapped, attachments()) is not None


def test_the_calendar_uid_and_sequence_are_exposed_for_dedupe():
    """The covering mail and the invitation are one interview; the UID is what
    lets anything downstream know that."""
    result = trusted_interview_result(mail(), attachments())
    assert result["calendar_uid"] == "syntheticuid000000000001ab@google.com"
    assert result["calendar_sequence"] == 0


def test_a_reschedule_keeps_the_uid_and_advances_the_sequence():
    first = trusted_interview_result(mail(), attachments())
    moved = trusted_interview_result(mail(), attachments(ics(sequence=2)))

    assert moved is not None
    assert moved["calendar_uid"] == first["calendar_uid"]
    assert moved["calendar_sequence"] == 2


# ── what must still be refused ───────────────────────────────────────────────


def test_an_invite_from_a_consumer_mailbox_is_not_an_interview():
    """A friend's Gmail invitation is somebody's diary, not an employer."""
    personal = mail(
        sender_email="friend@example.com",
        subject="Dinner",
        authentication_results="mx.google.com; spf=pass smtp.mailfrom=gmail.com; dmarc=pass header.from=gmail.com",
        received_spf="pass (google.com: domain of friend@example.com designates 203.0.113.4)",
    )
    payload = attachments(ics(summary="Dinner", organizer="friend@example.com",
                              attendees=("lavanya.rathore@example.com", "friend@example.com")))
    assert trusted_interview_result(personal, payload) is None


def test_a_mass_invitation_is_not_an_interview():
    """A webinar is authenticated, external and corporate — and not a round."""
    crowd = tuple(
        ["lavanya.rathore@example.com"] + [f"guest{i}@example.com" for i in range(9)]
    )
    payload = attachments(ics(summary="Careers Open Day", attendees=crowd))
    assert trusted_interview_result(mail(subject="Careers Open Day"), payload) is None


def test_an_invitation_the_candidate_is_not_attending_is_refused():
    payload = attachments(ics(attendees=("someone.else@example.com", "neha@sourcebae.com")))
    assert trusted_interview_result(mail(), payload) is None


def test_a_cross_domain_organizer_is_still_refused():
    payload = attachments(ics(organizer="attacker@evil.example"))
    assert trusted_interview_result(mail(), payload) is None


def test_an_unauthenticated_sender_is_still_refused():
    unauthenticated = mail(authentication_results="mx.google.com; spf=fail; dmarc=fail", received_spf="fail")
    assert trusted_interview_result(unauthenticated, attachments()) is None


def test_a_malformed_calendar_is_refused():
    assert trusted_interview_result(mail(), attachments("not a calendar at all")) is None
    assert trusted_interview_result(mail(), attachments("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")) is None


def test_a_reply_rather_than_a_request_is_refused():
    assert trusted_interview_result(mail(), attachments(ics(method="REPLY"))) is None


# ── the keyword route must keep working exactly as before ────────────────────


def test_the_keyword_still_accepts_on_its_own():
    """Nothing that worked before may stop working.

    Same-domain sender and recipient, which the employer route refuses — the
    invitation is accepted here purely because it says "Interview".
    """
    internal = mail(
        sender_email="hr@sourcebae.com",
        recipient_email="candidate@sourcebae.com",
        subject="Interview | Backend Engineer",
        authentication_results="mx.google.com; spf=pass smtp.mailfrom=sourcebae.com; dmarc=pass header.from=sourcebae.com",
    )
    payload = attachments(ics(
        summary="Interview | Backend Engineer",
        organizer="hr@sourcebae.com",
        attendees=("candidate@sourcebae.com", "hr@sourcebae.com"),
    ))
    assert trusted_interview_result(internal, payload) is not None
