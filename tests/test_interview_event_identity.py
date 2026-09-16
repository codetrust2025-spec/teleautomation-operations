"""One interview, however many mails describe it — and no more than that.

The real shape, from the Sourcebae booking that prompted this: a covering note
at 05:12 and the calendar invitation at 05:13, both for the 4:15pm interview on
11 Aug. Different subjects and different bodies, so neither message_hash nor
the subject-scoped body_hash dedupe sees them as related.
"""
from __future__ import annotations

from features import interview_event_identity as identity

UID = "syntheticuid000000000001ab@google.com"


def covering_mail(**changes):
    """The recruiter's own note. Classified by AI, so it has no calendar UID."""
    row = {
        "interview_date": "2026-08-11",
        "interview_time": "04:15 PM",
        "recruiter_email": "neha@sourcebae.com",
        "company_domain": "sourcebae.com",
        "calendar_uid": None,
        "calendar_sequence": None,
    }
    row.update(changes)
    return row


def invitation(**changes):
    """The ICS mail for the same meeting."""
    row = {
        "interview_date": "2026-08-11",
        "interview_time": "04:15 PM",
        "recruiter_email": "neha@sourcebae.com",
        "company_domain": "sourcebae.com",
        "calendar_uid": UID,
        "calendar_sequence": 0,
    }
    row.update(changes)
    return row


# ── one interview ────────────────────────────────────────────────────────────


def test_the_invitation_does_not_duplicate_the_covering_mail():
    assert identity.duplicate_of([covering_mail()], invitation()) is not None


def test_the_covering_mail_does_not_duplicate_the_invitation():
    """Order of arrival must not change the answer."""
    assert identity.duplicate_of([invitation()], covering_mail()) is not None


def test_the_same_invitation_twice_is_one_meeting():
    """Google attaches the invitation twice; a resend arrives again later."""
    assert identity.duplicate_of([invitation()], invitation()) is not None


def test_a_new_interview_is_not_a_duplicate_of_anything():
    assert identity.duplicate_of([], invitation()) is None


# ── genuinely different interviews stay separate ─────────────────────────────


def test_a_different_time_the_same_day_is_a_different_interview():
    """Lavanya really did have two Sourcebae interviews: 10:30am and 4:15pm."""
    morning = invitation(interview_time="10:30 AM", calendar_uid="syntheticuid000000000003ef@google.com")
    assert identity.duplicate_of([morning], invitation()) is None


def test_two_employers_at_the_same_hour_stay_separate():
    """A candidate cannot attend both, but merging would silently lose one."""
    other_company = covering_mail(
        recruiter_email="swati.sharma@winwire.com", company_domain="winwire.com"
    )
    assert identity.duplicate_of([other_company], covering_mail()) is None


def test_two_invitations_are_compared_only_by_uid():
    """Both sides carrying a UID means the schedule rule must not fire.

    Same organisation, same slot, different meetings — the UIDs settle it.
    """
    first = invitation(calendar_uid="uid-one@google.com")
    second = invitation(calendar_uid="uid-two@google.com")
    assert identity.duplicate_of([first], second) is None


def test_a_covering_mail_with_no_schedule_matches_nothing():
    """No date or time is not evidence of sameness."""
    vague = covering_mail(interview_date=None, interview_time=None)
    assert identity.duplicate_of([vague], covering_mail(interview_date=None, interview_time=None)) is None


# ── reschedules ──────────────────────────────────────────────────────────────


def test_a_reschedule_is_not_swallowed_as_a_duplicate():
    """Same UID, higher SEQUENCE: the meeting moved and the caller must apply it."""
    moved = invitation(interview_time="05:00 PM", calendar_sequence=1)
    assert identity.is_reschedule(invitation(), moved) is True
    assert identity.duplicate_of([invitation()], moved) is None


def test_the_same_sequence_arriving_again_is_still_a_duplicate():
    assert identity.is_reschedule(invitation(), invitation()) is False
    assert identity.duplicate_of([invitation()], invitation()) is not None


def test_an_older_sequence_is_not_treated_as_a_reschedule():
    """A late-delivered earlier copy must not rewind the booking."""
    current = invitation(calendar_sequence=3)
    stale = invitation(calendar_sequence=1)
    assert identity.is_reschedule(current, stale) is False
    assert identity.duplicate_of([current], stale) is not None


def test_a_reschedule_is_recognised_among_several_recorded_events():
    history = [covering_mail(), invitation(), invitation(calendar_uid="unrelated@google.com")]
    moved = invitation(interview_time="05:30 PM", calendar_sequence=2)
    assert identity.duplicate_of(history, moved) is None


# ── shape guards ─────────────────────────────────────────────────────────────


def test_matching_is_case_and_whitespace_insensitive():
    noisy = invitation(calendar_uid=f"  {UID.upper()}  ")
    assert identity.same_calendar_meeting(invitation(), noisy) is True


def test_a_missing_uid_on_both_sides_never_matches_by_uid():
    assert identity.same_calendar_meeting(covering_mail(), covering_mail()) is False


def test_company_domain_is_used_when_the_recruiter_address_is_absent():
    without = covering_mail(recruiter_email=None)
    assert identity.duplicate_of([without], invitation()) is not None
