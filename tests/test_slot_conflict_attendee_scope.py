"""A clash is a clash for a person, not for the clock.

Two candidates interviewed at the same hour by two different attendees are not
competing for anything. Blocking one of them loses a real interview to a rule
that was never about the schedule.

The guard rails matter as much as the change: when the caller does not say who
is attending, or when either side's attendee is unknown, every overlap still
blocks. An unattributed clash is exactly the case where guessing is unsafe.
"""
from __future__ import annotations

import pytest

from features import candidate_store as cs


@pytest.fixture
def roster(monkeypatch):
    """Two confirmed interviews at 16:00-16:30, held by different attendees."""

    def seed(rows):
        monkeypatch.setattr(cs, "_load", lambda: {"candidates": rows})

    return seed


def _slot(name, attendee, *, time="16:00", time_end="16:30", cid=None):
    return {
        "id": cid or f"id-{name.lower()}",
        "name": name,
        "technology": "Full Stack",
        "date": "2026-08-11",
        "time": time,
        "time_end": time_end,
        "slot_confirmed": True,
        "stage": "in_progress",
        "interview_attendee": attendee,
    }


def test_the_same_attendee_cannot_be_in_two_places(roster):
    """The protection that must survive: one person, overlapping interviews."""
    roster([_slot("Nitin", "Bhavana")])

    conflicts = cs.find_interview_slot_conflicts(
        "2026-08-11", "16:15", "16:45", attendee="Bhavana"
    )
    assert [c["name"] for c in conflicts] == ["Nitin"]
    assert conflicts[0]["interview_attendee"] == "Bhavana"


def test_two_attendees_can_interview_in_parallel(roster):
    """The change: a different attendee is not a clash."""
    roster([_slot("Nitin", "Bhavana")])

    assert cs.find_interview_slot_conflicts(
        "2026-08-11", "16:15", "16:45", attendee="Tool"
    ) == []


def test_only_the_named_attendee_is_considered(roster):
    roster([
        _slot("Nitin", "Bhavana", cid="a"),
        _slot("Someone Else", "Tool", cid="b"),
    ])

    for_bhavana = cs.find_interview_slot_conflicts(
        "2026-08-11", "16:15", "16:45", attendee="Bhavana"
    )
    for_tool = cs.find_interview_slot_conflicts(
        "2026-08-11", "16:15", "16:45", attendee="Tool"
    )
    assert [c["name"] for c in for_bhavana] == ["Nitin"]
    assert [c["name"] for c in for_tool] == ["Someone Else"]


def test_attendee_match_is_case_insensitive(roster):
    roster([_slot("Nitin", "Bhavana")])
    assert cs.find_interview_slot_conflicts(
        "2026-08-11", "16:15", "16:45", attendee="bhavana"
    )


def test_naming_no_attendee_keeps_the_old_global_behaviour(roster):
    """Every existing caller passes nothing, and must be unaffected."""
    roster([
        _slot("Nitin", "Bhavana", cid="a"),
        _slot("Someone Else", "Tool", cid="b"),
    ])

    conflicts = cs.find_interview_slot_conflicts("2026-08-11", "16:15", "16:45")
    assert {c["name"] for c in conflicts} == {"Nitin", "Someone Else"}


def test_non_overlapping_times_are_never_a_conflict(roster):
    roster([_slot("Nitin", "Bhavana", time="14:00", time_end="14:30")])
    assert cs.find_interview_slot_conflicts(
        "2026-08-11", "16:15", "16:45", attendee="Bhavana"
    ) == []


def test_unconfirmed_and_dropped_slots_still_do_not_block(roster):
    dropped = _slot("Dropped", "Bhavana", cid="c")
    dropped["stage"] = "dropped"
    unconfirmed = _slot("Unconfirmed", "Bhavana", cid="d")
    unconfirmed["slot_confirmed"] = False
    roster([dropped, unconfirmed])

    assert cs.find_interview_slot_conflicts(
        "2026-08-11", "16:15", "16:45", attendee="Bhavana"
    ) == []


def test_the_real_pujitha_case_stays_blocked(roster):
    """Both live rows resolve to Bhavana, so scoping does not unblock her.

    Recorded because it is the outcome that matters operationally: the 4:15pm
    interview is not freed by per-attendee scoping — it needs a different
    attendee or a deliberate override.
    """
    roster([_slot("Nitin", "Bhavana")])

    conflicts = cs.find_interview_slot_conflicts(
        "2026-08-11", "16:15", "16:45", attendee="Bhavana"
    )
    assert conflicts, "same attendee on both interviews must still block"
