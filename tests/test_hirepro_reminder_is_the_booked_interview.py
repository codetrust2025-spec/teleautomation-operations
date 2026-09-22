"""A HirePro reminder is the interview it reminds about, not a second booking.

HirePro sends an interview as "<Company> - Interview Schedule" and then, on the
day, as "Interview Reminder - 11:00AM ...": separate threads, no calendar
event, no Teams link. On 22 Sep the reminder was booked beside the 11:00
interview booked from the invite the day before, so the candidate showed twice
in Confirmed slots. Both mails carry the candidate's interview room, and its
login token names that one interview, so the same person, date and start in
the same room is one interview. Nothing looser: another room, another start,
another person, or only HirePro's shared compatibility page are left to book.
"""

from __future__ import annotations

import base64
import json

import pytest

from services import interview_auto_booking as booking
from tests.test_interview_auto_booking import install_store_fakes, result, slot_writer

TOKEN = "00000000-1111-4222-8333-444444444444"
OTHER_TOKEN = "00000000-1111-4222-8333-555555555555"
CHECK = "https://ams.hirepro.in/testcompatibility/check.html"
INVITE_SUBJECT = "Example Co - Interview Schedule"
REMINDER_SUBJECT = "Interview Reminder - 11:00AM Tuesday, 22 September 2099"
BOOKED = {
    "id": "booked-from-invite", "name": "Rahul", "slot_confirmed": True,
    "date": "2099-09-22", "time": "11:00", "time_end": "11:30",
    "interview_calendar_uid": "", "interview_source_message_id": "invite-message",
    "interview_source_thread_id": "invite-thread",
}


def room(token=TOKEN, *, payload=None):
    """The interview-room link, as HirePro writes it."""
    body = json.dumps(payload if payload is not None else {"lt": f"Tkn:{token}"})
    return "https://ams.hirepro.in/v2/interview/home/" + base64.b64encode(body.encode()).decode()


def wrapped(link):
    """The same room behind the compatibility check, base64 in `data`."""
    return ("https://ams.hirepro.in/testcompatibility/interview/default/candidate.html?data="
            + base64.b64encode(link.encode()).decode())


def invite(link):
    return {"subject": INVITE_SUBJECT, "sent_at": "2026-09-21T06:45:37Z",
            "html_body_text": f'<a href="{CHECK}">Check your system</a> <a href="{link}">Join</a>'}


def reminder(link, *, subject=REMINDER_SUBJECT):
    body = f'<a href="{CHECK}">Check your system</a>' + (f' <a href="{link}">Join</a>' if link else "")
    return {"provider_message_id": "reminder-message", "provider_thread_id": "reminder-thread",
            "subject": subject, "html_body": body, "sent_at": "2026-09-22T04:30:20Z"}


def book(monkeypatch, message, *, source=None, rows=(BOOKED,), calendar=None, **interview):
    """Run the incoming mail through the real executor against the stored bookings."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    _, audits = install_store_fakes(monkeypatch, rows=[dict(row) for row in rows])
    stored_mail = source or invite(wrapped(room()))
    monkeypatch.setattr(booking.mail_store, "booking_source_message",
                        lambda source_id: stored_mail if source_id == "invite-message" else None)
    writes, linked = [], []
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot",
                        slot_writer("second-booking", capture=writes))
    monkeypatch.setattr(booking.candidate_store, "link_invite_to_slot",
                        lambda cid, **identity: linked.append(cid) or {"id": cid}, raising=False)
    value = result(**{"date": "2099-09-22", "time": "11:00 AM", "end_time": "11:30 AM", **interview})
    if calendar:
        value["calendar"] = calendar
    outcome = booking.execute_auto_booking(
        mailbox={"id": "mb1", "candidate_id": "c1"}, message=message,
        event={"mailbox_message_id": "reminder-row", "notification": {"id": "n1"}},
        result=value,
    )
    return outcome, writes, linked


def ignored_as_the_booked_interview(outcome, writes):
    return (writes == [] and outcome.get("failure_code") == "DUPLICATE_BOOKING"
            and outcome["status"] == "Duplicate Ignored"
            and outcome["notification"]["booking_id"] == "booked-from-invite")


def booked_separately(outcome, writes):
    return outcome["status"] == "Auto Booked" and len(writes) == 1


# ── The 22 Sep case, and the forms the room link takes ──────────────────────

def test_the_reminder_of_a_booked_hirepro_interview_is_ignored_as_a_duplicate(monkeypatch):
    outcome, writes, _ = book(monkeypatch, reminder(wrapped(room())))

    assert ignored_as_the_booked_interview(outcome, writes)


@pytest.mark.parametrize(("invite_link", "reminder_link"), [
    (wrapped(room()), room()),
    (room(), wrapped(room())),
    (room(), room()),
    (room(TOKEN), room(TOKEN.upper())),
], ids=["wrapped-then-direct", "direct-then-wrapped", "direct-both", "token-case"])
def test_the_same_room_in_either_form_is_the_same_interview(monkeypatch, invite_link, reminder_link):
    outcome, writes, _ = book(monkeypatch, reminder(reminder_link), source=invite(invite_link))

    assert ignored_as_the_booked_interview(outcome, writes)


def test_its_own_subject_and_end_time_do_not_make_it_another_interview(monkeypatch):
    outcome, writes, _ = book(monkeypatch, reminder(wrapped(room()), subject="Interview Notification"),
                              end_time="11:45 AM")

    assert ignored_as_the_booked_interview(outcome, writes)


def test_a_plus_sign_in_the_wrapped_base64_survives(monkeypatch):
    # The link is read raw, so a "+" in its base64 stays a "+" rather than the
    # space a query parser would make of it. "~" in the room link's own query
    # puts one there.
    link = wrapped(room() + "?utm=~~~")
    assert "+" in link.split("data=")[1]

    outcome, writes, _ = book(monkeypatch, reminder(link), source=invite(room()))

    assert ignored_as_the_booked_interview(outcome, writes)


def test_the_identity_is_the_decoded_login_token():
    both = {"html_body": f"{wrapped(room())} {room(TOKEN.upper())} {CHECK}"}

    assert booking._source_hirepro_interviews(both) == {("ams.hirepro.in", "interview-room", TOKEN)}


# ── Anything short of the same room at the same start is booked ─────────────

@pytest.mark.parametrize("link", [
    room(OTHER_TOKEN),
    wrapped(room(OTHER_TOKEN)),
    None,                                                    # only the shared compatibility page
    wrapped("not a link"),                                   # data that is no room link
    room(payload={"token": f"Tkn:{TOKEN}"}),                 # no login token
    room(payload={"lt": "Tkn:not-a-token"}),                 # a login token that is not one
    "https://ams.hirepro.in/v2/interview/home/%%%",          # not base64 at all
    wrapped(room()) + "&data=" + base64.b64encode(room(OTHER_TOKEN).encode()).decode(),  # two rooms
    room().replace("ams.hirepro.in", "genpact-candidatesupport.hirepro.in"),  # another host
], ids=["another-room", "another-room-wrapped", "check-page-only", "junk-data", "no-login-token",
        "bad-login-token", "not-base64", "two-rooms-in-one-link", "another-host"])
def test_anything_but_the_same_room_is_a_separate_booking(monkeypatch, link):
    outcome, writes, _ = book(monkeypatch, reminder(link))

    assert booked_separately(outcome, writes)


def test_the_same_room_at_another_start_is_not_merged(monkeypatch):
    """HirePro keeps the room when an interview moves; a new start is a new sitting."""
    outcome, writes, _ = book(monkeypatch, reminder(wrapped(room())), time="05:00 PM", end_time="05:30 PM")

    assert booked_separately(outcome, writes)


def test_the_same_room_in_another_persons_booking_never_merges(monkeypatch):
    someone_else = {**BOOKED, "id": "another-persons-booking", "name": "Another Person"}

    outcome, writes, _ = book(monkeypatch, reminder(wrapped(room())), rows=(someone_else,))

    assert booked_separately(outcome, writes)


def test_calendar_events_on_both_sides_still_decide_first(monkeypatch):
    booked = {**BOOKED, "interview_calendar_uid": "syntheticuid-invite-event"}

    outcome, writes, _ = book(monkeypatch, reminder(wrapped(room())), rows=(booked,),
                              calendar={"uid": "syntheticuid-reminder-event", "sequence": 0})

    assert booked_separately(outcome, writes)


def test_a_hand_booking_keeps_its_exact_slot_rule(monkeypatch):
    by_hand = {**BOOKED, "interview_source_message_id": "", "interview_source_thread_id": ""}

    outcome, writes, linked = book(monkeypatch, reminder(wrapped(room())), rows=(by_hand,), end_time="11:45 AM")
    assert booked_separately(outcome, writes) and linked == []

    outcome, writes, linked = book(monkeypatch, reminder(wrapped(room())), rows=(by_hand,))
    assert writes == [] and linked == ["booked-from-invite"]
