"""A candidate at the booking form is never queued behind background mail work.

On 2026-09-14 a round-wise candidate could not book an interview for the next
day. His ₹500 payment screenshot came back "Ollama Vision could not extract a
usable payment result" and his Gmail invite "does not look like an interview
invite". Neither image was the problem. Production logged OLLAMA_QUEUE_TIMEOUT
for every one of those vision calls: the gateway had one slot for the whole
service, mail analysis held it for about 90 seconds a call from a backlog of
over a thousand messages, and a booking-form request gave up after its
30-second queue wait -- although mail runs on one machine and the vision model
on another.

These drive the real gateway `chat()` with the nodes, the health probe and the
HTTP call replaced, so the queueing under test is the production code.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest

from core import ai_gateway, ollama_nodes

ROOT = Path(__file__).resolve().parents[1]

TEXT_MODEL = "qwen2.5:7b"
VISION_MODEL = "qwen3-vl:8b-instruct"
ROUTES = {TEXT_MODEL: ["jagadeesh"], VISION_MODEL: ["rtx4060"]}


@pytest.fixture()
def gateway(monkeypatch):
    """Two machines, one model each, and a mail call that blocks until told."""
    release_mail = threading.Event()
    mail_running = threading.Event()
    calls: list[str] = []
    failures: list[str] = []

    monkeypatch.setenv("AI_RECRUITMENT_QUEUE_WAIT_SECONDS", "1")
    monkeypatch.setattr(ai_gateway, "_host_slots", ai_gateway._HostQueue(1))
    monkeypatch.setattr(ollama_nodes, "candidate_order", lambda model=None: list(ROUTES[model]))
    monkeypatch.setattr(
        ollama_nodes, "select_available_node",
        lambda model=None, timeout=None, exclude=None: {"node_id": ROUTES[model][0]},
    )
    monkeypatch.setattr(ollama_nodes, "base_url_for", lambda node: f"http://{node}")
    monkeypatch.setattr(ollama_nodes, "inference_host_id", lambda node: node)
    monkeypatch.setattr(ollama_nodes, "node", lambda node: {"label": node})
    monkeypatch.setattr(ollama_nodes, "record_success", lambda node: None)
    monkeypatch.setattr(ollama_nodes, "record_failure", lambda node, message=None: failures.append(node))
    monkeypatch.setattr(ai_gateway.ollama_status, "record_request_success", lambda *a, **k: None)
    monkeypatch.setattr(ai_gateway.ollama_status, "record_request_failure", lambda *a, **k: None)
    monkeypatch.setattr(ai_gateway, "health", lambda **kwargs: {
        "endpoint_reachable": True, "model_available": True,
        "error_message": "", "error_code": "",
    })

    def request(path, method="GET", body=None, connect_timeout=None, response_timeout=None, base_url=None):
        calls.append(base_url)
        if base_url == "http://jagadeesh":
            mail_running.set()
            release_mail.wait(10)
        return {"message": {"content": "ok"}}

    monkeypatch.setattr(ai_gateway, "_request_json", request)

    class Gateway:
        def __init__(self):
            self.release_mail = release_mail
            self.mail_running = mail_running
            self.calls = calls
            self.failures = failures

        def start_mail_analysis(self, workload="recruitment_mail_primary"):
            worker = threading.Thread(
                target=lambda: ai_gateway.chat(messages=[{"role": "user", "content": "mail"}],
                                               model=TEXT_MODEL, workload=workload),
                daemon=True,
            )
            worker.start()
            assert mail_running.wait(5), "the mail call never started"
            return worker

    yield Gateway()
    release_mail.set()


class TestTheIncident:
    def test_a_payment_screenshot_is_read_while_mail_analysis_runs_elsewhere(self, gateway):
        worker = gateway.start_mail_analysis()
        started = time.monotonic()
        result = ai_gateway.chat(
            messages=[{"role": "user", "content": "receipt"}], model=VISION_MODEL,
            images=["aW1hZ2U="], workload="payment_screenshot_vision",
        )
        assert result.content == "ok"
        assert result.node_id == "rtx4060"
        # Well inside the 1-second queue wait: it did not queue at all.
        assert time.monotonic() - started < 1
        gateway.release_mail.set()
        worker.join(5)

    def test_an_interview_invite_is_read_while_mail_analysis_runs_elsewhere(self, gateway):
        worker = gateway.start_mail_analysis()
        result = ai_gateway.chat(
            messages=[{"role": "user", "content": "invite"}], model=VISION_MODEL,
            images=["aW1hZ2U="], workload="interview_screenshot_vision",
        )
        assert result.node_id == "rtx4060"
        gateway.release_mail.set()
        worker.join(5)

    def test_one_machine_still_takes_one_request_at_a_time(self, gateway):
        """The concurrency bound moved from the service to each machine; it did
        not loosen for any machine."""
        worker = gateway.start_mail_analysis()
        with pytest.raises(ai_gateway.AIGatewayError) as raised:
            ai_gateway.chat(messages=[{"role": "user", "content": "second mail"}],
                            model=TEXT_MODEL, workload="recruitment_mail_validator")
        assert raised.value.code == "OLLAMA_QUEUE_TIMEOUT"
        assert gateway.calls.count("http://jagadeesh") == 1
        gateway.release_mail.set()
        worker.join(5)

    def test_waiting_for_the_queue_never_cools_the_node(self, gateway):
        worker = gateway.start_mail_analysis()
        with pytest.raises(ai_gateway.AIGatewayError):
            ai_gateway.chat(messages=[{"role": "user", "content": "second mail"}],
                            model=TEXT_MODEL, workload="recruitment_mail_validator")
        assert gateway.failures == []
        gateway.release_mail.set()
        worker.join(5)

    def test_the_slot_is_released_when_the_call_fails(self, gateway, monkeypatch):
        def refuse(*args, **kwargs):
            raise ai_gateway.AIGatewayError("down", code="OLLAMA_CONNECTION_FAILED")

        monkeypatch.setattr(ai_gateway, "_request_json", refuse)
        with pytest.raises(ai_gateway.AIGatewayError):
            ai_gateway.chat(messages=[{"role": "user", "content": "x"}], model=VISION_MODEL,
                            workload="payment_screenshot_vision", max_retries=0)
        assert ai_gateway._host_slots.acquire("rtx4060", interactive=False, timeout=0.1)


class TestPriorityOnOneMachine:
    def _wait_until(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("condition never held")

    def test_a_waiting_interactive_request_is_served_before_waiting_background_work(self):
        queue = ai_gateway._HostQueue(1)
        assert queue.acquire("rtx4060", interactive=False, timeout=1)
        order: list[str] = []

        def take(name, interactive):
            if queue.acquire("rtx4060", interactive=interactive, timeout=5):
                order.append(name)
                time.sleep(0.05)
                queue.release("rtx4060")

        background = threading.Thread(target=take, args=("mail attachment", False))
        background.start()
        time.sleep(0.1)  # the background request is queued first
        interactive = threading.Thread(target=take, args=("booking form", True))
        interactive.start()
        self._wait_until(lambda: queue._interactive_waiting.get("rtx4060") == 1)

        queue.release("rtx4060")
        background.join(5)
        interactive.join(5)
        assert order == ["booking form", "mail attachment"]

    def test_an_interactive_request_that_gives_up_stops_holding_background_back(self):
        queue = ai_gateway._HostQueue(1)
        assert queue.acquire("rtx4060", interactive=False, timeout=1)
        assert queue.acquire("rtx4060", interactive=True, timeout=0.1) is False
        queue.release("rtx4060")
        assert queue.acquire("rtx4060", interactive=False, timeout=0.1)

    def test_machines_do_not_share_a_slot(self):
        queue = ai_gateway._HostQueue(1)
        assert queue.acquire("jagadeesh", interactive=False, timeout=0.1)
        assert queue.acquire("rtx4060", interactive=False, timeout=0.1)
        assert queue.acquire("jagadeesh", interactive=False, timeout=0.1) is False


class TestWhatCountsAsInteractive:
    @pytest.mark.parametrize("module", [
        "features/ollama_payment_extract.py",
        "features/ollama_invite_extract.py",
        "features/ollama_resume_extract.py",
    ])
    def test_every_booking_and_upload_workload_is_interactive(self, module):
        source = (ROOT / module).read_text(encoding="utf-8")
        workloads = set(re.findall(r'workload="([a-z_]+)"', source))
        assert workloads, f"no workloads found in {module}"
        assert workloads <= ai_gateway.INTERACTIVE_WORKLOADS

    def test_mail_analysis_is_background(self):
        source = (ROOT / "services" / "recruitment_mail_agent.py").read_text(encoding="utf-8")
        for workload in re.findall(r'workload="([a-z_]+)"', source):
            assert workload not in ai_gateway.INTERACTIVE_WORKLOADS
