"""The dashboard's AI uploads name the node reading them, as the booking page does.

A candidate's payment screenshot, its replacement, a resume, and a referrer
expense screenshot are each read by an AI node. The dashboard used to say
"Processing screenshot…", "AI analyzing (~30-60s)…" or "Verifying details…"
over them -- nothing about where the work ran, and nothing about a failover.
Each route now takes the same `analysis_id` the booking page sends, reports the
analysis live at /public/slots/analysis/{id}, and returns it with the response.

These drive the real routes and the real gateway `chat()`; only storage, access
checks and the transport to the nodes are replaced (see `Pool`).
"""

from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import candidates as candidate_routes
from api.routers import expenses as expense_routes
from core import ai_activity, ai_gateway
from core.public_slot_api import install_public_slot_routes
from tests.test_booking_analysis_names_its_node import (  # noqa: F401 - `pool` is a fixture
    TEXT_MODEL,
    VISION_MODEL,
    busy_nodes,
    pool,
)

CANDIDATE = {"id": "cand-1", "name": "Ravi", "reference": "Thrilok", "payment": 0, "service_type": "profile"}


def read_receipt(*_args, **_kwargs) -> dict:
    """The payment verifier, reading the screenshot through the real gateway."""
    ai_gateway.chat(messages=[{"role": "user", "content": "receipt"}], model=VISION_MODEL,
                    images=["aW1hZ2U="], workload="payment_screenshot_vision", max_retries=0)
    return {"is_payment_screenshot": True, "amount": 5000, "status": "success",
            "deterministic_verified": True, "verification_state": "VERIFIED_REFERRER_PAYMENT",
            "utr_number": "829368041653", "receiver_name": "B THRILOKNATH", "deterministic_reasons": []}


def refuse_receipt(*_args, **_kwargs) -> dict:
    result = read_receipt()
    return {**result, "deterministic_verified": False, "verification_state": "UNREGISTERED_RECEIVER",
            "deterministic_reasons": ["The receiver is not present in the configured receiver registry."]}


def read_resume(*_args, **_kwargs) -> dict:
    ai_gateway.chat(messages=[{"role": "user", "content": "resume"}], model=TEXT_MODEL,
                    workload="resume_text", max_retries=0)
    return {"is_resume": True, "candidate_name": "Ravi Kumar", "technology": "Java", "phone": "9876500001"}


@pytest.fixture()
def client(pool, monkeypatch) -> TestClient:
    from features import candidate_store, handler_expenses, payment_receipts

    monkeypatch.setattr("core.dashboard_access.assert_candidate_row_access", lambda request, row: None)
    monkeypatch.setattr(candidate_store, "get_candidate", lambda cid: dict(CANDIDATE))
    monkeypatch.setattr(candidate_store, "effective_expected_payment", lambda row: 5000)
    monkeypatch.setattr(candidate_store, "add_payment_proof", lambda cid, **kwargs: {"id": "proof-1"})
    monkeypatch.setattr(candidate_store, "add_resume", lambda cid, **kwargs: {"id": "resume-1"})
    monkeypatch.setattr(payment_receipts, "api_summary", lambda row: {})
    monkeypatch.setattr("features.payment_fraud_detection.assess_payment_proof", lambda raw, extraction, **kwargs: {
        "decision": "accepted", "sha256": "0" * 64, "reasons": [], "warnings": [], "checked_at": "now",
    })
    monkeypatch.setattr("features.payment_verification_engine.verify_payment_screenshot", read_receipt)
    monkeypatch.setattr("features.ollama_resume_extract.extract_resume_with_ollama", read_resume)
    monkeypatch.setattr("features.referrer_registry.resolve_referrer",
                        lambda name: {"id": "referrer-thrilok", "name": "Thrilok"})
    monkeypatch.setattr(handler_expenses, "create_expense", lambda body: {"id": "exp-1", **body})
    monkeypatch.setattr(handler_expenses, "add_proof", lambda eid, **kwargs: {"id": "exp-proof-1"})
    monkeypatch.setattr(handler_expenses, "list_expenses",
                        lambda **kwargs: [{"id": "exp-1", "reference": "Thrilok", "amount": 5000,
                                           "category": "commission"}])

    app = FastAPI()
    app.include_router(candidate_routes.router)
    app.include_router(expense_routes.router)
    app.dependency_overrides[expense_routes._require_fleet_admin] = lambda: None
    install_public_slot_routes(app)
    return TestClient(app)


def post_proof(client: TestClient, analysis_id: str):
    return client.post("/candidates/cand-1/proofs",
                       data={"attachment_type": "payment_proof", "analysis_id": analysis_id},
                       files={"file": ("receipt.png", b"receipt", "image/png")})


def post_expense(client: TestClient, analysis_id: str):
    return client.post("/handler-expenses",
                       data={"reference": "Thrilok", "amount": "5000", "category": "commission",
                             "analysis_id": analysis_id},
                       files={"file": ("receipt.png", b"receipt", "image/png")})


def in_thread(call) -> tuple[threading.Thread, dict]:
    out: dict = {}
    worker = threading.Thread(target=lambda: out.setdefault("response", call()), daemon=True)
    worker.start()
    return worker, out


class TestCandidatePaymentScreenshots:
    def test_the_upload_is_followed_live_and_the_response_names_the_node(self, client, pool):
        pool.routes[VISION_MODEL] = ["jagadeesh"]
        release = pool.hold("jagadeesh")
        analysis = "a1" * 16
        worker, out = in_thread(lambda: post_proof(client, analysis))

        assert pool.on_node["jagadeesh"].wait(10)
        live = client.get(f"/public/slots/analysis/{analysis}").json()["analysis"]
        assert (live["state"], live["node"]) == ("running", "Jagadeesh")
        # The nodes panel calls it what it is: a payment, not a booking.
        assert busy_nodes() == {"jagadeesh": ["Payment analysis"]}

        release.set()
        worker.join(10)
        body = out["response"].json()
        assert body["status"] == "ok"
        assert body["analysis"] == {"analysis_id": analysis, "state": "done", "node": "Jagadeesh",
                                    "analysed_by": ["Jagadeesh"], "failed_on": []}

    def test_a_failover_is_reported_with_the_node_it_left(self, client, pool):
        pool.routes[VISION_MODEL] = ["our_machine", "rtx4060"]
        pool.failing.add("our_machine")
        body = post_proof(client, "a2" * 16).json()
        assert body["analysis"]["analysed_by"] == ["RTX 4060"]
        assert body["analysis"]["failed_on"] == ["Praveen"]

    def test_a_refused_screenshot_still_says_which_node_read_it(self, client, pool, monkeypatch):
        monkeypatch.setattr("features.payment_fraud_detection.assess_payment_proof",
                            lambda raw, extraction, **kwargs: {"decision": "rejected", "reasons": ["Duplicate."],
                                                               "duplicate_matches": []})
        body = post_proof(client, "a3" * 16).json()
        assert body["status"] == "error"
        assert body["analysis"]["analysed_by"] == ["RTX 4060"]

    def test_a_replacement_is_followed_the_same_way(self, client, pool, monkeypatch):
        from features import candidate_store, payment_evidence_store

        monkeypatch.setattr(candidate_routes, "_reviewer_name", lambda request: "admin")
        monkeypatch.setattr("features.candidate_attachments.partition_candidate_attachments",
                            lambda row: {"payment_proofs": [{"id": "proof-1"}]})
        monkeypatch.setattr(payment_evidence_store, "store", lambda raw, **kwargs: {
            "sha256": "1" * 64, "storage_key": "key", "byte_size": len(raw), "deduplicated": False})
        monkeypatch.setattr(candidate_store, "apply_replacement_proof", lambda *args: {"id": "proof-1"})
        monkeypatch.setattr(candidate_store, "recalculate_received_total", lambda *args, **kwargs: None)

        body = client.post("/candidates/cand-1/proofs/proof-1/replace", data={"analysis_id": "a4" * 16},
                           files={"file": ("receipt.png", b"receipt", "image/png")}).json()
        assert body["status"] == "ok"
        assert body["analysis"]["analysed_by"] == ["RTX 4060"]
        assert body["analysis"]["analysis_id"] == "a4" * 16


class TestResumes:
    def test_a_resume_upload_names_the_node_that_read_it(self, client, pool):
        release = pool.hold("jagadeesh")
        analysis = "b1" * 16
        worker, out = in_thread(lambda: client.post(
            "/candidates/cand-1/resumes", data={"analysis_id": analysis},
            files={"file": ("resume.pdf", b"%PDF-1.4", "application/pdf")}))

        assert pool.on_node["jagadeesh"].wait(10)
        assert busy_nodes() == {"jagadeesh": ["Resume analysis"]}
        release.set()
        worker.join(10)
        body = out["response"].json()
        assert body["status"] == "ok"
        assert body["analysis"]["analysed_by"] == ["Jagadeesh"]

    def test_a_document_that_is_not_a_resume_is_refused_with_its_node(self, client, pool, monkeypatch):
        monkeypatch.setattr("features.ollama_resume_extract.extract_resume_with_ollama",
                            lambda raw, mime: {**read_resume(), "is_resume": False})
        body = client.post("/candidates/cand-1/resumes", data={"analysis_id": "b2" * 16},
                           files={"file": ("offer.pdf", b"%PDF-1.4", "application/pdf")}).json()
        assert body["status"] == "error"
        assert body["analysis"]["analysed_by"] == ["Jagadeesh"]

    def test_the_auto_fill_extraction_names_its_node(self, client, pool):
        body = client.post("/public/slots/extract-resume-ai", data={"analysis_id": "b3" * 16},
                           files={"file": ("resume.pdf", b"%PDF-1.4", "application/pdf")}).json()
        assert body["success"] is True
        assert body["analysis"]["analysed_by"] == ["Jagadeesh"]

    def test_a_document_no_node_reads_claims_no_node(self, client, pool):
        """A .docx is filed without an AI read, so nothing is said to have read it."""
        body = client.post("/candidates/cand-1/resumes", data={"analysis_id": "b4" * 16},
                           files={"file": ("resume.docx", b"PK", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}).json()
        assert body["status"] == "ok"
        assert body["analysis"]["analysed_by"] == [] and body["analysis"]["failed_on"] == []
        assert pool.served == []


class TestReferrerExpenses:
    def test_a_new_expense_names_the_node_that_verified_its_screenshot(self, client, pool):
        release = pool.hold("rtx4060")
        analysis = "c1" * 16
        worker, out = in_thread(lambda: post_expense(client, analysis))

        assert pool.on_node["rtx4060"].wait(10)
        live = client.get(f"/public/slots/analysis/{analysis}").json()["analysis"]
        assert (live["state"], live["node"]) == ("running", "RTX 4060")
        assert busy_nodes() == {"rtx4060": ["Payment analysis"]}

        release.set()
        worker.join(10)
        body = out["response"].json()
        assert body["status"] == "ok"
        assert body["analysis"]["analysed_by"] == ["RTX 4060"]

    def test_a_refused_expense_screenshot_still_names_its_node(self, client, pool, monkeypatch):
        monkeypatch.setattr("features.payment_verification_engine.verify_payment_screenshot", refuse_receipt)
        body = post_expense(client, "c2" * 16).json()
        assert body["status"] == "error"
        assert "not present in the configured receiver registry" in body["message"]
        assert body["analysis"]["analysed_by"] == ["RTX 4060"]

    def test_a_screenshot_added_to_an_existing_expense_names_its_node(self, client, pool):
        body = client.post("/handler-expenses/exp-1/proofs", data={"analysis_id": "c3" * 16},
                           files={"file": ("receipt.png", b"receipt", "image/png")}).json()
        assert body["status"] == "ok"
        assert body["analysis"]["analysed_by"] == ["RTX 4060"]


def test_an_upload_sent_without_an_id_still_works_and_is_still_tracked(client, pool):
    """Older dashboards send no analysis_id; the route answers exactly as before."""
    body = client.post("/candidates/cand-1/proofs", data={"attachment_type": "payment_proof"},
                       files={"file": ("receipt.png", b"receipt", "image/png")}).json()
    assert body["status"] == "ok" and body["proof"] == {"id": "proof-1"}
    assert ai_activity.valid_analysis_id(body["analysis"]["analysis_id"])
    assert body["analysis"]["analysed_by"] == ["RTX 4060"]
