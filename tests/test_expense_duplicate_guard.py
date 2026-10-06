"""One payment must not become several expenses -- on save, on edit, or by re-attaching its receipt.

On 6 Oct 2026 one Rs 42,500 payment was saved as three expense rows: same bank
reference, same screenshot, same amount. The duplicate check compared a single
"strongest" identity per record. A stored expense inherits the payment
engine's id from its screenshot (identity `pay:...`); the same payment filed
again carries only its bank reference (identity `UTR...`); the two never
equalled, so the check never fired.

The check now compares every identifier the two records share: the payment id,
the bank reference, and the same screenshot filed for the same amount. These
tests reproduce that exact pattern, and then hold the same line for edits and
for a receipt attached to an expense after the fact.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from features import transaction_identity

UTR = "829368041653"
IMAGE = b"receipt-image-bytes"
OTHER_IMAGE = b"a-different-receipt"


def digest(data: bytes) -> str:
    return transaction_identity.screenshot_hash(data)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A scratch expense store and a fake payment engine, nothing else.

    `engine` is the engine's ledger as the duplicate scan reads it: screenshot
    digest -> the payment the engine assigned to it. A stored expense inherits
    its payment from there, exactly as in production.
    """
    from features import financial_reconciliation as fr
    from features import handler_expenses

    monkeypatch.setattr(handler_expenses, "_FILE", str(tmp_path / "handler_expenses.json"))
    monkeypatch.setattr(handler_expenses, "PROOFS_DIR", str(tmp_path / "proofs"))
    engine: dict[str, str] = {}
    monkeypatch.setattr(fr, "_payment_id_by_screenshot", lambda: dict(engine))
    monkeypatch.setattr(fr, "_ledger_transactions", lambda: [])
    monkeypatch.setattr(fr, "_company_expense_transactions", lambda: [])
    monkeypatch.setattr(fr, "_canonical", lambda name: str(name or "").strip().lower())
    return handler_expenses, engine


def save(world, *, amount=42500, date="2026-10-06", utr=UTR, image=IMAGE, payment="pay_1", reference="Thrilok"):
    """Save an expense the way the route does: the receipt's identity goes in
    with the record, then the proof is attached."""
    store, engine = world
    row = store.create_expense({
        "reference": reference, "amount": amount, "date": date, "category": "commission",
        "external_transaction_id": utr, "payer": "B Thriloknath",
        "payment_id": payment, "screenshot_hash": digest(image) if image else "",
    })
    if image:
        store.add_proof(row["id"], data=image, original_name="receipt.png", mime_type="image/png")
        if payment:
            engine[digest(image)] = payment
    return row


def rows(world):
    return world[0].list_expenses(include_voided=True)


class TestTheSecondIdenticalSave:
    def test_is_refused(self, world):
        save(world)
        with pytest.raises(transaction_identity.DuplicateTransactionError) as caught:
            save(world)
        assert "already recorded as a handler expense on 2026-10-06" in str(caught.value)
        assert len(rows(world)) == 1

    def test_and_so_is_the_third(self, world):
        save(world)
        for _ in range(2):
            with pytest.raises(transaction_identity.DuplicateTransactionError):
                save(world)
        assert len(rows(world)) == 1

    def test_the_refusal_says_what_matched_and_which_record(self, world):
        first = save(world)
        with pytest.raises(transaction_identity.DuplicateTransactionError) as caught:
            save(world)
        assert caught.value.existing["record_id"] == first["id"]
        assert caught.value.existing["matched_on"] in {"payment_id", "external_id"}
        assert "Reclassify the existing record" in str(caught.value)


class TestEachIdentifierAloneIsEnough:
    """The old check needed both records to rank the same identifier strongest."""

    def test_the_bank_reference_alone(self, world):
        save(world)
        with pytest.raises(transaction_identity.DuplicateTransactionError) as caught:
            save(world, payment="", image=OTHER_IMAGE)
        assert caught.value.existing["matched_on"] == "external_id"
        assert "same bank reference" in str(caught.value)

    def test_the_payment_id_alone(self, world):
        save(world)
        with pytest.raises(transaction_identity.DuplicateTransactionError) as caught:
            save(world, utr="", image=OTHER_IMAGE)
        assert caught.value.existing["matched_on"] == "payment_id"
        assert "same payment" in str(caught.value)

    def test_the_screenshot_alone_for_the_same_amount(self, world):
        save(world)
        with pytest.raises(transaction_identity.DuplicateTransactionError) as caught:
            save(world, utr="", payment="")
        assert caught.value.existing["matched_on"] == "screenshot_hash"
        assert "same screenshot and amount" in str(caught.value)

    def test_the_screenshot_for_a_different_amount_is_not_by_itself_a_duplicate(self, world):
        # An image alone proves nothing: it can be attached to the wrong record.
        save(world)
        row = save(world, amount=5000, utr="", payment="")
        assert row["amount"] == 5000
        assert len(rows(world)) == 2


class TestGenuineSecondPaymentsAreUntouched:
    def test_a_different_payment_of_the_same_amount_on_the_same_day(self, world):
        save(world)
        save(world, utr="900000000001", image=OTHER_IMAGE, payment="pay_2")
        assert len(rows(world)) == 2

    def test_the_same_amount_to_another_referrer(self, world):
        save(world)
        save(world, utr="900000000002", image=OTHER_IMAGE, payment="pay_3", reference="Venugopal")
        assert len(rows(world)) == 2

    def test_a_voided_duplicate_stops_blocking_and_the_record_stays(self, world):
        store, _ = world
        first = save(world)
        store.void_expense(first["id"], status="VOIDED_DUPLICATE", reason="entered twice")
        again = save(world)
        assert again["id"] != first["id"]
        assert len(rows(world)) == 2
        assert len(store.list_expenses()) == 1
        with pytest.raises(transaction_identity.DuplicateTransactionError):
            save(world)


class TestTheCheckDoesNotGoQuiet:
    def test_when_the_payment_engine_ledger_cannot_be_read(self, world, monkeypatch):
        """The scan skips a source it cannot read. A guard must not."""
        from features import financial_reconciliation as fr

        save(world)

        def unreadable():
            raise OSError("ledger unavailable")

        monkeypatch.setattr(fr, "_payment_id_by_screenshot", unreadable)
        with pytest.raises(transaction_identity.DuplicateTransactionError):
            save(world, payment="", image=OTHER_IMAGE)
        assert len(rows(world)) == 1


class TestEdits:
    def test_an_edit_cannot_make_one_expense_a_copy_of_another(self, world):
        store, _ = world
        save(world)
        other = save(world, amount=3000, utr="900000000003", image=OTHER_IMAGE, payment="pay_9")
        with pytest.raises(transaction_identity.DuplicateTransactionError) as caught:
            store.update_expense(other["id"], {"external_transaction_id": UTR})
        assert caught.value.existing["matched_on"] == "external_id"
        unchanged = next(r for r in rows(world) if r["id"] == other["id"])
        assert unchanged["external_transaction_id"] == "900000000003"

    def test_nor_by_changing_the_amount_and_day_to_match_a_payment_with_no_reference(self, world):
        store, _ = world
        first = save(world, utr="", payment="", image=None, amount=7000, date="2026-10-01")
        second = save(world, utr="", payment="", image=None, amount=7000, date="2026-10-02")
        with pytest.raises(transaction_identity.DuplicateTransactionError):
            store.update_expense(second["id"], {"date": "2026-10-01"})
        assert next(r for r in rows(world) if r["id"] == second["id"])["date"] == "2026-10-02"
        assert first["id"] != second["id"]

    def test_an_ordinary_edit_goes_through(self, world):
        store, _ = world
        row = save(world)
        updated = store.update_expense(row["id"], {"note": "paid by UPI"})
        assert updated["note"] == "paid by UPI"
        assert len(rows(world)) == 1

    def test_editing_a_row_is_never_blocked_by_itself(self, world):
        store, _ = world
        row = save(world)
        updated = store.update_expense(row["id"], {"amount": 40000, "date": "2026-10-07"})
        assert (updated["amount"], updated["date"]) == (40000, "2026-10-07")

    def test_a_pair_that_was_already_a_duplicate_can_still_be_edited(self, world):
        """The edit did not create that duplicate, so it must not trap the row
        -- the operator still has to be able to correct or void it."""
        store, engine = world
        first = save(world)
        # The two later rows as production holds them: written before the guard.
        legacy = [
            {**{k: v for k, v in first.items() if k not in {"id", "created_at"}}, "id": f"legacy-{n}",
             "created_at": f"2026-10-06T12:{18 + n}:00+00:00"}
            for n in (0, 2)
        ]
        data = json.loads(open(store._FILE, encoding="utf-8").read())
        data["expenses"].extend(legacy)
        open(store._FILE, "w", encoding="utf-8").write(json.dumps(data))
        assert len(rows(world)) == 3
        edited = store.update_expense("legacy-0", {"note": "duplicate of the first, to be voided"})
        assert edited["note"].startswith("duplicate")
        # ...and voiding it works as before.
        assert store.void_expense("legacy-0", status="VOIDED_DUPLICATE", reason="same payment")["void_status"]

    def test_an_old_duplicate_cannot_hide_a_new_one(self, world):
        """B already duplicates A (it shares A's receipt). Editing B so it also
        copies C is a new duplicate, and the old one must not excuse it."""
        store, _ = world
        first = save(world)
        unrelated = save(world, amount=900, utr="900000000008", image=OTHER_IMAGE, payment="pay_8")
        data = json.loads(open(store._FILE, encoding="utf-8").read())
        data["expenses"].append({**{k: v for k, v in first.items() if k not in {"id", "created_at"}},
                                 "id": "legacy-b", "created_at": "2026-10-06T12:18:00+00:00"})
        open(store._FILE, "w", encoding="utf-8").write(json.dumps(data))
        with pytest.raises(transaction_identity.DuplicateTransactionError) as caught:
            store.update_expense("legacy-b", {"external_transaction_id": "900000000008", "amount": 900})
        assert caught.value.existing["record_id"] == unrelated["id"]

    def test_a_voided_row_is_not_checked(self, world):
        store, _ = world
        first = save(world)
        store.void_expense(first["id"], status="VOIDED_DUPLICATE", reason="entered twice")
        updated = store.update_expense(first["id"], {"external_transaction_id": UTR, "note": "audit note"})
        assert updated["void_status"] == "VOIDED_DUPLICATE"


class TestAReceiptAttachedLater:
    def test_cannot_be_another_expenses_receipt(self, world):
        store, _ = world
        save(world)
        second = save(world, utr="900000000004", image=None, payment="", amount=42500)
        with pytest.raises(transaction_identity.DuplicateTransactionError) as caught:
            store.refuse_duplicate_proof(second["id"], screenshot_hash=digest(IMAGE))
        assert caught.value.existing["matched_on"] == "screenshot_hash"

    def test_nor_by_carrying_the_same_payment_id_or_bank_reference(self, world):
        store, _ = world
        save(world)
        second = save(world, utr="900000000005", image=None, payment="", amount=1000)
        with pytest.raises(transaction_identity.DuplicateTransactionError):
            store.refuse_duplicate_proof(second["id"], screenshot_hash=digest(OTHER_IMAGE), payment_id="pay_1")
        with pytest.raises(transaction_identity.DuplicateTransactionError):
            store.refuse_duplicate_proof(second["id"], screenshot_hash=digest(OTHER_IMAGE), reference=UTR)

    def test_a_different_receipt_is_accepted(self, world):
        store, _ = world
        save(world)
        second = save(world, utr="900000000006", image=None, payment="", amount=42500)
        store.refuse_duplicate_proof(second["id"], screenshot_hash=digest(OTHER_IMAGE), reference="900000000006")

    def test_the_expenses_own_receipt_can_be_attached_again(self, world):
        store, _ = world
        first = save(world)
        store.refuse_duplicate_proof(first["id"], screenshot_hash=digest(IMAGE), payment_id="pay_1", reference=UTR)


# ── the real routes ──────────────────────────────────────────────────────────

@pytest.fixture()
def client(world, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import expenses as routes

    store, engine = world
    monkeypatch.setattr("features.referrer_registry.resolve_referrer",
                        lambda name: {"id": "referrer-thrilok", "name": "Thrilok"})

    def read_receipt(image_data, mime_type="image/jpeg", **kwargs):
        # The engine gives one receipt one payment, and remembers it.
        payment = engine.setdefault(digest(image_data), "pay_1" if digest(image_data) == digest(IMAGE) else "pay_other")
        return {"deterministic_verified": True, "deterministic_reasons": [], "payment_id": payment,
                "utr_number": UTR if image_data == IMAGE else "900000000007",
                "sender_name": "B Thriloknath"}

    monkeypatch.setattr("features.payment_verification_engine.verify_payment_screenshot", read_receipt)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes._require_fleet_admin] = lambda: None
    return TestClient(app)


def post_expense(client, image=IMAGE, amount="42500", date="2026-10-06"):
    return client.post(
        "/handler-expenses",
        data={"reference": "Thrilok", "amount": amount, "category": "commission", "date": date},
        files={"file": ("receipt.png", image, "image/png")},
    ).json()


class TestTheRoutes:
    def test_the_second_identical_save_is_rejected(self, client, world):
        first = post_expense(client)
        assert first["status"] == "ok"
        second = post_expense(client)
        assert second["status"] == "error"
        assert "already recorded as a handler expense" in second["message"]
        assert second["duplicate_of"]["record_id"] == first["expense"]["id"]
        assert second["duplicate_of"]["amount"] == 42500
        assert second["duplicate_of"]["matched_on"] in {"payment_id", "external_id"}
        assert len(rows(world)) == 1

    def test_a_third_is_rejected_too(self, client, world):
        post_expense(client)
        assert [post_expense(client)["status"] for _ in range(2)] == ["error", "error"]
        assert len(rows(world)) == 1

    def test_a_different_receipt_for_the_same_amount_is_saved(self, client, world):
        post_expense(client)
        assert post_expense(client, image=OTHER_IMAGE)["status"] == "ok"
        assert len(rows(world)) == 2

    def test_an_edit_that_would_duplicate_is_rejected_with_the_same_answer(self, client, world):
        first = post_expense(client)["expense"]
        other = post_expense(client, image=OTHER_IMAGE, amount="3000")["expense"]
        response = client.patch(f"/handler-expenses/{other['id']}", json={"external_transaction_id": UTR}).json()
        assert response["status"] == "error"
        assert response["duplicate_of"]["record_id"] == first["id"]
        assert client.patch(f"/handler-expenses/{other['id']}", json={"note": "fine"}).json()["status"] == "ok"

    def test_a_receipt_re_attached_to_another_expense_is_rejected(self, client, world):
        post_expense(client)
        second = post_expense(client, image=OTHER_IMAGE)["expense"]
        # The operator "replaces" the second expense's screenshot with the first one's.
        response = client.post(
            f"/handler-expenses/{second['id']}/proofs",
            files={"file": ("receipt.png", IMAGE, "image/png")},
        ).json()
        assert response["status"] == "error"
        assert "already recorded as a handler expense" in response["message"]
        assert response["duplicate_of"]["matched_on"] in {"payment_id", "external_id", "screenshot_hash"}
        stored = next(r for r in rows(world) if r["id"] == second["id"])
        assert [p["sha256"] for p in stored["proofs"]] == [digest(OTHER_IMAGE)]


def test_the_reconciliation_report_is_still_read_only(world):
    from features import financial_reconciliation

    save(world)
    before = rows(world)
    report = financial_reconciliation.reconciliation_report()
    assert report["mode"] == "report_only"
    assert rows(world) == before
