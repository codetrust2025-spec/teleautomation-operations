"""Selection Related holds only mail that states a selection outcome.

Owner, 12 Nov 2026: Selection is selected, offer received, joining confirmed
and the final HR confirmation; Interview is invitations, scheduling,
rescheduling, assessments and rounds. Interview progress or a shortlist is
never filed as a confirmed selection, and the filing is read from the mail's
own text, not from the label a model gave it.

Live on 11 Nov 2026, under Selection: "HCLTech | Evaluation 2 Selection"
(progressed through an evaluation), a TEKsystems "Reconfirmation Details"
(confirm your interest, share your LWD), an "RTR Mail" ("If selected you will
be employed ...") and an HCL "Verification Code" OTP. Texts keep production
wording; names and companies are invented.
"""
from __future__ import annotations

import pytest

from core import recruitment_mail_store as store
from services.recruitment_semantics import selection_alert_filing, states_confirmed_selection

STAYS_SELECTION = [
    ("job_selection_confirmed", "Congratulations on Your Selection – Software Engineer",
     "Dear Ravi, Congratulations! You have been selected for the position of Software Engineer at Acme."),
    ("offer_received", "Review Your Offer Letter from Acme",
     "Dear Ravi, Please find the attached offer of employment. Kindly review your offer letter."),
    ("joining_confirmed", "Joining details", "Dear Ravi, Your date of joining is 1 December 2026."),
    ("background_verification", "Candidate invite for background check submission",
     "Dear Ravi, Congratulations on your acceptance of a position at Acme! "
     "We will be overseeing the background verification process."),
    ("hr_confirmation", "Final HR", "Dear Ravi, Your final HR discussion has been completed and the offer is being prepared."),
]

MOVES_TO_INTERVIEW = [
    ("job_selection_confirmed", "Acme | Evaluation 2 Selection for 141850",
     "Dear Ravi, You've successfully progressed through the [Evaluation 2] for 141850. "
     "Priya will be in touch with you on the next steps.", "post_interview_shortlisted"),
    ("job_selection_confirmed", "Next round", "Dear Ravi, You have been selected for the next round of interviews.",
     "interview_shortlisted"),
    ("job_selection_confirmed", "RTR Mail_ QA Testing _C2H",
     "Hi Ravi, We would process your candidature with Acme QA Testing in Bangalore. "
     "If selected you will be employed on our payroll. Please send me the documents.", "interview_shortlisted"),
    ("job_selection_confirmed", "Verification Code",
     "Dear Ravi, Thank you for your interest in Acme. To proceed with your application, please enter the OTP.",
     "interview_shortlisted"),
    ("hr_confirmation", "Re: Reconfirmation Details || Acme",
     "Hi Ravi, please share the following documents/details at your earliest convenience: "
     "Last Working Day (LWD) confirmation email, Passport-size photograph.", "interview_shortlisted"),
    ("hr_confirmation", "Documents Required for HR Discussion",
     "Dear Ravi, You have been shortlisted for the position. Please share below documents at earliest for HR Discussion.",
     "post_interview_shortlisted"),
]


@pytest.mark.parametrize("classification,subject,body", STAYS_SELECTION, ids=[c[1][:30] for c in STAYS_SELECTION])
def test_a_stated_selection_outcome_stays_selection(classification, subject, body):
    assert states_confirmed_selection([subject, body])
    assert selection_alert_filing(classification, [subject, body]) == classification
    assert classification in store.SELECTION_RELATED_CLASSIFICATIONS


@pytest.mark.parametrize("classification,subject,body,_was_filed", MOVES_TO_INTERVIEW, ids=[c[1][:30] for c in MOVES_TO_INTERVIEW])
def test_interview_progress_or_an_unproven_claim_makes_no_alert(classification, subject, body, _was_filed):
    # Until 13 Nov 2026 these were refiled under Interview (`_was_filed`). Whether
    # a mail is an interview is now Prompt 12's verdict alone, so a selection
    # claim the mail does not prove is filed not_relevant: no alert anywhere.
    assert not states_confirmed_selection([subject, body])
    assert selection_alert_filing(classification, [subject, body]) == "not_relevant"
    assert not store.notification_is_tracked("not_relevant")


def test_a_post_offer_workday_task_stays_selection_with_that_companys_offer():
    texts = ["A Task Awaits You: Review Documents - Reference Check for Job Application", "Review Documents in Workday."]
    assert selection_alert_filing("job_selection_confirmed", texts, selection_context=True) == "job_selection_confirmed"
    assert selection_alert_filing("job_selection_confirmed", texts) == "not_relevant"


def test_an_unproven_post_offer_label_is_not_moved_without_interview_evidence():
    # "Countdown: 1 day to go!" from an employer the candidate is joining.
    assert selection_alert_filing("joining_confirmed", ["Countdown: 1 day to go!", "See you soon."]) == "joining_confirmed"


def test_interview_classifications_are_never_touched():
    for classification in store.INTERVIEW_RELATED_CLASSIFICATIONS:
        assert selection_alert_filing(classification, ["You have been selected for the role."]) == classification


class _Cursor:
    def __init__(self, body, context):
        self.body, self.context, self.calls = body, context, []
        self._next = None

    def execute(self, sql, params=()):
        self.calls.append(sql)
        self._next = (self.body,) if "FROM mailbox_messages" in sql else ((1,) if self.context else None)

    def fetchone(self):
        return self._next


def test_the_alert_write_path_files_by_the_mails_own_text():
    cursor = _Cursor("You've successfully progressed through the [Evaluation 1] for 162746.", context=True)
    event = {"candidate_id": "c1", "company_name": "Acme"}
    filed = store._selection_alert_filing(cursor, event, {}, "job_selection_confirmed", "m1", "Evaluation 1 Selection")
    assert filed == "not_relevant"
    assert store._CLASSIFICATION_STATUS[filed]


def test_the_alert_write_path_reads_nothing_for_interview_mail():
    cursor = _Cursor("", context=False)
    assert store._selection_alert_filing(cursor, {"candidate_id": "c1"}, {}, "interview_confirmed", "m1", "x") == "interview_confirmed"
    assert cursor.calls == []
