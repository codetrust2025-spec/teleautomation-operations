"""An invite titled by the candidate's name is still an interview.

Two real Microsoft Teams invitations were never booked. Both were
authenticated RFC 5545 requests from a recruiting organisation, addressed to
the monitored candidate as an ATTENDEE, with a start, an end and a join link,
and the parser accepted each one as a TRUSTED interview. Their titles were
"<candidate>-TR1" and "Call for <candidate>"; their bodies were the Teams
boilerplate Exchange writes.

The relevance model contradicted itself on both, and a contradiction goes to a
deterministic tie-breaker. That tie-breaker booked only when the text named
hiring or a role from two word lists, and a name is neither, so it answered
RETRY on every attempt. One invite arrived a day ahead and was still parked
when its interview took place; the other ran out of attempts after twelve.

The parser had already answered the question the word lists were asking. When
a title carries no interview word it accepts the invitation only on its
employer shape -- an outside, non-consumer organisation inviting this
candidate to a small meeting. The tie-breaker now reads that answer, from the
same function, instead of asking again.

These tests drive the chain `process_message` runs: the parser's trusted
result, then `calendar_invite_verdict` with that result and the mail, then the
schedule the booking would write. Every person, address and meeting here is a
pseudonym at a reserved domain; the structure is the real invitations' own.
"""

from __future__ import annotations

import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services import calendar_invite_parser, recruitment_mail_agent
from services.calendar_interview_evidence import evidence_for
from services.calendar_invite_parser import trusted_interview_result
from services.interview_auto_booking import BookingValidationError, normalized_schedule
from services.recruitment_automation import AutomationState, outcome_for
from services.recruitment_mail_agent import calendar_invite_verdict

IST = ZoneInfo("Asia/Kolkata")
CANDIDATE = "irfan.kelkar@example.com"
RECRUITER = "recruiter.one@example.org"
PANEL = [(f"Panel {n}", f"panel.{n}@example.org") for n in ("one", "two", "three", "four")]
TEAMS_JOIN = "https://teams.microsoft.com/l/meetup-join/00000000-1111-4222-8333-444444444444"

# Both pairings `calendar_invite_verdict` treats as a contradiction.
CONTRADICTIONS = [
    ("NOT_ESTABLISHED", "RECIPIENT_HIRING_PROCESS"),
    ("ESTABLISHED", "GENERAL"),
]


def answer(decision, kind):
    return {
        "decision": decision, "message_kind": kind, "confidence": 0.85,
        "evidence": [{"source": "EMAIL_BODY", "text": "Microsoft Teams meeting"}],
        "reason": "relevance answer as the model returned it",
    }


def exchange_invite(*, summary, attendees, organizer=RECRUITER,
                    start="20990918T133000", end="20990918T140000"):
    """An Exchange 2010 Teams invitation, property for property."""
    # Exchange escapes line breaks inside DESCRIPTION as a literal \n.
    description = "\\n".join((
        "", "_" * 40, "Microsoft Teams meeting", f"Join: {TEAMS_JOIN}", "_" * 40,
    ))
    return "\r\n".join([
        "BEGIN:VCALENDAR", "METHOD:REQUEST", "PRODID:Microsoft Exchange Server 2010",
        "VERSION:2.0",
        "BEGIN:VTIMEZONE", "TZID:India Standard Time",
        "BEGIN:STANDARD", "DTSTART:16010101T000000", "TZOFFSETFROM:+0530",
        "TZOFFSETTO:+0530", "END:STANDARD",
        "BEGIN:DAYLIGHT", "DTSTART:16010101T000000", "TZOFFSETFROM:+0530",
        "TZOFFSETTO:+0530", "END:DAYLIGHT",
        "END:VTIMEZONE",
        "BEGIN:VEVENT",
        f"ORGANIZER;CN=Recruiter One:mailto:{organizer}",
        *(
            "ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;"
            f"CN={name}:mailto:{address}"
            for name, address in attendees
        ),
        f"DESCRIPTION;LANGUAGE=en-US:{description}",
        "UID:SYNTHETICUID000000000000000000000000000000000000000000000001",
        f"SUMMARY;LANGUAGE=en-US:{summary}",
        f"DTSTART;TZID=India Standard Time:{start}",
        f"DTEND;TZID=India Standard Time:{end}",
        "CLASS:PUBLIC", "PRIORITY:5", "DTSTAMP:20990917T084600Z", "TRANSP:OPAQUE",
        "STATUS:CONFIRMED", "SEQUENCE:1",
        "LOCATION;LANGUAGE=en-US:Microsoft Teams Meeting",
        "END:VEVENT", "END:VCALENDAR", "",
    ])


def mail(*, subject, ics, sender=RECRUITER, recipient=CANDIDATE):
    """The decoded message and attachment list, shaped as process_message has them.

    No DKIM and no DMARC, SPF alone passing: the real invitation authenticated
    exactly this way, which the parser accepts.
    """
    domain = sender.rsplit("@", 1)[1]
    decoded = {
        "subject": subject,
        "body": "Microsoft Teams meeting Join: " + TEAMS_JOIN,
        "html_body": "",
        "sender_name": "Recruiter One",
        "sender_email": sender,
        "recipient_email": recipient,
        "message_direction": "INBOUND",
        "authentication_results": (
            "dkim=none (message not signed) header.d=none;"
            f"dmarc=none action=none header.from={domain};"
        ),
        "received_spf": (
            f"pass (google.com: domain of {sender} designates 192.0.2.10 as "
            "permitted sender) client-ip=192.0.2.10;"
        ),
    }
    safe = [{"filename": "invite.ics", "mime_type": "text/calendar", "text": ics}]
    return decoded, safe


def name_titled(**overrides):
    """Title "<candidate>-TR1", the candidate among five attendees."""
    attendees = overrides.pop("attendees", PANEL + [("Irfan Kelkar", CANDIDATE)])
    summary = overrides.pop("summary", "Irfan Kelkar-TR1")
    organizer = overrides.pop("organizer", RECRUITER)
    ics = exchange_invite(summary=summary, attendees=attendees, organizer=organizer)
    return mail(subject=summary, ics=ics, **overrides)


def call_for_candidate():
    """Title "Call for <candidate>", one attendee named only by address."""
    summary = "Call for Tara Sheth"
    recipient = "tara.sheth@example.com"
    ics = exchange_invite(summary=summary, attendees=[(recipient, recipient)])
    return mail(subject=summary, ics=ics, recipient=recipient)


def verdict_for(decoded, safe, decision, kind):
    result = trusted_interview_result(decoded, safe)
    return calendar_invite_verdict(answer(decision, kind), calendar_result=result, message=decoded)


class TestTheInvitesThatWereNeverBooked:
    def test_the_parser_reads_the_exact_slot(self):
        result = trusted_interview_result(*name_titled())
        assert result["classification"] == "interview_confirmed"
        assert result["calendar_validation_status"] == "TRUSTED"
        interview = result["interview"]
        assert (interview["date"], interview["time"], interview["timezone"]) == (
            "2099-09-18", "01:30 PM", "Asia/Kolkata",
        )
        assert interview["duration_minutes"] == 30
        assert interview["meeting_link"] == TEAMS_JOIN

    def test_and_the_booking_would_write_that_exact_slot(self):
        result = trusted_interview_result(*name_titled())
        schedule = normalized_schedule(result, now=datetime(2099, 9, 17, 14, 16, tzinfo=IST))
        assert (schedule["date"], schedule["time"], schedule["time_end"]) == (
            "2099-09-18", "13:30", "14:00",
        )

    @pytest.mark.parametrize("decision,kind", CONTRADICTIONS)
    def test_a_name_titled_invite_books_on_either_contradiction(self, decision, kind):
        assert verdict_for(*name_titled(), decision, kind) == "BOOK"

    @pytest.mark.parametrize("decision,kind", CONTRADICTIONS)
    def test_so_does_a_call_for_the_candidate_with_one_attendee(self, decision, kind):
        assert verdict_for(*call_for_candidate(), decision, kind) == "BOOK"

    def test_it_is_the_employer_shape_that_decides_not_a_word(self):
        decoded, safe = name_titled()
        evidence = evidence_for(trusted_interview_result(decoded, safe), decoded)
        assert evidence == {
            "trusted_request": True,
            "hiring_context": False,
            "role_context": False,
            "clear_marketing": False,
            "employer_invitation": True,
        }


class TestNothingElseIsLoosened:
    @pytest.mark.parametrize("decision,kind", CONTRADICTIONS)
    def test_a_marketing_word_anywhere_still_refuses(self, decision, kind):
        # The parser accepts this one on the employer shape too. It is the
        # word, not the shape, that keeps it from booking.
        decoded, safe = name_titled(summary="Cloud Careers Webinar")
        assert trusted_interview_result(decoded, safe) is not None
        assert verdict_for(decoded, safe, decision, kind) == "RETRY"

    @pytest.mark.parametrize("decision,kind", CONTRADICTIONS)
    def test_a_large_meeting_is_not_an_employer_invitation(self, decision, kind):
        crowd = PANEL + [(f"Guest {n}", f"guest.{n}@example.org") for n in range(3)]
        decoded, safe = name_titled(attendees=crowd + [("Irfan Kelkar", CANDIDATE)])
        assert trusted_interview_result(decoded, safe) is None
        assert verdict_for(decoded, safe, decision, kind) == "RETRY"

    @pytest.mark.parametrize("decision,kind", CONTRADICTIONS)
    def test_an_invite_from_the_candidates_own_domain_is_their_diary(self, decision, kind):
        own = "irfan.personal@example.com"
        decoded, safe = name_titled(organizer=own, sender=own)
        assert trusted_interview_result(decoded, safe) is None
        assert verdict_for(decoded, safe, decision, kind) == "RETRY"

    @pytest.mark.parametrize("decision,kind", CONTRADICTIONS)
    def test_a_consumer_mail_organiser_is_not_an_employer(self, monkeypatch, decision, kind):
        # A reserved domain stands in for a consumer provider, so no fixture
        # carries a real consumer address.
        monkeypatch.setattr(
            calendar_invite_parser, "_CONSUMER_MAIL_DOMAINS", frozenset({"example.org"}),
        )
        decoded, safe = name_titled()
        assert trusted_interview_result(decoded, safe) is None
        assert verdict_for(decoded, safe, decision, kind) == "RETRY"

    def test_a_confident_non_hiring_answer_still_ignores(self):
        # Not a contradiction, so the tie-breaker is never asked.
        assert verdict_for(*name_titled(), "NOT_ESTABLISHED", "GENERAL") == "IGNORE"


class TestOneRuleGovernsBoth:
    def test_the_parser_and_the_tie_breaker_read_the_same_limit(self, monkeypatch):
        """Two copies of this rule are how the tie-breaker came to veto the parser."""
        decoded, safe = name_titled()
        result = trusted_interview_result(decoded, safe)
        assert evidence_for(result, decoded)["employer_invitation"] is True

        monkeypatch.setattr(calendar_invite_parser, "_MAX_INTERVIEW_ATTENDEES", 4)
        assert evidence_for(result, decoded)["employer_invitation"] is False
        assert trusted_interview_result(decoded, safe) is None

    def test_process_message_hands_the_tie_breaker_its_evidence(self):
        # Without the parsed invite and the mail, every flag is False and the
        # verdict falls back to RETRY, which is the failure these tests pin.
        source = inspect.getsource(recruitment_mail_agent.process_message)
        call = source[source.index("verdict = calendar_invite_verdict("):]
        call = call[:call.index(")")]
        assert "calendar_result=calendar_result" in call
        assert "message=decoded" in call


class TestAReplayOfAPastInviteCannotBookAgain:
    """The retry this fix unblocks must not add a second row.

    The invite that exposed this was still parked when its interview took
    place, and by then the slot had been booked by hand. Its next automatic
    retry books through this same path, so it has to end without writing one.
    """

    def test_a_past_start_is_refused_before_any_slot_is_written(self):
        result = trusted_interview_result(*name_titled())
        with pytest.raises(BookingValidationError) as refused:
            normalized_schedule(result, now=datetime(2099, 9, 19, 9, 0, tzinfo=IST))
        assert refused.value.code == "PAST_INTERVIEW"

    def test_and_that_refusal_is_terminal_rather_than_retried(self):
        state = outcome_for({"status": "Skipped", "failure_code": "PAST_INTERVIEW"})
        assert state == AutomationState.AUTO_IGNORE
