"""An interview platform saying "after opening" is not advertising a job.

`_JOB_AD_PATTERNS` matched `\\bopen(?:ing|ings)\\b`, which reads the gerund as
readily as the noun. Zealogics runs AI interviews and puts anti-cheating
wording in every mail -- "do not switch devices/browsers after opening",
"opening or switching to other applications or windows is not permitted" --
so eleven genuine interview invitations and reminders were classified
JOB_ADVERTISEMENT.

That is not cosmetic. `is_promotional_or_job_ad` makes `validate_interview_event`
refuse the transition with reason JOB_ADVERTISEMENT, and the mail is silently
ignored: no event, no notification, nothing on any screen. The candidate's own
interview link expiry warning never reached anyone.

Checked against the whole production mailbox: 636 mails match the old rule, 57
stop being job ads under the new one, and almost all of those were never job
ads -- eight GraphoTherapy marketing sends, three Jio e-bills, a LinkedIn
newsletter, an OpenAI pricing announcement. All eleven Zealogics mails are
recovered.
"""

from __future__ import annotations

import pytest

from services.recruitment_semantics import _is_job_ad, classify_context

SENDER = "support@zeaiq.zeasale.com"

# Verbatim from the production mails.
INVITATION = (
    "INTERVIEW INVITATION - AZURE ENGINEER Hello Naveen Prakash! We're excited to "
    "invite you for an interview for the Azure Engineer position at Zealogics. "
    "INTERVIEW DETAILS: - Position: Azure Engineer - Please remain on the interview "
    "screen throughout the interview. Opening or switching to other applications or "
    "windows is not permitted."
)
REMINDER = (
    "INTERVIEW REMINDER - AZURE ENGINEER Hello Naveen Prakash! This is a friendly "
    "reminder that your interview link for the Azure Engineer position at Zealogics "
    "will expire in 1 hour. Note: This interview link is personal and must not be "
    "shared externally. Do not switch devices/browsers after opening. If you have any "
    "questions or need to reschedule, please contact us."
)


class TestTheGerundIsNotAnAdvert:
    @pytest.mark.parametrize("subject,body", [
        ("Interview Invitation - Azure Engineer at Zealogics", INVITATION),
        ("Reminder: Interview Link Expires in 1 hour - Azure Engineer at Zealogics", REMINDER),
        ("Reminder: Interview Link Expires in 1 day - Azure Engineer at Zealogics", REMINDER),
    ])
    def test_a_zealogics_interview_mail_is_not_a_job_ad(self, subject, body):
        assert _is_job_ad(subject, body, SENDER) is False

    @pytest.mark.parametrize("subject,body", [
        ("Interview Invitation - Azure Engineer at Zealogics", INVITATION),
        ("Reminder: Interview Link Expires in 1 hour - Azure Engineer at Zealogics", REMINDER),
    ])
    def test_and_is_no_longer_marked_promotional(self, subject, body):
        """The flag that made validate_interview_event refuse the transition."""
        context = classify_context(subject, body, sender_email=SENDER,
                                   sent_at="2026-09-09T15:45:00+05:30")
        assert context["is_promotional_or_job_ad"] is False
        assert context["email_intent"] != "JOB_ADVERTISEMENT"

    @pytest.mark.parametrize("phrase", [
        "do not switch devices/browsers after opening.",
        "opening or switching to other applications or windows is not permitted",
        "please do not close the window after opening the link",
        "we will be opening the results next week",
    ])
    def test_the_bare_gerund_never_reads_as_a_vacancy(self, phrase):
        assert _is_job_ad("Interview details", phrase, SENDER) is False


class TestARealVacancyStillReadsAsOne:
    @pytest.mark.parametrize("subject", [
        "Opening for Automation Testing-Hyderabad",
        "Senior Data Scientist opening at Aon",
        "Automation Testing Opening -Tech Mahindra",
        "Openings: ServiceNow Developer",
        "Immediate Openings for CMDB - Discovery & Service Mapping",
        "Top Openings for Developer",
        "Job opening for AWS Cloud Developer",
        "openings for ServiceNow in Accenture",
        "We have a current opening in our Bangalore office",
        "New opening with our client for a DevOps Engineer",
    ])
    def test_it_is_still_a_job_ad(self, subject):
        assert _is_job_ad(subject, "Please share your updated profile.", "recruiter@example.com")

    def test_the_other_advert_rules_are_untouched(self):
        for subject in ("We are hiring a Java developer",
                        "Job description attached",
                        "Apply now for this role",
                        "Job requirement: 5 years experience"):
            assert _is_job_ad(subject, "", "recruiter@example.com")


class TestNothingElseMoved:
    def test_a_portal_advert_still_counts(self):
        assert _is_job_ad(
            "Job | Devops (SRE) in APPIT Software Solutions",
            "Experience: 5 years. Skills: AWS. Location: Hyderabad. Notice period: 30 days.",
            "recruiter@naukri.com",
        )

    def test_an_ordinary_recruiter_mail_is_not_swept_up(self):
        assert _is_job_ad(
            "Interview scheduled for tomorrow",
            "Your interview is confirmed for 3 PM IST.",
            "recruiter@example.com",
        ) is False
