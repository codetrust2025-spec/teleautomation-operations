"""Every "Proof file lost" the Candidates table can show is a Pending Works task.

Pending Works decided per merged profile. A candidate whose newer slot holds a
verified, stored proof and whose older slot holds an unverified record whose
image was lost (the pre-25-Aug files were never carried over) was therefore
"evidenced" there and never listed, while any Candidates month view holding only
the older slot badged it "Proof file lost". In production that was 11 candidates.

A lost record is now checked per proof: listed as "Restore / Upload payment
proof", opening the slot that holds it, Medium when nothing else backs the
amount, Low when another stored proof does. A verified proof whose image is gone
keeps its amount and is not a task (its verification record is the evidence).

Also: a profile row with no phone and no name could not be grouped and was
dropped from Pending Works altogether, while the table still showed it.

No payment amount or proof is changed by any of this. Names and numbers are
invented.
"""
import json
import os

import pytest

from features import candidate_store

PHONE = "9000000801"
LOST_LABEL = "Restore / Upload payment proof"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(candidate_store, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setenv("PAYMENT_RECALCULATION_AUDIT_FILE", str(tmp_path / "recalc.json"))
    monkeypatch.setattr(candidate_store, "_load_cache", None)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0)
    return candidate_store


def proof(pid, *, owner=None, verified=False):
    p = {"id": pid, "attachment_type": "payment_proof", "filename": f"{pid}.jpg",
         "uploaded_at": "2026-06-01T00:00:00+00:00"}
    if owner:
        p["candidate_id"] = owner
    if verified:
        p.update(verification_state="VERIFIED_COMPANY_PAYMENT", verified_amount=20000, utr_number=f"U{pid}")
    return p


def add(store, row_id, *, proofs=(), date="2026-09-01", payment=20000, phone=PHONE, name="Lost Person",
        service="profile_service", resumes=True):
    data = store._load()
    data.setdefault("candidates", []).append({
        "id": row_id, "name": name, "phone": phone, "stage": "in_progress", "service_type": service,
        "expected_payment": 20000, "payment": payment, "payment_proofs": [dict(p) for p in proofs],
        "payment_proof_controlled": any(p.get("verification_state") for p in proofs),
        "date": date, "reference": "Test Owner",
        "resumes": [{"id": f"res-{row_id}", "filename": "cv.pdf"}] if resumes else [],
        "created_at": f"{date}T00:00:00+00:00", "updated_at": f"{date}T00:00:00+00:00",
    })
    store._save(data)


def stored_file(store, owner, name):
    folder = os.path.join(store.PROOFS_DIR, owner, "payment_proof")
    os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, name), "wb").write(b"image")


def lost_tasks(store, name="Lost Person"):
    return [w for w in store.pending_works(month="all")["works"]
            if w["candidate_name"] == name and w["kind"] == "payment_proof_file_lost"]


def production_shape(store):
    """Older slot: unverified record, image gone. Newer slot: verified, stored."""
    add(store, "old", proofs=[proof("lost1")], date="2026-07-02")
    add(store, "new", proofs=[proof("lost1", owner="old"), proof("ok1", verified=True)], date="2026-09-10")
    stored_file(store, "new", "ok1.jpg")


# --- the production bug ---------------------------------------------------------------

def test_a_lost_record_beside_a_verified_proof_is_listed(store):
    production_shape(store)
    tasks = lost_tasks(store)
    assert len(tasks) == 1, "it used to be hidden by the verified proof on the newer slot"
    assert tasks[0]["label"] == LOST_LABEL


def test_it_opens_the_slot_that_holds_the_lost_record(store):
    production_shape(store)
    task = lost_tasks(store)[0]
    assert task["candidate_id"] == "old"


def test_covered_by_another_stored_proof_is_low_priority_and_says_so(store):
    production_shape(store)
    task = lost_tasks(store)[0]
    assert task["priority"] == candidate_store.PENDING_WORK_PRIORITY_LOST_BUT_COVERED == 45
    assert "backed by another stored proof" in task["detail"]


def test_nothing_else_backing_the_amount_is_medium_priority(store):
    add(store, "solo", proofs=[proof("lost1")])
    task = lost_tasks(store)[0]
    assert task["priority"] == candidate_store.PENDING_WORK_PRIORITY["payment_proof_file_lost"] == 26
    assert "no other stored proof backs the recorded amount" in task["detail"]


def test_one_task_per_candidate_however_many_records_or_slot_copies(store):
    add(store, "a", proofs=[proof("lost1"), proof("lost2")], date="2026-07-01")
    for n, day in enumerate(("2026-07-08", "2026-07-15", "2026-07-22")):
        add(store, f"clone{n}", proofs=[proof("lost1", owner="a"), proof("lost2", owner="a")], date=day)
    tasks = lost_tasks(store)
    assert len(tasks) == 1
    assert tasks[0]["detail"].startswith("2 payment proof records have no stored image")


# --- what is not a task ------------------------------------------------------------------

def test_a_verified_proof_whose_image_is_gone_is_not_a_task(store):
    add(store, "r1", proofs=[proof("v1", verified=True)])
    assert lost_tasks(store) == []


def test_a_record_whose_file_sits_under_its_owner_is_not_lost(store):
    add(store, "owner", proofs=[proof("p1")], date="2026-07-01")
    add(store, "clone", proofs=[proof("p1")], date="2026-07-08")  # copy without candidate_id
    stored_file(store, "owner", "p1.jpg")
    assert lost_tasks(store) == []


def test_no_proof_at_all_is_still_upload_proof_not_restore(store):
    add(store, "r1", proofs=[])
    kinds = [w["kind"] for w in store.pending_works(month="all")["works"] if w["candidate_name"] == "Lost Person"]
    assert "missing_payment_proof" in kinds and "payment_proof_file_lost" not in kinds


def test_a_round_wise_row_is_checked_the_same_way(store):
    add(store, "rw", service="round_wise", proofs=[proof("lost1"), proof("ok1", verified=True)], resumes=False)
    stored_file(store, "rw", "ok1.jpg")
    tasks = lost_tasks(store)
    assert len(tasks) == 1 and tasks[0]["priority"] == 45


# --- the table and Pending Works never disagree ------------------------------------------

def _table_badges(row):
    if not row.get("payment_unevidenced"):
        return set()
    if row.get("payment_evidenced_on"):
        return {"payment_evidenced_elsewhere"}
    return {"payment_proof_file_lost" if row.get("payment_proof_files_lost") else "missing_payment_proof"}


@pytest.mark.parametrize("month", ["all", "2026-07", "2026-09"])
def test_every_payment_badge_in_every_month_view_has_a_task(store, month):
    production_shape(store)
    add(store, "other", proofs=[], name="Other Person", phone="9000000802")
    pending = {(w["candidate_name"], w["kind"]) for w in store.pending_works(month="all")["works"]}
    for row in store.list_candidates(stage="in_progress", month=month):
        for badge in _table_badges(row):
            assert (row["name"], badge) in pending, (month, row["name"], badge)


def test_the_july_view_really_shows_the_badge_that_was_missing(store):
    """The premise: this is the view the operator saw."""
    production_shape(store)
    july = [r for r in store.list_candidates(stage="in_progress", month="2026-07") if r["name"] == "Lost Person"]
    assert july and july[0]["payment_proof_files_lost"] and july[0]["payment_unevidenced"]


# --- the ungroupable row ------------------------------------------------------------------

def test_a_row_with_no_phone_and_no_name_still_gets_its_tasks(store):
    add(store, "anon", proofs=[], name="", phone="")
    works = [w for w in store.pending_works(month="all")["works"] if w["candidate_id"] == "anon"]
    assert {"missing_phone", "missing_payment_proof"} <= {w["kind"] for w in works}


# --- nothing is written --------------------------------------------------------------------

def test_listing_the_tasks_changes_no_payment_or_proof(store):
    production_shape(store)
    before = json.dumps(store._load(force=True)["candidates"], sort_keys=True)
    store.pending_works(month="all")
    after = json.dumps(store._load(force=True)["candidates"], sort_keys=True)
    assert before == after
