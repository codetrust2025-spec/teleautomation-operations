"""One payment, one ledger credit -- however many ways it reaches the ledger (12 Oct).

Posting a verified payment after the fact (a confirmed booking, a confirmed
receiver, the owner-approved backfill) must never credit a transaction twice,
never credit one transaction to two candidates, and never post a payment that a
rebooking only reused. Names and references are invented.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from features import candidate_store as cs
from features import payment_verification_engine as engine

ROOT = Path(__file__).resolve().parent.parent
PAYEE = "Example Payee"
PAYEE_UPI = "examplepayee@ybl"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    registry = tmp_path / "payment_receiver_accounts.json"
    registry.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(registry))
    monkeypatch.setenv("REFERRER_REGISTRY_FILE", str(tmp_path / "referrers.json"))
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("COMPANY_PAYMENT_UPI_IDS", PAYEE_UPI)
    monkeypatch.setenv("COMPANY_PAYMENT_RECEIVER_NAMES", PAYEE)
    candidate_file = tmp_path / "candidates.json"
    candidate_file.write_text(json.dumps({"candidates": []}), encoding="utf-8")
    monkeypatch.setattr(cs, "_FILE", str(candidate_file))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)


def _proof(pid="p1", utr="612300007777", amount=5000, **extra):
    return {"id": pid, "attachment_type": "payment_proof", "verification_state": "VERIFIED_COMPANY_PAYMENT",
            "company_payment_verified": True, "verified_amount": amount, "utr_number": utr,
            "sha256": (utr * 6)[:64], "receiver_name": PAYEE, "payment_scope": "ROUND",
            "uploaded_at": "2026-09-02T10:00:00+00:00", **extra}


def _store(*rows):
    Path(cs._FILE).write_text(json.dumps({"candidates": list(rows)}), encoding="utf-8")
    cs._load_cache = None


def _row(cid, name, proofs, created="2026-09-02T09:00:00+00:00"):
    return {"id": cid, "name": name, "phone": "9000000" + cid[-3:], "service_type": "round_wise", "stage": "in_progress",
            "payment_proofs": proofs, "created_at": created}


def test_posting_the_same_proof_again_lands_on_the_same_credit():
    first = engine.credit_confirmed_payment(_proof(), source_module="public_slot_confirmation", candidate_id="c-001")
    again = engine.credit_confirmed_payment(_proof(), source_module="owner_approved_backfill", candidate_id="c-001")
    assert first["ledger_entry_id"] and again["ledger_entry_id"] == first["ledger_entry_id"]
    assert len(engine.ledger_entries()) == 1
    assert len(engine._load_ledger()["payments"]) == 1


def test_one_transaction_is_never_credited_to_two_candidates():
    engine.credit_confirmed_payment(_proof(), source_module="public_slot_confirmation", candidate_id="c-001")
    other = engine.credit_confirmed_payment(_proof(pid="p2"), source_module="public_slot_confirmation", candidate_id="c-002")
    assert other.get("duplicate_payment_id")
    assert "ledger_entry_id" not in other
    assert len(engine.ledger_entries()) == 1


def test_a_receiver_confirmation_for_a_transaction_credited_elsewhere_is_refused_and_changes_nothing():
    engine.credit_confirmed_payment(_proof(), source_module="public_slot_confirmation", candidate_id="c-001")
    pending_proof = _proof(pid="p2", verification_state="INCOMPLETE_PAYMENT_EVIDENCE", company_payment_verified=False)
    _store(_row("c-002", "Bala Fakename", [pending_proof]))

    with pytest.raises(ValueError, match="already credited"):
        cs.confirm_proof_receiver("c-002", "p2", receiver_type="company", receiver_name=PAYEE,
                                  reason="test", reviewer="test reviewer")

    [stored] = cs._load(force=True)["candidates"][0]["payment_proofs"]
    assert stored["verification_state"] == "INCOMPLETE_PAYMENT_EVIDENCE"
    assert "ledger_entry_id" not in stored
    assert len(engine.ledger_entries()) == 1


def test_a_receipt_filed_again_from_edit_candidate_does_not_credit_twice(monkeypatch):
    # Posted after a form booking...
    engine.credit_confirmed_payment(_proof(utr="612300008888"), source_module="public_slot_confirmation", candidate_id="c-001")
    # ...then the same receipt uploaded on the candidate (the Edit Candidate path reads and posts).
    monkeypatch.setattr(
        "features.ollama_payment_extract.extract_payment_with_ollama",
        lambda *_a, **_kw: {"amount": 5000, "receiver_name": PAYEE, "receiver_upi_id": PAYEE_UPI, "utr_number": "612300008888",
                            "transaction_id": "", "status": "success", "confidence_score": 98,
                            "is_payment_screenshot": True, "payment_date": "2026-09-02"},
    )
    result = engine.verify_payment_screenshot(
        b"another-copy-of-the-receipt", "image/jpeg", source_module="candidate_payment_proof",
        entity_id="c-001", entity_name="Asha Fakename", candidate_id="c-001",
        purpose="candidate_payment", payment_scope="ROUND",
    )
    assert result["verification_state"] == "VERIFIED_COMPANY_PAYMENT"
    assert len(engine.ledger_entries()) == 1


def test_a_rebooking_that_reuses_a_payment_posts_nothing(monkeypatch):
    from core import public_slot_api

    calls = []
    monkeypatch.setattr(engine, "credit_confirmed_payment", lambda *a, **k: calls.append(k) or {})
    # The decision is made where the booking finishes: a reused payment is skipped.
    source = (ROOT / "core" / "public_slot_api.py").read_text(encoding="utf-8")
    assert 'if not payment_reuse.get("reuse_allowed"):\n                _post_booking_payments(row, requested_proof_ids)' in source
    # And _post_booking_payments itself only posts receipts this booking attached.
    _store(_row("c-001", "Asha Fakename", [_proof(pending_proof_id="pend-1")]))
    monkeypatch.setattr(cs, "list_attachments", lambda cid, kind: cs._load(force=True)["candidates"][0]["payment_proofs"])
    public_slot_api._post_booking_payments({"id": "c-001", "name": "Asha Fakename"}, ["some-other-pending-id"])
    assert calls == []


def _tool():
    spec = importlib.util.spec_from_file_location("pending_corrections", ROOT / "scripts" / "pending_corrections.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_backfill_posts_each_transaction_once_links_every_copy_and_is_a_no_op_after(capsys):
    shared = _proof(pid="shared", utr="612300001010", amount=10000, payment_scope="PROFILE")
    _store(
        _row("c-010", "Pria Fakename", [dict(shared)], created="2026-09-02T09:00:00+00:00"),
        _row("c-011", "Pria Fakename", [dict(shared)], created="2026-09-10T09:00:00+00:00"),  # a booking copy
        _row("c-012", "Ravi Fakename", [_proof(pid="own", utr="612300002020")]),
        _row("c-013", "Mani Fakename", [_proof(pid="unverified", utr="612300003030",
                                               verification_state="INCOMPLETE_PAYMENT_EVIDENCE")]),
    )
    tool = _tool()

    assert tool.main(["--item", "N1"]) == 0
    assert "item N1: 2 change(s)" in capsys.readouterr().out
    assert engine.ledger_entries() == []          # a dry run writes nothing

    assert tool.main(["--item", "N1", "--apply", "--confirm", "1"]) == 2   # stale count refused
    assert engine.ledger_entries() == []

    assert tool.main(["--item", "N1", "--apply", "--confirm", "2"]) == 0
    entries = engine.ledger_entries()
    assert len(entries) == 2
    rows = {r["id"]: r for r in cs._load(force=True)["candidates"]}
    shared_links = {rows[cid]["payment_proofs"][0].get("ledger_entry_id") for cid in ("c-010", "c-011")}
    assert len(shared_links) == 1 and None not in shared_links       # both copies point at one credit
    assert rows["c-013"]["payment_proofs"][0].get("ledger_entry_id") is None   # unverified is not touched

    capsys.readouterr()
    assert tool.main(["--item", "N1"]) == 0
    assert "item N1: 0 change(s)" in capsys.readouterr().out
