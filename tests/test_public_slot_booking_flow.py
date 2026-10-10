import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.public_slot_api import install_public_slot_routes
from features import candidate_store as cs
from features import pending_slot_payment as pending


def _api_booking_key(booking: dict) -> str:
    """The key /bookings/confirm derives, mirrored so a test can plant it."""
    import hashlib

    service_type = booking.get("service_type") or "round_wise"
    phone = booking.get("phone", "") if service_type == "round_wise" else ""
    return hashlib.sha256(
        "|".join([
            booking.get("idempotency_key", "").strip(),
            booking["name"].strip().lower(),
            service_type,
            phone,
            booking.get("date", ""),
            booking.get("time", ""),
            booking.get("time_end", ""),
            cs.normalise_interview_round(booking.get("interview_round", "")).lower(),
            booking.get("payment_proof_id", ""),
        ]).encode("utf-8")
    ).hexdigest()


def _verified_payment() -> dict:
    return {
        "verification_engine": "central_payment_verification_v2",
        "booking_eligible": True,
        "company_payment_verified": True,
        "verification_state": "VERIFIED_COMPANY_PAYMENT",
        "is_payment_screenshot": True,
        "status": "success",
        "amount": 5000,
        "amount_sufficient": True,
        "confidence_score": 99,
        "utr_number": "123456789012",
        "receiver_type": "company",
        "receiver_upi_id": "company@ybl",
        "deterministic_reasons": [],
    }


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "candidate-proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr(pending, "PENDING_PAYMENT_DIR", str(tmp_path / "pending"))
    monkeypatch.setattr(pending, "PENDING_PAYMENT_INDEX", str(tmp_path / "pending" / "index.json"))
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)
    monkeypatch.setattr("features.payment_verification_engine.verify_payment_screenshot", lambda *_args, **_kwargs: _verified_payment())
    monkeypatch.setattr("features.ollama_payment_extract.generate_payment_narrative", lambda *_args, **_kwargs: "Verified payment")
    monkeypatch.setattr("features.payment_proof_validator.validate_interview_invite", lambda *_args, **_kwargs: (True, ""))
    app = FastAPI()
    install_public_slot_routes(app)
    return TestClient(app)


def _upload(client: TestClient) -> str:
    response = client.post(
        "/public/slots/payment-proof",
        data={"name": "Raju", "service_type": "round_wise", "phone": "9876543210", "technology": "ETL", "interview_round": "L1"},
        files={"file": ("payment.jpg", b"verified-payment", "image/jpeg")},
    )
    assert response.status_code == 200
    return response.json()["proof_id"]


def _booking(proof_id: str) -> dict:
    return {
        "name": "Raju", "service_type": "round_wise", "phone": "9876543210",
        "technology": "ETL", "interview_round": "L1", "company": "Capgemini", "date": "2026-08-01",
        "time": "02:00 PM", "time_end": "03:00 PM", "payment_proof_id": proof_id,
        "idempotency_key": "raju-booking-2026-08-01-1400",
    }


def test_upload_creates_nothing_and_confirm_is_idempotent(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    proof_id = _upload(client)
    assert cs.list_candidates(stage="all", month="all") == []
    booking = _booking(proof_id)
    first = client.post("/bookings/confirm", data=booking, files={"file": ("invite.jpg", b"invite", "image/jpeg")})
    second = client.post("/bookings/confirm", data=booking, files={"file": ("invite.jpg", b"invite", "image/jpeg")})
    assert first.status_code == second.status_code == 200
    rows = cs.list_candidates(stage="all", month="all")
    assert len(rows) == 1
    assert rows[0]["payment"] == 5000
    assert len(rows[0]["payment_proofs"]) == 1


def _profile_booking(key: str = "abilash-2026-08-12-1600") -> dict:
    """A profile-service booking, which needs no payment proof."""
    return {
        "name": "Abilash Perla", "service_type": "profile_service",
        "interview_round": "L1", "company": "Capgemini", "date": "2026-08-12",
        "time": "16:00", "time_end": "17:00",
        "idempotency_key": key,
    }


def _confirm(client: TestClient, booking: dict):
    return client.post(
        "/bookings/confirm",
        data=booking,
        files={"file": ("invite.jpg", b"invite", "image/jpeg")},
    )


def test_stale_idempotency_key_without_a_slot_does_not_fake_success(monkeypatch, tmp_path):
    """A key left on an unbooked row must not replay as a confirmed booking.

    Production case: an earlier confirm stored the idempotency key on the
    candidate row but never applied the slot, leaving date empty and
    slot_confirmed false. Every later confirm for that exact slot then matched
    the key, returned 200 with that row, and the UI reported "Slot confirmed"
    while Confirmed slots stayed empty — and the slot could never be booked.
    """
    client = _client(monkeypatch, tmp_path)
    booking = _booking(_upload(client))

    # Reproduce the poisoned row: right candidate, the key, but no slot at all.
    # This is how production got there — the key is written, then the booking is
    # blocked, and rollback cannot remove a row that already existed.
    stale = cs.create_candidate({
        "name": "Raju", "phone": "9876543210",
        "service_type": "round_wise", "interview_round": "L1",
    })
    cs.update_candidate(
        str(stale["id"]),
        {"booking_idempotency_key": _api_booking_key(booking), "slot_confirmed": False},
        allow_slot_without_rules=True,
    )

    response = _confirm(client, booking)
    assert response.status_code == 200, response.text

    confirmed = [
        row for row in cs.list_candidates(stage="all", month="all")
        if cs.candidate_has_confirmed_slot(row)
    ]
    assert confirmed, "the slot must actually be booked, not replayed from the stale key"
    assert confirmed[0]["date"] == "2026-08-01"


def test_confirmed_booking_is_returned_by_the_confirmed_slots_api(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    # The confirmed-slots API serves a forward window, so this books ahead of
    # today rather than on the fixed past date the other cases use.
    from datetime import date as _date, timedelta as _timedelta

    ahead = (_date.today() + _timedelta(days=7)).isoformat()
    booking = _booking(_upload(client))
    booking.update({"date": ahead, "idempotency_key": f"raju-booking-{ahead}"})
    assert _confirm(client, booking).status_code == 200

    booked = client.get("/public/slots/booked")
    assert booked.status_code == 200
    payload = booked.json()
    rows = payload if isinstance(payload, list) else (
        payload.get("booked") or payload.get("slots") or payload.get("candidates") or []
    )
    assert any(
        str(row.get("date")) == ahead and "Raju" in str(row.get("name", ""))
        for row in rows
    ), f"confirmed booking missing from confirmed-slots API: {payload}"


def test_confirmed_slots_carry_their_booking_source(monkeypatch, tmp_path):
    """The page labels how a slot was booked, so the source must be exposed."""
    client = _client(monkeypatch, tmp_path)
    from datetime import date as _date, timedelta as _timedelta

    ahead = (_date.today() + _timedelta(days=5)).isoformat()
    booking = _booking(_upload(client))
    booking.update({"date": ahead, "idempotency_key": f"raju-source-{ahead}"})
    assert _confirm(client, booking).status_code == 200

    slots = client.get("/public/slots/booked").json()["slots"]
    mine = [s for s in slots if s["date"] == ahead and "Raju" in s["name"]]
    assert mine, f"booking missing from confirmed slots: {slots}"
    assert "interview_booking_source" in mine[0]
    assert mine[0]["interview_booking_source"] == "candidate_booked"


def _legacy_slot(name: str, ahead: str, notes: str = "") -> dict:
    return cs.create_candidate(
        {
            "name": name, "phone": "9000000123",
            "service_type": "profile_service", "interview_round": "L1",
            "date": ahead, "time": "10:00", "time_end": "10:30",
            "slot_confirmed": True, "notes": notes,
        },
        allow_slot_without_rules=True,
    )


def test_a_legacy_row_resolves_through_the_shared_source_resolver(monkeypatch, tmp_path):
    """Legacy rows go through the resolver Daily Ops uses, not a second guess.

    The resolver reads the persisted booking note, so an older AI-mail row is
    still recognised without a data migration, and the confirmed-slots page
    cannot disagree with the roster about the same booking.
    """
    client = _client(monkeypatch, tmp_path)
    from datetime import date as _date, timedelta as _timedelta

    ahead = (_date.today() + _timedelta(days=6)).isoformat()
    _legacy_slot("Legacy Person", ahead)
    _legacy_slot(
        "Legacy Ai Person",
        ahead,
        notes="Automatically booked from validated interview email (AI Mail Monitoring).",
    )

    slots = client.get("/public/slots/booked").json()["slots"]
    by_name = {s["name"]: s for s in slots if s["date"] == ahead}
    assert "Legacy Person" in by_name, f"legacy booking missing: {slots}"

    # Every returned slot carries the field, so the badge always has an input.
    for slot in slots:
        assert "interview_booking_source" in slot

    # The note is persisted evidence, so it is honoured rather than ignored.
    assert by_name["Legacy Ai Person"]["interview_booking_source"] == "ai_auto_booked"
    # A row with neither an explicit source nor an AI note keeps the resolver's
    # documented default, matching what Daily Ops already shows for it.
    assert by_name["Legacy Person"]["interview_booking_source"] == "candidate_booked"


def test_success_is_never_reported_without_a_persisted_slot(monkeypatch, tmp_path):
    """If the store hands back a row with no slot, the boundary must not 200."""
    client = _client(monkeypatch, tmp_path)
    unbooked = {"id": "ghost01", "name": "Abilash Perla", "date": "", "time": "",
                "slot_confirmed": False}
    monkeypatch.setattr(
        cs, "import_confirmed_interview_slot",
        lambda *_a, **_k: (unbooked, "cloned"),
    )
    monkeypatch.setattr(cs, "finalize_public_booking_payment", lambda row, **_k: row)

    response = _confirm(client, _profile_booking(key="ghost-key"))
    assert response.status_code == 500
    assert "not saved" in response.json().get("message", "").lower()


def test_a_genuine_duplicate_still_short_circuits(monkeypatch, tmp_path):
    """Real idempotency is preserved: the same slot twice books once."""
    client = _client(monkeypatch, tmp_path)
    booking = _booking(_upload(client))
    assert _confirm(client, booking).status_code == 200
    assert _confirm(client, booking).status_code == 200

    confirmed = [
        row for row in cs.list_candidates(stage="all", month="all")
        if cs.candidate_has_confirmed_slot(row) and row.get("date") == "2026-08-01"
    ]
    assert len(confirmed) == 1


def test_failed_invite_extraction_creates_no_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "features.ollama_invite_extract.extract_interview_invite_with_ollama",
        lambda *_args, **_kwargs: {
            "confidence_score": 0,
            "auto_booking_safe": False,
            "manual_fields_required": True,
            "failure_stage": "vision",
            "failure_reason": "Vision model returned no parseable JSON.",
            "missing_fields": ["interview_date", "start_time"],
            "warnings": ["Enter the date and time manually."],
        },
    )
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/public/slots/extract-invite-ai",
        files={"file": ("invite.jpg", b"invalid-image", "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.json()["data"]["manual_fields_required"] is True
    assert len(response.json()["data"]["invite_trace_id"]) == 32
    assert cs.list_candidates(stage="all", month="all") == []


def test_invite_trace_records_raw_normalized_submitted_and_stored_times(
    monkeypatch, tmp_path, caplog
):
    monkeypatch.setattr(
        "features.ollama_invite_extract.extract_interview_invite_with_ollama",
        lambda *_args, **_kwargs: {
            "_model_raw_interview_date": "2026-08-12",
            "_model_raw_start_time": "04:00",
            "_model_raw_end_time": "05:00 PM",
            "interview_date": "2026-08-12",
            "start_time": "04:00 AM",
            "end_time": "05:00 PM",
            "time": "04:00",
            "confidence_score": 95,
            "auto_booking_safe": True,
            "manual_fields_required": False,
            "primary_model": "qwen3-vl:8b-instruct",
            "inference_node_id": "rtx4060",
            "extraction_method": "ai_only",
        },
    )
    caplog.set_level(logging.INFO, logger="core.public_slot_api")
    client = _client(monkeypatch, tmp_path)

    extracted = client.post(
        "/public/slots/extract-invite-ai",
        files={"file": ("invite.jpg", b"same-invite", "image/jpeg")},
    )
    assert extracted.status_code == 200
    data = extracted.json()["data"]
    trace_id = data["invite_trace_id"]
    assert "_model_raw_start_time" not in data

    booking = _booking(_upload(client))
    booking.update(
        {
            "time": "04:00",
            "time_end": "04:30",
            "invite_trace_id": trace_id,
            "invite_display_date": "2026-08-12",
            "invite_display_time": "04:00 AM",
            "invite_extracted_start_time": data["start_time"],
        }
    )
    confirmed = client.post(
        "/bookings/confirm",
        data=booking,
        files={"file": ("invite.jpg", b"same-invite", "image/jpeg")},
    )
    assert confirmed.status_code == 200

    messages = "\n".join(caplog.messages)
    assert f"phase=extract outcome=complete trace_id={trace_id}" in messages
    assert "raw_start='04:00'" in messages
    assert "normalized_start='04:00 AM'" in messages
    assert f"phase=confirm_received trace_id={trace_id}" in messages
    assert "displayed_time='04:00 AM'" in messages
    assert "submitted_time='04:00'" in messages
    assert f"phase=confirm_stored trace_id={trace_id}" in messages
    assert "stored_time='04:00'" in messages
    trace_records = [
        record
        for record in caplog.records
        if record.name == "core.public_slot_api"
        and record.getMessage().startswith("Invite booking trace")
    ]
    assert len(trace_records) == 3
    assert all(record.levelno == logging.WARNING for record in trace_records)


def test_missing_payment_blocks_confirmation_without_records(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post(
        "/bookings/confirm",
        data={**_booking(""), "payment_proof_id": ""},
        files={"file": ("invite.jpg", b"invite", "image/jpeg")},
    )
    assert response.status_code == 400
    assert response.json()["message"] == "Upload and verify the payment screenshot to continue."
    assert cs.list_candidates(stage="all", month="all") == []


def test_failed_confirmation_rolls_back_new_candidate(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    booking = _booking(_upload(client))
    original = cs.finalize_public_booking_payment

    def fail_after_creation(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated finalization failure")

    monkeypatch.setattr(cs, "finalize_public_booking_payment", fail_after_creation)
    response = client.post("/bookings/confirm", data=booking, files={"file": ("invite.jpg", b"invite", "image/jpeg")})
    assert response.status_code == 500
    assert cs.list_candidates(stage="all", month="all") == []


def test_legacy_booking_endpoint_cannot_create(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post("/public/slots/book")
    assert response.status_code == 410
    assert cs.list_candidates(stage="all", month="all") == []


def test_re_service_uses_final_confirmation_and_consumes_only_after_completion(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    original = cs.create_candidate(
        {
            "name": "Re Service Candidate",
            "phone": "9876500088",
            "service_type": "round_wise",
            "interview_round": "L1",
            "technology": "Testing",
        }
    )
    cs.set_interview_attendance(original["id"], status="re_service", by="admin")
    booking = {
        **_booking(""),
        "name": original["name"],
        "phone": original["phone"],
        "candidate_id": original["id"],
        "technology": "Testing",
        "idempotency_key": "re-service-final-confirmation",
    }

    first = client.post(
        "/bookings/confirm",
        data=booking,
        files={"file": ("invite.jpg", b"invite", "image/jpeg")},
    )
    second = client.post(
        "/bookings/confirm",
        data=booking,
        files={"file": ("invite.jpg", b"invite", "image/jpeg")},
    )

    assert first.status_code == second.status_code == 200
    booked_id = first.json()["candidate"]["id"]
    assert first.json()["candidate"]["re_service_booking"] is True
    assert second.json()["candidate"]["id"] == booked_id
    assert cs.candidate_is_re_service_eligible(candidate_id=original["id"]) is True

    cs.set_interview_attendance(
        booked_id,
        status="attended",
        remark="completed",
        feedback="positive",
        by="admin",
        allow_future=True,
    )
    assert cs.candidate_is_re_service_eligible(candidate_id=original["id"]) is False
