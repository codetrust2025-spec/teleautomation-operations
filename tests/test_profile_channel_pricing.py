"""Profile channel pricing: Direct = ₹20,000, Consultancy = ₹15,000.

    Direct       the candidate came directly to us       -> expected ₹20,000
    Consultancy  the candidate came through a consultancy -> expected ₹15,000

The BGV certificates add-on (₹30,000) rides on either. An amount an operator
agreed by hand is a charge, not a stale default, and is never rewritten by a
channel change. Nothing here touches a payment that was received.
"""
import pytest

from features import candidate_store as cs

DIRECT, CONSULTANCY, BGV = 20_000, 15_000, 30_000


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    return cs


def make(store, **fields):
    base = {"name": f"Channel Person {len(store._load().get('candidates', []))}",
            "phone": f"90000007{len(store._load().get('candidates', [])):02d}",
            "service_type": "profile_service", "reference": "Test Owner"}
    base.update(fields)
    return store.create_candidate(base)


def expected_of(store, cid):
    return store.get_candidate(cid)["expected_payment"]


# --- the two channels, on create -------------------------------------------------

def test_a_direct_candidate_is_expected_to_pay_20000(store):
    row = make(store, consultancy=False)
    assert expected_of(store, row["id"]) == DIRECT
    assert store.get_candidate(row["id"])["consultancy"] is False


def test_a_candidate_with_no_channel_stated_is_direct(store):
    row = make(store)
    assert expected_of(store, row["id"]) == DIRECT and store.get_candidate(row["id"])["consultancy"] is False


def test_a_consultancy_candidate_is_expected_to_pay_15000(store):
    row = make(store, consultancy=True)
    assert expected_of(store, row["id"]) == CONSULTANCY


@pytest.mark.parametrize("value,channel,amount", [
    (True, True, CONSULTANCY), ("true", True, CONSULTANCY), ("yes", True, CONSULTANCY),
    ("consultancy", True, CONSULTANCY), (1, True, CONSULTANCY),
    (False, False, DIRECT), ("false", False, DIRECT), ("direct", False, DIRECT),
    ("", False, DIRECT), (None, False, DIRECT), (0, False, DIRECT),
])
def test_every_spelling_the_form_or_an_import_can_send_resolves_to_the_right_channel(store, value, channel, amount):
    row = make(store, consultancy=value)
    got = store.get_candidate(row["id"])
    assert got["consultancy"] is channel and got["expected_payment"] == amount


# --- editing the channel ----------------------------------------------------------

def test_switching_a_candidate_to_consultancy_reprices_the_default(store):
    row = make(store)
    store.update_candidate(row["id"], {"consultancy": True})
    assert expected_of(store, row["id"]) == CONSULTANCY


def test_switching_back_to_direct_restores_20000(store):
    row = make(store, consultancy=True)
    store.update_candidate(row["id"], {"consultancy": False})
    assert expected_of(store, row["id"]) == DIRECT


def test_the_bgv_add_on_follows_the_channel_in_both_directions(store):
    row = make(store, bgv_certificates=True)
    assert expected_of(store, row["id"]) == DIRECT + BGV
    store.update_candidate(row["id"], {"consultancy": True})
    assert expected_of(store, row["id"]) == CONSULTANCY + BGV, "a BGV profile must not stay at the direct price"
    store.update_candidate(row["id"], {"consultancy": False})
    assert expected_of(store, row["id"]) == DIRECT + BGV


def test_an_amount_agreed_by_hand_survives_a_channel_change(store):
    row = make(store, expected_payment=22_000)
    store.update_candidate(row["id"], {"consultancy": True})
    assert expected_of(store, row["id"]) == 22_000
    store.update_candidate(row["id"], {"consultancy": False})
    assert expected_of(store, row["id"]) == 22_000


def test_a_custom_amount_with_bgv_survives_a_channel_change(store):
    row = make(store, bgv_certificates=True, expected_payment=60_000)
    store.update_candidate(row["id"], {"consultancy": True})
    assert expected_of(store, row["id"]) == 60_000


def test_editing_something_else_never_flips_the_channel(store):
    row = make(store, consultancy=True)
    store.update_candidate(row["id"], {"follow_up": "called"})
    got = store.get_candidate(row["id"])
    assert got["consultancy"] is True and got["expected_payment"] == CONSULTANCY


def test_a_round_wise_row_has_no_channel(store):
    row = make(store, service_type="round_wise", consultancy=True)
    got = store.get_candidate(row["id"])
    assert got["consultancy"] is False and got["expected_payment"] == 5_000


# --- what the channel does to what is owed ------------------------------------------

def test_the_same_payment_is_partial_for_direct_and_paid_for_consultancy(store):
    direct = make(store, consultancy=False, payment=15_000)
    consultancy = make(store, consultancy=True, payment=15_000)
    d, c = store.get_candidate(direct["id"]), store.get_candidate(consultancy["id"])
    assert (d["payment_status"], d["balance_due"]) == ("partial", 5_000)
    assert (c["payment_status"], c["balance_due"]) == ("paid", 0)


def test_baselines_are_the_documented_amounts():
    assert cs.baseline_for(False) == DIRECT and cs.baseline_for(True) == CONSULTANCY
    assert cs.baseline_for_service("profile_service", consultancy=True, bgv_certificates=True) == CONSULTANCY + BGV


# --- a stored consultancy row left at the direct default ----------------------------

def test_a_consultancy_row_stored_at_the_direct_default_is_read_at_15000(store):
    """Written by an older path that set the flag but not the amount."""
    data = store._load()
    data.setdefault("candidates", []).append({
        "id": "stale", "name": "Stale Default", "phone": "9000000799", "stage": "in_progress",
        "service_type": "profile_service", "consultancy": True, "expected_payment": DIRECT,
        "payment": 0, "date": "2026-09-01",
    })
    store._save(data)
    assert expected_of(store, "stale") == CONSULTANCY


def test_a_direct_row_with_a_custom_15000_is_not_silently_repriced_on_read(store):
    """Reading never rewrites an amount; only a deliberate channel edit does."""
    data = store._load()
    data.setdefault("candidates", []).append({
        "id": "custom", "name": "Custom Fifteen", "phone": "9000000798", "stage": "in_progress",
        "service_type": "profile_service", "consultancy": False, "expected_payment": CONSULTANCY,
        "payment": 0, "date": "2026-09-01",
    })
    store._save(data)
    assert expected_of(store, "custom") == CONSULTANCY


def test_slot_clones_keep_the_channel_and_price_of_their_profile(store):
    row = make(store, consultancy=True, payment=5_000)
    clone = store._duplicate_candidate_slot(store.get_candidate(row["id"]), date="2026-10-05", time="10:00")
    assert store.get_candidate(clone["id"])["consultancy"] is True
    assert expected_of(store, clone["id"]) == CONSULTANCY


def test_a_bgv_profile_clone_keeps_its_channel_price(store):
    row = make(store, consultancy=True, bgv_certificates=True)
    assert expected_of(store, row["id"]) == CONSULTANCY + BGV
    clone = store._duplicate_candidate_slot(store.get_candidate(row["id"]), date="2026-10-06", time="11:00")
    assert expected_of(store, clone["id"]) == CONSULTANCY + BGV
