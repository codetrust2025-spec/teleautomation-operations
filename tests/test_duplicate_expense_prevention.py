"""A duplicate is refused before it is saved, and a voided one stops counting."""
from __future__ import annotations

import importlib

import pytest

from features import transaction_identity


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A handler-expense store on a scratch file, with no other money sources."""
    from features import handler_expenses

    monkeypatch.setattr(handler_expenses, "_FILE", str(tmp_path / "handler_expenses.json"))
    monkeypatch.setattr(handler_expenses, "PROOFS_DIR", str(tmp_path / "proofs"))

    from features import financial_reconciliation

    monkeypatch.setattr(financial_reconciliation, "_ledger_transactions", lambda: [])
    monkeypatch.setattr(financial_reconciliation, "_company_expense_transactions", lambda: [])
    monkeypatch.setattr(financial_reconciliation, "_canonical", lambda n: str(n or "").strip().lower())
    return handler_expenses


def _recovery(**over):
    row = {
        "record_id": "le_recovery",
        "kind": "recovery",
        "source_module": "public_slot_booking",
        "amount": 5000,
        "date": "2026-07-22",
        "receiver": "pavan kalyan",
        "handler": "Pavan Kalyan",
        "created_at": "2026-07-22T11:47:00+00:00",
    }
    row.update(over)
    return row


def test_an_expense_repeating_a_posted_recovery_is_refused(store, monkeypatch):
    from features import financial_reconciliation

    monkeypatch.setattr(
        financial_reconciliation, "_ledger_transactions", lambda: [_recovery()]
    )
    with pytest.raises(transaction_identity.DuplicateTransactionError) as caught:
        store.create_expense(
            {"reference": "Pavan Kalyan", "amount": 5000, "date": "2026-07-22",
             "category": "commission"}
        )
    assert "already recorded as a candidate-payment recovery" in str(caught.value)
    # Nothing was written: a refused entry must not half-exist.
    assert store.list_expenses(include_voided=True) == []


def test_a_genuine_second_expense_on_another_day_is_accepted(store, monkeypatch):
    from features import financial_reconciliation

    monkeypatch.setattr(
        financial_reconciliation, "_ledger_transactions", lambda: [_recovery()]
    )
    row = store.create_expense(
        {"reference": "Pavan Kalyan", "amount": 5000, "date": "2026-07-29",
         "category": "travel"}
    )
    assert row["amount"] == 5000
    assert len(store.list_expenses()) == 1


def test_two_unrelated_expenses_of_the_same_amount_both_survive(store):
    store.create_expense(
        {"reference": "Pavan Kalyan", "amount": 1200, "date": "2026-07-22", "category": "food"}
    )
    # Same amount and day, different handler: two real expenses.
    store.create_expense(
        {"reference": "Venugopal", "amount": 1200, "date": "2026-07-22", "category": "food"}
    )
    assert len(store.list_expenses()) == 2


def test_the_same_expense_entered_twice_is_refused(store):
    store.create_expense(
        {"reference": "Pavan Kalyan", "amount": 3000, "date": "2026-07-10",
         "category": "commission"}
    )
    with pytest.raises(transaction_identity.DuplicateTransactionError):
        store.create_expense(
            {"reference": "Pavan Kalyan", "amount": 3000, "date": "2026-07-10",
             "category": "commission"}
        )
    assert len(store.list_expenses()) == 1


def test_a_voided_expense_keeps_its_record_but_stops_counting(store):
    row = store.create_expense(
        {"reference": "Pavan Kalyan", "amount": 5000, "date": "2026-07-22",
         "category": "commission", "note": "candidate payment"}
    )
    voided = store.void_expense(
        row["id"],
        status="RECLASSIFIED_TO_RECOVERY",
        reason="Same UPI transaction already posted as a recovery",
        actor="admin",
        ledger_ref="le_recovery",
    )
    assert voided["void_status"] == "RECLASSIFIED_TO_RECOVERY"
    assert voided["note"] == "candidate payment"          # history preserved
    assert voided["voided_at"] and voided["voided_by"] == "admin"

    assert store.list_expenses() == []                     # no longer money paid
    assert len(store.list_expenses(include_voided=True)) == 1
    assert store.total_for_handler("Pavan Kalyan") == 0
    assert store.summary_by_handler() == {}


def test_voiding_frees_the_slot_so_a_correct_entry_can_be_made(store):
    row = store.create_expense(
        {"reference": "Pavan Kalyan", "amount": 5000, "date": "2026-07-22",
         "category": "commission"}
    )
    store.void_expense(row["id"], status="VOIDED_DUPLICATE", reason="entered twice")
    replacement = store.create_expense(
        {"reference": "Pavan Kalyan", "amount": 5000, "date": "2026-07-22",
         "category": "commission", "note": "corrected"}
    )
    assert replacement["note"] == "corrected"
    assert len(store.list_expenses()) == 1


def test_a_void_must_say_why(store):
    row = store.create_expense(
        {"reference": "Pavan Kalyan", "amount": 500, "date": "2026-07-22", "category": "food"}
    )
    with pytest.raises(ValueError):
        store.void_expense(row["id"], status="VOIDED_DUPLICATE", reason="")
    with pytest.raises(ValueError):
        store.void_expense(row["id"], status="DELETED", reason="because")


def test_voiding_is_idempotent(store):
    row = store.create_expense(
        {"reference": "Pavan Kalyan", "amount": 500, "date": "2026-07-22", "category": "food"}
    )
    first = store.void_expense(row["id"], status="VOIDED_DUPLICATE", reason="entered twice")
    again = store.void_expense(row["id"], status="VOIDED_DUPLICATE", reason="different reason")
    assert again["voided_at"] == first["voided_at"]
    assert again["void_reason"] == "entered twice"


def test_editing_an_expense_never_resurrects_a_void(store):
    row = store.create_expense(
        {"reference": "Pavan Kalyan", "amount": 500, "date": "2026-07-22", "category": "food"}
    )
    store.void_expense(row["id"], status="VOIDED_DUPLICATE", reason="entered twice")
    store.update_expense(row["id"], {"note": "checked by admin"})
    assert store.list_expenses() == []


def test_a_proof_upload_records_the_image_hash(store):
    row = store.create_expense(
        {"reference": "Pavan Kalyan", "amount": 500, "date": "2026-07-22", "category": "food"}
    )
    entry = store.add_proof(
        row["id"], data=b"fake-image-bytes", original_name="p.jpg", mime_type="image/jpeg"
    )
    assert entry["sha256"] == transaction_identity.screenshot_hash(b"fake-image-bytes")
    stored = store.list_expenses()[0]
    assert stored["screenshot_hash"] == entry["sha256"]


def test_the_same_screenshot_filed_against_a_second_expense_is_reported(store):
    # add_proof itself stores whatever it is given; this is what the read-only report finds.
    # Refusing the re-attached receipt happens in refuse_duplicate_proof, which the proof
    # route calls first -- see tests/test_expense_duplicate_guard.py.
    first = store.create_expense(
        {"reference": "Pavan Kalyan", "amount": 500, "date": "2026-07-22", "category": "food"}
    )
    store.add_proof(first["id"], data=b"receipt", original_name="p.jpg", mime_type="image/jpeg")
    second = store.create_expense(
        {"reference": "Pavan Kalyan", "amount": 500, "date": "2026-08-02", "category": "food"}
    )
    store.add_proof(second["id"], data=b"receipt", original_name="p.jpg", mime_type="image/jpeg")
    from features import financial_reconciliation

    report = financial_reconciliation.reconciliation_report()
    assert report["duplicate_groups"] == 1
    assert report["groups"][0]["basis"] == "screenshot_hash"


def test_the_report_changes_nothing(store, monkeypatch):
    from features import financial_reconciliation

    monkeypatch.setattr(
        financial_reconciliation, "_ledger_transactions", lambda: [_recovery()]
    )
    store.create_expense(
        {"reference": "Pavan Kalyan", "amount": 5000, "date": "2026-07-29", "category": "travel"}
    )
    before = store.list_expenses(include_voided=True)
    report = financial_reconciliation.reconciliation_report()
    assert report["mode"] == "report_only"
    assert store.list_expenses(include_voided=True) == before
