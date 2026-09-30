"""A proof whose image was lost is not a proof that was never uploaded, and one
row removing a proof must not strand its siblings' copies.

The files uploaded before the 25 Aug 2026 server move were never carried over,
so their records remain with nothing behind them. Payment amounts are never
touched by any of this.
"""
import os

import pytest

from features import candidate_store

PHONE = "9000000501"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(candidate_store, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setenv("PAYMENT_RECALCULATION_AUDIT_FILE", str(tmp_path / "recalc.json"))
    monkeypatch.setattr(candidate_store, "_load_cache", None)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0)
    return candidate_store


def legacy_proof(pid, *, owner=None, state=""):
    proof = {"id": pid, "attachment_type": "payment_proof", "filename": f"{pid}.jpg",
             "uploaded_at": "2026-06-01T00:00:00+00:00", "legacy_storage": True}
    if owner:
        proof["candidate_id"] = owner
    if state:
        proof.update(verification_state=state, verified_amount=20000, utr_number=f"U{pid}")
    return proof


def row(store, row_id, *, proofs=(), payment=20000, phone=PHONE, name="Lost Proof"):
    data = store._load()
    data.setdefault("candidates", []).append({
        "id": row_id, "name": name, "phone": phone, "stage": "in_progress",
        "service_type": "profile_service", "expected_payment": 20000, "payment": payment,
        "payment_proofs": [dict(p) for p in proofs], "date": "2026-09-01",
        "created_at": "2026-09-01T00:00:00+00:00", "updated_at": "2026-09-01T00:00:00+00:00",
    })
    store._save(data)


def put_file(store, owner, name):
    folder = os.path.join(store.PROOFS_DIR, owner, "payment_proof")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "wb") as handle:
        handle.write(b"image")
    return path


def listed(store, name="Lost Proof"):
    return next(r for r in store.list_candidates(stage="all", month="all") if r["name"] == name)


def kinds(store, name="Lost Proof"):
    payment_kinds = {"missing_payment_proof", "payment_proof_file_lost"}
    return [
        w["kind"] for w in store.pending_works(month="all")["works"]
        if w["candidate_name"] == name and w["kind"] in payment_kinds
    ]


# --- B: lost is not missing ----------------------------------------------------

def test_a_proof_record_with_no_file_is_reported_as_lost_not_missing(store):
    row(store, "r1", proofs=[legacy_proof("p1")])
    candidate = listed(store)
    assert candidate["payment_proof_files_lost"] is True
    assert kinds(store) == ["payment_proof_file_lost"]


def test_the_recorded_amount_is_never_changed_by_a_lost_file(store):
    row(store, "r1", proofs=[legacy_proof("p1")], payment=20000)
    candidate = listed(store)
    assert candidate["payment"] == 20000
    assert candidate["recorded_payment"] == 20000


def test_no_proof_record_at_all_is_still_missing(store):
    row(store, "r1", proofs=[])
    assert listed(store)["payment_proof_files_lost"] is False
    assert kinds(store) == ["missing_payment_proof"]


def test_an_unreviewed_proof_whose_image_is_stored_is_neither_lost_nor_missing(store):
    row(store, "r1", proofs=[legacy_proof("p1")])
    put_file(store, "r1", "p1.jpg")
    assert listed(store)["payment_proof_files_lost"] is False
    assert kinds(store) == []


def test_a_copy_on_a_sibling_row_finds_the_file_in_the_owners_folder(store):
    row(store, "owner", proofs=[legacy_proof("p1", owner="owner")])
    row(store, "copy", proofs=[legacy_proof("p1", owner="owner")])
    put_file(store, "owner", "p1.jpg")
    assert listed(store)["payment_proof_files_lost"] is False


def test_a_verified_proof_keeps_its_amount_even_when_its_file_is_gone(store):
    row(store, "r1", proofs=[legacy_proof("p1", state="VERIFIED_COMPANY_PAYMENT")])
    candidate = listed(store)
    assert candidate["payment"] == 20000 and candidate["payment_unevidenced"] is False
    assert candidate["payment_proof_files_lost"] is False
    assert kinds(store) == []


def test_lost_files_on_one_slot_do_not_hide_a_stored_file_on_another(store):
    row(store, "a", proofs=[legacy_proof("p1")])
    row(store, "b", proofs=[legacy_proof("p2")])
    put_file(store, "b", "p2.jpg")
    assert listed(store)["payment_proof_files_lost"] is False


def test_the_summary_carries_the_flag_to_the_editor(store):
    from features import payment_receipts

    row(store, "r1", proofs=[legacy_proof("p1")])
    assert payment_receipts.api_summary(listed(store))["proof_files_lost"] is True


# --- C: deleting one row's copy must not strand the others ---------------------

def test_removing_a_sibling_copy_leaves_the_owners_file(store):
    row(store, "owner", proofs=[legacy_proof("p1", owner="owner")])
    row(store, "copy", proofs=[legacy_proof("p1", owner="owner")])
    path = put_file(store, "owner", "p1.jpg")

    assert store.delete_proof("copy", "p1") is True

    assert os.path.isfile(path), "the owner still holds the proof"


def test_removing_the_owners_record_keeps_the_file_while_a_copy_remains(store):
    row(store, "owner", proofs=[legacy_proof("p1", owner="owner")])
    row(store, "copy", proofs=[legacy_proof("p1", owner="owner")])
    path = put_file(store, "owner", "p1.jpg")

    assert store.delete_proof("owner", "p1") is True

    assert os.path.isfile(path), "a sibling row's copy still points at this file"


def test_the_file_goes_with_the_last_record(store):
    row(store, "owner", proofs=[legacy_proof("p1", owner="owner")])
    row(store, "copy", proofs=[legacy_proof("p1", owner="owner")])
    path = put_file(store, "owner", "p1.jpg")

    store.delete_proof("copy", "p1")
    store.delete_proof("owner", "p1")

    assert not os.path.exists(path)


def test_deleting_a_sole_proof_still_removes_its_file(store):
    row(store, "solo", proofs=[legacy_proof("p1")])
    path = put_file(store, "solo", "p1.jpg")

    assert store.delete_proof("solo", "p1") is True

    assert not os.path.exists(path)


def test_deleting_one_proof_never_touches_another_proofs_file(store):
    row(store, "r1", proofs=[legacy_proof("p1"), legacy_proof("p2")])
    keep = put_file(store, "r1", "p2.jpg")
    put_file(store, "r1", "p1.jpg")

    store.delete_proof("r1", "p1")

    assert os.path.isfile(keep)
