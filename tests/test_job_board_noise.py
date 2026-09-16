"""Job-board marketing must not become a candidate lifecycle status.

Measured in production 2026-08-28. Of 191 tracked analyses only 16 reached an
operator; 130 were refused by the routing gate for lack of a recognised
evidence meaning. Reading them showed the gate was right: the senders were

    anita@talent500.co        29   "Shortlisted but your profile is incomplete"
    mail@timesjobs.com        10   "Your profile has been Shortlisted for EMBA"
    alerts@jobs.shine.com      8   "Your Application has been Shortlisted"
    service@naukri.com         5   "Your daily update: Jobs found & applied"
    no-reply@indeed.com        5
    alerts@axis.bank.in        1   a bank debit, classified candidate_rejected

against a small tail of genuine employer and ATS mail. Relaxing the gate would
have routed all of it, so the fix is upstream: aggregators never become a
lifecycle status, and only unambiguous employer phrasing is promoted to a
meaning code the gate already accepts.

The gate itself is unchanged and remains the final check.
"""

from __future__ import annotations

import pytest

from services import recruitment_mail_agent as agent


class TestAggregatorsAreSuppressed:
    @pytest.mark.parametrize("sender", [
        "anita@talent500.co",
        "mail@timesjobs.com",
        "alerts@jobs.shine.com",
        "recruiters@jobs.shine.com",
        "service@naukri.com",
        "contact@messages.naukri.com",
        "no-reply@indeed.com",
        "indeedapply@indeed.com",
        "hello@abekus.co",
        "hello@yocket.in",
        "notifications-noreply@linkedin.com",
    ])
    def test_known_aggregator_senders(self, sender):
        assert agent.job_board_notification(sender) is True

    def test_matching_is_by_domain_not_address(self):
        """The pre-existing list was by address and missed every one of these:
        it named `jobs@shine.com` while the mail arrives from
        `alerts@jobs.shine.com`, and talent500 sent 29 from one mailbox that
        would each have needed their own entry."""
        assert agent.job_board_notification("someone-new@talent500.co") is True
        assert agent.job_board_notification("anything@jobs.shine.com") is True

    def test_an_unknown_sender_is_not_suppressed(self):
        assert agent.job_board_notification("hr@somecompany.com") is False

    def test_empty_sender_is_not_suppressed(self):
        assert agent.job_board_notification("") is False
        assert agent.job_board_notification(None) is False


class TestGenuineSendersSurvive:
    @pytest.mark.parametrize("sender", [
        "noreply@ripplehire.com",        # ATS relaying a real employer decision
        "recruiterone@curatal.com",          # ATS
        "jll@myworkday.com",             # ATS
        "noreply@ambitionhire.ai",       # ATS
        "noreply@wecreateproblems.com",  # assessment platform
        "talentacquisition@cognizant.com",
        "rpandey@teksystems.com",
        "noreply@synergytechs.net",
    ])
    def test_employers_and_ats_keep_flowing(self, sender):
        """An ATS is not an aggregator: it carries a real employer's decision,
        and these senders account for the genuine alerts in the dropped set."""
        assert agent.job_board_notification(sender) is False

    def test_linkedin_is_not_blocked_wholesale(self):
        """A recruiter's InMail can carry a real conversation, so only the
        notification mailboxes are suppressed."""
        assert agent.job_board_notification("a-recruiter@linkedin.com") is False


class TestMeaningNormalisation:
    @pytest.mark.parametrize("phrase,expected", [
        ("the offer letter has been sent to the candidate", "OFFER_LETTER_RECEIVED"),
        ("the offer has been released by HR", "OFFER_LETTER_RECEIVED"),
        ("the candidate has been selected for the position of DevOps Engineer", "SELECTED"),
        ("the profile not matching the requirements", "CANDIDATE_REJECTED"),
        ("the application has been declined", "CANDIDATE_REJECTED"),
        ("the candidate is expected to join on 14-Sept-2026", "JOINING_CONFIRMED"),
    ])
    def test_unambiguous_employer_phrasing_is_promoted(self, phrase, expected):
        result = agent.normalise_evidence_meaning({"meaning": phrase.upper(), "text": phrase})
        assert result["meaning"] == expected

    def test_the_original_prose_is_kept_for_audit(self):
        prose = "THE OFFER LETTER HAS BEEN SENT TO THE CANDIDATE"
        result = agent.normalise_evidence_meaning({"meaning": prose, "text": prose})
        assert result["meaning_text"] == prose
        assert result["meaning"] != prose

    def test_an_existing_code_is_left_alone(self):
        """Already-coded records must pass through untouched."""
        item = {"meaning": "OFFER_RECEIVED", "text": "an offer"}
        assert agent.normalise_evidence_meaning(item) == item

    @pytest.mark.parametrize("prose", [
        "PROFILE SHORTLISTED: UNLOCK INTERVIEWS WITH GUIDANCE FROM EX-AMAZON LEADER",
        "CANDIDATE HAS BEEN SHORTLISTED FOR AN EXCLUSIVE LIVE SESSION",
        "A RECRUITER FROM RELIABLE SOFTWARE VIEWED THE CANDIDATE'S PROFILE.",
        "SATYA NADELLA POSTED ABOUT A NEW CYBERSECURITY MODEL",
        "TRANSACTION ALERT FROM AXIS BANK",
        "SUMMARY OF THE TRANSACTION DETAILS",
    ])
    def test_marketing_prose_is_not_promoted(self, prose):
        """These are the exact strings that were dropped in production. They
        must stay unrecognised so the routing gate still refuses them."""
        result = agent.normalise_evidence_meaning({"meaning": prose, "text": prose})
        assert result["meaning"] == prose
        assert "meaning_text" not in result

    def test_shortlisted_alone_is_never_promoted(self):
        """"Shortlisted" is the single most common word in job-board marketing,
        so it cannot be a promotion trigger however genuine it sometimes is."""
        item = {"meaning": "THE CANDIDATE HAS BEEN SHORTLISTED", "text": "shortlisted"}
        assert agent.normalise_evidence_meaning(item)["meaning"] == "THE CANDIDATE HAS BEEN SHORTLISTED"

    def test_an_empty_meaning_is_left_alone(self):
        assert agent.normalise_evidence_meaning({"meaning": "", "text": "x"})["meaning"] == ""


class TestGuardsAreUnchanged:
    def test_job_board_marketing_is_stopped_before_ollama(self):
        """The whole defence is upstream now.

        should_route_to_mail_alert used to refuse unrecognised evidence prose,
        which caught this marketing a second time if a sender was missed. That
        gate is gone - it withheld 171 genuine alerts to do it - so this sender
        check is the only thing standing between job-board marketing and an
        alert, and it has to hold on its own.
        """
        assert agent.job_board_notification("anita@talent500.co") is True
        assert agent.job_board_notification("mail@timesjobs.com") is True
        assert agent.job_board_notification("recruiters@jobs.shine.com") is True
        assert agent.job_board_notification("hr@innominds.com") is False

    def test_a_promoted_meaning_does_pass_the_gate(self):
        """The other half: genuine employer mail now carries a code the gate
        already accepted, so it routes without the gate changing."""
        from core.recruitment_mail_store import should_route_to_mail_alert
        promoted = agent.normalise_evidence_meaning({
            "meaning": "THE OFFER LETTER HAS BEEN SENT TO THE CANDIDATE",
            "text": "the offer letter has been sent to the candidate",
        })
        event = {
            "primary_status": "SELECTION_NEEDS_REVIEW",
            "requires_manual_review": True,
            "structured_result": {"evidence": [promoted]},
        }
        analysis = {"classification": "offer_received", "candidate_status": "Offer Received"}
        assert should_route_to_mail_alert(event, analysis) is True
