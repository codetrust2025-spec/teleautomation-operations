"""A booked interview slot must survive everything written after it.

The bug these cover was silent and total: `attach_public_slot_screenshot` and
`_store_typed_attachment` each did a whole-store `_load()` -> mutate ->
`_save(data)` to change one field on one row. That wrote every row back from one
in-memory snapshot, so a snapshot taken before a booking landed carried the
pre-booking `date`, `time`, `time_end` and `slot_confirmed` back over the row
that had just been booked — 6 ms after it was written, and reported as success.

In Production this cleared row `b3500fe1b0` (Aniket / HSBC, 14 Aug 2026
1:00-1:45 PM) while the audit already read `Auto Booked`.

These tests drive the real file-backed store, not a fake, because the defect
lived in the persistence layer itself.
"""

import json

import pytest

from features import candidate_store as cs

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A real, isolated candidate store on disk."""
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    return tmp_path


def make_candidate(name="Aniket", phone="9000000001"):
    return cs.create_candidate({
        "name": name,
        "phone": phone,
        "technology": "DevOps",
        "service_type": "profile_service",
        "stage": "in_progress",
        "payment": 20000,
        "expected_payment": 20000,
        "logged_date": "2026-08-01",
    })


def stored_row(cid):
    """Read straight past every cache, the way another process would."""
    return next(
        (
            r for r in (cs._load(force=True).get("candidates") or [])
            if str(r.get("id")) == str(cid)
        ),
        None,
    )


def assert_slot(cid, date, time, time_end):
    row = stored_row(cid)
    assert row is not None, f"row {cid} vanished from storage"
    assert row["date"][:10] == date
    assert row["time"] == time
    assert row["time_end"] == time_end
    assert row["slot_confirmed"] is True


# ── a second and third interview for the same candidate ─────────────────────

def test_second_and_third_interview_each_keep_their_own_slot(store):
    """Aniket's real case: three interviews, three intact rows."""
    row = make_candidate()

    first = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="14:00", time_end="15:00",
        interview_company="Altimetrik", interview_booking_source="ai_auto_booked",
    )
    second = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
        interview_company="HSBC", interview_booking_source="ai_auto_booked",
    )
    third = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-17", time="11:30", time_end="12:00",
        interview_company="Infosys", interview_booking_source="ai_auto_booked",
    )

    assert len({first["id"], second["id"], third["id"]}) == 3, "each interview is its own row"
    assert_slot(first["id"], "2026-08-14", "14:00", "15:00")
    assert_slot(second["id"], "2026-08-14", "13:00", "13:45")
    assert_slot(third["id"], "2026-08-17", "11:30", "12:00")


def test_a_later_booking_never_disturbs_an_earlier_one(store):
    row = make_candidate()
    first = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="14:00", time_end="15:00",
    )
    before = dict(stored_row(first["id"]))

    cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
    )

    after = stored_row(first["id"])
    for field in cs.BOOKING_SLOT_FIELDS:
        assert after.get(field) == before.get(field), f"{field} moved on the earlier booking"


# ── evidence attached after the booking ─────────────────────────────────────

def test_screenshot_attached_after_booking_keeps_the_slot(store):
    """The exact Production sequence: book, then attach evidence to that row."""
    row = make_candidate()
    booked = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
        interview_company="HSBC", interview_booking_source="ai_auto_booked",
    )

    proof = cs.attach_public_slot_screenshot(
        booked["id"], data=PNG, original_name="evidence.png",
        mime_type="image/png", source="AI Mail Monitoring",
    )

    assert proof and proof["id"]
    assert_slot(booked["id"], "2026-08-14", "13:00", "13:45")


def test_the_evidence_pointer_survives_a_later_edit(store):
    """`_normalise` rebuilds rows from known keys — the pointer must be one."""
    row = make_candidate()
    booked = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
    )
    proof = cs.attach_public_slot_screenshot(
        booked["id"], data=PNG, original_name="evidence.png", mime_type="image/png",
    )
    assert stored_row(booked["id"])["slot_screenshot_proof_id"] == proof["id"]

    cs.update_candidate(booked["id"], {"notes": "an unrelated later edit"})

    assert stored_row(booked["id"])["slot_screenshot_proof_id"] == proof["id"]
    assert_slot(booked["id"], "2026-08-14", "13:00", "13:45")


def test_evidence_on_one_booking_leaves_the_others_alone(store):
    row = make_candidate()
    first = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="14:00", time_end="15:00",
    )
    second = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
    )

    cs.attach_public_slot_screenshot(
        second["id"], data=PNG, original_name="evidence.png", mime_type="image/png",
    )

    assert_slot(first["id"], "2026-08-14", "14:00", "15:00")
    assert_slot(second["id"], "2026-08-14", "13:00", "13:45")
    assert not stored_row(first["id"]).get("slot_screenshot_proof_id"), (
        "evidence must not be credited to a booking it does not belong to"
    )


def test_a_clone_does_not_inherit_the_source_rows_evidence(store):
    row = make_candidate()
    first = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="14:00", time_end="15:00",
    )
    cs.attach_public_slot_screenshot(
        first["id"], data=PNG, original_name="evidence.png", mime_type="image/png",
    )

    second = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
    )

    assert not stored_row(second["id"]).get("slot_screenshot_proof_id")
    assert not stored_row(second["id"]).get("slot_screenshot_proofs")


# ── the stale snapshot itself ───────────────────────────────────────────────

def test_a_stale_whole_store_snapshot_cannot_erase_a_newer_booking(store):
    """Reproduces the corruption directly: save a pre-booking snapshot after
    the booking landed. The snapshot is stale, so it must lose."""
    row = make_candidate()
    stale = cs._load(force=True)
    stale_rows = json.loads(json.dumps(stale.get("candidates")))
    stale_versions = dict(stale.get("_snapshot_versions") or {})

    booked = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
    )

    cs._save({"candidates": stale_rows, "_snapshot_versions": stale_versions})

    assert_slot(booked["id"], "2026-08-14", "13:00", "13:45")


def test_a_targeted_patch_refuses_to_carry_booking_fields(store):
    """The class of write that caused this may not touch a slot at all."""
    row = make_candidate()
    booked = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
    )

    with pytest.raises(ValueError, match="Booking fields"):
        cs._patch_row_fields(booked["id"], {"time": "", "slot_confirmed": False})

    assert_slot(booked["id"], "2026-08-14", "13:00", "13:45")


def test_a_targeted_patch_leaves_every_other_row_untouched(store):
    first = make_candidate(name="Aniket", phone="9000000001")
    second = make_candidate(name="Anil Kumar", phone="9000000002")
    booked = cs.assign_interview_slot(
        candidate_id=second["id"], date="2026-08-14", time="11:00", time_end="11:30",
    )
    before = dict(stored_row(first["id"]))

    cs._patch_row_fields(first["id"], {"follow_up": "called"})

    assert stored_row(first["id"])["follow_up"] == "called"
    assert stored_row(second["id"]) is not None, "an unrelated row was deleted"
    assert_slot(booked["id"], "2026-08-14", "11:00", "11:30")
    assert stored_row(first["id"])["name"] == before["name"]


# ── the invariant that reports the booking ──────────────────────────────────

def test_booking_is_certified_against_storage_not_memory(store):
    row = make_candidate()
    booked = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
    )

    assert cs.assert_slot_persisted(
        booked["id"], date="2026-08-14", time="13:00", time_end="13:45",
    )["id"] == booked["id"]

    with pytest.raises(cs.SlotNotPersistedError):
        cs.assert_slot_persisted(
            booked["id"], date="2026-08-14", time="09:00", time_end="09:30",
        )


def test_a_booking_that_did_not_persist_raises_instead_of_reporting_success(store, monkeypatch):
    """A write that silently does nothing must never read as a booking."""
    row = make_candidate()
    monkeypatch.setattr(cs, "_save", lambda _data: None)

    with pytest.raises(cs.SlotNotPersistedError):
        cs.assign_interview_slot(
            candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
        )


def test_slot_row_matches_requires_a_confirmed_slot(store):
    row = {"date": "2026-08-14", "time": "13:00", "time_end": "13:45", "slot_confirmed": False}
    assert not cs.slot_row_matches(row, date="2026-08-14", time="13:00", time_end="13:45")
    assert not cs.slot_row_matches(None, date="2026-08-14", time="13:00", time_end="13:45")
    row["slot_confirmed"] = True
    assert cs.slot_row_matches(row, date="2026-08-14", time="13:00", time_end="13:45")


# ── retry, idempotency and real duplicates ──────────────────────────────────

def test_reattaching_evidence_is_idempotent_for_the_slot(store):
    """A retry of the evidence step adds a proof; it never moves the booking."""
    row = make_candidate()
    booked = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
    )

    first = cs.attach_public_slot_screenshot(
        booked["id"], data=PNG, original_name="evidence.png", mime_type="image/png",
    )
    second = cs.attach_public_slot_screenshot(
        booked["id"], data=PNG, original_name="evidence.png", mime_type="image/png",
    )

    assert first["id"] != second["id"]
    assert stored_row(booked["id"])["slot_screenshot_proof_id"] == second["id"]
    assert len(stored_row(booked["id"])["slot_screenshot_proofs"]) == 2
    assert_slot(booked["id"], "2026-08-14", "13:00", "13:45")


def test_repeating_the_same_booking_is_a_duplicate_row_not_a_lost_slot(store):
    """Booking the identical slot twice must leave both rows describing it.

    The AI path blocks this earlier as DUPLICATE_BOOKING; the store's own job is
    simply never to lose a slot, whichever way it is called.
    """
    row = make_candidate()
    first = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
    )
    second = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
    )

    assert first["id"] != second["id"]
    assert_slot(first["id"], "2026-08-14", "13:00", "13:45")
    assert_slot(second["id"], "2026-08-14", "13:00", "13:45")


def test_no_confirmed_slot_is_lost_across_the_whole_sequence(store):
    """Book, book again, attach evidence, edit, reschedule — nothing is lost."""
    row = make_candidate()
    altimetrik = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="14:00", time_end="15:00",
        interview_company="Altimetrik",
    )
    hsbc = cs.assign_interview_slot(
        candidate_id=row["id"], date="2026-08-14", time="13:00", time_end="13:45",
        interview_company="HSBC",
    )
    cs.attach_public_slot_screenshot(
        hsbc["id"], data=PNG, original_name="hsbc.png", mime_type="image/png",
    )
    cs.attach_public_slot_screenshot(
        altimetrik["id"], data=PNG, original_name="altimetrik.png", mime_type="image/png",
    )
    cs.update_candidate(hsbc["id"], {"interview_role": "DevOps Engineer"})
    cs.update_interview_slot(
        candidate_id=altimetrik["id"], date="2026-08-14", time="16:00", time_end="17:00",
    )

    assert_slot(hsbc["id"], "2026-08-14", "13:00", "13:45")
    assert_slot(altimetrik["id"], "2026-08-14", "16:00", "17:00")
    confirmed = [
        r for r in cs._load(force=True)["candidates"] if r.get("slot_confirmed")
    ]
    assert len(confirmed) == 2, "both interviews must still be booked"
    assert all(r.get("slot_screenshot_proof_id") for r in confirmed), (
        "each booking keeps its own evidence pointer"
    )
