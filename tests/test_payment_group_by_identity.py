"""One candidate's money is derived from the whole candidate, identified by phone.

Regression for a profile whose slots each carried a different subset of its two
distinct verified payments (10,000 and 20,000): the displayed slot showed
whatever that one slot held, and the display grouped rows by name alone, so two
different people sharing a name would have pooled their payments.
"""
import pytest

from features import candidate_store


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setenv(
        "PAYMENT_RECALCULATION_AUDIT_FILE", str(tmp_path / "recalc_audit.json")
    )
    monkeypatch.setattr(candidate_store, "_load_cache", None)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0)
    return candidate_store


def proof(amount, pid, utr, *, sha=None, state="VERIFIED_COMPANY_PAYMENT"):
    return {
        "id": pid,
        "attachment_type": "payment_proof",
        "verified_amount": amount,
        "verification_state": state,
        "utr_number": utr,
        "transaction_id": f"T{utr}",
        "sha256": sha or f"sha-{pid}",
        "filename": f"{pid}.jpg",
    }


_counter = iter(range(1, 10_000))


def slot(store, *, name, phone, proofs, recorded, expected=20000, updated):
    """Insert a slot row directly: production holds one row per interview slot,
    which `create_candidate` (one active profile per phone) will not build."""
    row_id = f"slot{next(_counter):06d}"
    data = store._load()
    data.setdefault("candidates", []).append({
        "id": row_id, "name": name, "phone": phone, "stage": "in_progress",
        "service_type": "profile_service", "expected_payment": expected,
        "payment": recorded, "payment_proofs": [dict(p) for p in proofs],
        "payment_proof_controlled": bool(proofs), "date": updated[:10],
        "created_at": updated, "updated_at": updated,
    })
    store._save(data)
    return row_id


def by_phone(rows, phone):
    return [r for r in rows if r.get("phone") == phone]


TEN = proof(10000, "p10", "111111111111")
TWENTY = proof(20000, "p20", "222222222222")


def test_exact_payment(store):
    slot(store, name="Exact Pay", phone="9000000201", proofs=[TWENTY], recorded=20000,
         updated="2026-09-01T00:00:00+00:00")
    (row,) = by_phone(store.list_candidates(), "9000000201")
    assert (row["payment"], row["balance_due"], row["above_minimum"]) == (20000, 0, 0)
    assert row["payment_status"] == "paid"


def test_partial_payment(store):
    slot(store, name="Partial Pay", phone="9000000202", proofs=[TEN], recorded=10000,
         updated="2026-09-01T00:00:00+00:00")
    (row,) = by_phone(store.list_candidates(), "9000000202")
    assert (row["payment"], row["balance_due"], row["above_minimum"]) == (10000, 10000, 0)
    assert row["payment_status"] == "partial"


def test_excess_payment_is_preserved_and_reported(store):
    slot(store, name="Excess Pay", phone="9000000203", proofs=[TEN, TWENTY], recorded=30000,
         updated="2026-09-01T00:00:00+00:00")
    (row,) = by_phone(store.list_candidates(), "9000000203")
    assert row["payment"] == 30000, "a genuine payment is never trimmed to expected"
    assert row["above_minimum"] == 10000
    assert row["balance_due"] == 0
    assert row["verified_proof_count"] == 2


def test_same_transaction_on_every_slot_counts_once(store):
    for day in ("01", "02", "03"):
        slot(store, name="Dup Slots", phone="9000000204", proofs=[TWENTY], recorded=20000,
             updated=f"2026-09-{day}T00:00:00+00:00")
    (row,) = by_phone(store.list_candidates(), "9000000204")
    assert row["payment"] == 20000
    assert row["verified_proof_count"] == 1
    assert row["above_minimum"] == 0


def test_same_screenshot_under_another_reference_counts_once(store):
    twin = proof(20000, "p20b", "999999999999", sha=TWENTY["sha256"])
    twin["transaction_id"] = TWENTY["transaction_id"]
    slot(store, name="Dup Shot", phone="9000000205", proofs=[TWENTY, twin], recorded=20000,
         updated="2026-09-01T00:00:00+00:00")
    (row,) = by_phone(store.list_candidates(), "9000000205")
    assert row["payment"] == 20000 and row["verified_proof_count"] == 1


def test_newest_slot_holding_only_part_of_the_proofs_still_shows_the_whole(store):
    """The displayed slot is the newest; an older slot holds the later proof."""
    slot(store, name="Split Slots", phone="9000000206", proofs=[TEN, TWENTY], recorded=30000,
         updated="2026-09-01T00:00:00+00:00")
    slot(store, name="Split Slots", phone="9000000206", proofs=[TEN], recorded=30000,
         updated="2026-09-09T00:00:00+00:00")
    (row,) = by_phone(store.list_candidates(), "9000000206")
    assert row["payment"] == 30000
    assert row["above_minimum"] == 10000
    assert row["balance_due"] == 0
    assert row["verified_proof_count"] == 2


def test_two_people_with_one_name_do_not_pool_payments(store):
    slot(store, name="Same Name", phone="9000000207", proofs=[TWENTY], recorded=20000,
         updated="2026-09-01T00:00:00+00:00")
    slot(store, name="Same Name", phone="9000000208", proofs=[TEN], recorded=10000,
         updated="2026-09-02T00:00:00+00:00")
    rows = [r for r in store.list_candidates() if r["name"] == "Same Name"]
    assert len(rows) == 2
    by = {r["phone"]: r for r in rows}
    assert by["9000000207"]["payment"] == 20000
    assert by["9000000208"]["payment"] == 10000
    assert by["9000000208"]["above_minimum"] == 0
    assert by["9000000208"]["balance_due"] == 10000


def test_one_person_spelled_two_ways_is_one_candidate(store):
    slot(store, name="Venkat Spelling", phone="9000000209", proofs=[TEN, TWENTY], recorded=30000,
         updated="2026-09-01T00:00:00+00:00")
    slot(store, name="Venkata Spelling", phone="+91 90000 00209", proofs=[TEN], recorded=30000,
         updated="2026-09-02T00:00:00+00:00")
    rows = [r for r in store.list_candidates() if "Spelling" in r["name"]]
    assert len(rows) == 1
    assert rows[0]["payment"] == 30000


def test_a_phoneless_row_joins_the_only_phone_its_name_has(store):
    slot(store, name="No Phone Row", phone="9000000210", proofs=[TWENTY], recorded=20000,
         updated="2026-09-01T00:00:00+00:00")
    slot(store, name="No Phone Row", phone="", proofs=[], recorded=0,
         updated="2026-09-02T00:00:00+00:00")
    rows = [r for r in store.list_candidates() if r["name"] == "No Phone Row"]
    assert len(rows) == 1 and rows[0]["payment"] == 20000


def test_a_phoneless_row_with_an_ambiguous_name_stays_separate(store):
    slot(store, name="Two Owners", phone="9000000211", proofs=[TWENTY], recorded=20000,
         updated="2026-09-01T00:00:00+00:00")
    slot(store, name="Two Owners", phone="9000000212", proofs=[TEN], recorded=10000,
         updated="2026-09-02T00:00:00+00:00")
    slot(store, name="Two Owners", phone="", proofs=[], recorded=0,
         updated="2026-09-03T00:00:00+00:00")
    rows = [r for r in store.list_candidates() if r["name"] == "Two Owners"]
    assert len(rows) == 3


def test_detail_view_agrees_with_the_list_for_every_slot_of_the_profile(store):
    first = slot(store, name="Detail Agree", phone="9000000213", proofs=[TEN], recorded=30000,
                 updated="2026-09-01T00:00:00+00:00")
    slot(store, name="Detail Agree", phone="9000000213", proofs=[TEN, TWENTY], recorded=30000,
         updated="2026-09-09T00:00:00+00:00")
    detail = store.get_candidate_detail(first)
    assert (detail["payment"], detail["above_minimum"], detail["balance_due"]) == (30000, 10000, 0)


def test_detail_view_of_a_namesake_never_includes_the_other_persons_proofs(store):
    mine = slot(store, name="Namesake", phone="9000000214", proofs=[TEN], recorded=10000,
                updated="2026-09-01T00:00:00+00:00")
    slot(store, name="Namesake", phone="9000000215", proofs=[TWENTY], recorded=20000,
         updated="2026-09-02T00:00:00+00:00")
    detail = store.get_candidate_detail(mine)
    assert detail["payment"] == 10000
    assert [p["id"] for p in detail["payment_proofs"]] == ["p10"]
