"""Deleting one payment screenshot removes that one, by its id (owner, 24 Nov).

A profile candidate's screenshots can be stored on several of its rows (each
slot row keeps what was uploaded through it). The editor shows them together.
Deleting one used to answer with the single row that owned it, and the editor
replaced its whole list with that row's: the other screenshots vanished from
the screen. Nothing else was deleted, but it looked as if everything was. The
answer is now the candidate as the editor shows it, every remaining proof with
its verification, and the totals recalculated from what is left.
"""
import asyncio

import pytest
from starlette.requests import Request

from features import candidate_store

PHONE = "9000000731"
NAME = "Delete Onetest"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(candidate_store, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setenv("PAYMENT_RECALCULATION_AUDIT_FILE", str(tmp_path / "recalc.json"))
    monkeypatch.setattr(candidate_store, "_load_cache", None)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0)
    return candidate_store


def proof(pid, owner, amount, utr):
    return {
        "id": pid, "candidate_id": owner, "attachment_type": "payment_proof",
        "filename": f"{pid}.jpg", "uploaded_at": "2026-11-13T10:16:00+00:00",
        "verification_state": "VERIFIED_COMPANY_PAYMENT", "verified_amount": amount,
        "amount": amount, "utr_number": utr, "status": "verified",
    }


def add_row(store, row_id, proofs, *, date):
    data = store._load()
    data.setdefault("candidates", []).append({
        "id": row_id, "name": NAME, "phone": PHONE, "stage": "in_progress",
        "service_type": "profile_service", "expected_payment": 20000, "payment": 0,
        "payment_proofs": proofs, "date": date,
        "created_at": f"{date}T00:00:00+00:00", "updated_at": f"{date}T00:00:00+00:00",
    })
    store._save(data)


def request(view=""):
    query = f"view={view}".encode() if view else b""
    return Request({"type": "http", "method": "DELETE", "path": "/", "headers": [], "query_string": query})


@pytest.fixture()
def three(store):
    # Row A holds the ₹40,000 screenshot; row B the two ₹3,000 ones.
    add_row(store, "rowa", [proof("p40", "rowa", 40000, "559789457773")], date="2026-11-13")
    add_row(store, "rowb", [proof("p3a", "rowb", 3000, "111795298203"),
                            proof("p3b", "rowb", 3000, "126427114041")], date="2026-11-14")
    store.recalculate_received_total("rowa", trigger="test", reason="test setup")
    store.recalculate_received_total("rowb", trigger="test", reason="test setup")
    return store


def ids(candidate):
    from features.candidate_attachments import partition_candidate_attachments
    return sorted(str(p["id"]) for p in partition_candidate_attachments(candidate)["payment_proofs"])


def stored_ids(store):
    return sorted(str(p["id"]) for r in store._load()["candidates"] for p in (r.get("payment_proofs") or []))


def test_the_editor_shows_all_three_before_any_delete(three):
    shown = three.get_candidate_detail("rowa")
    assert ids(shown) == ["p3a", "p3b", "p40"]


def test_deleting_one_of_three_removes_only_that_one(three):
    from api.routers import candidates as route

    shown = three.get_candidate_detail("rowa")
    view = shown["id"]
    result = asyncio.run(route.candidates_delete_proof("rowb", "p3a", request(view)))
    assert result["status"] == "ok"
    # Stored: only p3a is gone.
    assert stored_ids(three) == ["p3b", "p40"]
    # Answered: the candidate as the editor shows it, with the other two.
    assert ids(result["candidate"]) == ["p3b", "p40"]
    kept = {str(p["id"]): p for p in result["candidate"]["payment_proofs"]}
    assert kept["p40"]["verification_state"] == "VERIFIED_COMPANY_PAYMENT"
    assert kept["p3b"]["verification_state"] == "VERIFIED_COMPANY_PAYMENT"
    assert kept["p40"]["utr_number"] == "559789457773"
    # Totals are the remaining proofs': 40,000 + 3,000.
    assert result["payment_summary"]["verified_proof_total"] == 43000
    assert result["payment_summary"]["verified_proof_count"] == 2
    # And they persist: a fresh read agrees.
    assert ids(three.get_candidate_detail(view)) == ["p3b", "p40"]


def test_deleting_the_owner_rows_only_proof_keeps_the_other_rows_proofs(three):
    from api.routers import candidates as route

    view = three.get_candidate_detail("rowb")["id"]
    result = asyncio.run(route.candidates_delete_proof("rowa", "p40", request(view)))
    assert result["status"] == "ok"
    assert stored_ids(three) == ["p3a", "p3b"]
    assert ids(result["candidate"]) == ["p3a", "p3b"]
    assert result["payment_summary"]["verified_proof_total"] == 6000


def test_an_unknown_id_deletes_nothing(three):
    from api.routers import candidates as route

    result = asyncio.run(route.candidates_delete_proof("rowb", "nope", request("rowa")))
    assert result["status"] == "error"
    assert stored_ids(three) == ["p3a", "p3b", "p40"]


def test_without_view_the_answer_is_still_the_owner_row(three):
    from api.routers import candidates as route

    result = asyncio.run(route.candidates_delete_proof("rowb", "p3a", request()))
    assert result["candidate"]["id"] == "rowb"
    assert stored_ids(three) == ["p3b", "p40"]
