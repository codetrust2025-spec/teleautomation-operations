"""L3 is no longer an interview round (owner, 24 Nov 2026).

Nothing new is set to L3: not a booking, not an invite read, not the evidence
reader, not the round edit. Interviews that already say L3 keep it -- read,
shown and saved unchanged -- so no history is lost.
"""
import pytest

from features import candidate_store


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(candidate_store, "_load_cache", None, raising=False)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0, raising=False)
    return candidate_store


def add(store, row_id, rnd):
    data = store._load()
    data.setdefault("candidates", []).append({
        "id": row_id, "name": f"Person {row_id}", "phone": "9000000999", "stage": "in_progress",
        "service_type": "round_wise", "interview_round": rnd, "date": "2026-11-20", "time": "10:00 AM",
        "created_at": "2026-11-20T00:00:00+00:00", "updated_at": "2026-11-20T00:00:00+00:00",
    })
    store._save(data)


def stored_round(store, row_id):
    return next(r for r in store._load()["candidates"] if r["id"] == row_id).get("interview_round")


@pytest.mark.parametrize("raw", ["L3", "l3", "Round 3", "3", "Technical Round 3"])
def test_nothing_new_can_be_l3(raw):
    assert candidate_store.bookable_interview_round(raw) == ""


@pytest.mark.parametrize("raw,label", [("L1", "L1"), ("Round 2", "L2"), ("HR", "HR"), ("Final", "Final")])
def test_the_other_rounds_are_bookable(raw, label):
    assert candidate_store.bookable_interview_round(raw) == label


def test_l3_is_not_offered_but_still_reads_as_itself():
    assert "L3" not in candidate_store.VALID_INTERVIEW_ROUNDS
    assert "L3" not in candidate_store.STANDARD_INTERVIEW_ROUNDS
    assert candidate_store.normalise_interview_round("L3") == "L3"


def test_an_l3_interview_keeps_its_round_when_other_fields_are_saved(store):
    add(store, "old3", "L3")
    store.update_candidate("old3", {"notes": "checked"}, allow_slot_without_rules=True)
    assert stored_round(store, "old3") == "L3"
    # Saving the form as it stands re-sends L3: still kept.
    store.update_candidate("old3", {"interview_round": "L3", "notes": "again"}, allow_slot_without_rules=True)
    assert stored_round(store, "old3") == "L3"


def test_an_l3_interview_can_move_to_another_round(store):
    add(store, "old3", "L3")
    store.set_interview_round("old3", interview_round="L2", by="admin")
    assert stored_round(store, "old3") == "L2"


def test_l3_cannot_be_set_on_another_interview(store):
    add(store, "two", "L2")
    with pytest.raises(ValueError, match="L3 is no longer an interview round"):
        store.set_interview_round("two", interview_round="L3", by="admin")
    store.update_candidate("two", {"interview_round": "L3"}, allow_slot_without_rules=True)
    assert stored_round(store, "two") == "L2"


def test_resending_l3_on_an_l3_interview_is_not_a_change(store):
    add(store, "old3", "L3")
    row = store.set_interview_round("old3", interview_round="L3", by="admin")
    assert row["interview_round"] == "L3"
    assert stored_round(store, "old3") == "L3"


def test_the_round_edit_refuses_l3(store):
    from api.routers import candidates as route

    class Req:
        headers = {}

    refusal = route._round_change_refusal(Req(), candidate_store, {"interview_round": "L2"}, "L3")
    assert refusal == {"status": "error", "message": "L3 is no longer an interview round."}
    assert route._round_change_refusal(Req(), candidate_store, {"interview_round": "L3"}, "L3") is None


def test_an_invite_that_says_l3_fills_no_round(monkeypatch):
    from features import slot_screenshot_parse as parse

    source = open(parse.__file__, encoding="utf-8").read()
    assert 'merged["interview_round"] = bookable_interview_round(' in source
