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

#: Video meeting and interview links that name an individual 1-on-1 interview session.
_VIDEO_CALL_URL_RE = re.compile(
    r"https?://(?:meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}|teams\.microsoft\.com/l/meetup-join/|[a-z0-9.-]+\.zoom\.us/j/|[a-z0-9.-]+\.webex\.com/|ams\.hirepro\.in/v2/interview/|[a-z0-9.-]+\.veriklick\.com/)",
    re.I,
)

#: Generic walk-in and mass hiring drive patterns
_GENERIC_WALKIN_RE = re.compile(
    r"\b(?:walk[- ]?in|open[- ]?drive|job[- ]?fair|mass[- ]?recruitment|hiring[- ]?drive)\b",
    re.I,
)

#: Candidate-specific confirmation wording that overrides a generic walk-in label
_CANDIDATE_SLOT_RE = re.compile(
    r"\b(?:your\s+slot\s+is|slot\s+confirmed|interview\s+scheduled\s+for\s+you|assigned\s+interviewer|individual\s+slot)\b",
    re.I,
)


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


def is_ai_rejected_non_transition(message: dict[str, Any]) -> bool:
    """Did the AI or deterministic analysis reject this as a non-transition event?"""
    err = str(message.get("ai_last_error_code") or "").strip()
    reason = str(message.get("analysis_reason") or "").strip()
    return (
        err == "EVIDENCE_DOES_NOT_ENTAIL_TRANSITION" or
        reason == "EVIDENCE_DOES_NOT_ENTAIL_TRANSITION" or
        err == "RECRUITMENT_RELEVANCE_UNRESOLVED"
    )


def is_generic_walkin(message: dict[str, Any], attachments: list[dict[str, Any]] | None = None) -> bool:
    """Is this a generic walk-in recruitment campaign rather than a candidate interview?

    A walk-in drive is an open event or job fair broadcast to candidates.
    It must not be treated as an actionable individual interview unless it contains
    a confirmed candidate-specific appointment or 1-on-1 meeting room.
    """
    subject = str(message.get("subject") or "")
    body = _body(message)
    recipient = str(message.get("recipient_email") or "").strip().lower()

    # If the mail has a genuine 1-on-1 video call link, it is candidate-specific
    if _VIDEO_CALL_URL_RE.search(body) or _GOOGLE_MEET.search(body):
        return False

    # If the mail has explicit candidate slot confirmation wording
    if _CANDIDATE_SLOT_RE.search(body):
        return False

    has_walkin_wording = bool(_GENERIC_WALKIN_RE.search(subject))

    # Check calendar attachments
    for attachment in attachments or []:
        if not str(attachment.get("filename") or "").lower().endswith(".ics"):
            continue
        invite = parse_calendar(str(attachment.get("text") or attachment.get("extracted_text") or ""))
        if not invite:
            continue

        summary = str(invite.get("summary") or "")
        desc = str(invite.get("description") or "")
        uid = str(invite.get("uid") or "")
        attendees = [str(a).strip().lower() for a in invite.get("attendees", []) if a]

        # If candidate's email is explicitly listed as an attendee, it is candidate-specific
        if recipient and recipient in attendees:
            return False

        # If the calendar event has a video meeting link
        if invite.get("meeting_link") and _VIDEO_CALL_URL_RE.search(invite.get("meeting_link")):
            return False

        # Walk-in keywords in subject, summary, or description
        has_invite_walkin = bool(
            has_walkin_wording or
            _GENERIC_WALKIN_RE.search(summary) or
            _GENERIC_WALKIN_RE.search(desc)
        )

        is_naukri_mass_uid = uid.startswith("RMJ_")
        is_naukri_sender = "@naukri.com" in str(message.get("sender_email") or "").lower()

        # Check duration: normal interviews are < 4 hours; walk-in drives span whole days or multiple days
        is_long_window = False
        start = invite.get("start")
        end = invite.get("end")
        if start and end and (end - start).total_seconds() >= 14400:  # >= 4 hours
            is_long_window = True

        if has_invite_walkin:
            return True

        if (is_naukri_mass_uid or is_naukri_sender) and (is_long_window or not attendees):
            return True

    return has_walkin_wording


def interview_evidence(message: dict[str, Any], attachments: list[dict[str, Any]] | None = None) -> str | None:
    """What in this mail names a specific interview, in words, or None.

    A calendar event, a meeting room the booking code already recognises, or a
    Google Meet room. Never a subject line, a company name or a date alone:
    those are what a job board sends a thousand of.
    """
    # If the mail is a generic walk-in recruitment campaign, it does not name a specific interview
    if is_generic_walkin(message, attachments):
        return None

    # If the AI explicitly rejected the transition, an attached .ics file alone is not proof of an interview
    ai_rejected = is_ai_rejected_non_transition(message)

    for attachment in attachments or []:
        if not str(attachment.get("filename") or "").lower().endswith(".ics"):
            continue
        invite = parse_calendar(str(attachment.get("text") or attachment.get("extracted_text") or ""))
        if invite and invite.get("uid"):
            # If AI rejected the transition and calendar has no candidate attendee or meeting link,
            # this .ics file alone does not count as interview evidence
            if ai_rejected:
                recipient = str(message.get("recipient_email") or "").strip().lower()
                attendees = [str(a).strip().lower() for a in invite.get("attendees", []) if a]
                if not (recipient and recipient in attendees) and not invite.get("meeting_link"):
                    continue

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

        # Check if generic walk-in recruitment campaign
        if is_generic_walkin(message, readable):
            alert = store.record_walkin_alert(message)
            if alert:
                created.append(alert)
            continue

        evidence = interview_evidence(message, readable)
        if not evidence:
            continue
        alert = store.record_unread_interview_alert(
            message, evidence=evidence, cancelling=cancels_an_interview(message, readable))
        if alert:
            created.append(alert)
    return created
