"""Add Referrer Expense: the amount comes from the screenshot.

An expense used to be saved with whatever amount the operator typed, checked only
for being covered by the receipt. The amount is now read off the attached
screenshot and that is the amount saved. It is used only when the screenshot gives
one clear answer: an unreadable amount, one the receipt contradicts itself on, or
one a digit may have been dropped from blocks the save with the reason, because a
guessed amount is worse than none. The operator confirms the figure that was read,
and the server saves it only if it is still the figure on the screenshot.

Three layers are held here: the policy that decides whether a reading is clear
(`receipt_amount.read_amount`), the engine's reuse of a reading so the slow AI step
runs once per screenshot, and the two routes -- one that reads and saves nothing,
and the save.
"""
from __future__ import annotations

import pytest

from features import payment_verification_engine as engine
from features import receipt_amount, transaction_identity

UTR = "829368041653"
IMAGE = b"the-thrilok-receipt"
OTHER_IMAGE = b"a-different-receipt"


def digest(data: bytes) -> str:
    return transaction_identity.screenshot_hash(data)


# ── the policy ────────────────────────────────────────────────────────────────

def verified(**over):
    """What the engine returns for a clean, verified receipt."""
    base = {"amount": 42500, "deterministic_verified": True, "deterministic_reasons": []}
    base.update(over)
    return base


class TestOneClearAmount:
    def test_a_clean_verified_receipt_gives_its_amount(self):
        read = receipt_amount.read_amount(verified())
        assert (read.ok, read.amount, read.problem) == (True, 42500, "")

    def test_a_corroborated_amount_is_just_as_good(self):
        assert receipt_amount.read_amount(verified(amount_corroborated=True)).amount == 42500

    @pytest.mark.parametrize("amount", [0, None, "", "not a number", -5])
    def test_an_amount_that_was_not_read_is_blocked(self, amount):
        read = receipt_amount.read_amount(verified(amount=amount))
        assert read.ok is False and read.amount == 0
        assert "could not be read" in read.problem

    def test_an_amount_a_digit_may_have_been_dropped_from_is_blocked(self):
        read = receipt_amount.read_amount(verified(
            amount=4250, amount_extraction_review_required=True,
            amount_review_reason="A visible amount of ₹42,500 is exactly ten times the parsed ₹4,250. A digit was probably dropped.",
        ))
        assert read.ok is False and read.amount == 0
        assert "ten times the parsed" in read.problem
        assert "Attach a clearer screenshot" in read.problem

    def test_an_amount_the_receipt_contradicts_itself_on_is_blocked(self):
        read = receipt_amount.read_amount(verified(
            amount_mismatch_reason="OCR found conflicting visible amounts: INR 4,250, INR 42,500.",
            amount_crosscheck="mismatch",
        ))
        assert read.ok is False and read.amount == 0
        assert "conflicting visible amounts" in read.problem

    def test_a_mismatch_flag_with_no_reason_is_still_blocked(self):
        read = receipt_amount.read_amount(verified(amount_crosscheck="mismatch"))
        assert read.ok is False and "disagree" in read.problem

    def test_ambiguity_is_blocked_even_when_everything_else_verified(self):
        # `deterministic_verified` is True here: the amount alone is in doubt.
        assert receipt_amount.read_amount(verified(amount_extraction_review_required=True)).ok is False

    def test_a_receipt_that_cannot_be_verified_is_blocked_with_the_reasons(self):
        read = receipt_amount.read_amount(verified(
            deterministic_verified=False,
            deterministic_reasons=["This is not a valid payment receipt.", "The receiver is not present in the configured receiver registry."],
        ))
        assert read.ok is False and read.amount == 0
        assert "not a valid payment receipt" in read.problem and "receiver registry" in read.problem

    def test_unverified_with_no_reason_still_says_something(self):
        assert receipt_amount.read_amount(verified(deterministic_verified=False)).problem == "Payment screenshot could not be verified."

    def test_nothing_at_all_is_blocked(self):
        assert receipt_amount.read_amount({}).ok is False
        assert receipt_amount.read_amount(None).ok is False


# ── the engine reads a screenshot once ────────────────────────────────────────

@pytest.fixture()
def reader(monkeypatch, tmp_path):
    """The engine with a fake AI node that counts how often it is asked."""
    registry = tmp_path / "payment_receiver_accounts.json"
    registry.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(registry))
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    engine.forget_remembered_extractions()
    state = {"calls": 0, "reading": {
        "amount": 5000, "receiver_name": "SAMPLE RECEIVER", "receiver_upi_id": "company@upi", "receiver_phone": "",
        "receiver_account": "", "utr_number": UTR, "transaction_id": "", "reference_number": "",
        "payment_date": "2026-10-06", "payment_time": "11:40 AM", "status": "success", "confidence_score": 98,
        "is_payment_screenshot": True, "primary_model": "qwen3-vl:8b-instruct", "receiver_type": "company",
    }}

    def fake_extract(_raw, _mime, **_kwargs):
        state["calls"] += 1
        return dict(state["reading"])

    monkeypatch.setattr("features.ollama_payment_extract.extract_payment_with_ollama", fake_extract)
    state["ledger"] = tmp_path / "ledger.json"
    yield state
    engine.forget_remembered_extractions()


def read(image=IMAGE, **over):
    options = {"source_module": "handler_expense_create", "expected_amount": 0, "entity_name": "SAMPLE RECEIVER",
               "purpose": "handler_payout", "create_ledger": False}
    options.update(over)
    return engine.verify_payment_screenshot(image, **options)


class TestTheScreenshotIsReadOnce:
    def test_by_default_every_call_reads_the_screenshot(self, reader):
        read()
        read()
        assert reader["calls"] == 2

    def test_a_fresh_reading_is_remembered_and_reused_by_the_save(self, reader):
        first = read(extraction_cache="fresh")
        saved = read(extraction_cache="reuse", create_ledger=True)
        assert reader["calls"] == 1
        assert first["amount"] == saved["amount"] == 5000
        assert saved["deterministic_verified"] is True

    def test_a_newly_attached_screenshot_is_read_again_when_asked_to_be(self, reader):
        read(extraction_cache="fresh")
        read(extraction_cache="fresh")
        assert reader["calls"] == 2

    def test_reuse_reads_when_there_is_nothing_to_reuse_and_keeps_that_reading(self, reader):
        read(extraction_cache="reuse")
        read(extraction_cache="reuse")
        assert reader["calls"] == 1

    def test_a_different_screenshot_is_a_different_reading(self, reader):
        read(extraction_cache="fresh")
        read(image=OTHER_IMAGE, extraction_cache="reuse")
        assert reader["calls"] == 2

    def test_a_reading_with_no_amount_is_never_kept_so_it_is_tried_again(self, reader):
        reader["reading"]["amount"] = 0
        read(extraction_cache="reuse")
        read(extraction_cache="reuse")
        assert reader["calls"] == 2

    def test_a_reading_of_something_that_is_not_a_payment_is_never_kept(self, reader):
        reader["reading"]["is_payment_screenshot"] = False
        read(extraction_cache="reuse")
        read(extraction_cache="reuse")
        assert reader["calls"] == 2

    def test_a_reading_is_forgotten_after_its_time(self, reader, monkeypatch):
        read(extraction_cache="fresh")
        monkeypatch.setattr(engine, "_EXTRACTION_TTL_SECONDS", -1)
        read(extraction_cache="reuse")
        assert reader["calls"] == 2

    def test_only_a_few_readings_are_kept(self, reader, monkeypatch):
        monkeypatch.setattr(engine, "_EXTRACTION_LIMIT", 2)
        for image in (b"one", b"two", b"three"):
            read(image=image, extraction_cache="fresh")
        calls = reader["calls"]
        read(image=b"three", extraction_cache="reuse")
        read(image=b"one", extraction_cache="reuse")  # evicted: read again
        assert reader["calls"] == calls + 1

    def test_a_reused_reading_cannot_be_changed_by_a_caller(self, reader):
        read(extraction_cache="fresh")
        engine._remembered_extraction((engine.hashlib.sha256(IMAGE).hexdigest(), "image/jpeg", engine.payment_ocr_enabled()))["amount"] = 1
        assert read(extraction_cache="reuse")["amount"] == 5000

    def test_a_clean_read_changes_nothing_stored(self, reader):
        read(create_ledger=False)
        assert not reader["ledger"].exists()

    def test_reading_with_no_amount_expected_still_judges_the_receipt(self, reader):
        reader["reading"]["is_payment_screenshot"] = False
        result = read()
        assert result["deterministic_verified"] is False


# ── the routes ────────────────────────────────────────────────────────────────

@pytest.fixture()
def world(tmp_path, monkeypatch):
    from features import financial_reconciliation as fr
    from features import handler_expenses

    monkeypatch.setattr(handler_expenses, "_FILE", str(tmp_path / "handler_expenses.json"))
    monkeypatch.setattr(handler_expenses, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setattr(fr, "_payment_id_by_screenshot", lambda: {})
    monkeypatch.setattr(fr, "_ledger_transactions", lambda: [])
    monkeypatch.setattr(fr, "_company_expense_transactions", lambda: [])
    monkeypatch.setattr(fr, "_canonical", lambda name: str(name or "").strip().lower())
    return handler_expenses


@pytest.fixture()
def receipt():
    """What the engine says about the next screenshot, and how it was asked."""
    return {"verdict": verified(utr_number=UTR, payment_id="pay_1", sender_name="B Thriloknath"), "asked": []}


@pytest.fixture()
def client(world, receipt, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import expenses as routes

    monkeypatch.setattr("features.referrer_registry.resolve_referrer",
                        lambda name: {"id": "referrer-thrilok", "name": "Thrilok"} if str(name).lower() == "thrilok" else None)

    def read_receipt(image_data, mime_type="image/jpeg", **kwargs):
        receipt["asked"].append(kwargs)
        return dict(receipt["verdict"])

    monkeypatch.setattr("features.payment_verification_engine.verify_payment_screenshot", read_receipt)
    routes.forget_reads()
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes._require_fleet_admin] = lambda: None
    return TestClient(app)


def start(client, image=IMAGE, **form):
    data = {"reference": "Thrilok", "date": "2026-10-06"}
    data.update(form)
    return client.post("/handler-expenses/extract", data=data, files={"file": ("receipt.png", image, "image/png")}).json()


def extract(client, image=IMAGE, **form):
    """Start a reading and collect its answer, as the dashboard does."""
    started = start(client, image, **form)
    if started.get("status") != "pending":
        return started  # refused at once: the request itself was unusable
    return client.get(f"/handler-expenses/extract/{started['read_id']}").json()


def save(client, image=IMAGE, **form):
    data = {"reference": "Thrilok", "category": "commission", "date": "2026-10-06"}
    data.update(form)
    return client.post("/handler-expenses", data=data, files={"file": ("receipt.png", image, "image/png")}).json()


def stored(world):
    return world.list_expenses(include_voided=True)


class TestReadingTheAmount:
    def test_returns_the_amount_on_the_screenshot(self, client):
        body = extract(client)
        assert body["status"] == "ok" and body["amount"] == 42500

    def test_needs_no_amount_from_the_caller(self, client):
        assert extract(client)["status"] == "ok"

    def test_saves_nothing(self, client, world):
        extract(client)
        assert stored(world) == []

    def test_does_not_touch_the_payment_ledger(self, client, receipt):
        extract(client)
        assert receipt["asked"][0]["create_ledger"] is False

    def test_leaves_the_amount_for_the_screenshot_to_decide(self, client, receipt):
        extract(client)
        assert receipt["asked"][0]["expected_amount"] == 0

    def test_reads_a_newly_attached_screenshot_afresh_when_told_to(self, client, receipt):
        extract(client, fresh="1")
        extract(client)
        assert [call["extraction_cache"] for call in receipt["asked"]] == ["fresh", "reuse"]

    def test_tells_the_operator_when_the_figure_is_corroborated(self, client, receipt):
        receipt["verdict"]["amount_corroborated"] = True
        assert extract(client)["corroborated"] is True

    def test_is_judged_as_a_payment_to_the_selected_referrer(self, client, receipt):
        extract(client)
        asked = receipt["asked"][0]
        assert (asked["referrer_hint"], asked["referrer_id"], asked["purpose"]) == ("Thrilok", "referrer-thrilok", "handler_payout")


class TestStartAndCollect:
    """A reading takes about a minute -- as long as the proxy in front of the server
    waits -- so the request that starts it must not be the one that answers it."""

    def test_starting_answers_at_once_with_an_id_and_no_amount(self, client):
        body = start(client)
        assert body["status"] == "pending" and body["read_id"]
        assert "amount" not in body

    def test_the_answer_is_collected_with_that_id(self, client):
        started = start(client)
        answer = client.get(f"/handler-expenses/extract/{started['read_id']}").json()
        assert answer["status"] == "ok" and answer["amount"] == 42500

    def test_it_is_pending_until_the_reading_finishes(self, client, monkeypatch):
        from api.routers import expenses as routes

        async def never_finishes(*args, **kwargs):
            return None

        monkeypatch.setattr(routes, "_run_read", never_finishes)
        started = start(client)
        url = f"/handler-expenses/extract/{started['read_id']}"
        assert client.get(url).json() == {"status": "pending"}
        routes._finish_read(started["read_id"], {"status": "ok", "amount": 7})
        assert client.get(url).json() == {"status": "ok", "amount": 7}

    def test_the_answer_can_be_collected_more_than_once(self, client):
        started = start(client)
        url = f"/handler-expenses/extract/{started['read_id']}"
        assert client.get(url).json() == client.get(url).json()

    def test_the_dashboards_own_id_is_kept_so_its_progress_follows_the_same_reading(self, client):
        mine = "ab" * 16
        assert start(client, analysis_id=mine)["read_id"] == mine
        assert client.get(f"/handler-expenses/extract/{mine}").json()["analysis"]["analysis_id"] == mine

    def test_an_unusable_id_is_replaced(self, client):
        read_id = start(client, analysis_id="not-an-id")["read_id"]
        assert len(read_id) == 32 and read_id != "not-an-id"

    def test_an_id_already_in_use_is_not_taken_over(self, client):
        mine = "cd" * 16
        first = start(client, analysis_id=mine)["read_id"]
        second = start(client, analysis_id=mine)["read_id"]
        assert first == mine and second != mine

    def test_an_unknown_id_says_the_reading_is_gone(self, client):
        body = client.get("/handler-expenses/extract/" + "ef" * 16).json()
        assert body["status"] == "error" and "no longer available" in body["message"]

    def test_a_reading_is_forgotten_after_its_time(self, client, monkeypatch):
        from api.routers import expenses as routes

        started = start(client)
        monkeypatch.setattr(routes, "_READ_TTL_SECONDS", -1)
        assert client.get(f"/handler-expenses/extract/{started['read_id']}").json()["status"] == "error"

    def test_only_the_latest_readings_are_kept(self, client, monkeypatch):
        from api.routers import expenses as routes

        monkeypatch.setattr(routes, "_READ_LIMIT", 2)
        first, second, third = (start(client)["read_id"] for _ in range(3))
        assert client.get(f"/handler-expenses/extract/{first}").json()["status"] == "error"
        assert client.get(f"/handler-expenses/extract/{second}").json()["status"] == "ok"
        assert client.get(f"/handler-expenses/extract/{third}").json()["status"] == "ok"

    def test_a_reading_that_blows_up_ends_as_an_error_not_as_pending_forever(self, client, monkeypatch):
        from api.routers import expenses as routes

        async def explode(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(routes, "_read_expense_amount", explode)
        answer = client.get(f"/handler-expenses/extract/{start(client)['read_id']}").json()
        assert answer["status"] == "error" and "could not be read" in answer["message"]

    def test_the_answer_names_the_node_that_read_it(self, client):
        assert "analysis" in extract(client)

    def test_starting_checks_the_request_before_anything_is_read(self, client, receipt):
        assert start(client, reference="Nobody")["status"] == "error"
        assert receipt["asked"] == []


class TestWhenTheAmountCannotBeRead:
    def test_an_unreadable_amount_is_an_error_with_no_amount(self, client, receipt):
        receipt["verdict"]["amount"] = 0
        body = extract(client)
        assert body["status"] == "error" and "could not be read" in body["message"]
        assert "amount" not in body

    def test_an_ambiguous_amount_is_an_error_with_the_reason_and_no_amount(self, client, receipt):
        receipt["verdict"].update(amount=4250, amount_extraction_review_required=True,
                                  amount_review_reason="A visible amount of ₹42,500 is exactly ten times the parsed ₹4,250.")
        body = extract(client)
        assert body["status"] == "error" and "ten times the parsed" in body["message"]
        assert "amount" not in body

    def test_conflicting_amounts_are_an_error(self, client, receipt):
        receipt["verdict"]["amount_mismatch_reason"] = "OCR found conflicting visible amounts: INR 4,250, INR 42,500."
        body = extract(client)
        assert body["status"] == "error" and "conflicting" in body["message"] and "amount" not in body

    def test_a_receipt_that_cannot_be_verified_is_an_error_with_the_reasons(self, client, receipt):
        receipt["verdict"].update(deterministic_verified=False,
                                  deterministic_reasons=["The receiver is not present in the configured receiver registry."])
        body = extract(client)
        assert body["status"] == "error" and "receiver registry" in body["message"] and "amount" not in body

    def test_an_engine_failure_is_an_error_not_a_crash(self, client, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("the AI node is unreachable")
        monkeypatch.setattr("features.payment_verification_engine.verify_payment_screenshot", boom)
        body = extract(client)
        assert body["status"] == "error" and "unreachable" in body["message"]

    def test_an_unknown_referrer_is_an_error(self, client):
        assert extract(client, reference="Nobody")["status"] == "error"

    def test_a_file_that_is_not_an_image_is_an_error(self, client):
        body = client.post("/handler-expenses/extract", data={"reference": "Thrilok"},
                           files={"file": ("receipt.txt", b"text", "text/plain")}).json()
        assert body["status"] == "error" and "image" in body["message"]

    def test_an_empty_file_is_an_error(self, client):
        assert extract(client, image=b"")["status"] == "error"


class TestAReceiptThatIsAlreadyRecorded:
    def test_is_refused_when_it_is_attached_not_after_it_is_confirmed(self, client, world):
        first = save(client)
        assert first["status"] == "ok"
        body = extract(client)
        assert body["status"] == "error"
        assert "already recorded as a handler expense" in body["message"]
        assert body["duplicate_of"]["record_id"] == first["expense"]["id"]
        assert "amount" not in body
        assert len(stored(world)) == 1

    def test_a_different_receipt_for_the_same_amount_is_read_normally(self, client, receipt):
        save(client)
        receipt["verdict"].update(utr_number="900000000007", payment_id="pay_other")
        assert extract(client, image=OTHER_IMAGE)["status"] == "ok"


class TestSavingUsesTheAmountOnTheScreenshot:
    def test_no_amount_has_to_be_sent(self, client, world):
        body = save(client)
        assert body["status"] == "ok" and body["expense"]["amount"] == 42500
        assert [row["amount"] for row in stored(world)] == [42500]

    def test_the_confirmed_amount_is_accepted_when_it_is_the_screenshots(self, client):
        body = save(client, amount="42500")
        assert body["status"] == "ok" and body["expense"]["amount"] == 42500

    def test_an_amount_that_is_not_the_screenshots_is_refused_and_nothing_is_saved(self, client, world):
        body = save(client, amount="5000")
        assert body["status"] == "error"
        assert "₹5,000" in body["message"] and "₹42,500" in body["message"] and "Nothing was saved" in body["message"]
        assert stored(world) == []

    def test_an_amount_larger_than_the_screenshots_is_refused_too(self, client, world):
        assert save(client, amount="50000")["status"] == "error"
        assert stored(world) == []

    def test_a_confirmed_amount_that_is_not_a_number_is_refused(self, client, world):
        body = save(client, amount="forty thousand")
        assert body["status"] == "error" and "must be a number" in body["message"]
        assert stored(world) == []

    def test_an_unreadable_amount_blocks_the_save(self, client, receipt, world):
        receipt["verdict"]["amount"] = 0
        body = save(client, amount="42500")
        assert body["status"] == "error" and "could not be read" in body["message"]
        assert stored(world) == []

    def test_an_ambiguous_amount_blocks_the_save_even_when_one_is_sent(self, client, receipt, world):
        receipt["verdict"].update(amount=4250, amount_extraction_review_required=True,
                                  amount_review_reason="A digit was probably dropped.")
        body = save(client, amount="4250")
        assert body["status"] == "error" and "digit was probably dropped" in body["message"]
        assert stored(world) == []

    def test_a_receipt_that_cannot_be_verified_blocks_the_save(self, client, receipt, world):
        receipt["verdict"].update(deterministic_verified=False, deterministic_reasons=["Only a successful, completed transaction can be accepted."])
        body = save(client)
        assert body["status"] == "error" and "completed transaction" in body["message"]
        assert stored(world) == []

    def test_the_reading_made_at_attach_time_is_reused_and_the_ledger_is_written_now(self, client, receipt):
        extract(client, fresh="1")
        save(client, amount="42500")
        read_at_attach, read_at_save = receipt["asked"]
        assert (read_at_attach["create_ledger"], read_at_attach["extraction_cache"]) == (False, "fresh")
        assert (read_at_save["create_ledger"], read_at_save["extraction_cache"]) == (True, "reuse")
        assert read_at_save["expected_amount"] == 0

    def test_the_receipt_s_identity_is_stored_with_the_expense(self, client, world):
        row = save(client)["expense"]
        assert row["external_transaction_id"] == UTR
        assert row["screenshot_hash"] == digest(IMAGE)
        assert len(row["proofs"]) == 1

    def test_the_same_payment_cannot_be_saved_twice(self, client, world):
        assert save(client)["status"] == "ok"
        again = save(client)
        assert again["status"] == "error" and "already recorded" in again["message"]
        assert len(stored(world)) == 1

    def test_read_then_save_is_one_expense_at_the_amount_read(self, client, world):
        read = extract(client, fresh="1")
        saved = save(client, amount=str(read["amount"]))
        assert saved["status"] == "ok"
        assert [row["amount"] for row in stored(world)] == [read["amount"]]
