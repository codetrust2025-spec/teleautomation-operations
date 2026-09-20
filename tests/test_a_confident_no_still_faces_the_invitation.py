"""A label the model is sure of does not silence an authenticated invitation.

Ten interviews were dropped without an event, an alert or an audit row between
14 Aug and 16 Sep 2026. Every one carried an authenticated employer REQUEST --
TRUSTED validation, a UID, an end time, a meeting link, the candidate among a
handful of attendees -- and every one was answered NOT_ESTABLISHED with some
label other than the recipient's own hiring process. None carried a marketing
word anywhere. Their bodies are Teams boilerplate, a disclaimer, a CV
attachment: the model had almost nothing to read and labelled the mail
confidently wrong.

Until now the deterministic evidence was consulted only when the answer
contradicted itself, so a confident wrong label went straight to silence. The
ten shapes below are the ones production lost, rebuilt synthetically, and each
must now reach the same tie-breaker the contradictory cases already reach.

Nothing about marketing changes. A webinar is authenticated and external too,
and it must still be ignored -- on the word list first, and on the invitation
shape behind it.
"""

from __future__ import annotations

import pytest

from services.recruitment_mail_agent import calendar_invite_verdict

CANDIDATE = "candidate@example.invalid"


def answer(decision="NOT_ESTABLISHED", kind="GENERAL"):
    """What the relevance model said, in the shape the gate receives."""
    return {"decision": decision, "message_kind": kind, "confidence": 0.9,
            "evidence": [{"source": "EMAIL_BODY", "text": "Microsoft Teams meeting"}],
            "reason": "synthetic"}


def invitation(sender, *, attendees=3, method="REQUEST", trusted=True, link=True,
               cancelled=False):
    return {
        "calendar_validation_status": "TRUSTED" if trusted else "UNVERIFIED",
        "calendar": {"method": method, "uid": "uid-1", "has_dtend": True,
                     "attendee_count": attendees},
        "interview": {"date": "2099-09-10",
                      "meeting_link": "https://teams.microsoft.com/l/meetup-join/x" if link else ""},
        "classification": "interview_cancelled" if cancelled else "interview_confirmed",
        "recruiter": {"email": sender},
        "candidate": {"email": CANDIDATE},
    }


def mail(subject, body="Microsoft Teams meeting. Join the meeting."):
    return {"subject": subject, "body": body,
            "sender_email": "recruiter@example-employer.com", "recipient_email": CANDIDATE}


# Each row is one of the ten production shapes: the subject as recruiters write
# them, and the body those mails actually had.
AUDITED = [
    ("recruiter@perficient-example.com",
     "Interviewer to discuss with Synthetic Candidate - Python",
     "Thank you for expressing interest in the Python position. Based on your qualifications..."),
    ("recruiter@lancesoft-example.in", "Example interview", "Hi, Please join the call. Microsoft Teams meeting"),
    ("recruiter@cloudbase-example.digital", "L1 discussion || Synthetic || ServiceNow Developer",
     "Please find the invite for interview tomorrow at 12 PM."),
    ("talent@cognizant-example.com", "Invitation for Live Interview with Example Corp",
     "Following evaluation of your application, we are pleased to invite you to a virtual interview."),
    ("recruiter@synechron-example.com", "Client call | Synthetic | Python FS | Example Corp",
     "As discussed, please be available for the client discussion tomorrow at 11:30 AM."),
    ("recruiter@cognizant-example.com", "Example Corp L2 Interview || Synthetic Candidate",
     "Microsoft Teams meeting Join: https://teams.microsoft.com/meet/1"),
    ("recruiter@exaze-example.com", "Tech Interview - Data Engineer - Synthetic Candidate - Example",
     "Kindly accept this invite and set a reminder for the same."),
    ("recruiter@ey-example.com", "Example L1 Discussion : Synthetic_Senior Full Stack Developer",
     "Blocking your calendar for the interview. This will be a 30 minute round."),
    ("recruiter@kwe-example.com", "Sr. Platform Engineer L1 interview with Synthetic Candidate @ 2.30 PM",
     "Microsoft Teams meeting Join: https://teams.microsoft.com/meet/2"),
    ("recruiter@softshala-example.com", "Candidate Screening Round - [Synthetic Candidate] - [ CMDB ]",
     "SyntheticCandidate.pdf attached."),
]


@pytest.mark.parametrize(("sender", "subject", "body"), AUDITED)
def test_every_shape_production_lost_now_reaches_the_tie_breaker(sender, subject, body):
    verdict = calendar_invite_verdict(
        answer(), calendar_result=invitation(sender), message=mail(subject, body),
    )

    assert verdict == "BOOK"


@pytest.mark.parametrize("kind", ["GENERAL", "MARKETING_OR_TRAINING", "PUBLIC_EVENT", "NEWSLETTER"])
def test_the_label_the_model_chose_no_longer_decides_on_its_own(kind):
    """Whatever it called the mail, the invitation is what is weighed."""
    assert calendar_invite_verdict(
        answer(kind=kind),
        calendar_result=invitation("recruiter@example-employer.com"),
        message=mail("L1 interview with Synthetic Candidate"),
    ) == "BOOK"


# ── The webinar defence is untouched ────────────────────────────────────────

@pytest.mark.parametrize("subject", [
    "Free webinar: breaking into data engineering",
    "Join our Python bootcamp",
    "Career fair - register now",
    "Masterclass on system design",
])
def test_a_marketing_invitation_is_still_ignored(subject):
    assert calendar_invite_verdict(
        answer(kind="MARKETING_OR_TRAINING"),
        calendar_result=invitation("events@example-training.com"),
        message=mail(subject, "Join the workshop. Microsoft Teams meeting"),
    ) == "IGNORE"


def test_a_marketing_word_anywhere_refuses_even_under_a_hiring_subject():
    assert calendar_invite_verdict(
        answer(kind="MARKETING_OR_TRAINING"),
        calendar_result=invitation("events@example-training.com"),
        message=mail("Interview skills masterclass", "A training session for candidates."),
    ) == "IGNORE"


def test_an_open_day_sized_meeting_is_not_an_employer_invitation():
    """Authenticated, external, hiring words -- and forty people in the room."""
    assert calendar_invite_verdict(
        answer(), calendar_result=invitation("events@example-employer.com", attendees=40),
        message=mail("Hiring day - engineering roles"),
    ) == "IGNORE"


# ── Nothing weaker books ────────────────────────────────────────────────────

def test_a_trusted_invitation_with_no_hiring_or_role_context_does_not_book():
    assert calendar_invite_verdict(
        answer(), calendar_result=invitation("someone@example-employer.com"),
        message=mail("Catch up", "Let us sync on the plan."),
    ) == "IGNORE"


@pytest.mark.parametrize("weakness", [dict(trusted=False), dict(link=False), dict(attendees=0)])
def test_an_invitation_that_is_not_authenticated_enough_does_not_book(weakness):
    assert calendar_invite_verdict(
        answer(), calendar_result=invitation("recruiter@example-employer.com", **weakness),
        message=mail("L1 interview with Synthetic Candidate"),
    ) == "IGNORE"


def test_an_unreadable_answer_still_fails_closed():
    assert calendar_invite_verdict(
        {"decision": "", "message_kind": ""},
        calendar_result=invitation("recruiter@example-employer.com"),
        message=mail("L1 interview with Synthetic Candidate"),
    ) == "IGNORE"


def test_a_trusted_cancellation_is_acted_on_rather_than_dropped():
    """BOOK is this gate's word for "act on it", not for "create a slot": the
    calendar result classifies the mail, and a cancellation releases a booking.
    Acting on one can only ever take a slot away, which is why the source
    parser has been allowed to release without a quoted sentence all along."""
    result = invitation("recruiter@example-employer.com", method="CANCEL", cancelled=True)

    verdict = calendar_invite_verdict(
        answer(), calendar_result=result,
        message=mail("Cancelled: L1 interview with Synthetic Candidate"),
    )

    assert verdict == "BOOK"
    assert result["classification"] == "interview_cancelled"


def test_a_cancellation_with_no_employer_invitation_behind_it_is_still_ignored():
    assert calendar_invite_verdict(
        answer(), calendar_result=invitation(CANDIDATE, method="CANCEL", cancelled=True),
        message=mail("Cancelled: L1 interview with Synthetic Candidate"),
    ) == "IGNORE"


# ── What already worked still works ─────────────────────────────────────────

def test_the_model_agreeing_with_itself_books_as_before():
    assert calendar_invite_verdict(answer("ESTABLISHED", "RECIPIENT_HIRING_PROCESS")) == "BOOK"


def test_a_contradiction_without_structural_proof_still_retries():
    assert calendar_invite_verdict(answer("NOT_ESTABLISHED", "RECIPIENT_HIRING_PROCESS")) == "RETRY"
    assert calendar_invite_verdict(answer("ESTABLISHED", "MARKETING_OR_TRAINING")) == "RETRY"


def test_a_contradiction_with_the_invitation_behind_it_still_books():
    assert calendar_invite_verdict(
        answer("NOT_ESTABLISHED", "RECIPIENT_HIRING_PROCESS"),
        calendar_result=invitation("recruiter@example-employer.com"),
        message=mail("ServiceNow Developer", "Please join the discussion."),
    ) == "BOOK"
