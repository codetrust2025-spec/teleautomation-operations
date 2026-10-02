"""One round-wise payment recorded on two rows is not a missing receipt.

A round-wise candidate has a ledger row before they book. The booking form reuses
it only while it is still unpaid, so once an operator typed the amount received
onto it, the booking created a second row with its own verified proof and left
the first paid, unbooked and proofless. Pending Works then told someone to upload
a receipt that already existed a row away, while the editor of the booked row
read "Paid, 1 verified proof".

Matching is by phone identity, never by name. Nothing here changes a payment or
a proof; it only decides what the row is called.
"""
import pytest

from features import candidate_store

PHONE = "9000000601"
PROOF = {
    "id": "pf1", "attachment_type": "payment_proof", "filename": "pf1.jpg",
    "verification_state": "VERIFIED_COMPANY_PAYMENT", "verified_amount": 5000,
    "utr_number": "U-pf1", "transaction_id": "T-pf1",
}


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(candidate_store, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setenv("PAYMENT_RECALCULATION_AUDIT_FILE", str(tmp_path / "recalc.json"))
    monkeypatch.setattr(candidate_store, "_load_cache", None)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0)
    return candidate_store


def row(store, row_id, *, created, booked=False, proofs=(), payment=5000, phone=PHONE,
        scope="external", name="Round Person", service="round_wise", date="2026-09-03"):
    data = store._load()
    data.setdefault("candidates", []).append({
        "id": row_id, "name": name, "phone": phone, "stage": "in_progress",
        "service_type": service, "interview_scope": scope, "expected_payment": 5000,
        "payment": payment, "payment_proofs": [dict(p) for p in proofs],
        "payment_proof_controlled": bool(proofs), "slot_confirmed": booked,
        "date": date, "created_at": created, "updated_at": created, "reference": "Test Owner",
    })
    store._save(data)


def payment_kinds(store):
    kinds = {"missing_payment_proof", "payment_proof_file_lost", "payment_evidenced_elsewhere"}
    return {
        w["candidate_id"]: w["kind"] for w in store.pending_works(month="all")["works"]
        if w["kind"] in kinds
    }


def sowmya_shape(store):
    row(store, "lead", created="2026-09-02T08:51:00+00:00", date="2026-09-02")
    row(store, "booked", created="2026-09-02T17:59:00+00:00", booked=True, proofs=[PROOF])


def test_the_unbooked_row_is_named_for_what_it_is_not_asked_for_a_receipt(store):
    sowmya_shape(store)
    assert payment_kinds(store) == {"lead": "payment_evidenced_elsewhere"}


def test_the_item_says_which_row_and_where_the_proof_is(store):
    sowmya_shape(store)
    item = next(w for w in store.pending_works(month="all")["works"] if w["candidate_id"] == "lead")
    assert item["date"] == "2026-09-02" and "2026-09-03" in item["detail"]
    assert "twice" in item["detail"]


def test_the_row_carries_the_same_reconciled_state_to_the_list_and_the_editor(store):
    sowmya_shape(store)
    listed = next(r for r in store.list_candidates(stage="all", month="all") if r["id"] == "lead")
    single = store.get_candidate("lead")
    detail = store.get_candidate_detail("lead")
    for view in (listed, single, detail):
        assert view["payment_evidenced_on"] == {"id": "booked", "date": "2026-09-03"}
        assert view["payment"] == 5000, "the recorded amount is never changed"


def test_the_booked_row_is_unaffected(store):
    sowmya_shape(store)
    booked = store.get_candidate("booked")
    assert "payment_evidenced_on" not in booked and booked["payment_unevidenced"] is False
    assert "booked" not in payment_kinds(store)


def test_another_person_with_the_same_name_does_not_evidence_it(store):
    row(store, "lead", created="2026-09-02T08:51:00+00:00")
    row(store, "booked", created="2026-09-02T17:59:00+00:00", booked=True, proofs=[PROOF],
        phone="9000000699")
    assert payment_kinds(store) == {"lead": "missing_payment_proof"}


def test_a_proof_that_does_not_cover_the_amount_does_not_evidence_it(store):
    row(store, "lead", created="2026-09-02T08:51:00+00:00", payment=9000)
    row(store, "booked", created="2026-09-02T17:59:00+00:00", booked=True, proofs=[PROOF])
    assert payment_kinds(store) == {"lead": "missing_payment_proof"}


def test_an_unverified_proof_does_not_evidence_it(store):
    pending = dict(PROOF, verification_state="UNKNOWN_RECEIVER")
    row(store, "lead", created="2026-09-02T08:51:00+00:00")
    row(store, "booked", created="2026-09-02T17:59:00+00:00", booked=True, proofs=[pending])
    assert payment_kinds(store)["lead"] == "missing_payment_proof"


def test_an_earlier_round_does_not_evidence_a_later_row(store):
    row(store, "lead", created="2026-09-02T08:51:00+00:00")
    row(store, "earlier", created="2026-08-20T10:00:00+00:00", booked=True, proofs=[PROOF])
    assert payment_kinds(store) == {"lead": "missing_payment_proof"}


def test_a_different_scope_does_not_evidence_it(store):
    row(store, "lead", created="2026-09-02T08:51:00+00:00", scope="external")
    row(store, "booked", created="2026-09-02T17:59:00+00:00", booked=True, proofs=[PROOF], scope="internal")
    assert payment_kinds(store) == {"lead": "missing_payment_proof"}


def test_an_unbooked_sibling_does_not_evidence_it(store):
    row(store, "lead", created="2026-09-02T08:51:00+00:00")
    row(store, "other", created="2026-09-02T17:59:00+00:00", booked=False, proofs=[PROOF])
    assert payment_kinds(store).get("lead") == "missing_payment_proof"


def test_a_booked_row_with_no_proof_is_still_missing(store):
    """The rule only excuses an unbooked placeholder, never a real booking."""
    row(store, "booked", created="2026-09-02T17:59:00+00:00", booked=True)
    assert payment_kinds(store) == {"booked": "missing_payment_proof"}


def test_a_row_with_nothing_recorded_is_not_flagged_at_all(store):
    row(store, "lead", created="2026-09-02T08:51:00+00:00", payment=0)
    row(store, "booked", created="2026-09-02T17:59:00+00:00", booked=True, proofs=[PROOF])
    assert payment_kinds(store) == {}


def test_pending_works_and_the_editor_never_disagree_for_any_row(store):
    """Every row Pending Works flags for a payment gap is the row the editor
    calls unevidenced, and every row the editor calls evidenced is never flagged."""
    sowmya_shape(store)
    row(store, "plain-missing", created="2026-09-05T08:00:00+00:00", booked=True, phone="9000000602", name="Plain")
    row(store, "plain-ok", created="2026-09-05T08:00:00+00:00", booked=True, proofs=[PROOF], phone="9000000603", name="Fine")
    flagged = payment_kinds(store)
    for cid in ("lead", "booked", "plain-missing", "plain-ok"):
        edit = store.get_candidate_detail(cid)
        if cid in flagged:
            assert edit["payment_unevidenced"] is True, cid
        else:
            assert edit["payment_unevidenced"] is False, cid
