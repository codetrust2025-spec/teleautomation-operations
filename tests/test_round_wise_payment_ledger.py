from pathlib import Path

from features import candidate_store as cs


def _round_row(
    row_id: str,
    *,
    payment: int,
    slot_confirmed: bool = False,
) -> dict:
    return {
        "id": row_id,
        "name": "Krishna",
        "service_type": "round_wise",
        "stage": "in_progress",
        "expected_payment": 5000,
        "payment": payment,
        "slot_confirmed": slot_confirmed,
        "date": "2026-07-20" if slot_confirmed else "",
        "time": "10:00 AM" if slot_confirmed else "",
        "created_at": "2026-07-27T10:00:00+05:30",
    }


def test_next_round_due_does_not_reuse_historical_paid_round(monkeypatch):
    old_paid_round = _round_row("old-paid", payment=5000, slot_confirmed=True)
    monkeypatch.setattr(cs, "list_candidates", lambda **_kwargs: [old_paid_round])

    assert cs.round_wise_payment_due_for_name("Krishna") == 5000


def test_pending_unbooked_round_is_reused_for_payment(monkeypatch):
    pending_round = _round_row("pending", payment=0)
    monkeypatch.setattr(cs, "list_candidates", lambda **_kwargs: [pending_round])

    assert cs._round_wise_pending_payment_row("Krishna")["id"] == "pending"
    assert cs.round_wise_payment_due_for_name("Krishna") == 5000


def test_first_time_round_wise_payment_creates_stable_owner(monkeypatch):
    monkeypatch.setattr(cs, "_round_wise_pending_payment_row", lambda _name: None)
    monkeypatch.setattr(cs, "_round_wise_identity_source", lambda *_a, **_k: None)
    created = {}

    def fake_create(record, *, allow_slot_without_rules=False):
        created.update(record)
        created["id"] = "new-round-owner"
        created["allow_slot_without_rules"] = allow_slot_without_rules
        return dict(created)

    monkeypatch.setattr(cs, "create_candidate", fake_create)

    row = cs.ensure_round_wise_payment_row(
        "Pavan Ravi",
        phone="9876543210",
        technology="ETL",
        interview_round="L1",
    )

    assert row["id"] == "new-round-owner"
    assert created["phone"] == "9876543210"
    assert created["technology"] == "ETL"
    assert created["interview_round"] == "L1"
    assert created["service_type"] == "round_wise"
    assert created.get("reference", "") == ""
    assert created["allow_slot_without_rules"] is True


def test_round_wise_payment_reuses_and_refreshes_pending_owner(monkeypatch):
    pending = {
        **_round_row("pending", payment=0),
        "phone": "",
        "technology": "Unspecified",
        "interview_round": "",
    }
    monkeypatch.setattr(cs, "_round_wise_pending_payment_row", lambda _name: pending)
    monkeypatch.setattr(cs, "_round_wise_identity_source", lambda *_a, **_k: None)
    updated = {}

    def fake_update(cid, patch, *, allow_slot_without_rules=False):
        updated.update(
            cid=cid,
            patch=patch,
            allow_slot_without_rules=allow_slot_without_rules,
        )
        return {**pending, **patch}

    monkeypatch.setattr(cs, "update_candidate", fake_update)

    row = cs.ensure_round_wise_payment_row(
        "Krishna",
        phone="9876543210",
        technology="ETL",
        interview_round="L1",
    )

    assert row["id"] == "pending"
    assert updated["cid"] == "pending"
    assert updated["patch"] == {
        "phone": "9876543210",
        "technology": "ETL",
        "interview_round": "L1",
    }
    assert updated["allow_slot_without_rules"] is True


def test_round_wise_payment_owner_requires_identity_fields(monkeypatch):
    monkeypatch.setattr(cs, "_round_wise_pending_payment_row", lambda _name: None)
    monkeypatch.setattr(cs, "_round_wise_identity_source", lambda *_a, **_k: None)

    for field, kwargs in [
        ("phone", {"phone": "", "technology": "ETL", "interview_round": "L1"}),
        ("technology", {"phone": "9876543210", "technology": "", "interview_round": "L1"}),
        ("round", {"phone": "9876543210", "technology": "ETL", "interview_round": ""}),
    ]:
        try:
            cs.ensure_round_wise_payment_row("Krishna", **kwargs)
        except ValueError as exc:
            assert field in str(exc).lower() or (
                field == "round" and "interview round" in str(exc).lower()
            )
        else:
            raise AssertionError(f"{field} should be required")


def test_legacy_round_payment_upload_can_create_provisional_owner(monkeypatch):
    monkeypatch.setattr(cs, "_round_wise_pending_payment_row", lambda _name: None)
    monkeypatch.setattr(cs, "_round_wise_identity_source", lambda *_a, **_k: None)
    created = {}

    def fake_create(record, *, allow_slot_without_rules=False):
        created.update(record)
        return {**record, "id": "legacy-owner"}

    monkeypatch.setattr(cs, "create_candidate", fake_create)

    row = cs.ensure_round_wise_payment_row(
        "Pavan Ravi",
        phone="",
        technology="",
        interview_round="",
        allow_incomplete=True,
    )

    assert row["id"] == "legacy-owner"
    assert created["phone"] == ""
    assert created["technology"] == "Unspecified"
    assert created["interview_round"] == ""
    assert created["slot_confirmed"] is False


def test_legacy_round_payment_inherits_existing_candidate_identity(monkeypatch):
    pending = {
        **_round_row("provisional", payment=0),
        "phone": "",
        "technology": "Unspecified",
        "reference": "",
    }
    identity = {
        **_round_row("paid-old-round", payment=5000, slot_confirmed=True),
        "phone": "9000000105",
        "technology": "ETL",
        "reference": "Referrer One",
    }
    monkeypatch.setattr(cs, "_round_wise_pending_payment_row", lambda _name: pending)
    monkeypatch.setattr(cs, "_round_wise_identity_source", lambda *_a, **_k: identity)
    updated = {}

    def fake_update(cid, patch, *, allow_slot_without_rules=False):
        updated.update(cid=cid, patch=patch)
        return {**pending, **patch}

    monkeypatch.setattr(cs, "update_candidate", fake_update)

    row = cs.ensure_round_wise_payment_row(
        "Sri Ram",
        phone="",
        technology="",
        interview_round="",
        allow_incomplete=True,
    )

    assert row["id"] == "provisional"
    assert updated["patch"] == {
        "phone": "9000000105",
        "technology": "ETL",
        "reference": "Referrer One",
    }


def test_booking_finds_proof_on_round_ledger_instead_of_profile(monkeypatch):
    profile = {
        "id": "profile",
        "name": "Krishna",
        "service_type": "profile_service",
    }
    paid_round = _round_row("paid-round", payment=5000)
    proof = {
        "id": "proof-1",
        "company_payment_verified": True,
        "uploaded_at": "2099-01-01T00:00:00+05:30",
    }
    monkeypatch.setattr(cs, "list_candidates", lambda **_kwargs: [profile, paid_round])
    monkeypatch.setattr(
        cs,
        "get_proof",
        lambda cid, proof_id: (Path("receipt.jpg"), proof)
        if cid == "paid-round" and proof_id == "proof-1"
        else None,
    )
    monkeypatch.setattr(cs, "merged_balance_due_for_name", lambda _name: 0)
    monkeypatch.setattr(cs, "_proof_uploaded_recently", lambda _entry: True)
    monkeypatch.setattr(
        "features.company_payment_verification.stored_proof_is_verified_company_payment",
        lambda _entry: True,
    )

    assert cs.slot_booking_payment_block_reason(
        "Krishna",
        payment_proof_id="proof-1",
        require_payment_proof=True,
    ) is None


def test_recent_same_round_duplicate_is_idempotent(monkeypatch):
    paid_round = _round_row("paid-round", payment=5000)
    proof = {
        "id": "proof-1",
        "company_payment_verified": True,
        "uploaded_at": "2099-01-01T00:00:00+05:30",
    }
    monkeypatch.setattr(
        cs,
        "_load",
        lambda **_kwargs: {"candidates": [{**paid_round, "proofs": [proof]}]},
    )
    monkeypatch.setattr(
        cs,
        "get_proof",
        lambda cid, proof_id: (Path("receipt.jpg"), proof)
        if cid == "paid-round" and proof_id == "proof-1"
        else None,
    )
    monkeypatch.setattr(cs, "_proof_uploaded_recently", lambda _entry: True)
    monkeypatch.setattr(
        "features.company_payment_verification.stored_proof_is_verified_company_payment",
        lambda _entry: True,
    )

    reused = cs._idempotent_round_wise_duplicate(
        "Krishna",
        {
            "duplicate_matches": [
                {
                    "candidate_id": "paid-round",
                    "candidate_name": "Krishna",
                    "proof_id": "proof-1",
                    "match": "image",
                }
            ]
        },
    )

    assert reused["candidate_id"] == "paid-round"
    assert reused["proof"]["id"] == "proof-1"


def test_booked_round_duplicate_cannot_be_reused(monkeypatch):
    booked_round = _round_row("booked", payment=5000, slot_confirmed=True)
    monkeypatch.setattr(
        cs,
        "_load",
        lambda **_kwargs: {"candidates": [booked_round]},
    )

    assert cs._idempotent_round_wise_duplicate(
        "Krishna",
        {
            "duplicate_matches": [
                {"candidate_id": "booked", "proof_id": "old-proof", "match": "image"}
            ]
        },
    ) is None


def test_round_booking_assigns_exact_row_that_owns_receipt(monkeypatch):
    paid_round = _round_row("paid-round", payment=5000)
    monkeypatch.setattr(cs, "slot_booking_payment_block_reason", lambda *_a, **_k: None)
    monkeypatch.setattr(cs, "list_candidates", lambda **_kwargs: [paid_round])
    monkeypatch.setattr(
        cs,
        "_payment_proof_owner_for_slot_name",
        lambda *_a, **_k: (paid_round, (Path("receipt.jpg"), {"id": "proof-1"})),
    )
    monkeypatch.setattr(cs, "_find_existing_slot_row", lambda *_a, **_k: None)
    monkeypatch.setattr(cs, "_resolve_public_slot_conflicts", lambda **_kwargs: None)

    assigned: dict = {}

    def fake_assign_interview_slot(*, candidate_id, **kwargs):
        assigned["candidate_id"] = candidate_id
        return {**paid_round, **kwargs, "slot_confirmed": True}

    monkeypatch.setattr(cs, "assign_interview_slot", fake_assign_interview_slot)
    monkeypatch.setattr(
        cs,
        "_finish_public_slot_import",
        lambda row, action, **_kwargs: (row, action),
    )

    _row, action = cs.import_confirmed_interview_slot(
        name="Krishna",
        date="2026-07-27",
        time="12:00 PM",
        time_end="12:25 PM",
        interview_round="L1",
        service_type="round_wise",
        payment_proof_id="proof-1",
    )

    assert assigned["candidate_id"] == "paid-round"
    assert action == "assigned_round_payment"
