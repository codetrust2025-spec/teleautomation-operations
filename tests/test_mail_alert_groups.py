"""The Mail Alerts filter offers two groups; together they must mean "all".

The screen replaced a dropdown of eighteen classifications with "Selection
Related" and "Interview Related". That only works if the two groups partition
the tracked set exactly: every tracked classification in one group, none in
both. Otherwise an alert type added later belongs to neither, and it vanishes
from a filtered screen while still being counted in the totals — with nothing
failing anywhere.

The frontend asserts the same property over its own copy of the lists
(MailAlertsFilters.test.jsx). Both are needed: the browser decides which
options exist, the server decides which rows come back, and a mismatch between
them shows up as a filter that returns nothing.
"""

from __future__ import annotations

from core.recruitment_mail_store import (
    CLASSIFICATION_GROUPS,
    INTERVIEW_RELATED_CLASSIFICATIONS,
    SELECTION_RELATED_CLASSIFICATIONS,
    TRACKED_NOTIFICATION_CLASSIFICATIONS,
)


def test_the_groups_cover_every_tracked_classification():
    assert (
        SELECTION_RELATED_CLASSIFICATIONS | INTERVIEW_RELATED_CLASSIFICATIONS
        == TRACKED_NOTIFICATION_CLASSIFICATIONS
    )


def test_no_classification_is_in_both_groups():
    assert not (SELECTION_RELATED_CLASSIFICATIONS & INTERVIEW_RELATED_CLASSIFICATIONS)


def test_neither_group_is_empty():
    """An empty group would render a filter that always returns nothing."""
    assert SELECTION_RELATED_CLASSIFICATIONS
    assert INTERVIEW_RELATED_CLASSIFICATIONS


def test_interview_group_holds_only_interview_classifications():
    # A shortlist after the interviews and a cleared round are interview
    # progress, never a confirmed selection (owner, 12 Nov 2026).
    progress = {"post_interview_shortlisted", "final_round_cleared"}
    for value in INTERVIEW_RELATED_CLASSIFICATIONS - progress:
        assert value.startswith("interview_"), value
    assert progress <= INTERVIEW_RELATED_CLASSIFICATIONS


def test_selection_is_selected_offer_joining_and_final_hr_only():
    from services.recruitment_semantics import SELECTION_ALERT_CLASSIFICATIONS
    # selection_needs_reading is a Selection alert but not a selection outcome:
    # it states only that a parked mail needs reading, so selection filing
    # (which asks whether a claimed outcome is proved) never applies to it.
    assert SELECTION_RELATED_CLASSIFICATIONS == SELECTION_ALERT_CLASSIFICATIONS | {"selection_needs_reading"}
    progress = {"post_interview_shortlisted", "final_round_cleared", "interview_shortlisted"}
    assert not (SELECTION_RELATED_CLASSIFICATIONS & progress)


def test_selection_group_holds_no_interview_classification():
    """`candidate_rejected` and the offer/joining statuses are selection
    outcomes, so they belong here rather than under Interview."""
    for value in SELECTION_RELATED_CLASSIFICATIONS:
        assert not value.startswith("interview_"), value


def test_the_two_group_keys_are_what_the_screen_sends():
    # The UI sends classification_group=selection / interview. A rename on
    # either side silently produces an unfiltered screen, because an unknown
    # group falls back to the tracked default.
    assert set(CLASSIFICATION_GROUPS) == {"selection", "interview"}
    assert CLASSIFICATION_GROUPS["interview"] == INTERVIEW_RELATED_CLASSIFICATIONS
    assert CLASSIFICATION_GROUPS["selection"] == SELECTION_RELATED_CLASSIFICATIONS


def test_an_unknown_group_does_not_empty_the_screen():
    """Falling back to the tracked default is deliberate: a filter that
    silently returns nothing reads to an operator as "there are no alerts"."""
    assert CLASSIFICATION_GROUPS.get("nonsense") is None
