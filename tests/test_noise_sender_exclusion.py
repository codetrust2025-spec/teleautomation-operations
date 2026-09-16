"""Senders that can never carry a hiring outcome stop before inference.

With the evidence.meaning gate removed, the deterministic sender check is the
only thing between marketing and a Selection alert with a sound. A July rescan
dry run found foundit.in "Your CV was downloaded" classified
job_selection_confirmed three times in its first 140 messages - foundit was
absent from the list, so it reached the model, and the model answered because
the schema requires an answer.

The second half of this file is the more important half. Blocking an employer
or an ATS produces a missed Selection alert, which is worse than the noise: the
noise is visible and a miss is not. Every domain in the exclusion lists was
read from the July-August population with a sample subject.
"""

from __future__ import annotations

import pytest

from services.recruitment_mail_agent import (
    _JOB_BOARD_DOMAINS, _SERVICE_NOISE_DOMAINS, job_board_notification,
)


class TestNoiseIsStoppedBeforeInference:
    @pytest.mark.parametrize("sender", [
        "opportunities@foundit.in",          # "Your CV was downloaded"
        "alerts@ziprecruiter.in",            # "we think you would be interested"
        "noreply@ambitionbox.com",           # salary and review marketing
        "promo@bankbazaar.com",
        "custcomm@custcomm.icici.bank.in",   # subdomain, matched by suffix
        "care@customer.icici.bank.in",
        "offers@easemytrip.com",
        "billing@jio.com",
        "noreply@cdr.bsnl.co.in",
        "hello@miteshkhatri.com",            # GraphoTherapy newsletter
        "team@namastedev.com",               # course marketing
        "deals@students.udemy.com",
        "news@email.openai.com",
        "noreply@infomails.microsoft.com",
        "support@hackingflix.com",
        "hyrefast@m.hyrefast.io",
        "news@talenttitanletters.com",
        # Second pass: the long tail. resumeworded produced a live false
        # Selection alert during the July run before it was listed.
        "team@cs.resumeworded.com",
        "info@money.hindustantimes.com",
        "offers@safeopt.com",
        "support@confirmtkt.com",
        "offers@federalbank.co.in",
        "alerts@axis.bank.in",
        "noreply@g.joinhandshake.com",
        "alerts@alerts.joinhyra.com",
        "jobs@abekus.co.in",
        "hello@primepathway.in",
        "info@ibrowsejobs.com",
        "hiring@sernexuss.in",
        # Third pass, from the verifier benchmark: Jobrapido reached the final
        # gate and one verifier let it through as a selection.
        "alert@jobrapidoalert.com",
        "noreply@jobrapido.com",
        "alerts@instahyre.com",
        "info@hirist.tech",
        # LinkedIn broadcast mailboxes. updates-noreply produced a
        # joining_confirmed on a job posting during the July sweep.
        "updates-noreply@linkedin.com",
        "newsletters-noreply@linkedin.com",
        "invitations-noreply@linkedin.com",
    ])
    def test_it_never_reaches_the_model(self, sender):
        assert job_board_notification(sender) is True

    def test_foundit_specifically(self):
        """Named because it was the largest sender still reaching inference."""
        assert job_board_notification("opportunities@foundit.in") is True
        assert "foundit.in" in _JOB_BOARD_DOMAINS


class TestEmployersAndATSAreUntouched:
    @pytest.mark.parametrize("sender", [
        # ATS platforms carrying real offers, BGV and joining mail
        "no-reply@kekamail.com", "noreply@myworkday.com", "otp@otp.workday.com",
        "no-reply@talent.icims.com", "no-reply@us.greenhouse-mail.io",
        "no-reply@smartrecruiters.com", "no-reply@ashbyhq.com",
        "no-reply@hire.lever.co", "no-reply@candidates.workablemail.com",
        "no-reply@ceipalmail.com", "no-reply@darwinbox.in",
        "workflow@workflow.mail.us2.cloud.oracle.com",
        # Employers
        "hr@tcs.com", "careers@deloitte.com", "hr@infosys.com",
        "hr@cognizant.com", "hr@coforge.com", "recruiter@in.ey.com",
        "hr@kpmg.com", "hr@mphasis.com", "hr@capgemini.com",
        "hr@techmahindra.com", "careers@ford.com", "hr@siemens.com",
        "recruiting@recruiting.experian.com",
        "recruitment@recruitment.americanexpress.com",
        "hr@onniglobal.in", "hr@kaivale.com", "hr@innominds.com",
        # Interview and verification platforms
        "no-reply@flocareer.com", "no-reply@hirepro.in", "no-reply@monjin.com",
        "onboardingteam@profilelens.co.in",
        # Staffing firms
        "recruiter@spectraforce.com", "recruiter@insightglobal.com",
        "recruiter@2coms.com", "recruiter@adroitinnovative.com",
        # Individuals
        "recruiter@example.com", "candidate@example.com",
    ])
    def test_it_still_reaches_the_model(self, sender):
        assert job_board_notification(sender) is False

    @pytest.mark.parametrize("sender,why", [
        ("careers@google.com", "Google's own recruiting mail comes from here"),
        ("inmail-hit-reply@linkedin.com", "a recruiter's InMail is a real conversation"),
        ("noreply@e.read.ai", "carries interview meeting notes"),
        ("noreply@clickup.com", "seen carrying an Analytics Engineer application"),
        ("hello@outlier.ai", "a work-platform invitation, not marketing"),
    ])
    def test_ambiguous_domains_are_deliberately_not_blocked(self, sender, why):
        """Each of these looks like noise and is left alone. Blocking a domain
        to silence notifications would silently drop the real mail beside
        them."""
        assert job_board_notification(sender) is False, why


class TestTheListsStayReviewable:
    def test_the_two_lists_do_not_overlap(self):
        assert not (set(_JOB_BOARD_DOMAINS) & set(_SERVICE_NOISE_DOMAINS))

    def test_no_entry_is_a_bare_public_suffix(self):
        """A one-label entry would match by suffix far beyond its intent."""
        for domain in _JOB_BOARD_DOMAINS + _SERVICE_NOISE_DOMAINS:
            assert domain.count(".") >= 1, domain
            assert not domain.startswith("."), domain

    def test_matching_is_by_whole_label_not_substring(self):
        """'notfoundit.in' must not match 'foundit.in'."""
        assert job_board_notification("hr@notfoundit.in") is False
        assert job_board_notification("hr@myjio.com") is False
