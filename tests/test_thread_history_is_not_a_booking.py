"""Threads: Prompt 12 reads the latest message; older messages are history.

TCS, Nov 2026 (thread 1a08f585a55849de): a September invitation, a new
invitation for 30 Oct 12 PM, the candidate's "I will be available", and on
3 Nov a document request quoting all of it. The latest message is the
document request. Its quoted invitation is history: it is kept and labelled,
and it can never create, cancel or move a booking.
"""
from services import prompt12_classifier as p12
from services.gmail_mailbox_provider import select_message_body
from services.recruitment_mail_agent import clean_email

TCS_PLAIN = ("Dear Gangadhar,\r\n1. Full Name (according to PAN card) :\r\n"
             "2. Latest Company’s relieving letter :\r\nshare me asap.\r\n")
TCS_HTML = (
    "<div>Dear Gangadhar,</div><div>Full Name (according to PAN card) : Latest Company’s relieving letter :</div>"
    "<div>share me asap.</div><div><b>From:</b> Gangadhar Nagarale &lt;gangadhar@example.com&gt;<br>"
    "<b>Sent:</b> Friday, October 30, 2026 10:04 AM<br><b>Subject:</b> Re: Virtual Technical Interview Scheduled_TCS</div>"
    "<div>I will be available at the scheduled time.</div>"
    "<div>On Fri, 30 Oct 2026, 10:03 am prathima, &lt;recruiter@example.net&gt; wrote:</div>"
    "<blockquote>Dear Gangadhar, As discussed, your Virtual Interview is scheduled at 12 PM on 30 th Oct 2026 (Friday). "
    "Interview Scheduled 30 th Oct 2026 (Friday) at 12 PM. Microsoft Teams meeting</blockquote>"
)
SUBJECT = "RE: Virtual Technical Interview Scheduled_TCS"
QUOTED = "your Virtual Interview is scheduled at 12 PM on 30 th Oct 2026 (Friday)"


def test_prompt12_is_given_only_the_latest_message_of_tcs_24():
    latest = clean_email(select_message_body(TCS_PLAIN, TCS_HTML))
    assert "relieving letter" in latest
    assert "12 PM" not in latest and "I will be available" not in latest
    message = p12.user_message(SUBJECT, latest)
    assert message.startswith("SUBJECT: " + SUBJECT) and "Interview Scheduled 30" not in message


def _reading(status, *evidence):
    return {"status": status, "primary_status": status, "ai_validation_status": "VALIDATED",
            "backend_transition_validated": True, "should_create_review_record": True,
            "is_selection_or_offer_related": True,
            "interview": {"date": "2026-10-30", "time": "12:00", "original_date": "2026-09-14"},
            "evidence": [dict(item) for item in evidence]}


def test_a_schedule_proved_only_by_quoted_history_never_books():
    result = _reading("INTERVIEW_CONFIRMED", {"source": "THREAD_CONTEXT", "text": QUOTED})
    record = {"verdict": "SCHEDULED", "prompt_version": "prompt12@x"}
    assert p12.apply_verdict(result, record, subject=SUBJECT) == "unconfirmed"
    assert result["ai_validation_status"] == p12.NOT_BOOKABLE
    assert result["prompt12"]["only_historical_evidence"] is True
    assert result["evidence"][0]["historical"] is True
    assert result["prompt12"]["historical_evidence"][0]["text"].startswith("your Virtual Interview")


def test_a_reply_subject_is_history_too():
    result = _reading("INTERVIEW_CANCELLED", {"source": "EMAIL_SUBJECT", "text": "Interview Cancelled"})
    assert p12.apply_verdict(result, {"verdict": "CANCELED"}, subject="RE: Interview Cancelled") == "unconfirmed"
    own = _reading("INTERVIEW_CANCELLED", {"source": "EMAIL_SUBJECT", "text": "Interview Cancelled"})
    assert p12.apply_verdict(own, {"verdict": "CANCELED"}, subject="Interview Cancelled") == "agreed"


def test_quoted_history_never_keeps_the_reschedule_exception():
    result = _reading("INTERVIEW_RESCHEDULED", {"source": "THREAD_CONTEXT", "text": QUOTED})
    assert p12.apply_verdict(result, {"verdict": "OTHER"}, subject=SUBJECT) == "dropped"


def test_the_latest_messages_own_proof_still_books_and_history_is_kept():
    result = _reading("INTERVIEW_CONFIRMED",
                      {"source": "EMAIL_BODY", "text": "Your interview is scheduled at 12 PM on 30 Oct."},
                      {"source": "THREAD_CONTEXT", "text": QUOTED})
    assert p12.apply_verdict(result, {"verdict": "SCHEDULED"}, subject=SUBJECT) == "agreed"
    assert result["ai_validation_status"] == "VALIDATED"
    assert [item.get("historical", False) for item in result["evidence"]] == [False, True]


def test_reply_subjects_are_recognised():
    for subject in ("RE: x", "Re: x", "FW: x", "Fwd: x", "RE: RE: x", "AW: x", "Re[2]: x"):
        assert p12.is_reply_subject(subject), subject
    for subject in ("Interview Scheduled", "Regarding your interview", "Fwd x"):
        assert not p12.is_reply_subject(subject), subject
