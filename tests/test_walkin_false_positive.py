"""Regression tests: generic walk-in drives must not become Unread Invitations.

Covers three confirmed real-world cases (using synthetic data per codebase rules):
  - Case A: Naukri mass walk-in (RMJ_ UID, naukri.com sender, no ATTENDEE)
  - Case B: Naukri invite with walk-in subject and long duration
  - Case C: Veriklick genuine interview (must be preserved)

Also covers edge cases the fix introduced:
  - Candidate-specific slot confirmation wording overrides walk-in label
  - Video meeting link overrides walk-in ICS
  - AI-rejected transition with no attendee/link → no evidence
  - AI-rejected transition WITH candidate as attendee → still evidence
  - Subject walk-in keyword alone (no ICS) → generic
  - Generic organizer + long window + no attendee → generic
"""

from __future__ import annotations

import textwrap
from datetime import datetime, timezone

import pytest

from services.unread_interview_mail import (
    interview_evidence,
    is_ai_rejected_non_transition,
    is_generic_walkin,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ics(
    *,
    uid: str = "TEST-UID-001",
    summary: str = "Interview",
    attendees: list[str] | None = None,
    duration_hours: float = 1.0,
    organizer: str = "organizer@example.com",
    method: str = "REQUEST",
    meeting_link: str = "",
    description: str = "",
) -> str:
    """Build a minimal but parseable ICS string."""
    dtstart = "20261028T093000Z"
    end_hour = 9 + int(duration_hours)
    end_min = int((duration_hours % 1) * 60)
    dtend = f"20261028T{end_hour:02d}{end_min:02d}00Z"

    attendee_lines = ""
    for email in (attendees or []):
        attendee_lines += f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED:mailto:{email}\n"

    desc = f"DESCRIPTION:{meeting_link or description}" if (meeting_link or description) else ""

    return textwrap.dedent(f"""\
        BEGIN:VCALENDAR
        METHOD:{method}
        VERSION:2.0
        BEGIN:VEVENT
        UID:{uid}
        SUMMARY:{summary}
        DTSTART:{dtstart}
        DTEND:{dtend}
        ORGANIZER:mailto:{organizer}
        {attendee_lines}{desc}
        END:VEVENT
        END:VCALENDAR
    """)


def _attachment(ics_text: str, filename: str = "invite.ics") -> dict:
    return {"filename": filename, "text": ics_text, "extracted_text": ics_text}


def _message(
    *,
    subject: str = "Interview",
    sender_email: str = "recruiter@company.com",
    recipient_email: str = "synthetic.applicant@example.com",
    body_text: str = "",
    ai_last_error_code: str = "",
    analysis_reason: str = "",
) -> dict:
    return {
        "subject": subject,
        "sender_email": sender_email,
        "recipient_email": recipient_email,
        "body_text": body_text,
        "ai_last_error_code": ai_last_error_code,
        "analysis_reason": analysis_reason,
    }


# ---------------------------------------------------------------------------
# Case A: Naukri mass walk-in with RMJ_ UID, no ATTENDEE, 8h window
# (mirrors a confirmed false positive — all names and addresses are synthetic)
# ---------------------------------------------------------------------------

class TestNaukriMassDriveWalkin:
    """Synthetic analogue of the confirmed Case A false positive.

    A generic Naukri walk-in campaign: RMJ_ UID, naukri.com sender,
    no ATTENDEE line, multi-hour block. Must never reach Unread Invitations.
    """

    SUBJECT = "Walk-in interview | Data Engineer in Example City"
    SENDER = "recruiter.mass@naukri.com"
    ICS = _ics(
        uid="RMJ_20261029_EXAMPLE_CITY_DATA_ENG",
        summary="Walk-in Drive: Data Engineer — Synthetic Tech Park",
        attendees=[],          # No ATTENDEE — mass broadcast
        duration_hours=8.5,    # 9:30 AM – 6:00 PM
        organizer="noreply@naukri.com",
    )

    def test_is_generic_walkin_returns_true(self):
        msg = _message(subject=self.SUBJECT, sender_email=self.SENDER)
        attachments = [_attachment(self.ICS)]
        assert is_generic_walkin(msg, attachments) is True

    def test_interview_evidence_returns_none(self):
        msg = _message(subject=self.SUBJECT, sender_email=self.SENDER)
        attachments = [_attachment(self.ICS)]
        assert interview_evidence(msg, attachments) is None

    def test_naukri_sender_alone_with_long_window_flags_walkin(self):
        """Even without walk-in subject wording, naukri sender + long ICS is flagged."""
        msg = _message(
            subject="Interview Invitation | Data Engineer",
            sender_email="recruiter@naukri.com",
        )
        attachments = [_attachment(_ics(
            uid="RMJ_GENERIC_001",
            attendees=[],
            duration_hours=8.0,
            organizer="noreply@naukri.com",
        ))]
        assert is_generic_walkin(msg, attachments) is True

    def test_rmj_uid_prefix_without_attendee_flags_walkin(self):
        """RMJ_ UID + no attendee = walk-in, regardless of sender domain."""
        msg = _message(subject="Job Fair — Apply Now", sender_email="hr@somecompany.com")
        attachments = [_attachment(_ics(
            uid="RMJ_JOBFAIR_2026_EXAMPLE",
            attendees=[],
            duration_hours=4.0,
        ))]
        assert is_generic_walkin(msg, attachments) is True


# ---------------------------------------------------------------------------
# Case B: walk-in subject + no ATTENDEE + long window
# (mirrors a confirmed false positive — all names and addresses are synthetic)
# ---------------------------------------------------------------------------

class TestNaukriWalkinInviteNoAttendee:
    """Synthetic analogue of the confirmed Case B false positive.

    Walk-in subject, no ATTENDEE, 9-hour block. Must never reach Unread Invitations.
    """

    SUBJECT = "Walk-in interview | Invitation for Walk-In Interview: Synthetic Role in Example City"
    SENDER = "recruiter.walkin@naukri.com"
    RECIPIENT = "synthetic.applicant.b@example.com"
    ICS = _ics(
        uid="RMJ_SYNTH_ROLE_EXAMPLE_2026_10_27",
        summary="Walk-in Interview — Synthetic Role",
        attendees=[],
        duration_hours=9.0,
        organizer="noreply@naukri.com",
    )

    def test_is_generic_walkin_returns_true(self):
        msg = _message(
            subject=self.SUBJECT,
            sender_email=self.SENDER,
            recipient_email=self.RECIPIENT,
        )
        assert is_generic_walkin(msg, [_attachment(self.ICS)]) is True

    def test_interview_evidence_returns_none(self):
        msg = _message(
            subject=self.SUBJECT,
            sender_email=self.SENDER,
            recipient_email=self.RECIPIENT,
        )
        assert interview_evidence(msg, [_attachment(self.ICS)]) is None

    def test_walkin_in_subject_alone_flags_even_without_ics(self):
        """Subject walk-in keyword with no ICS → generic."""
        msg = _message(
            subject="Walk-in interview | QA Engineer in Example City",
            sender_email=self.SENDER,
        )
        assert is_generic_walkin(msg, []) is True


# ---------------------------------------------------------------------------
# Case C: Veriklick genuine interview (must be preserved)
# (mirrors the confirmed true positive — all names and addresses are synthetic)
# ---------------------------------------------------------------------------

class TestGenuineVeriklickL1Interview:
    """Synthetic analogue of the confirmed Case C true positive.

    A Veriklick L1 interview with the candidate explicitly listed as ATTENDEE.
    Must reach Unread Invitations unchanged; must never be suppressed.
    """

    SUBJECT = "L1 Interview | Synthetic Applicant B | P-100001"
    SENDER = "noreply@veriklick.com"
    RECIPIENT = "synthetic.applicant.b@example.com"
    ICS = _ics(
        uid="SYNTH-P-100001-L1-2026",
        summary="L1 Interview | Synthetic Applicant B",
        attendees=["synthetic.applicant.b@example.com"],  # Candidate is ATTENDEE
        duration_hours=1.0,
        organizer="noreply@veriklick.com",
    )

    def test_is_generic_walkin_returns_false(self):
        msg = _message(
            subject=self.SUBJECT,
            sender_email=self.SENDER,
            recipient_email=self.RECIPIENT,
        )
        assert is_generic_walkin(msg, [_attachment(self.ICS)]) is False

    def test_interview_evidence_returns_something(self):
        msg = _message(
            subject=self.SUBJECT,
            sender_email=self.SENDER,
            recipient_email=self.RECIPIENT,
        )
        result = interview_evidence(msg, [_attachment(self.ICS)])
        assert result is not None, "Genuine L1 interview must produce evidence"
        assert "calendar" in result.lower()

    def test_ai_rejection_still_accepts_candidate_as_attendee(self):
        """When AI explicitly rejected the transition but candidate is in ATTENDEE,
        the ICS still counts as evidence (candidate was individually invited)."""
        msg = _message(
            subject=self.SUBJECT,
            sender_email=self.SENDER,
            recipient_email=self.RECIPIENT,
            ai_last_error_code="EVIDENCE_DOES_NOT_ENTAIL_TRANSITION",
        )
        result = interview_evidence(msg, [_attachment(self.ICS)])
        assert result is not None, "AI rejection should not veto an ICS with the candidate as ATTENDEE"


# ---------------------------------------------------------------------------
# Edge case: candidate slot confirmation wording overrides walk-in label
# ---------------------------------------------------------------------------

class TestCandidateSlotConfirmationOverride:
    """Slot confirmation in body prevents walk-in detection (safety valve)."""

    def test_your_slot_is_overrides_walkin_subject(self):
        msg = _message(
            subject="Walk-in interview | Data Engineer",
            sender_email="recruiter@naukri.com",
            body_text="Your slot is confirmed for 2 PM on 30 Oct. Please join the Teams meeting.",
        )
        attachments = [_attachment(_ics(uid="RMJ_001", attendees=[], duration_hours=8.0))]
        assert is_generic_walkin(msg, attachments) is False

    def test_individual_slot_wording_overrides_walkin(self):
        msg = _message(
            subject="Walk-in | Python Developer",
            sender_email="hr@company.com",
            body_text="An individual slot has been assigned to you. Please join the interview.",
        )
        assert is_generic_walkin(msg, []) is False


# ---------------------------------------------------------------------------
# Edge case: video meeting link in body → not a walk-in
# ---------------------------------------------------------------------------

class TestVideoLinkOverridesWalkin:
    """A genuine meeting room URL in the body makes the mail candidate-specific."""

    def test_teams_link_in_body_overrides_walkin_ics(self):
        msg = _message(
            subject="Walk-in interview | Backend Developer",
            sender_email="recruiter@naukri.com",
            body_text=(
                "Please join your interview via Microsoft Teams: "
                "https://teams.microsoft.com/l/meetup-join/19%3AABCDEF/thread"
            ),
        )
        attachments = [_attachment(_ics(uid="RMJ_001", attendees=[], duration_hours=8.0))]
        assert is_generic_walkin(msg, attachments) is False

    def test_zoom_link_in_body_overrides_walkin_ics(self):
        msg = _message(
            subject="Walk-in | Data Scientist",
            sender_email="hr@company.com",
            body_text="Join Zoom: https://company.zoom.us/j/98765432100",
        )
        assert is_generic_walkin(msg, []) is False

    def test_google_meet_in_body_overrides_walkin(self):
        msg = _message(
            subject="Walk-in interview | DevOps Engineer",
            sender_email="recruiter@example.com",
            body_text="Please join via https://meet.google.com/abc-defg-hij",
        )
        assert is_generic_walkin(msg, []) is False


# ---------------------------------------------------------------------------
# Edge case: AI rejection + no attendee/link → no evidence
# ---------------------------------------------------------------------------

class TestAiRejectedNonTransition:
    """AI rejected the transition. Without ATTENDEE or meeting link, ICS is not proof."""

    def test_ai_rejected_with_no_attendee_returns_none(self):
        msg = _message(
            subject="Interview | Software Engineer | Client",
            sender_email="no-reply@company.com",
            recipient_email="synthetic.applicant@example.com",
            ai_last_error_code="EVIDENCE_DOES_NOT_ENTAIL_TRANSITION",
        )
        # Non-naukri ICS without the candidate as attendee
        ics = _ics(
            uid="UUID-GENERIC-001",
            summary="Interview",
            attendees=["interviewer@company.com"],  # Only interviewer, not candidate
            duration_hours=1.0,
            organizer="hr@company.com",
        )
        attachments = [_attachment(ics)]
        assert interview_evidence(msg, attachments) is None

    def test_ai_rejected_is_recognised(self):
        msg = _message(ai_last_error_code="EVIDENCE_DOES_NOT_ENTAIL_TRANSITION")
        assert is_ai_rejected_non_transition(msg) is True

    def test_recruitment_relevance_unresolved_is_recognised(self):
        msg = _message(ai_last_error_code="RECRUITMENT_RELEVANCE_UNRESOLVED")
        assert is_ai_rejected_non_transition(msg) is True

    def test_analysis_reason_field_also_detected(self):
        msg = _message(analysis_reason="EVIDENCE_DOES_NOT_ENTAIL_TRANSITION")
        assert is_ai_rejected_non_transition(msg) is True

    def test_no_error_is_not_rejected(self):
        msg = _message()
        assert is_ai_rejected_non_transition(msg) is False


# ---------------------------------------------------------------------------
# Edge case: normal short interview ICS without walk-in keywords
# ---------------------------------------------------------------------------

class TestNormalInterviewICS:
    """A non-walk-in ICS with proper candidate ATTENDEE is genuine."""

    def test_short_interview_with_candidate_attendee(self):
        msg = _message(
            subject="L2 Interview | Synthetic Applicant C | P-200042",
            sender_email="noreply@veriklick.com",
            recipient_email="synthetic.applicant.c@example.com",
        )
        ics = _ics(
            uid="SYNTH-P200042-L2",
            summary="L2 Interview | Synthetic Applicant C",
            attendees=["synthetic.applicant.c@example.com"],
            duration_hours=1.0,
            organizer="noreply@veriklick.com",
        )
        assert is_generic_walkin(msg, [_attachment(ics)]) is False
        result = interview_evidence(msg, [_attachment(ics)])
        assert result is not None

    def test_interview_without_walkin_keyword_is_not_flagged(self):
        msg = _message(
            subject="Technical Round | Python Developer",
            sender_email="hr@startupco.com",
        )
        assert is_generic_walkin(msg, []) is False

    def test_open_drive_keyword_flags_as_walkin(self):
        msg = _message(subject="Open Drive | Multiple Roles | 1 Nov")
        assert is_generic_walkin(msg, []) is True

    def test_hiring_drive_keyword_flags_as_walkin(self):
        msg = _message(subject="Hiring Drive | Freshers | Example City")
        assert is_generic_walkin(msg, []) is True


# ---------------------------------------------------------------------------
# Edge case: ICS without UID is not considered evidence
# ---------------------------------------------------------------------------

class TestICSWithoutUID:
    """An ICS without a UID field is not a reliable interview identity."""

    def test_ics_without_uid_is_not_evidence(self):
        ics_no_uid = textwrap.dedent("""\
            BEGIN:VCALENDAR
            METHOD:REQUEST
            VERSION:2.0
            BEGIN:VEVENT
            SUMMARY:Interview
            DTSTART:20261028T100000Z
            DTEND:20261028T110000Z
            ORGANIZER:mailto:hr@company.com
            ATTENDEE;ROLE=REQ-PARTICIPANT:mailto:synthetic.applicant@example.com
            END:VEVENT
            END:VCALENDAR
        """)
        msg = _message(
            subject="Interview",
            sender_email="hr@company.com",
            recipient_email="synthetic.applicant@example.com",
        )
        result = interview_evidence(msg, [_attachment(ics_no_uid)])
        assert result is None


# ---------------------------------------------------------------------------
# TRACKED_NOTIFICATION_CLASSIFICATIONS partition sanity
# ---------------------------------------------------------------------------

class TestClassificationPartition:
    """recruitment_walkin must not appear in TRACKED_NOTIFICATION_CLASSIFICATIONS.

    Including it would place it in SELECTION_RELATED (the computed residual)
    and cause it to appear under the Selection Related filter, misleading operators.
    """

    def test_recruitment_walkin_not_in_tracked(self):
        from core.recruitment_mail_store import TRACKED_NOTIFICATION_CLASSIFICATIONS
        assert "recruitment_walkin" not in TRACKED_NOTIFICATION_CLASSIFICATIONS, (
            "recruitment_walkin must not be in TRACKED_NOTIFICATION_CLASSIFICATIONS — "
            "it would leak into the Selection Related filter. "
            "Walk-in records are written directly by record_walkin_alert()."
        )

    def test_recruitment_walkin_in_canonical(self):
        from core.recruitment_mail_store import CANONICAL_CLASSIFICATIONS
        assert "recruitment_walkin" in CANONICAL_CLASSIFICATIONS

    def test_partition_still_holds(self):
        from core.recruitment_mail_store import (
            INTERVIEW_RELATED_CLASSIFICATIONS,
            SELECTION_RELATED_CLASSIFICATIONS,
            TRACKED_NOTIFICATION_CLASSIFICATIONS,
        )
        assert (
            SELECTION_RELATED_CLASSIFICATIONS | INTERVIEW_RELATED_CLASSIFICATIONS
            == TRACKED_NOTIFICATION_CLASSIFICATIONS
        ), "The two filter groups must partition TRACKED_NOTIFICATION_CLASSIFICATIONS exactly"
