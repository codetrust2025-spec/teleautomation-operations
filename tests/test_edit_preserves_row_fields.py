"""Editing a candidate never discards what the edit did not touch.

`update_candidate` replaced the stored row with `_normalise(...)`, which builds a
row from a fixed list of keys, so every other stored field was dropped. In
production that was `interview_feedback`: the next unrelated edit of an attended
candidate silently erased "how the interview went". Names and numbers below are
invented.
"""
import pytest

from features import candidate_store as cs


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    return cs


def add(store, rid, **extra):
    data = store._load()
    row = {
        "id": rid, "name": "Edit Probe", "phone": "9000000601", "email": "", "technology": "Java",
        "stage": "in_progress", "service_type": "profile_service", "expected_payment": 20000,
        "payment": 5000, "reference": "Test Owner", "date": "2026-09-01",
        "created_at": "2026-09-01T00:00:00+00:00", "updated_at": "2026-09-01T00:00:00+00:00",
        "slot_confirmed": True, "interview_attendance_status": "attended", "interview_attended": True,
        "interview_attended_by": "Test Admin", "interview_attendance_remark": "went well",
        "interview_feedback": "positive",
    }
    row.update(extra)
    data.setdefault("candidates", []).append(row)
    store._save(data)


def stored(store, rid):
    return next(r for r in store._load(force=True)["candidates"] if r["id"] == rid)


# --- the production bug ----------------------------------------------------------------

@pytest.mark.parametrize("patch", [
    {"follow_up": "call on Monday"},
    {"email": "probe@example.test"},
    {"technology": "ServiceNow"},
    {"reference": "Another Owner"},
    {"consultancy": True},
    {"payment": 6000},
    {"notes": "a note"},
])
def test_an_unrelated_edit_keeps_the_interview_feedback(store, patch):
    add(store, "c1")
    store.update_candidate("c1", patch)
    assert stored(store, "c1")["interview_feedback"] == "positive"


def test_an_empty_feedback_is_kept_as_empty_not_turned_into_a_missing_key(store):
    add(store, "c1", interview_feedback="", interview_attendance_status="rescheduled", interview_attended=False)
    store.update_candidate("c1", {"email": "probe@example.test"})
    assert stored(store, "c1")["interview_feedback"] == ""


def test_the_rest_of_the_attendance_record_is_kept_too(store):
    add(store, "c1")
    store.update_candidate("c1", {"email": "probe@example.test"})
    row = stored(store, "c1")
    assert (row["interview_attendance_status"], row["interview_attended_by"], row["interview_attendance_remark"]) == (
        "attended", "Test Admin", "went well")


def test_the_feedback_of_every_slot_row_survives_a_shared_edit(store):
    add(store, "c1", interview_feedback="positive")
    add(store, "c2", interview_feedback="negative", date="2026-09-08")
    add(store, "c3", interview_feedback="", date="2026-09-15", interview_attendance_status="rescheduled", interview_attended=False)
    store.update_candidate("c1", {"email": "probe@example.test", "technology": "ServiceNow"})
    assert [stored(store, rid)["interview_feedback"] for rid in ("c1", "c2", "c3")] == ["positive", "negative", ""]
    assert all(stored(store, rid)["email"] == "probe@example.test" for rid in ("c1", "c2", "c3")), \
        "the edit itself still reaches the profile's other rows"


def test_any_stored_field_the_normaliser_does_not_model_survives(store):
    """The general guarantee: a field added tomorrow is not lost the day after."""
    add(store, "c1", some_future_field={"a": 1}, another_marker="keep me")
    add(store, "c2", some_future_field=[1, 2], date="2026-09-08")
    store.update_candidate("c1", {"email": "probe@example.test"})
    assert stored(store, "c1")["some_future_field"] == {"a": 1} and stored(store, "c1")["another_marker"] == "keep me"
    assert stored(store, "c2")["some_future_field"] == [1, 2]


def test_a_dropped_row_keeps_its_feedback_too(store):
    add(store, "c1")
    store.update_candidate("c1", {"stage": "dropped"})
    assert stored(store, "c1")["interview_feedback"] == "positive"


# --- the edit still does its job ---------------------------------------------------------

def test_the_fields_that_were_edited_do_change(store):
    add(store, "c1")
    store.update_candidate("c1", {"email": "probe@example.test", "follow_up": "later"})
    row = stored(store, "c1")
    assert row["email"] == "probe@example.test" and row["follow_up"] == "later"


def test_normalisation_still_applies(store):
    add(store, "c1")
    store.update_candidate("c1", {"email": "  PROBE@Example.TEST  "})
    assert stored(store, "c1")["email"] == "probe@example.test"


def test_a_payment_and_its_rules_are_unchanged_by_the_fix(store):
    add(store, "c1", payment=7000)
    store.update_candidate("c1", {"follow_up": "x"})
    row = stored(store, "c1")
    assert row["payment"] == 7000 and row["expected_payment"] == 20000


def test_the_attendance_function_can_still_clear_feedback(store):
    """Feedback is cleared on purpose when a round stops being 'attended'."""
    add(store, "c1")
    store.set_interview_attendance("c1", status="not_attended", by="Test Admin", allow_future=True)
    assert stored(store, "c1")["interview_feedback"] == ""


def test_the_attendance_function_can_still_set_feedback(store):
    add(store, "c1", interview_feedback="")
    store.set_interview_attendance("c1", status="attended", feedback="negative", by="Test Admin", allow_future=True)
    assert stored(store, "c1")["interview_feedback"] == "negative"


# --- the name backfill used the same replacement ------------------------------------------

def test_the_name_backfill_keeps_feedback(store):
    add(store, "c1", name="PERLA ABHILASH")
    store.backfill_canonical_candidate_names()
    row = stored(store, "c1")
    assert row["name"] == "Abilash Perla" and row["interview_feedback"] == "positive"


def test_a_candidate_with_no_feedback_ever_is_not_given_one(store):
    add(store, "c1")
    data = store._load()
    del data["candidates"][0]["interview_feedback"]
    store._save(data)
    store.update_candidate("c1", {"email": "probe@example.test"})
    assert "interview_feedback" not in stored(store, "c1") or stored(store, "c1")["interview_feedback"] in ("", None)
