"""A Teams short link names one meeting exactly, just as the classic join link does.

Teams now sends meetings as teams.microsoft.com/meet/<meeting id>?p=<passcode>
rather than the long /l/meetup-join/ link, and the rule that a reminder in the
same meeting is the interview it reminds about only knew the long form: UST's
mail of 21 Sep carried nothing but a short link. The short link's identity is
its meeting id, digit for digit, with its passcode when it has one; nothing
else in the query identifies a meeting. The rest of the rule is unchanged --
same person, same date, same start; calendar events decide first; bookings
made by hand keep their exact-slot rule.
"""

import pytest

from services import interview_auto_booking as booking
from tests.test_interview_auto_booking import install_store_fakes, result, slot_writer

MEETING_ID = "111222333444555"
SHORT = f"https://teams.microsoft.com/meet/{MEETING_ID}?p=TestPassAbc0"
CLASSIC = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_classic%40thread.v2/0?context=invite"
INVITE_SUBJECT = "Interview scheduled with Example Co on Thu, September 18, 3:00 PM - 3:30 PM IST"
BOOKED = {
    "id": "booked-from-invite", "name": "Rahul", "slot_confirmed": True,
    "date": "2099-09-18", "time": "15:00", "time_end": "15:30",
    "interview_calendar_uid": "", "interview_source_message_id": "invite-message",
    "interview_source_thread_id": "invite-thread",
}


def invite(link):
    return {"subject": INVITE_SUBJECT, "html_body_text": f'<a href="{link}">Join the meeting now</a>',
            "sent_at": "2026-09-17T12:58:00Z"}


def reminder(link, *, subject="Reminder: Upcoming interview | Example Co | Sep 18", plain=False):
    body = "See you at the interview." if link is None else (
        f"Time: 3:00 PM (IST) at {link} . We look forward to it." if plain else f'<a href="{link}">Join</a>')
    return {"provider_message_id": "reminder-message", "provider_thread_id": "reminder-thread",
            "subject": subject, "body" if plain else "html_body": body, "sent_at": "2026-09-18T08:31:00Z"}


def book(monkeypatch, message, *, source=None, rows=(BOOKED,), calendar=None, **interview):
    """Run the incoming mail through the real executor against the stored bookings."""
    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    _, audits = install_store_fakes(monkeypatch, rows=[dict(row) for row in rows])
    stored_mail = source or invite(SHORT)
    monkeypatch.setattr(booking.mail_store, "booking_source_message",
                        lambda source_id: stored_mail if source_id == "invite-message" else None)
    writes, linked = [], []
    monkeypatch.setattr(booking.candidate_store, "assign_interview_slot",
                        slot_writer("second-booking", capture=writes))
    monkeypatch.setattr(booking.candidate_store, "link_invite_to_slot",
                        lambda cid, **identity: linked.append(cid) or {"id": cid}, raising=False)
    value = result(**{"date": "2099-09-18", "time": "03:00 PM", "end_time": "03:30 PM", **interview})
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


# 1 and 2 -----------------------------------------------------------------

@pytest.mark.parametrize("plain", [False, True], ids=["html-link", "plain-text-link"])
def test_same_short_link_with_a_different_subject_is_ignored_as_a_duplicate(monkeypatch, plain):
    outcome, writes, _ = book(monkeypatch, reminder(SHORT, plain=plain))

    assert ignored_as_the_booked_interview(outcome, writes)


def test_same_short_link_with_a_different_end_time_is_ignored_as_a_duplicate(monkeypatch):
    outcome, writes, _ = book(monkeypatch, reminder(SHORT, subject=INVITE_SUBJECT), end_time="03:45 PM")

    assert ignored_as_the_booked_interview(outcome, writes)


# 3 -----------------------------------------------------------------------

@pytest.mark.parametrize("link", [
    f"{SHORT}&anon=true",
    f"https://teams.microsoft.com/meet/{MEETING_ID}?anon=true&p=TestPassAbc0",
    f"{SHORT}&amp;launchAgent=join_launcher&amp;utm_source=mail",
    f"https://teams.microsoft.com/meet/{MEETING_ID}/?p=TestPassAbc0",
    f"https://teams.microsoft.com/meet/{MEETING_ID}?p=testpassabc0",
    f"https://Teams.Microsoft.com/meet/{MEETING_ID}?p=TestPassAbc0",
], ids=["extra-flag", "reordered", "html-escaped-tracking", "trailing-slash", "passcode-case", "host-case"])
def test_harmless_query_differences_are_the_same_meeting(monkeypatch, link):
    outcome, writes, _ = book(monkeypatch, reminder(link), end_time="03:45 PM")

    assert ignored_as_the_booked_interview(outcome, writes)


def test_the_identity_is_the_meeting_id_and_its_passcode_and_nothing_else():
    found = booking._source_teams_meetings({"html_body": f'<a href="{SHORT}&amp;anon=true&amp;utm_source=x">Join</a>'})

    assert found == {("teams.microsoft.com", f"/meet/{MEETING_ID}", "testpassabc0")}


# 4 -----------------------------------------------------------------------

@pytest.mark.parametrize("link", [
    "https://teams.microsoft.com/meet/555444333222111?p=TestPassAbc0",
    f"https://teams.microsoft.com/meet/{MEETING_ID}000?p=TestPassAbc0",
    "https://teams.microsoft.com/meet/111222333444?p=TestPassAbc0",
    f"https://teams.microsoft.com/meet/{MEETING_ID}?p=OtherPass999",
    f"https://teams.microsoft.com/meet/{MEETING_ID}",
    f"https://teams.microsoft.com/meet/{MEETING_ID}?p=TestPassAbc0&p=OtherPass999",
    "https://teams.microsoft.com/meet",
    f"https://teams.live.com/meet/{MEETING_ID}?p=TestPassAbc0",
], ids=["another-meeting-id", "id-with-more-digits", "id-with-fewer-digits", "another-passcode",
        "no-passcode-is-not-proof", "two-passcodes", "join-by-id-page", "another-service"])
def test_anything_but_the_same_short_meeting_is_a_separate_booking(monkeypatch, link):
    outcome, writes, _ = book(monkeypatch, reminder(link), end_time="03:45 PM")

    assert booked_separately(outcome, writes)


# 5 -----------------------------------------------------------------------

def test_without_a_meeting_link_the_existing_rules_decide(monkeypatch):
    outcome, writes, _ = book(monkeypatch, reminder(None), end_time="03:45 PM")

    assert booked_separately(outcome, writes)


def test_a_hand_booking_keeps_its_exact_slot_rule(monkeypatch):
    by_hand = {**BOOKED, "interview_source_message_id": "", "interview_source_thread_id": ""}

    # No mail on the hand booking, so its meeting cannot be compared: another
    # end time is another booking, exactly as before.
    outcome, writes, linked = book(monkeypatch, reminder(SHORT), rows=(by_hand,), end_time="03:45 PM")
    assert booked_separately(outcome, writes) and linked == []

    # The exact slot is still the hand booking, linked rather than booked again.
    outcome, writes, linked = book(monkeypatch, reminder(SHORT), rows=(by_hand,))
    assert writes == [] and linked == ["booked-from-invite"]
    assert outcome["failure_code"] == "DUPLICATE_BOOKING"


# 6 -----------------------------------------------------------------------

def test_the_same_short_link_in_another_persons_booking_never_merges(monkeypatch):
    someone_else = {**BOOKED, "id": "another-persons-booking", "name": "Another Person"}

    outcome, writes, _ = book(monkeypatch, reminder(SHORT), rows=(someone_else,))

    assert booked_separately(outcome, writes)


# 7 -----------------------------------------------------------------------

def test_classic_join_links_still_work(monkeypatch):
    outcome, writes, _ = book(monkeypatch, reminder(CLASSIC), source=invite(CLASSIC), end_time="03:45 PM")

    assert ignored_as_the_booked_interview(outcome, writes)


def test_a_classic_link_and_a_short_link_are_never_compared(monkeypatch):
    outcome, writes, _ = book(monkeypatch, reminder(SHORT), source=invite(CLASSIC), end_time="03:45 PM")

    assert booked_separately(outcome, writes)


# unchanged precedence ------------------------------------------------------

def test_calendar_events_on_both_sides_still_decide_first(monkeypatch):
    booked = {**BOOKED, "interview_calendar_uid": "syntheticuid-invite-event"}

    outcome, writes, _ = book(monkeypatch, reminder(SHORT), rows=(booked,),
                              calendar={"uid": "syntheticuid-reminder-event", "sequence": 0})

    assert booked_separately(outcome, writes)


def test_another_start_or_date_is_still_another_interview(monkeypatch):
    outcome, writes, _ = book(monkeypatch, reminder(SHORT), time="03:15 PM", end_time="03:45 PM")
    assert booked_separately(outcome, writes)

    outcome, writes, _ = book(monkeypatch, reminder(SHORT), date="2099-09-19")
    assert booked_separately(outcome, writes)
