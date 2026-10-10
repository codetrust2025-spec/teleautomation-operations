"""A payment is spent by a booking that happened, and by nothing else.

Attaching a proof to a candidate row is what consumes it: the fraud check scans
the payment evidence on candidate rows, so a receipt that reaches a row is
spent from then on. Two paths spent one before the booking existed.

  1. The slot-persisted check ran *after* the payment was linked and *outside*
     the try, so a slot that failed to persist returned a 500 with the receipt
     already attached and no rollback of any kind.
  2. The rollback only deleted candidates created during the attempt. A slot
     assigned to a candidate already on file left that row -- and the evidence
     attached to it -- in place.

Either way the next attempt was refused as "already linked to an active or
completed booking" for a booking that never happened, and the only receipt that
could pay for it was the one now permanently spent.

These drive the real endpoints. `test_the_guard_is_what_keeps_it_reusable` is
the negative control: it disables the ordering guard and asserts the payment is
consumed, so the tests above it are known to be reading the guard rather than
passing for some unrelated reason.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.public_slot_api import install_public_slot_routes
from features import candidate_store as cs
from features import pending_slot_payment as pending


def _verified_payment(utr: str = "123456789012", amount: int = 5000) -> dict:
    return {
        "verification_engine": "central_payment_verification_v2",
        "booking_eligible": True,
        "company_payment_verified": True,
        "verification_state": "VERIFIED_COMPANY_PAYMENT",
        "is_payment_screenshot": True,
        "status": "success",
        "amount": amount,
        "amount_sufficient": True,
        "confidence_score": 99,
        "utr_number": utr,
        "transaction_id": utr,
        "receiver_type": "company",
        "receiver_upi_id": "company@ybl",
        "deterministic_reasons": [],
    }


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "candidate-proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr(pending, "PENDING_PAYMENT_DIR", str(tmp_path / "pending"))
    monkeypatch.setattr(
        pending, "PENDING_PAYMENT_INDEX", str(tmp_path / "pending" / "index.json")
    )
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)
    monkeypatch.setattr(
        "features.payment_verification_engine.verify_payment_screenshot",
        lambda *_a, **_k: _verified_payment(),
    )
    monkeypatch.setattr(
        "features.ollama_payment_extract.generate_payment_narrative",
        lambda *_a, **_k: "Verified payment",
    )
    monkeypatch.setattr(
        "features.payment_proof_validator.validate_interview_invite",
        lambda *_a, **_k: (True, ""),
    )
    app = FastAPI()
    install_public_slot_routes(app)
    return TestClient(app)


def upload(client: TestClient, *, name: str = "Raju", body: bytes = b"receipt-a") -> str:
    response = client.post(
        "/public/slots/payment-proof",
        data={"name": name, "service_type": "round_wise", "phone": "9876543210",
              "technology": "ETL", "interview_round": "L1"},
        files={"file": ("payment.jpg", body, "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    return response.json()["proof_id"]


def booking(proof_id: str, **overrides) -> dict:
    data = {
        "name": "Raju", "service_type": "round_wise", "phone": "9876543210",
        "technology": "ETL", "interview_round": "L1", "company": "Capgemini", "date": "2026-08-01",
        "time": "02:00 PM", "time_end": "03:00 PM", "payment_proof_id": proof_id,
        "idempotency_key": "raju-2026-08-01-1400",
    }
    data.update(overrides)
    return data


def confirm(client: TestClient, proof_id: str, **overrides):
    return client.post(
        "/bookings/confirm",
        data=booking(proof_id, **overrides),
        files={"file": ("invite.jpg", b"invite", "image/jpeg")},
    )


def is_utilized(proof_id: str) -> bool:
    entry = (pending._load().get("proofs") or {}).get(proof_id) or {}
    return bool(entry.get("utilized_at"))


def rows_holding(proof_id: str) -> list[dict]:
    """Every candidate row carrying an attachment made from this pending proof."""
    held = []
    for row in cs.list_candidates(stage="all", month="all"):
        for proof in cs.list_attachments(str(row.get("id") or ""), "payment_proof") or []:
            if str(proof.get("pending_proof_id") or "") == proof_id:
                held.append(row)
    return held


def unpersisted_booking(monkeypatch) -> dict:
    """Make the slot fail to reach the row, the way production can.

    Returns a switch so a test can stop the failure and retry for real.
    `monkeypatch.undo()` cannot be used for that: it would also revert the
    fixture's store and pending-directory patches, and the retry would then
    look for the proof in the real data directory and not find it.
    """
    real = cs.import_confirmed_interview_slot
    switch = {"broken": True}

    def broken(**kwargs):
        row, action = real(**kwargs)
        if not switch["broken"]:
            return row, action
        # The row exists, the slot did not stick. This is the shape of the
        # failure the confirm handler must not pay for.
        cs.update_candidate(
            str(row["id"]),
            {"date": "", "time": "", "slot_confirmed": False},
            allow_slot_without_rules=True,
        )
        return cs.get_candidate(str(row["id"])), action

    monkeypatch.setattr(cs, "import_confirmed_interview_slot", broken)
    return switch


class TestNothingBeforeTheBookingConsumesIt:
    def test_uploading_does_not_utilize_it(self, client):
        proof_id = upload(client)
        assert is_utilized(proof_id) is False
        assert rows_holding(proof_id) == []

    def test_uploading_creates_no_candidate_at_all(self, client):
        upload(client)
        assert cs.list_candidates(stage="all", month="all") == []

    def test_a_verified_upload_is_still_only_verified(self, client):
        """Validation succeeding is not the same event as money moving."""
        proof_id = upload(client)
        entry = (pending._load().get("proofs") or {})[proof_id]
        assert entry["verification"]["booking_eligible"] is True
        assert entry.get("utilized_at") in (None, "")

    def test_abandoning_the_form_leaves_it_reusable(self, client):
        """No confirm is ever sent; the proof must still book later."""
        proof_id = upload(client)
        assert is_utilized(proof_id) is False
        assert confirm(client, proof_id).status_code == 200

    def test_a_refused_booking_does_not_utilize_it(self, client):
        # No interview round: refused by validation, before anything is booked.
        proof_id = upload(client)
        refused = confirm(client, proof_id, interview_round="")
        assert refused.status_code == 400
        assert is_utilized(proof_id) is False
        assert rows_holding(proof_id) == []

    def test_a_refused_booking_can_be_retried_with_the_same_payment(self, client):
        proof_id = upload(client)
        assert confirm(client, proof_id, interview_round="").status_code == 400
        retry = confirm(client, proof_id)
        assert retry.status_code == 200, retry.text
        assert retry.json()["candidate"]["date"] == "2026-08-01"


class TestASlotThatDidNotPersistCostsNothing:
    """The proven early-consumption path."""

    def test_the_booking_is_reported_as_failed(self, client, monkeypatch):
        proof_id = upload(client)
        unpersisted_booking(monkeypatch)
        response = confirm(client, proof_id)
        assert response.status_code == 500
        assert "did not complete" in response.json()["message"]

    def test_the_payment_is_not_utilized(self, client, monkeypatch):
        proof_id = upload(client)
        unpersisted_booking(monkeypatch)
        confirm(client, proof_id)
        assert is_utilized(proof_id) is False

    def test_no_row_keeps_a_claim_on_the_receipt(self, client, monkeypatch):
        proof_id = upload(client)
        unpersisted_booking(monkeypatch)
        confirm(client, proof_id)
        assert rows_holding(proof_id) == []

    def test_the_same_receipt_still_books(self, client, monkeypatch):
        """The whole point: the payer paid, so the payer can book."""
        proof_id = upload(client)
        switch = unpersisted_booking(monkeypatch)
        assert confirm(client, proof_id).status_code == 500
        switch["broken"] = False
        retry = confirm(client, proof_id)
        assert retry.status_code == 200, retry.text
        assert is_utilized(proof_id) is True

    def test_the_guard_is_what_keeps_it_reusable(self, client, monkeypatch):
        """Negative control.

        Disable the ordering guard -- let an unpersisted row read as confirmed
        -- and the payment is spent on a booking that did not happen, which is
        exactly the behaviour the tests above exist to prevent. If this ever
        fails while the tests above pass, they are no longer reading the guard.
        """
        proof_id = upload(client)
        unpersisted_booking(monkeypatch)
        monkeypatch.setattr(cs, "candidate_has_confirmed_slot", lambda _row: True)
        confirm(client, proof_id)
        assert is_utilized(proof_id) is True
        assert rows_holding(proof_id) != []


class TestAPreExistingCandidateIsRolledBackToo:
    def test_evidence_does_not_stay_on_a_candidate_already_on_file(
        self, client, monkeypatch
    ):
        """Deleting rows created during the attempt cannot help here.

        The slot goes to someone already on file, so there is no new row to
        delete; only detaching the evidence puts the payment back.
        """
        cs.create_candidate({"name": "Raju", "phone": "9876543210",
                             "technology": "ETL", "interview_round": "L1"})
        assert cs.list_candidates(stage="all", month="all")

        proof_id = upload(client)
        unpersisted_booking(monkeypatch)
        confirm(client, proof_id)

        assert rows_holding(proof_id) == []
        assert is_utilized(proof_id) is False

    def test_an_earlier_payment_on_that_row_is_not_disturbed(self, client, monkeypatch):
        """Rolling back this attempt must not touch money already recorded.

        The recorded amount is derived from the attached evidence, and both
        `recalculate_received_total` and the `update_candidate` floor refuse to
        reduce it on a row that was never under proof control -- a payment made
        before proofs were captured is real money and deleting it would be
        worse than the bug being fixed. So the rollback has to put the row back
        exactly where it was, not recompute it to zero.
        """
        created = cs.create_candidate({"name": "Raju", "phone": "9876543210",
                                       "technology": "ETL", "interview_round": "L1"})
        row_id = str(created["id"])
        cs.update_candidate(row_id, {"payment": 3000}, allow_slot_without_rules=True)
        before = int(cs.get_candidate(row_id)["payment"])
        assert before == 3000

        proof_id = upload(client)
        unpersisted_booking(monkeypatch)
        confirm(client, proof_id)

        assert rows_holding(proof_id) == []
        assert is_utilized(proof_id) is False
        surviving = cs.get_candidate(row_id)
        if surviving:
            assert int(surviving["payment"]) == before


class TestOnlyASuccessfulBookingSpendsIt:
    def test_a_successful_booking_utilizes_it_once(self, client):
        proof_id = upload(client)
        assert confirm(client, proof_id).status_code == 200
        assert is_utilized(proof_id) is True
        assert len(rows_holding(proof_id)) == 1

    def test_the_slot_is_really_on_the_row(self, client):
        proof_id = upload(client)
        row = confirm(client, proof_id).json()["candidate"]
        assert cs.candidate_has_confirmed_slot(cs.get_candidate(str(row["id"])))
        assert row["payment"] == 5000

    def test_reusing_it_after_success_is_blocked(self, client):
        proof_id = upload(client)
        assert confirm(client, proof_id).status_code == 200
        again = confirm(
            client, proof_id, date="2026-08-02", idempotency_key="raju-second"
        )
        assert again.status_code == 400
        assert "already linked" in again.json()["message"]

    def test_a_second_booking_is_not_created_by_the_blocked_retry(self, client):
        proof_id = upload(client)
        confirm(client, proof_id)
        confirm(client, proof_id, date="2026-08-02", idempotency_key="raju-second")
        booked = [
            r for r in cs.list_candidates(stage="all", month="all")
            if cs.candidate_has_confirmed_slot(r)
        ]
        assert len(booked) == 1

    def test_retrying_the_same_booking_does_not_double_consume(self, client):
        """A double-click sends the identical request twice."""
        proof_id = upload(client)
        first = confirm(client, proof_id)
        second = confirm(client, proof_id)
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["action"] == "skip_exists"
        assert len(rows_holding(proof_id)) == 1
        assert first.json()["candidate"]["id"] == second.json()["candidate"]["id"]

    def test_the_recorded_payment_is_not_doubled_by_a_retry(self, client):
        proof_id = upload(client)
        confirm(client, proof_id)
        row = confirm(client, proof_id).json()["candidate"]
        assert cs.get_candidate(str(row["id"]))["payment"] == 5000


class TestReuseAfterACancelledBookingIsStillAllowed:
    """Spending a payment is not the same as forfeiting it.

    A cancelled or unattended booking releases its payment back to the payer,
    and assess_payment_proof implements that. The utilisation marker records
    when a payment was spent; it must not become a second gate that refuses a
    rebooking this policy allows.
    """

    def test_the_marker_does_not_pre_empt_the_reuse_policy(self, client):
        proof_id = upload(client)
        assert confirm(client, proof_id).status_code == 200
        assert is_utilized(proof_id) is True

        booked = next(
            r for r in cs.list_candidates(stage="all", month="all")
            if cs.candidate_has_confirmed_slot(r)
        )
        cs.update_candidate(str(booked["id"]), {"stage": "cancelled"},
                            allow_slot_without_rules=True)

        # Whatever the fraud check decides, it must be the thing deciding --
        # not a refusal raised ahead of it because the marker was set.
        from features.pending_slot_payment import get_verified_proof, validate_for_confirmation

        resolved = get_verified_proof(
            proof_id, name="Raju", service_type="round_wise", phone="9876543210",
        )
        if resolved:
            try:
                validate_for_confirmation(resolved, phone="9876543210")
            except ValueError as exc:
                # Only ever from the fraud check's own reading of the row.
                assert "already linked" in str(exc)

    def test_the_marker_is_a_record_not_a_gate(self):
        """No refusal is raised from the marker itself.

        Read from the syntax tree so the comment explaining *why* the marker is
        not consulted here does not itself look like consulting it.
        """
        import ast
        import inspect
        import textwrap

        from features import pending_slot_payment

        tree = ast.parse(
            textwrap.dedent(
                inspect.getsource(pending_slot_payment.validate_for_confirmation)
            )
        )
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        constants = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert "utilized_by" not in names
        assert not any("utilized" in text for text in constants if len(text) < 200)


class TestSplitPaymentsStillWork:
    def test_two_instalments_book_one_slot_and_are_both_spent(
        self, client, monkeypatch
    ):
        utrs = iter(["111111111111", "222222222222"])
        monkeypatch.setattr(
            "features.payment_verification_engine.verify_payment_screenshot",
            lambda *_a, **_k: _verified_payment(utr=next(utrs), amount=2500),
        )
        first = upload(client, body=b"receipt-1")
        second = upload(client, body=b"receipt-2")
        assert first != second

        response = client.post(
            "/bookings/confirm",
            data=booking(first, payment_proof_ids=f"{first},{second}"),
            files={"file": ("invite.jpg", b"invite", "image/jpeg")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["candidate"]["payment"] == 5000
        assert is_utilized(first) is True
        assert is_utilized(second) is True

    def test_a_failed_split_booking_spends_neither(self, client, monkeypatch):
        utrs = iter(["111111111111", "222222222222"])
        monkeypatch.setattr(
            "features.payment_verification_engine.verify_payment_screenshot",
            lambda *_a, **_k: _verified_payment(utr=next(utrs), amount=2500),
        )
        first = upload(client, body=b"receipt-1")
        second = upload(client, body=b"receipt-2")
        unpersisted_booking(monkeypatch)

        client.post(
            "/bookings/confirm",
            data=booking(first, payment_proof_ids=f"{first},{second}"),
            files={"file": ("invite.jpg", b"invite", "image/jpeg")},
        )
        assert is_utilized(first) is False
        assert is_utilized(second) is False
        assert rows_holding(first) == []
        assert rows_holding(second) == []


class TestTheOrderingIsStructural:
    """The behaviour above depends on one ordering. Pin it in the source too,
    so a reorder is caught even if a future refactor changes the failure mode.
    """

    def test_the_slot_is_checked_before_the_payment_is_linked(self):
        import inspect

        from core import public_slot_api

        source = inspect.getsource(public_slot_api.install_public_slot_routes)
        guard = source.index("raise BookingNotPersisted(action)")
        linkage = source.index("cs.finalize_public_booking_payment(")
        assert guard < linkage

    def test_the_utilisation_marker_comes_after_the_success_check(self):
        import inspect

        from core import public_slot_api

        source = inspect.getsource(public_slot_api.install_public_slot_routes)
        assert source.index("phase=confirm_unpersisted") < source.index("mark_utilized(")

    def test_every_failure_path_rolls_the_attempt_back(self):
        """Every handler around the booking must unwind, not merely return.

        Read from the syntax tree rather than by matching text: a handler that
        returns an error without rolling back is the exact shape of the
        original bug, and it should be caught however the file is formatted.
        """
        import ast
        import inspect
        import textwrap

        from core import public_slot_api

        tree = ast.parse(
            textwrap.dedent(inspect.getsource(public_slot_api.install_public_slot_routes))
        )
        booking_try = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Try)
            and "import_confirmed_interview_slot"
            in ast.dump(ast.Module(body=node.body, type_ignores=[]))
        )
        assert booking_try.handlers, "the booking try/except lost its handlers"
        for handler in booking_try.handlers:
            called = {
                child.func.id
                for child in ast.walk(ast.Module(body=handler.body, type_ignores=[]))
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
            }
            assert "rollback_attempt" in called, (
                f"handler for {ast.unparse(handler.type)} returns without rolling back"
            )
