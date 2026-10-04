"""An interview mail the reader could not read must still reach a person.

Two gates decide an interview mail, and both fail closed. The deterministic
calendar path takes only an invitation whose organizer is aligned with its
sender, so an invitation relayed by a calendar service -- Zoho, Google -- on
behalf of the recruiter is not trusted. The model then reads it, and when it
cannot decide, `_queued_for_another_attempt` parks the result for a retry;
when the attempts are spent, `claim_ai_messages` parks the message terminally.

Neither park writes an alert. Three of one candidate's September interviews
went that way -- L1 on the 18th, L1 again on the 21st, L2 on the 25th, each a
signed Zoho invitation with a calendar id, each parked after two attempts --
and Operations never saw one of them. The offer arrived on the 29th.

So: when the reader gives up on a mail that carries real interview details, an
alert is written for a person to read. Deliberately narrow. This is not a
second classifier and never books anything: it asks only whether the mail
names a specific meeting, which a marketing blast does not.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from core import recruitment_mail_store as store
from services.calendar_invite_parser import parse_calendar

#: How far back a surfacing pass looks, in hours. Only mail parked inside this
#: window: the pass runs every cycle, and an older backlog is a decision for a
#: person, not something a loop should quietly start filing.
DEFAULT_WINDOW_HOURS = 48

#: A Google Meet room, which names one meeting as exactly as a Teams link does.
#: Kept here rather than in `_source_meeting_identities`, whose set decides
#: whether two bookings are one interview: that is booking logic and this is
#: not.
_GOOGLE_MEET = re.compile(r"https?://meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})\b", re.I)


def _body(message: dict[str, Any]) -> str:
    return " ".join(str(message.get(key) or "") for key in ("body_text", "html_body_text", "body", "html_body"))


def cancels_an_interview(message: dict[str, Any], attachments: list[dict[str, Any]] | None = None) -> bool:
    """Does this mail call an interview off rather than arrange one?

    A cancellation is worth reading too -- a booking may need cancelling -- but
    telling somebody to "book the interview by hand" when the mail says the
    opposite wastes their time and risks a booking that should not exist.
    """
    for attachment in attachments or []:
        if not str(attachment.get("filename") or "").lower().endswith(".ics"):
            continue
        invite = parse_calendar(str(attachment.get("text") or attachment.get("extracted_text") or ""))
        if invite and (invite.get("method") == "CANCEL" or invite.get("status") == "CANCELLED"):
            return True
    return bool(re.match(r"\s*(Canceled|Cancelled)\s*:", str(message.get("subject") or ""), re.I))


def interview_evidence(message: dict[str, Any], attachments: list[dict[str, Any]] | None = None) -> str | None:
    """What in this mail names a specific interview, in words, or None.

    A calendar event, a meeting room the booking code already recognises, or a
    Google Meet room. Never a subject line, a company name or a date alone:
    those are what a job board sends a thousand of.
    """
    for attachment in attachments or []:
        if not str(attachment.get("filename") or "").lower().endswith(".ics"):
            continue
        invite = parse_calendar(str(attachment.get("text") or attachment.get("extracted_text") or ""))
        if invite and invite.get("uid"):
            when = invite.get("start")
            return f"a calendar invitation ({when:%d %b %H:%M} IST)" if when else "a calendar invitation"

    # The exact meeting identities the booking path trusts: Teams (classic and
    # short) and a HirePro interview room.
    from services.interview_auto_booking import _source_meeting_identities
    meetings = _source_meeting_identities(message)
    if meetings:
        host = sorted(meetings)[0][0]
        return f"a {'HirePro interview room' if 'hirepro' in host else 'Teams meeting'} link"

    if _GOOGLE_MEET.search(_body(message)):
        return "a Google Meet room link"
    return None


def surface_unread_interview_mail(*, since: datetime | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Write an alert for every parked mail that still looks like an interview.

    Returns the alerts created. Idempotent: a mail already carrying one is
    skipped by the query, and the unique index catches the race.
    """
    since = since or (datetime.now(timezone.utc) - timedelta(hours=DEFAULT_WINDOW_HOURS))
    created: list[dict[str, Any]] = []
    for message in store.unread_interview_candidates(since=since, limit=limit):
        attachments = store.attachments_for_message(str(message["id"]), include_text=True)
        readable = [{**a, "text": a.get("extracted_text") or ""} for a in attachments]
        evidence = interview_evidence(message, readable)
        if not evidence:
            continue
        alert = store.record_unread_interview_alert(
            message, evidence=evidence, cancelling=cancels_an_interview(message, readable))
        if alert:
            created.append(alert)
    return created
