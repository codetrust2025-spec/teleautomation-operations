"""The node named for a booking analysis is the node that ran it.

The booking page used to say "Analysing…" and nothing about where. It now shows
the node reading the upload, and the AI nodes panel shows that node as
"Busy · Booking analysis". Both read one record, written by the gateway at the
moments it actually decides things -- a node takes the request, the node lets
go, the request moves to another node -- so neither can name a node from
configuration while a different machine does the work.

These drive the real gateway `chat()` with only the transport, the health probe
and node selection replaced. Node labels come from the real registry: nothing
here tells the code that `our_machine` is called "Praveen".
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import ai_activity, ai_gateway, ollama_nodes, recruitment_realtime
from core.public_slot_api import install_public_slot_routes

VISION_MODEL = "qwen3-vl:8b-instruct"
TEXT_MODEL = "qwen2.5:7b"
ALL_NODES = [("rtx4060", "RTX 4060"), ("jagadeesh", "Jagadeesh"), ("our_machine", "Praveen")]


def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition never held")


class Pool:
    """Nodes whose calls the test holds open, answers or fails."""

    def __init__(self, monkeypatch):
        self.routes: dict[str, list[str]] = {VISION_MODEL: ["rtx4060"], TEXT_MODEL: ["jagadeesh"]}
        self.gates: dict[str, threading.Event] = {}
        self.failing: set[str] = set()
        self.on_node = {node: threading.Event() for node, _ in ALL_NODES}
        self.selecting = threading.Event()
        self.hold_selection: threading.Event | None = None
        self.served: list[str] = []

        monkeypatch.setenv("AI_RECRUITMENT_QUEUE_WAIT_SECONDS", "5")
        monkeypatch.setattr(ai_gateway, "_host_slots", ai_gateway._HostQueue(1))
        monkeypatch.setattr(ollama_nodes, "candidate_order", lambda model=None: list(self.routes[model]))
        monkeypatch.setattr(ollama_nodes, "select_available_node", self._select)
        monkeypatch.setattr(ollama_nodes, "base_url_for", lambda node: f"http://{node}")
        monkeypatch.setattr(ollama_nodes, "record_success", lambda node: None)
        monkeypatch.setattr(ollama_nodes, "record_failure", lambda node, message=None: None)
        monkeypatch.setattr(ai_gateway.ollama_status, "record_request_success", lambda *a, **k: None)
        monkeypatch.setattr(ai_gateway.ollama_status, "record_request_failure", lambda *a, **k: None)
        monkeypatch.setattr(ai_gateway, "health", lambda **kwargs: {
            "endpoint_reachable": True, "model_available": True, "error_message": "", "error_code": "",
        })
        monkeypatch.setattr(ai_gateway, "_request_json", self._request)

    def _select(self, model=None, timeout=None, exclude=None):
        order = [node for node in self.routes[model] if node not in (exclude or set())]
        if exclude and self.hold_selection is not None:
            # A retry after a failure: hold it here so the moment between
            # losing one node and gaining the next can be observed.
            self.selecting.set()
            self.hold_selection.wait(5)
        return {"node_id": order[0]}

    def _request(self, path, method="GET", body=None, connect_timeout=None,
                 response_timeout=None, base_url=None):
        node = base_url.removeprefix("http://")
        self.on_node.setdefault(node, threading.Event()).set()
        if node in self.gates:
            self.gates[node].wait(10)
        if node in self.failing:
            raise ai_gateway.AIGatewayError("down", code="OLLAMA_CONNECTION_FAILED")
        self.served.append(node)
        return {"message": {"content": "{}"}}

    def hold(self, node: str) -> threading.Event:
        self.gates[node] = threading.Event()
        return self.gates[node]


@pytest.fixture()
def pool(monkeypatch):
    ai_activity.reset()
    nodes = Pool(monkeypatch)
    yield nodes
    # A failed assertion must not leave a worker thread parked on a gate.
    for gate in nodes.gates.values():
        gate.set()
    if nodes.hold_selection is not None:
        nodes.hold_selection.set()
    ai_activity.reset()


def booking_call(analysis_id: str, *, model: str = VISION_MODEL, workload="payment_screenshot_vision",
                 calls: int = 1, results: list | None = None) -> threading.Thread:
    """One booking upload in a worker thread, as the endpoint runs it."""
    def run():
        with ai_activity.booking_analysis(analysis_id):
            for _ in range(calls):
                result = ai_gateway.chat(
                    messages=[{"role": "user", "content": "read"}], model=model,
                    images=["aW1hZ2U="], workload=workload, max_retries=0,
                )
                if results is not None:
                    results.append(result)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def status(analysis_id: str) -> dict:
    return ai_activity.analysis_status(analysis_id) or {}


def busy_nodes() -> dict:
    return {
        node: [item["label"] for item in items]
        for node, items in ai_activity.node_snapshot()["nodes"].items()
    }


class TestTheNodeShownIsTheNodeServing:
    @pytest.mark.parametrize("node_id,label", ALL_NODES)
    def test_every_node_is_named_while_it_serves_and_after(self, pool, node_id, label):
        pool.routes[VISION_MODEL] = [node_id]
        release = pool.hold(node_id)
        analysis = "a" * 32
        results: list = []
        worker = booking_call(analysis, results=results)

        assert pool.on_node[node_id].wait(5)
        assert status(analysis)["state"] == "running"
        assert status(analysis)["node"] == label
        assert busy_nodes() == {node_id: ["Booking analysis"]}

        release.set()
        worker.join(5)
        assert status(analysis) == {
            "analysis_id": analysis, "state": "done", "node": label, "analysed_by": [label],
            "failed_on": [],
        }
        assert busy_nodes() == {}
        # The gateway's own answer agrees with what was displayed.
        assert results[0].node_id == node_id
        assert pool.served == [node_id]

    def test_waiting_until_a_node_takes_the_request(self, pool):
        """A node busy with mail is not the node reading the booking yet."""
        release_mail = pool.hold("rtx4060")
        mail = threading.Thread(target=lambda: ai_gateway.chat(
            messages=[{"role": "user", "content": "attachment"}], model=VISION_MODEL,
            workload="recruitment_attachment_vision", max_retries=0,
        ), daemon=True)
        mail.start()
        assert pool.on_node["rtx4060"].wait(5)
        assert busy_nodes() == {"rtx4060": ["Mail analysis"]}

        analysis = "b" * 32
        worker = booking_call(analysis)
        wait_until(lambda: ai_gateway._host_slots._interactive_waiting.get("rtx4060-ollama") == 1)
        assert status(analysis)["state"] == "waiting"
        assert status(analysis)["node"] is None

        # Mail finishes; the booking takes the node, which is then named.
        pool.gates.pop("rtx4060")
        release_mail.set()
        mail.join(5)
        worker.join(5)
        assert status(analysis)["analysed_by"] == ["RTX 4060"]
        assert pool.served == ["rtx4060", "rtx4060"]

    def test_a_failover_moves_the_named_node(self, pool):
        pool.routes[VISION_MODEL] = ["rtx4060", "jagadeesh"]
        pool.failing.add("rtx4060")
        pool.hold_selection = threading.Event()
        release_jagadeesh = pool.hold("jagadeesh")
        analysis = "c" * 32
        worker = booking_call(analysis)

        # RTX 4060 refused the call and the request is looking for another node.
        assert pool.selecting.wait(5)
        assert status(analysis)["state"] == "waiting"
        assert status(analysis)["node"] is None
        # Said as a failover, not left to look like any other wait.
        assert status(analysis)["failed_on"] == ["RTX 4060"]
        assert busy_nodes() == {}

        pool.hold_selection.set()
        assert pool.on_node["jagadeesh"].wait(5)
        assert status(analysis)["node"] == "Jagadeesh"
        assert status(analysis)["failed_on"] == ["RTX 4060"]
        assert busy_nodes() == {"jagadeesh": ["Booking analysis"]}

        release_jagadeesh.set()
        worker.join(5)
        # Only the node that answered analysed it; the one it left is named apart.
        assert status(analysis)["analysed_by"] == ["Jagadeesh"]
        assert status(analysis)["failed_on"] == ["RTX 4060"]

    def test_a_node_that_fails_once_and_answers_later_is_not_a_failover(self, pool):
        pool.routes[VISION_MODEL] = ["rtx4060", "jagadeesh"]
        pool.routes[TEXT_MODEL] = ["rtx4060"]
        pool.failing.add("rtx4060")
        analysis = "1c" * 16

        with ai_activity.booking_analysis(analysis):
            ai_gateway.chat(messages=[{"role": "user", "content": "read"}], model=VISION_MODEL,
                            images=["aW1hZ2U="], workload="payment_screenshot_vision", max_retries=0)
            pool.failing.discard("rtx4060")
            ai_gateway.chat(messages=[{"role": "user", "content": "summarise"}], model=TEXT_MODEL,
                            workload="payment_screenshot_text", max_retries=0)

        assert status(analysis)["analysed_by"] == ["Jagadeesh", "RTX 4060"]
        assert status(analysis)["failed_on"] == []

    def test_between_two_calls_the_last_node_stays_named(self, pool):
        """No flicker to "waiting" for the moments between two model calls."""
        analysis = "d" * 32
        release_text = pool.hold("jagadeesh")
        results: list = []

        def run():
            with ai_activity.booking_analysis(analysis):
                results.append(ai_gateway.chat(
                    messages=[{"role": "user", "content": "read"}], model=VISION_MODEL,
                    images=["aW1hZ2U="], workload="payment_screenshot_vision", max_retries=0,
                ))
                between.set()
                resume.wait(5)
                results.append(ai_gateway.chat(
                    messages=[{"role": "user", "content": "summarise"}], model=TEXT_MODEL,
                    workload="payment_screenshot_text", max_retries=0,
                ))

        between, resume = threading.Event(), threading.Event()
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        assert between.wait(5)
        assert status(analysis)["state"] == "running"
        assert status(analysis)["node"] == "RTX 4060"
        assert busy_nodes() == {}

        resume.set()
        assert pool.on_node["jagadeesh"].wait(5)
        assert status(analysis)["node"] == "Jagadeesh"
        release_text.set()
        worker.join(5)
        assert status(analysis)["analysed_by"] == ["RTX 4060", "Jagadeesh"]

    def test_queued_behind_other_work_after_an_answer_reads_as_waiting(self, pool, monkeypatch):
        monkeypatch.setattr(ai_activity, "QUEUE_GRACE_SECONDS", 0.05)
        release_mail = pool.hold("jagadeesh")
        mail = threading.Thread(target=lambda: ai_gateway.chat(
            messages=[{"role": "user", "content": "mail"}], model=TEXT_MODEL,
            workload="recruitment_mail_primary", max_retries=0,
        ), daemon=True)
        mail.start()
        assert pool.on_node["jagadeesh"].wait(5)

        analysis = "e" * 32
        worker = threading.Thread(target=lambda: _two_calls(analysis), daemon=True)
        worker.start()
        wait_until(lambda: ai_gateway._host_slots._interactive_waiting.get("jagadeesh-ollama") == 1)
        time.sleep(0.1)
        assert status(analysis)["state"] == "waiting"
        assert busy_nodes() == {"jagadeesh": ["Mail analysis"]}

        pool.gates.pop("jagadeesh")
        release_mail.set()
        mail.join(5)
        worker.join(5)
        assert status(analysis)["analysed_by"] == ["RTX 4060", "Jagadeesh"]

    def test_a_node_added_to_the_registry_is_named_without_a_code_change(self, pool, monkeypatch):
        registry = ollama_nodes.configured_nodes() + [
            {"id": "rtx5090", "label": "RTX 5090", "base_url": "http://127.0.0.1:11440"},
        ]
        monkeypatch.setattr(ollama_nodes, "configured_nodes", lambda: registry)
        pool.routes[VISION_MODEL] = ["rtx5090"]
        release = pool.hold("rtx5090")
        analysis = "f" * 32
        worker = booking_call(analysis)
        wait_until(lambda: pool.on_node.get("rtx5090") and pool.on_node["rtx5090"].is_set())
        assert status(analysis)["node"] == "RTX 5090"
        assert busy_nodes() == {"rtx5090": ["Booking analysis"]}
        release.set()
        worker.join(5)

    def test_an_answer_that_fails_validation_still_came_from_that_node(self, pool, monkeypatch):
        monkeypatch.setattr(ai_gateway, "_request_json", lambda *a, **k: {"message": {"content": ""}})
        analysis = "0" * 32
        with ai_activity.booking_analysis(analysis):
            with pytest.raises(ai_gateway.AIGatewayError):
                ai_gateway.chat(messages=[{"role": "user", "content": "x"}], model=VISION_MODEL,
                                workload="payment_screenshot_vision", max_retries=0)
        assert status(analysis)["analysed_by"] == ["RTX 4060"]

    def test_work_outside_a_booking_is_named_from_its_workload(self, pool):
        release = pool.hold("jagadeesh")
        mail = threading.Thread(target=lambda: ai_gateway.chat(
            messages=[{"role": "user", "content": "mail"}], model=TEXT_MODEL,
            workload="recruitment_mail_validator", max_retries=0,
        ), daemon=True)
        mail.start()
        assert pool.on_node["jagadeesh"].wait(5)
        assert busy_nodes() == {"jagadeesh": ["Mail analysis"]}
        release.set()
        mail.join(5)
        assert busy_nodes() == {}

    def test_the_analysis_reaches_the_gateway_through_a_worker_thread(self, pool):
        """How the endpoints run extraction: `asyncio.to_thread` inside the block."""
        analysis = "9" * 32

        async def endpoint():
            with ai_activity.booking_analysis(analysis):
                await asyncio.to_thread(
                    ai_gateway.chat, messages=[{"role": "user", "content": "read"}],
                    model=VISION_MODEL, images=["aW1hZ2U="],
                    workload="interview_screenshot_vision", max_retries=0,
                )

        asyncio.run(endpoint())
        assert status(analysis)["analysed_by"] == ["RTX 4060"]


def _two_calls(analysis: str) -> None:
    with ai_activity.booking_analysis(analysis):
        ai_gateway.chat(messages=[{"role": "user", "content": "read"}], model=VISION_MODEL,
                        images=["aW1hZ2U="], workload="payment_screenshot_vision", max_retries=0)
        ai_gateway.chat(messages=[{"role": "user", "content": "summarise"}], model=TEXT_MODEL,
                        workload="payment_screenshot_text", max_retries=0)


class TestTheAnalysisRecord:
    def setup_method(self):
        ai_activity.reset()

    def test_a_malformed_id_is_replaced_and_still_tracked(self):
        with ai_activity.booking_analysis("<script>") as analysis:
            assert ai_activity.valid_analysis_id(analysis)
            assert analysis != "<script>"
        assert status(analysis)["state"] == "done"

    def test_an_unknown_id_is_unknown(self):
        assert ai_activity.analysis_status("1" * 32) is None

    def test_finished_analyses_expire(self, monkeypatch):
        with ai_activity.booking_analysis("2" * 32):
            pass
        monkeypatch.setattr(ai_activity, "ANALYSIS_TTL_SECONDS", -1)
        assert ai_activity.analysis_status("2" * 32) is None

    def test_the_record_is_bounded(self, monkeypatch):
        monkeypatch.setattr(ai_activity, "ANALYSIS_LIMIT", 3)
        for digit in "34567":
            with ai_activity.booking_analysis(digit * 32):
                pass
        assert ai_activity.analysis_status("3" * 32) is None
        assert ai_activity.analysis_status("7" * 32) is not None

    def test_live_streams_are_capped(self, monkeypatch):
        monkeypatch.setattr(ai_activity, "MAX_STREAMS", 2)
        assert ai_activity.open_stream() and ai_activity.open_stream()
        assert ai_activity.open_stream() is False
        ai_activity.close_stream()
        assert ai_activity.open_stream() is True


# ── the booking endpoints ────────────────────────────────────────────────────


@pytest.fixture()
def booking_app(pool, monkeypatch):
    """The real public booking routes; extraction calls the real gateway."""
    def read_invite(raw, mime):
        result = ai_gateway.chat(
            messages=[{"role": "user", "content": "invite"}], model=VISION_MODEL,
            images=["aW1hZ2U="], workload="interview_screenshot_vision", max_retries=0,
        )
        return {
            "interview_date": "2026-09-22", "start_time": "05:00 PM", "end_time": "06:00 PM",
            "confidence_score": 91, "auto_booking_safe": True,
            "inference_node_id": result.node_id,
        }

    monkeypatch.setattr("features.ollama_invite_extract.extract_interview_invite_with_ollama", read_invite)
    app = FastAPI()
    install_public_slot_routes(app)
    return TestClient(app)


def post_invite(client: TestClient, analysis_id: str | None, out: dict) -> threading.Thread:
    data = {"analysis_id": analysis_id} if analysis_id is not None else {}

    def run():
        out["response"] = client.post(
            "/public/slots/extract-invite-ai",
            files={"file": ("invite.png", b"png-bytes", "image/png")}, data=data,
        )
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


class TestTheBookingEndpoints:
    def test_the_invite_response_names_the_node_that_read_it(self, booking_app, pool):
        analysis = "ab" * 16
        out: dict = {}
        post_invite(booking_app, analysis, out).join(10)
        body = out["response"].json()
        assert body["analysis"] == {
            "analysis_id": analysis, "state": "done", "node": "RTX 4060", "analysed_by": ["RTX 4060"],
            "failed_on": [],
        }
        assert body["data"]["inference_node_id"] == "rtx4060"

    def test_the_status_endpoint_follows_the_invite_while_it_is_read(self, booking_app, pool):
        pool.routes[VISION_MODEL] = ["our_machine"]
        release = pool.hold("our_machine")
        analysis = "cd" * 16
        out: dict = {}
        worker = post_invite(booking_app, analysis, out)
        assert pool.on_node["our_machine"].wait(10)

        live = booking_app.get(f"/public/slots/analysis/{analysis}").json()["analysis"]
        assert live["state"] == "running" and live["node"] == "Praveen"

        release.set()
        worker.join(10)
        done = booking_app.get(f"/public/slots/analysis/{analysis}").json()["analysis"]
        assert done["state"] == "done" and done["analysed_by"] == ["Praveen"]

    def test_the_event_stream_pushes_waiting_the_node_and_done(self, booking_app, pool):
        release = pool.hold("jagadeesh")
        pool.routes[VISION_MODEL] = ["jagadeesh"]
        analysis = "ef" * 16
        stream: dict = {}

        def listen():
            stream["response"] = booking_app.get(f"/public/slots/analysis/{analysis}/events")
        listener = threading.Thread(target=listen, daemon=True)
        listener.start()
        time.sleep(0.5)  # the page opens its stream before the upload lands

        out: dict = {}
        worker = post_invite(booking_app, analysis, out)
        assert pool.on_node["jagadeesh"].wait(10)
        time.sleep(0.8)  # a few stream ticks while Jagadeesh holds the call
        release.set()
        worker.join(10)
        listener.join(10)

        response = stream["response"]
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        events = [json.loads(line[len("data: "):]) for line in response.text.splitlines()
                  if line.startswith("data: ")]
        states = [(event["state"], event["node"]) for event in events]
        assert states[0] == ("waiting", None)
        assert ("running", "Jagadeesh") in states
        assert states[-1] == ("done", "Jagadeesh")
        assert events[-1]["analysed_by"] == ["Jagadeesh"]

    def test_an_upload_without_an_id_is_still_a_booking_analysis(self, booking_app, pool):
        release = pool.hold("rtx4060")
        out: dict = {}
        worker = post_invite(booking_app, None, out)
        assert pool.on_node["rtx4060"].wait(10)
        assert busy_nodes() == {"rtx4060": ["Booking analysis"]}
        release.set()
        worker.join(10)
        analysis = out["response"].json()["analysis"]
        assert ai_activity.valid_analysis_id(analysis["analysis_id"])
        assert analysis["analysed_by"] == ["RTX 4060"]

    def test_a_refused_payment_still_reports_which_node_read_it(self, booking_app, pool, monkeypatch):
        """The JSON error path carries the analysis too."""
        pool.routes[VISION_MODEL] = ["our_machine"]

        def verify(raw, mime, **kwargs):
            ai_gateway.chat(messages=[{"role": "user", "content": "receipt"}], model=VISION_MODEL,
                            images=["aW1hZ2U="], workload="payment_screenshot_vision", max_retries=0)
            return {"booking_eligible": False, "verification_state": "UNREGISTERED_RECEIVER",
                    "deterministic_reasons": ["The receiver is not a registered account."]}

        monkeypatch.setattr("features.payment_verification_engine.verify_payment_screenshot", verify)
        monkeypatch.setattr(
            "features.candidate_store.public_booking_payment_requirement",
            lambda **kwargs: {"amount_due": 5000, "payment_required": True},
        )
        analysis = "12" * 16
        response = booking_app.post(
            "/public/slots/payment-proof",
            data={"name": "Probe", "service_type": "round_wise", "phone": "9000000000",
                  "analysis_id": analysis},
            files={"files": ("receipt.png", b"receipt", "image/png")},
        )
        assert response.status_code == 400
        body = response.json()
        assert "not a registered account" in body["message"]
        assert body["analysis"]["analysed_by"] == ["Praveen"]
        assert body["analysis"]["analysis_id"] == analysis

    def test_a_malformed_status_id_is_refused(self, booking_app):
        assert booking_app.get("/public/slots/analysis/not-an-id").status_code == 404
        assert booking_app.get("/public/slots/analysis/not-an-id/events").status_code == 404

    def test_an_unknown_id_reads_as_waiting_and_says_nothing_else(self, booking_app):
        body = booking_app.get(f"/public/slots/analysis/{'3' * 32}").json()
        assert body["analysis"] == {"analysis_id": "3" * 32, "state": "waiting", "node": None,
                                    "analysed_by": [], "failed_on": []}

    def test_too_many_streams_are_refused_without_touching_the_upload(self, booking_app, monkeypatch):
        monkeypatch.setattr(ai_activity, "MAX_STREAMS", 0)
        assert booking_app.get(f"/public/slots/analysis/{'4' * 32}/events").status_code == 429


# ── the AI nodes panel ───────────────────────────────────────────────────────


class FakeSocket:
    def __init__(self):
        self.frames: list[dict] = []

    async def send_json(self, payload):
        self.frames.append(payload)


class TestTheNodesPanelFeed:
    def test_the_nodes_endpoints_carry_what_each_node_is_serving(self, pool, monkeypatch):
        from core import recruitment_mail_api

        monkeypatch.setenv("AI_INTERVIEW_OFFER_TRACKING_ENABLED", "true")
        monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
        monkeypatch.setattr(recruitment_mail_api.store, "ensure_schema", lambda: None)
        monkeypatch.setattr(recruitment_mail_api, "_node_status", lambda node_id: {
            "id": node_id, "label": ollama_nodes.node(node_id)["label"], "endpoint_reachable": True,
        })
        app = FastAPI()
        recruitment_mail_api.install_recruitment_mail_routes(app)
        client = TestClient(app)

        release = pool.hold("rtx4060")
        worker = booking_call("56" * 16)
        assert pool.on_node["rtx4060"].wait(5)

        nodes = client.get("/api/ai-recruitment/ollama/nodes").json()
        by_id = {node["id"]: node for node in nodes["nodes"]}
        assert [item["label"] for item in by_id["rtx4060"]["activity"]] == ["Booking analysis"]
        assert by_id["jagadeesh"]["activity"] == [] and by_id["our_machine"]["activity"] == []

        activity = client.get("/api/ai-recruitment/ollama/activity").json()["activity"]
        assert list(activity["nodes"]) == ["rtx4060"]
        assert activity["boot"] == ai_activity.BOOT_ID

        release.set()
        worker.join(5)
        assert client.get("/api/ai-recruitment/ollama/activity").json()["activity"]["nodes"] == {}

    def test_connected_dashboards_are_pushed_each_change_and_nothing_else(self, pool, monkeypatch):
        socket = FakeSocket()
        monkeypatch.setitem(recruitment_realtime._connections, socket, {"username": "admin"})
        release = pool.hold("jagadeesh")

        async def scenario():
            pusher = asyncio.create_task(recruitment_realtime._push_node_activity(poll_seconds=0.01))
            await asyncio.sleep(0.05)
            assert socket.frames == []  # nothing changed, nothing sent

            worker = booking_call("78" * 16, model=TEXT_MODEL, workload="payment_screenshot_text")
            await asyncio.to_thread(pool.on_node["jagadeesh"].wait, 5)
            await asyncio.sleep(0.05)
            busy = [frame for frame in socket.frames if frame["nodes"]]
            assert busy and busy[-1]["event"] == "ai_node_activity"
            assert [item["label"] for item in busy[-1]["nodes"]["jagadeesh"]] == ["Booking analysis"]
            assert "event_id" not in busy[-1]  # live state, never a replayable alert

            release.set()
            await asyncio.to_thread(worker.join, 5)
            await asyncio.sleep(0.05)
            assert socket.frames[-1]["nodes"] == {}
            sent = len(socket.frames)
            await asyncio.sleep(0.05)
            assert len(socket.frames) == sent

            pusher.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pusher

        asyncio.run(scenario())

    def test_a_dashboard_that_connects_later_is_not_sent_a_snapshot_unasked(self, pool, monkeypatch):
        """Its opening frames stay what they were; it reads the snapshot itself."""
        release = pool.hold("rtx4060")
        worker = booking_call("9a" * 16)
        assert pool.on_node["rtx4060"].wait(5)
        socket = FakeSocket()

        async def scenario():
            pusher = asyncio.create_task(recruitment_realtime._push_node_activity(poll_seconds=0.01))
            await asyncio.sleep(0.05)
            recruitment_realtime._connections[socket] = {"username": "admin"}
            try:
                await asyncio.sleep(0.05)
                assert socket.frames == []
            finally:
                recruitment_realtime._connections.pop(socket, None)
                pusher.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pusher

        asyncio.run(scenario())
        release.set()
        worker.join(5)
