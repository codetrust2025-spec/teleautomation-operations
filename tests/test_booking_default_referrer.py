"""A candidate the booking form creates is Ravinder's until another referrer is
set (owner, 24 Nov). It used to be Thrilok."""
import pytest

from features import candidate_store


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(candidate_store, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setenv("PAYMENT_RECALCULATION_AUDIT_FILE", str(tmp_path / "recalc.json"))
    monkeypatch.setattr(candidate_store, "_load_cache", None)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0)
    return candidate_store


def test_the_default_is_ravinder():
    assert candidate_store.DEFAULT_SLOT_BOOKER_REFERENCE == "Ravinder"


def test_a_first_time_round_wise_booking_is_referred_by_ravinder(store, monkeypatch):
    # Payment is checked elsewhere; this is only about who the new row names.
    monkeypatch.setattr(store, "slot_booking_payment_block_reason", lambda *a, **k: None)
    row, _action = store.import_confirmed_interview_slot(
        name="Newbook Testcase", date="2026-12-01", time="10:00 AM", time_end="11:00 AM",
        phone="9000000741", interview_round="L1", service_type="round_wise", technology="Java",
    )
    saved = store.get_candidate(row["id"])
    assert saved["reference"] == "Ravinder"
    assert saved["service_type"] == "round_wise"


def test_an_explicit_referrer_is_never_replaced(store):
    created = store.create_candidate({
        "name": "Owned Testcase", "phone": "9000000742", "technology": "Java",
        "reference": "Pavan Kalyan", "stage": "in_progress", "service_type": "round_wise",
    })
    assert store.get_candidate(created["id"])["reference"] == "Pavan Kalyan"
