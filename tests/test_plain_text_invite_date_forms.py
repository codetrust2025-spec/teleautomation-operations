"""An invitation's day must be read however the recruiter spelled it.

Shailaja was sent an interview mail for 5:00 PM and no slot was ever booked.
The message named its day the way recruiters usually do - "3rd Sep", with no
year - and the plain-text parser only ever matched a day-first spelling that
carried an explicit year. "September 3, 2026", "Sep 3" and "3rd September"
all yielded no date at all.

That is not a harmless miss. `process_message` consults this parser before it
consults the AI, and when routing decides a message is not worth an AI call
the parser is the only thing left that can classify it: no date means the
invitation is filed as IGNORED_NOT_OFFER_RELATED and the slot is never
created. So these cases are pinned at the real entry point,
`trusted_interview_result`, not only against the date helper.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services.calendar_invite_parser import _plain_date, trusted_interview_result

SENT = datetime(2026, 9, 2, 9, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("written", [
    "3rd September 2026",       # the one spelling that already worked
    "3 September 2026",
    "3rd September",            # no year - the reported mail
    "3 Sep",
    "Wednesday, 3 September",
    "September 3, 2026",        # month first, which never parsed at all
    "September 3",
    "Sep 3, 2026",
    "Sept 3",
    "Wed, Sep 3",
])
def test_every_spelling_of_the_same_day_reads_as_that_day(written):
    assert _plain_date(f"Interview on {written} at 5:00 PM IST", SENT).date().isoformat() == "2026-09-03"


def test_a_year_less_day_resolves_against_the_message_send_date():
    """December invitation, January interview - the year has to roll over."""
    december = datetime(2026, 12, 28, tzinfo=timezone.utc)
    assert _plain_date("interview on 4th January", december).date().isoformat() == "2027-01-04"

    january = datetime(2027, 1, 3, tzinfo=timezone.utc)
    assert _plain_date("interview on 30th December", january).date().isoformat() == "2026-12-30"


def test_a_stated_year_still_wins_over_a_bare_day():
    """Text that already parsed keeps parsing to exactly the same day."""
    text = "sent 3 September; the interview is on September 15, 2026"
    assert _plain_date(text, SENT).date().isoformat() == "2026-09-15"


def test_the_numeric_indian_form_is_untouched():
    assert _plain_date("25-07-2026 10:30 IST", SENT).date().isoformat() == "2026-07-25"


@pytest.mark.parametrize("text", [
    "",
    "no date anywhere in this message",
    "we will confirm the schedule shortly",
    "interview in September 2026",   # a month and a year is not a day
    "31 September 2026",             # not a real day
    "interview on 29 February",      # no leap day within a year of the message
])
def test_text_without_a_readable_day_still_yields_nothing(text):
    assert _plain_date(text, SENT) is None


def test_the_year_needs_no_separator():
    """TCS writes the schedule as "15th September26" - one token, still a date."""
    assert _plain_date("Date: 15th September26", SENT).date().isoformat() == "2026-09-15"


def test_a_month_name_starting_an_ordinary_word_is_not_a_date():
    """Forgiving the year must not invent a day out of prose."""
    assert _plain_date("3 Marching orders were issued", SENT) is None


def test_an_hour_is_never_mistaken_for_a_year():
    """"Sep 3 10:30 AM" is the 3rd at half past ten, not the year 2010."""
    assert _plain_date("Sep 3 10:30 AM IST", SENT).date().isoformat() == "2026-09-03"


# ── the real entry point ─────────────────────────────────────────────────────

def _invite_mail(date_text: str, *, sent_at: datetime) -> dict:
    return {
        "sender_name": "Priya Nair",
        "sender_email": "talent-acquisition@infosys.com",
        "recipient_email": "shailaja.candidate@gmail.com",
        "subject": "Interview Invite: Shailaja",
        "body": (
            "We would like to invite you for an interview.\n"
            f"Interview Details: {date_text} at 5:00 PM IST\n"
            "Link to join: https://teams.microsoft.com/l/meetup-join/example\n"
        ),
        "message_direction": "INBOUND",
        "to_metadata": ["shailaja.candidate@gmail.com"],
        "sent_at": sent_at,
        "authentication_results": "spf=pass smtp.mailfrom=infosys.com; dmarc=pass header.from=infosys.com",
        "received_spf": "pass domain of infosys.com permitted sender",
    }


@pytest.mark.parametrize("date_text", [
    "3rd September 2026",
    "3rd September",
    "September 3, 2026",
    "Sep 3",
])
def test_the_5pm_invitation_is_classified_without_the_ai(date_text):
    """The reported mail: a 5:00 PM interview that produced no slot at all."""
    sent = datetime(2026, 9, 2, 9, 30, tzinfo=timezone.utc)
    result = trusted_interview_result(_invite_mail(date_text, sent_at=sent), [])

    assert result is not None, "an unread day drops the invitation entirely"
    assert result["classification"] == "interview_confirmed"
    assert result["classification_source"] == "STRUCTURED_EMAIL_VERIFIED"
    assert result["interview"]["date"] == "2026-09-03"
    assert result["interview"]["time"] == "05:00 PM"
    assert result["interview"]["timezone"] == "Asia/Kolkata"


def test_a_future_year_less_invitation_is_still_a_current_event():
    """The booking engine only books a schedule it is told is ahead."""
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    future = now + timedelta(days=9)
    result = trusted_interview_result(
        _invite_mail(f"{future:%-d} {future:%B}", sent_at=now.astimezone(timezone.utc)), [],
    )

    assert result is not None
    assert result["interview"]["date"] == future.date().isoformat()
    assert result["is_current_event"] is True


def test_a_mail_with_no_readable_day_is_still_left_to_the_ai():
    """The guard survives: only spelling is forgiven, never a missing day."""
    assert trusted_interview_result(
        _invite_mail("a date to be confirmed", sent_at=SENT), [],
    ) is None
