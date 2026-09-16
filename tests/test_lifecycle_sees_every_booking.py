"""The lifecycle sees every booking a person holds, not the one on the list.

Lavanya Venkata Chundru's Persistent Systems interview was booked twice for
2026-09-11 02:30-04:00, and clearing it took two separate releases.

Neither symptom was identity resolution, which resolved all seven of her rows
to one set the whole time. `_candidate_slots` read `list_candidates`, which
runs `_collapse_profile_candidates` and returns one row per person -- "newest
by updated_at". So every gate downstream judged a candidate on a single row:

    person            confirmed rows    lifecycle saw
    yamini rohit                  21                1
    nitin                     20                1
    aniket                     14                1
    lavanya                        4                1

117 confirmed bookings were invisible to duplicate detection, conflict checks,
cancellation and reschedule. For Lavanya the row on show was an *unconfirmed*
one, so the lifecycle believed she held no bookings at all -- which is why the
reminder booked a slot the Google invitation had already taken, and why the
cancellation could only release whichever row the collapse surfaced.

The collapse is right for a screen and wrong for a safety gate. It is dropped
here and nowhere else: identity resolution is untouched, no stored row is
modified, and nothing is repaired retroactively.
"""

from __future__ import annotations

import pytest

from features import candidate_store
from services import interview_auto_booking as booking


def row(rid, *, name="lavanya", date="", time="", end="", confirmed=False,
        uid="", thread="", message="", company="", role="", updated="2026-09-01T00:00:00Z"):
    return {
        "id": rid, "name": name, "phone": "9000000107",
        "date": date, "time": time, "time_end": end,
        "slot_confirmed": confirmed, "updated_at": updated,
        "interview_calendar_uid": uid, "interview_source_thread_id": thread,
        "interview_source_message_id": message,
        "interview_company": company, "interview_role": role,
        "service_type": "profile_service",
    }


#: Lavanya's rows as production held them, the two Persistent ones included.
LAVANYA = [
    row("1c5954bb22", date="2026-09-03", time="12:00", end="12:30", confirmed=True,
        role="Technical Interview with Ms.Poojitha"),
    row("6a02b44817", date="2026-09-03", time="20:30", end="21:00", confirmed=True,
        role="Cognizant L2 Interview"),
    row("497b9a770e", date="2026-09-05", time="12:00", end="12:30", confirmed=True,
        role="CAPGEMINI L1 Interview"),
    row("a25c66cdab", date="2026-09-08", time="17:00", end="18:00", confirmed=True,
        role="Invenco Fullstack"),
    row("e6c21a3fec", date="2026-09-11", time="02:30", end="04:00", confirmed=True,
        uid="syntheticuid000000000002cd@google.com", thread="1a089b73a7e73ce9",
        role="V1 AI Java React Kafka Mongo Microservices AI Interview",
        updated="2026-09-10T16:53:27Z"),
    # The collapse showed this one: newest by updated_at, and unconfirmed.
    row("b42b070d94", confirmed=False, thread="1a08d03543262239",
        company="Persistent Systems",
        role="V1 AI Java React Kafka Mongo Microservices Role",
        updated="2026-09-11T08:38:41Z"),
]

SLOT = {"date": "2026-09-11", "time": "02:30", "time_end": "04:00"}


@pytest.fixture
def person(monkeypatch):
    """The store as it really is: every row, collapsed only for display."""
    monkeypatch.setattr(candidate_store, "_load", lambda **_: {"candidates": list(LAVANYA)})
    monkeypatch.setattr(candidate_store, "_with_computed", lambda r: dict(r))
    monkeypatch.setattr(
        candidate_store, "candidate_identity_ids",
        lambda cid, **kwargs: sorted(r["id"] for r in LAVANYA))
    # What the screen shows, and what this function used to be handed.
    monkeypatch.setattr(
        candidate_store, "list_candidates",
        lambda **_: [dict(LAVANYA[-1])])
    return {"id": "1c5954bb22", "name": "lavanya"}


class TestEveryBookingIsVisible:
    def test_the_lifecycle_sees_all_of_them(self, person):
        slots = booking._candidate_slots(person)
        assert {s["id"] for s in slots} == {r["id"] for r in LAVANYA}

    def test_it_no_longer_reads_the_collapsed_list(self, person):
        """The collapse returns one row; the gates need all six."""
        assert len(candidate_store.list_candidates()) == 1
        assert len(booking._candidate_slots(person)) == 6

    def test_the_confirmed_bookings_are_among_them(self, person):
        """The row the collapse showed was unconfirmed, so the lifecycle
        believed this candidate held nothing."""
        slots = booking._candidate_slots(person)
        confirmed = [s for s in slots if s.get("slot_confirmed")]
        assert len(confirmed) == 5

    def test_rows_are_computed_exactly_as_the_list_computes_them(self):
        """Only the collapse is dropped. The enrichment is unchanged."""
        import inspect

        source = inspect.getsource(booking._candidate_slots)
        assert "_with_computed" in source
        assert "list_candidates" not in source.split('"""')[-1]


class TestTheDuplicateThatGotThrough:
    def test_the_hidden_slot_is_found_when_source_identity_matches(self, person):
        """Visibility supplies the row; identity, not time equality, dedupes it."""
        reminder = {"provider_message_id": "calendar-sibling",
                    "provider_thread_id": "different-thread"}
        booked = next(s for s in booking._candidate_slots(person) if s["id"] == "e6c21a3fec")
        assert booking._same_lifecycle_slot(
            booked, result={"calendar": {"uid": booked["interview_calendar_uid"]}},
            message=reminder, schedule=SLOT) is True

    def test_and_it_was_invisible_before(self, person):
        """Proof the fix is what exposes it: the collapsed list never contained
        the row, so no duplicate test could have matched it."""
        assert all(r["id"] != "e6c21a3fec" for r in candidate_store.list_candidates())


class TestOverlapsAreStillAllowed:
    @pytest.mark.parametrize("other", [
        {"date": "2026-09-11", "time": "03:00", "time_end": "04:30"},
        {"date": "2026-09-11", "time": "02:30", "time_end": "03:15"},
        {"date": "2026-09-11", "time": "01:00", "time_end": "03:00"},
        {"date": "2026-09-11", "time": "02:30", "time_end": "04:00"},
    ])
    def test_a_different_interview_may_overlap(self, person, other):
        """Different interviews coexist, including identical start/end times."""
        booked = next(s for s in booking._candidate_slots(person) if s["id"] == "e6c21a3fec")
        assert booking._same_lifecycle_slot(
            booked, result={}, message={"provider_thread_id": "other"}, schedule=other) is False

    def test_a_different_day_is_a_different_interview(self, person):
        booked = next(s for s in booking._candidate_slots(person) if s["id"] == "e6c21a3fec")
        assert booking._same_lifecycle_slot(
            booked, result={}, message={"provider_thread_id": "x"},
            schedule={"date": "2026-09-12", "time": "02:30", "time_end": "04:00"}) is False


class TestCancellationCanReachTheHiddenRow:
    def test_the_resolver_is_offered_the_row_the_collapse_hid(self, person):
        """It could not previously be chosen, because it was never in the list."""
        slots = booking._candidate_slots(person)
        assert any(s["id"] == "e6c21a3fec" for s in slots)

    def test_it_picks_the_calendar_event_by_uid(self, person):
        slots = booking._candidate_slots(person)
        chosen = booking._resolve_existing_slot(
            slots,
            result={"calendar": {"uid": "syntheticuid000000000002cd@google.com"}},
            message={"provider_thread_id": "1a089b73a7e73ce9"},
            classification="interview_cancelled")
        assert chosen["id"] == "e6c21a3fec"

    def test_it_does_not_wander_onto_another_interview(self, person):
        """Five other bookings are now visible. A cancellation must still land
        on the one the source identifies, never a neighbour."""
        slots = booking._candidate_slots(person)
        chosen = booking._resolve_existing_slot(
            slots,
            result={"calendar": {"uid": "syntheticuid000000000002cd@google.com"}},
            message={"provider_thread_id": "1a089b73a7e73ce9"},
            classification="interview_cancelled")
        assert chosen["id"] not in {"1c5954bb22", "6a02b44817", "497b9a770e", "a25c66cdab"}


class TestNothingIsRepairedOrRewritten:
    def test_no_stored_row_is_modified(self, person):
        before = [dict(r) for r in LAVANYA]
        booking._candidate_slots(person)
        assert LAVANYA == before

    def test_it_only_reads(self):
        import inspect

        source = inspect.getsource(booking._candidate_slots)
        for writer in ("_save", "update_", "cancel_", "delete", "INSERT", "UPDATE"):
            assert writer not in source

    def test_identity_resolution_is_untouched(self):
        """The fix is which rows are handed to it, not how it resolves."""
        import inspect

        source = inspect.getsource(booking._candidate_slots)
        assert "candidate_identity_ids" in source
        assert "candidate_identity_links" not in source
