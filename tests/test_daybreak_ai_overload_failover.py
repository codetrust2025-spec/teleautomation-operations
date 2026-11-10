"""Daybreak headlines keep flowing when RTX 4060 is overloaded (7 Nov 2026).

A benchmark loaded qwen3.8:27b, then qwen3.6:35b on RTX 4060. Its health probe
still answered, so every headline went there: one account waited out the 300 s
request timeout, the other failed after the 30 s queue wait with no log line,
and RTX 3050 stayed idle. The 12:43 IST manual round lost all 27 accounts and
the 14:30 round needed all three attempts. These tests drive ``ai_gateway.chat``
and the real route, with only the Ollama transport and probes replaced.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import daybreak_integration as integration
from core import ai_gateway, ollama_nodes

MSG = [{"role": "user", "content": "fixture"}]
RTX, BACKUP = "rtx4060", "jagadeesh"


@pytest.fixture
def pool(monkeypatch, tmp_path):
    """Two healthy Daybreak machines; each test sets what is resident and how each answers."""
    monkeypatch.setenv("OLLAMA_NODE_STATE_FILE", str(tmp_path / "nodes.json"))
    monkeypatch.setattr(ollama_nodes, "candidate_order", lambda model=None: ["our_machine", RTX, BACKUP])
    state = {"offline": set(), "resident": {RTX: ["qwen2.5:7b"], BACKUP: ["qwen2.5:7b"]},
             "ps_calls": [], "requests": [], "answer": {}}
    monkeypatch.setattr(ollama_nodes, "node_health", lambda node_id, **kw: {
        "endpoint_reachable": node_id not in state["offline"], "model_available": True, "error": "fixture"})
    monkeypatch.setattr(ai_gateway, "health", lambda **kw: {
        "endpoint_reachable": kw["node_id"] not in state["offline"], "model_available": True,
        "error_message": "fixture offline", "error_code": "OLLAMA_UNAVAILABLE"})

    def ps(node_id, path, **kw):
        assert path == "/api/ps"
        state["ps_calls"].append(node_id)
        loaded = state["resident"].get(node_id)
        if loaded is None:
            raise RuntimeError("fixture: /api/ps unreadable")
        return {"models": [{"name": name} for name in loaded]}
    monkeypatch.setattr(ollama_nodes, "_request", ps)
    monkeypatch.setattr(ollama_nodes, "production_models",
                        lambda: {"qwen2.5:7b", "gemma2:2b", "qwen3-vl:8b-instruct"})
    url_node = {ollama_nodes.base_url_for(n): n for n in (RTX, BACKUP, "our_machine")}

    def request(*args, **kwargs):
        node = url_node[kwargs["base_url"]]
        state["requests"].append((node, kwargs["response_timeout"]))
        behaviour = state["answer"].get(node)
        if behaviour is not None:
            behaviour()
        # A valid rewrite of BODY's headline: it keeps the exact job title and experience and adds
        # no word absent from the approved facts, so these tests exercise node failover rather than
        # the headline rules. Which node answered is asserted from the gateway result, not the text.
        return {"message": {"content": "React developer, 6 years"}}
    monkeypatch.setattr(ai_gateway, "_request_json", request)
    ollama_nodes.reset_breakers()
    yield state
    ollama_nodes.reset_breakers()


def daybreak(**kw):
    return ai_gateway.chat(messages=MSG, model="qwen2.5:7b", workload="daybreak_headline", max_retries=0, **kw)


def timeout():
    raise ai_gateway.AIGatewayError("fixture timeout", code="OLLAMA_REQUEST_TIMEOUT")


# --- overload: a benchmark on RTX 4060 ------------------------------------

def test_a_benchmark_model_on_rtx_sends_the_headline_to_rtx3050(pool):
    pool["resident"][RTX] = ["qwen3.6:35b"]
    result = daybreak()
    assert result.node_id == BACKUP
    assert [node for node, _ in pool["requests"]] == [BACKUP], "nothing was queued behind the benchmark"
    assert not ollama_nodes.in_cooldown(RTX), "busy is not broken: the breaker is untouched"
    assert ollama_nodes.breaker_state(RTX).get("failures", 0) == 0


@pytest.mark.parametrize("resident", [
    ["qwen2.5:7b"], ["gemma2:2b", "qwen2.5:7b"], ["qwen3-vl:8b-instruct"], ["qwen2.5:7b:latest"], [],
])
def test_production_models_never_count_as_a_benchmark(pool, resident):
    pool["resident"][RTX] = resident
    assert daybreak().node_id == RTX


def test_an_unreadable_load_report_does_not_take_rtx_out(pool):
    pool["resident"][RTX] = None
    assert daybreak().node_id == RTX


def test_a_benchmark_on_the_last_machine_left_is_still_tried(pool):
    """Busy beats nothing: with RTX 3050 offline the request still goes to RTX 4060."""
    pool["resident"][RTX] = ["qwen3.8:27b"]
    pool["offline"].add(BACKUP)
    assert daybreak().node_id == RTX


def test_other_workloads_are_not_rerouted_by_the_daybreak_policy(pool):
    pool["resident"]["our_machine"] = ["qwen3.6:35b"]
    result = ai_gateway.chat(messages=MSG, model="qwen2.5:7b", workload="generic", max_retries=0)
    assert result.node_id == "our_machine"
    assert pool["ps_calls"] == []


# --- recovery ---------------------------------------------------------------

def test_rtx_takes_headlines_again_as_soon_as_the_benchmark_is_unloaded(pool):
    pool["resident"][RTX] = ["qwen3.6:35b"]
    assert daybreak().node_id == BACKUP
    pool["resident"][RTX] = ["qwen2.5:7b"]
    assert daybreak().node_id == RTX


def test_a_timing_out_rtx_costs_each_request_one_bounded_try_then_recovers(pool):
    # A reachable RTX 4060 passes its health probe, which resets the breaker, so
    # it stays first in line. The bounded per-machine timeout is what keeps each
    # request quick; the moment it answers again it serves the next request.
    pool["answer"][RTX] = timeout
    for _ in range(3):
        assert daybreak().node_id == BACKUP
    assert [node for node, _ in pool["requests"]] == [RTX, BACKUP] * 3
    assert all(limit == 45 for node, limit in pool["requests"] if node == RTX)
    pool["answer"].pop(RTX)
    assert daybreak().node_id == RTX


# --- timeouts ---------------------------------------------------------------

def test_rtx_gets_a_bounded_time_while_rtx3050_is_left(pool, monkeypatch):
    monkeypatch.setenv("OLLAMA_REQUEST_TIMEOUT", "300")
    pool["answer"][RTX] = timeout
    result = daybreak()
    assert result.node_id == BACKUP
    (first, rtx_timeout), (second, backup_timeout) = pool["requests"]
    assert (first, second) == (RTX, BACKUP)
    assert rtx_timeout == ai_gateway.DAYBREAK_NODE_RESPONSE_TIMEOUT_SECONDS == 45
    assert backup_timeout == 300, "the last machine keeps the normal limit"


def test_the_last_machine_is_bounded_by_the_request_deadline(pool, monkeypatch):
    monkeypatch.setenv("OLLAMA_REQUEST_TIMEOUT", "300")
    pool["answer"][RTX] = timeout
    daybreak(deadline_monotonic=time.monotonic() + 60)
    assert pool["requests"][1][1] <= 60


def test_both_machines_timing_out_is_reported_not_hung(pool):
    pool["answer"][RTX] = pool["answer"][BACKUP] = timeout
    with pytest.raises(ai_gateway.AIGatewayError) as failed:
        daybreak(deadline_monotonic=time.monotonic() + 60)
    assert failed.value.code == "OLLAMA_REQUEST_TIMEOUT"
    assert [node for node, _ in pool["requests"]] == [RTX, BACKUP]


def test_the_budget_runs_out_cleanly(pool):
    with pytest.raises(ai_gateway.AIGatewayError) as failed:
        daybreak(deadline_monotonic=time.monotonic() - 1)
    assert failed.value.code == "OLLAMA_REQUEST_TIMEOUT"
    assert pool["requests"] == []


# --- failover under concurrency (one slot per machine) ---------------------

def test_a_second_account_moves_to_rtx3050_instead_of_failing_in_the_queue(pool, monkeypatch):
    """What happened at 14:30: two accounts, one slot on a slow RTX 4060."""
    monkeypatch.setenv("DAYBREAK_NODE_QUEUE_WAIT_SECONDS", "0.2")
    holding, release = threading.Event(), threading.Event()

    def slow():
        holding.set()
        assert release.wait(10)
    pool["answer"][RTX] = slow
    results = {}
    first = threading.Thread(target=lambda: results.setdefault("first", daybreak()))
    first.start()
    assert holding.wait(5)
    try:
        started = time.monotonic()
        results["second"] = daybreak()
        waited = time.monotonic() - started
    finally:
        release.set()
        first.join(10)
    assert results["first"].node_id == RTX
    assert results["second"].node_id == BACKUP
    assert waited < 5, "moved on after the short Daybreak queue wait, not the shared 30 s"
    assert not ollama_nodes.in_cooldown(RTX)


def test_a_full_queue_on_the_last_machine_still_fails_closed(pool, monkeypatch):
    monkeypatch.setenv("AI_RECRUITMENT_QUEUE_WAIT_SECONDS", "0.2")
    pool["offline"].add(BACKUP)
    holding, release = threading.Event(), threading.Event()
    pool["answer"][RTX] = lambda: (holding.set(), release.wait(10))
    first = threading.Thread(target=daybreak)
    first.start()
    assert holding.wait(5)
    try:
        with pytest.raises(ai_gateway.AIGatewayError) as failed:
            daybreak()
        assert failed.value.code == "OLLAMA_QUEUE_TIMEOUT"
    finally:
        release.set()
        first.join(10)


def test_both_machines_busy_fails_after_short_waits_not_minutes(pool, monkeypatch):
    monkeypatch.setenv("DAYBREAK_NODE_QUEUE_WAIT_SECONDS", "0.2")
    monkeypatch.setenv("AI_RECRUITMENT_QUEUE_WAIT_SECONDS", "0.3")
    holds = {n: (threading.Event(), threading.Event()) for n in (RTX, BACKUP)}

    def blocker(node):
        def run():
            holds[node][0].set()
            holds[node][1].wait(10)
        return run
    pool["answer"].update({RTX: blocker(RTX), BACKUP: blocker(BACKUP)})
    t1 = threading.Thread(target=daybreak)
    t1.start()
    assert holds[RTX][0].wait(5)
    t2 = threading.Thread(target=daybreak)  # finds RTX 4060 full and takes RTX 3050
    t2.start()
    assert holds[BACKUP][0].wait(5)
    started = time.monotonic()
    try:
        with pytest.raises(ai_gateway.AIGatewayError) as failed:
            daybreak()
        assert failed.value.code == "OLLAMA_QUEUE_TIMEOUT"
        assert time.monotonic() - started < 3
    finally:
        for _, release in holds.values():
            release.set()
        t1.join(10)
        t2.join(10)


# --- the route ---------------------------------------------------------------

def _client(monkeypatch):
    monkeypatch.setenv("DAYBREAK_HEADLINE_TOKEN", "d" * 48)
    app = FastAPI()
    app.include_router(integration.router)
    return TestClient(app)


BODY = {"candidate_ref": "candidate-a", "previous_headline": "React developer | 6 years",
        "facts": {"target_role": "React developer", "experience": "6 years",
                  "skills": ["React.js"], "profile_facts": ""},
        "max_length": 250}
AUTH = {"Authorization": "Bearer " + "d" * 48}


def test_the_route_reports_rtx3050_when_rtx_is_benchmarking(pool, monkeypatch):
    pool["resident"][RTX] = ["qwen3.6:35b"]
    response = _client(monkeypatch).post("/api/integrations/daybreak/headline", headers=AUTH, json=BODY)
    assert response.status_code == 200, response.text
    assert response.json()["node_id"] == BACKUP  # the stable ID Daybreak authorizes saves on


def test_the_route_gives_every_attempt_one_budget_inside_the_worker_limit(monkeypatch):
    seen = []

    class Result:
        content, node_id, node_label, model, duration_ms = "x", RTX, "RTX 4060", "qwen2.5:7b", 1

    def chat(**kw):
        seen.append(kw["deadline_monotonic"] - time.monotonic())
        return Result()
    monkeypatch.setattr(ai_gateway, "chat", chat)
    monkeypatch.setattr(ai_gateway, "chat_structured", chat)
    _client(monkeypatch).post("/api/integrations/daybreak/headline", headers=AUTH, json=BODY)
    assert seen and all(0 < left <= integration.HEADLINE_BUDGET_SECONDS for left in seen)
    assert integration.HEADLINE_BUDGET_SECONDS < 180, "the Daybreak worker gives up at 180 s"
