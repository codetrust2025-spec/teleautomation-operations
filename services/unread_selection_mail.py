"""A Selection mail the reader could not read must still reach a person.

The interview side has had this since September (`services.unread_interview_mail`):
when the reader gives up on a mail -- the queue parks it as AI_PROCESSING_FAILED
after its attempts -- a mail carrying real interview details is written into
Mail Alerts for a person to read. Nothing did the same for offers, joining and
background checks. On 11 Nov 2026 a VHS "Offer of Employment" with its offer
letter attached, its revised-date-of-joining follow-up, and a VHS "BGV Form
Completion" request were all parked that way: no alert anywhere, and found only
by hand two days later.

So: a parked mail whose own current message plainly states an offer, a joining
arrangement or a background check is filed as `selection_needs_reading` under
Selection Related. Deliberately narrow, like the interview side. It is not a
classifier and decides nothing -- no stage, no booking, no offer record; it
says what it saw and asks a person to read the mail. Quoted history, negated or
conditional wording ("this is not an offer of employment", "if selected, the
offer letter will ...") and job-board senders never count.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from core import recruitment_mail_store as store

#: Same window as the interview side: only mail parked recently. An older
#: backlog is a decision for a person, not something a loop should start filing.
DEFAULT_WINDOW_HOURS = 48

#: What the mail must state, grouped by what a person will be looking at.
_STATEMENTS = (
    ("an offer", re.compile(
        r"\boffer of employment\b|\bpleased to offer you\b|\b(?:revised )?offer letter\b|"
        r"\bappointment letter\b|\bletter of appointment\b|\baccept(?:ance of)? (?:the|this|our) offer\b", re.I)),
    ("a joining arrangement", re.compile(
        r"\bdate of joining\b|\bjoining date\b|\bjoining formalities\b|\bpre[- ]?onboarding\b|"
        r"\bonboarding formalities\b|\breporting (?:date|details)\b", re.I)),
    ("a background check", re.compile(
        r"\bbackground verification\b|\bbackground check\b|\bbgv\b", re.I)),
)
#: Mail that mentions the words without being about this candidate's outcome:
#: sign-in codes, portal registrations and job adverts. Replayed over the 1,254
#: mails parked since 1 Oct 2026, these were the only false hits ("Verification
#: Code" naming the BGV process, "J2WOfferLetter - Thank you for registering",
#: a "Job Opening" advert describing its hiring process).
_NOT_ABOUT_AN_OUTCOME = re.compile(
    r"\b(?:verification code|otp|one[- ]time (?:password|code)|sign[- ]?in code|"
    r"thank you for registering|registration (?:successful|complete)|"
    r"job opening|job alert|apply now|we are hiring|hiring for|"
    # Recruiter outreach: describes its hiring process, BGV included.
    r"we have an? (?:opening|requirement|position)|opening for|job description)\b", re.I)
#: An attachment whose name says it is the letter itself.
_LETTER_FILE = re.compile(r"offer[ _-]?letter|appointment[ _-]?letter|joining[ _-]?letter|\bbgv\b", re.I)
#: Negation or a condition anywhere earlier in the same clause: "this does not
#: constitute an offer of employment", "if selected, the offer letter will be
#: shared with your date of joining". Both govern the whole clause after them.
_NEGATED = re.compile(r"\b(?:not|no|never|nor|without|if|once|when|whether|unless|until|upon)\b", re.I)
_CLAUSE_START = re.compile(r"[.!?\n;]")


def _affirmed(pattern: re.Pattern[str], text: str) -> str | None:
    """The first place the text states it outright, or None."""
    for match in pattern.finditer(text):
        before = text[max(0, match.start() - 200):match.start()]
        clause = _CLAUSE_START.split(before)[-1]
        if _NEGATED.search(clause):
            continue
        return match.group(0)
    return None


def _current_message(message: dict[str, Any]) -> str:
    """This mail's own words: the current MIME part, quoted history cut."""
    from services import prompt12_classifier
    from services.gmail_mailbox_provider import select_message_body
    from services.recruitment_mail_agent import clean_email
    body = clean_email(select_message_body(message.get("body_text"), message.get("html_body_text")))
    latest, _quoted = prompt12_classifier.split_latest(body, message.get("subject"))
    return latest


def selection_evidence(message: dict[str, Any], attachments: list[dict[str, Any]] | None = None) -> str | None:
    """What this parked mail plainly states about a selection outcome, or None."""
    from services.recruitment_mail_agent import job_board_notification
    if job_board_notification(str(message.get("sender_email") or "")):
        return None
    subject = str(message.get("subject") or "")
    # ATS subjects glue words with underscores ("Offer of Employment_<name>"),
    # which regex word boundaries treat as part of the word.
    text = f"{subject}. {_current_message(message)}".replace("_", " ")
    if _NOT_ABOUT_AN_OUTCOME.search(subject.replace("_", " ")) or _NOT_ABOUT_AN_OUTCOME.search(text[:600]):
        return None
    found = []
    for label, pattern in _STATEMENTS:
        phrase = _affirmed(pattern, text)
        if phrase:
            found.append(f'{label} ("{phrase}")')
    letters = [str(a.get("filename") or "") for a in attachments or [] if _LETTER_FILE.search(str(a.get("filename") or ""))]
    if letters:
        found.append("an attached " + letters[0])
    return "; ".join(found) if found else None


def surface_unread_selection_mail(*, since: datetime | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Write a Selection 'needs reading' alert for every parked mail that states one.

    Returns the alerts created. Idempotent: a mail already carrying any alert is
    skipped by the query, and the unique index catches the race.
    """
    since = since or (datetime.now(timezone.utc) - timedelta(hours=DEFAULT_WINDOW_HOURS))
    created: list[dict[str, Any]] = []
    for message in store.unread_interview_candidates(since=since, limit=limit):
        attachments = store.attachments_for_message(str(message["id"]))
        evidence = selection_evidence(message, attachments)
        if not evidence:
            continue
        alert = store.record_unread_selection_alert(message, evidence=evidence)
        if alert:
            created.append(alert)
    return created
