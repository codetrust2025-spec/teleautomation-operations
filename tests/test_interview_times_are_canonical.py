"""A slot time is stored the way the schema promises: "HH:MM", 24 hour.

The roster stored whatever was typed, and production held a `3pm` and a `4pm`
booked by hand for the same afternoon. Sorting coped -- the roster's sort key
reads both -- so nothing looked wrong. Every reader that parses instead of
sorting did not: the exact-schedule match in the calendar recovery report, the
"had this interview started?" guard on missed-interview mail, anything reaching
for strptime. The recovery report duly listed one of those interviews as having
no booking at all, the day before it was due.

So the shape is enforced where a slot is written, and a booking refuses a time
nobody can read.
"""

from __future__ import annotations

import pytest

from features import candidate_store as cs


@pytest.mark.parametrize(("typed", "stored"), [
    ("3pm", "15:00"),
    ("4 PM", "16:00"),
    ("3:00 pm", "15:00"),
    ("03:00 PM", "15:00"),
    ("9:05 am", "09:05"),
    ("12am", "00:00"),
    ("12pm", "12:00"),
    ("15:00", "15:00"),
    ("16:30", "16:30"),
    ("", ""),
])
def test_a_time_is_stored_as_the_schema_promises(typed, stored):
    assert cs.normalise_interview_clock(typed) == stored


@pytest.mark.parametrize("typed", ["half four", "25:00", "later", "3pm-4pm"])
def test_an_unreadable_time_is_handed_back_rather_than_dropped(typed):
    """An import must not lose a value because this cannot express it."""
    assert cs.normalise_interview_clock(typed) == typed


@pytest.mark.parametrize("bad", ["half four", "25:00", "later"])
def test_a_booking_refuses_a_time_nobody_can_read(bad):
    with pytest.raises(ValueError, match="time of day"):
        cs._validate_interview_slot_times(bad, "16:30")
    with pytest.raises(ValueError, match="time of day"):
        cs._validate_interview_slot_times("15:00", bad)


def test_the_times_people_actually_type_are_still_accepted():
    cs._validate_interview_slot_times("3pm", "16:30")
    cs._validate_interview_slot_times("15:00", "15:30")


@pytest.fixture
def store(monkeypatch):
    data = {"candidates": []}
    monkeypatch.setattr(cs, "_load", lambda *args, **kwargs: data)
    monkeypatch.setattr(cs, "_save", lambda payload: data.update(payload))
    return data


def test_a_slot_booked_as_4pm_is_stored_as_16_00(store):
    """Both production rows came in this way -- by hand, for the same day."""
    store["candidates"].append({
        "id": "slot-1", "name": "Synthetic", "date": "2099-09-21", "time": "4pm",
        "time_end": "16:30", "slot_confirmed": True,
    })

    updated = cs.update_candidate("slot-1", {"notes": "touched"}, allow_slot_without_rules=True)

    assert (updated["time"], updated["time_end"]) == ("16:00", "16:30")


def test_a_canonical_time_makes_the_row_readable_to_every_reader(store):
    row = {"id": "slot-1", "name": "Synthetic", "date": "2099-09-21", "time": "4pm",
           "time_end": "16:30", "slot_confirmed": True}
    store["candidates"].append(row)
    cs.update_candidate("slot-1", {"notes": "touched"}, allow_slot_without_rules=True)

    stored = store["candidates"][0]
    # The shape every strptime-based reader expects, and the same instant the
    # sort key always read.
    assert stored["time"] == "16:00"
    assert cs._slot_range_minutes(stored["time"], stored["time_end"]) == (16 * 60, 16 * 60 + 30)
    assert cs.slot_still_stands(stored) is True
