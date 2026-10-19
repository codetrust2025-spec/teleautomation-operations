import json

import pytest

from core import ai_gateway
from core import ollama_nodes


def test_health_reports_model_availability(monkeypatch):
    monkeypatch.setattr(
        ai_gateway,
        "_request_json",
        lambda *args, **kwargs: {"models": [{"name": "qwen3.6:latest"}]},
    )
    result = ai_gateway.health(model="qwen3.6")
    assert result["endpoint_reachable"] is True
    assert result["model_available"] is True
    assert result["status"] == "healthy"
    assert result["diagnostic_status"] == "AVAILABLE"
    assert result["serviceReachable"] is True
    assert result["primaryModel"] == "qwen3.6"


def test_health_classifies_reverse_tunnel_failure(monkeypatch):
    monkeypatch.setenv("OLLAMA_EXPECT_REVERSE_SSH_TUNNEL", "true")
    monkeypatch.setattr(
        ai_gateway,
        "_request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ai_gateway.AIGatewayError(
                "Ollama is running on the laptop, but the VPS reverse SSH tunnel is unavailable.",
                code="REVERSE_SSH_TUNNEL_UNAVAILABLE",
            )
        ),
    )
    result = ai_gateway.health(model="qwen3.6")
    assert result["endpoint_reachable"] is False
    assert result["error_code"] == "REVERSE_SSH_TUNNEL_UNAVAILABLE"
    assert result["diagnostic_status"] == "TUNNEL_UNREACHABLE"


def test_health_separates_missing_primary_from_reachable_service(monkeypatch):
    monkeypatch.setattr(
        ai_gateway,
        "_request_json",
        lambda *args, **kwargs: {"models": [{"name": "qwen2.5:7b"}]},
    )
    result = ai_gateway.health(model="gemma4:12b")
    assert result["serviceReachable"] is True
    assert result["primaryModelAvailable"] is False
    assert result["diagnostic_status"] == "PRIMARY_MODEL_MISSING"


def test_invalid_node_url_is_configuration_error(monkeypatch):
    """A malformed endpoint is a configuration fault, not a service outage.

    The variable moved: node URLs now come from each node's own
    OLLAMA_NODE_*_URL, never from the generic OLLAMA_BASE_URL, so the fault is
    injected where the node is actually addressed. The distinction under test is
    unchanged — an operator must not go hunting for a dead laptop when the real
    problem is a typo in configuration.
    """
    # Injected into the node that is primary under normal priority.
    monkeypatch.setenv("OLLAMA_NODE_RTX4060_URL", "not-a-url")
    result = ai_gateway.health(model="gemma4:12b")
    assert result["diagnostic_status"] == "CONFIGURATION_ERROR"
    assert result["serviceReachable"] is False


def test_a_generic_base_url_can_no_longer_misconfigure_a_node(monkeypatch):
    """OLLAMA_BASE_URL is inert for node addressing — the production defect."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "not-a-url")
    result = ai_gateway.health(model="gemma4:12b")
    assert result["diagnostic_status"] != "CONFIGURATION_ERROR"


def test_missing_model_stops_before_chat(monkeypatch):
    monkeypatch.setattr(
        ai_gateway,
        "health",
        lambda **kwargs: {
            "endpoint_reachable": True,
            "model_available": False,
            "error_message": "missing",
            "error_code": "OLLAMA_MODEL_NOT_FOUND",
        },
    )
    with pytest.raises(ai_gateway.AIGatewayError) as error:
        ai_gateway.chat_structured(messages=[], schema={}, model="missing")
    assert error.value.code == "OLLAMA_MODEL_NOT_FOUND"


def test_timeout_retries_three_times_then_falls_back(monkeypatch):
    calls = []
    monkeypatch.setenv("OLLAMA_MAX_RETRIES", "3")
    monkeypatch.setattr(ai_gateway, "health", lambda **kwargs: {
        "endpoint_reachable": True, "model_available": True,
        "error_message": None, "error_code": None,
    })
    monkeypatch.setattr(ai_gateway.time, "sleep", lambda seconds: None)

    def fail(*args, **kwargs):
        calls.append(kwargs)
        raise ai_gateway.AIGatewayError("timed out", code="OLLAMA_REQUEST_TIMEOUT")

    monkeypatch.setattr(ai_gateway, "_request_json", fail)
    with pytest.raises(ai_gateway.AIGatewayError) as error:
        ai_gateway.chat_structured(messages=[{"role": "user", "content": "x"}], schema={}, model="qwen3.6")
    assert error.value.code == "OLLAMA_REQUEST_TIMEOUT"
    # A timeout now names the machine, so the request moves rather than
    # spending its whole retry budget on the node that just timed out. Each
    # node gets one attempt while somewhere else is left to try; the last one
    # keeps the retries, because on a pool of one, backoff is still the best
    # move available for a transient timeout.
    nodes = ollama_nodes.candidate_order("qwen3.6")
    assert len(calls) == (len(nodes) - 1) + 4
    assert len(calls) > 4, "failover must add attempts, not replace them"


def test_invalid_json_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_gateway, "health", lambda **kwargs: {
        "endpoint_reachable": True, "model_available": True,
        "error_message": None, "error_code": None,
    })

    def fail(*args, **kwargs):
        calls.append(1)
        raise ai_gateway.AIGatewayError("invalid", code="OLLAMA_INVALID_JSON")

    monkeypatch.setattr(ai_gateway, "_request_json", fail)
    with pytest.raises(ai_gateway.AIGatewayError) as error:
        ai_gateway.chat_structured(messages=[{"role": "user", "content": "x"}], schema={}, model="qwen3.6")
    assert error.value.code == "OLLAMA_INVALID_JSON"
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (500, '{"error":"failed to load model runner"}', "OLLAMA_MODEL_LOAD_FAILED"),
        (400, '{"error":"invalid format"}', "OLLAMA_BAD_REQUEST"),
        (404, '{"error":"model not found"}', "OLLAMA_MODEL_NOT_FOUND"),
        (500, '{"error":"unexpected"}', "OLLAMA_INTERNAL_ERROR"),
    ],
)
def test_http_error_classification(status, body, code):
    assert ai_gateway._http_error_code(status, body) == code


def test_tag_refresh_does_not_hide_a_newer_inference_failure():
    from core import ollama_status

    ollama_status.record_request_success(10)
    ollama_status.record_request_failure("OLLAMA_MODEL_LOAD_FAILED", "load failed")
    refreshed = ollama_status.record_health(
        status="healthy", endpoint_reachable=True, model_available=True,
        error_code=None, error_message=None,
    )
    assert refreshed["status"] == "degraded"
    assert refreshed["error_code"] == "OLLAMA_MODEL_LOAD_FAILED"

    ollama_status.record_request_success(10)
    assert ollama_status.snapshot()["status"] == "healthy"


def test_structured_chat_validates_and_normalizes_json(monkeypatch):
    monkeypatch.setattr(ai_gateway, "health", lambda **kwargs: {
        "endpoint_reachable": True, "model_available": True,
        "error_message": None, "error_code": None,
    })
    monkeypatch.setattr(
        ai_gateway,
        "_request_json",
        lambda *args, **kwargs: {"message": {"content": '{"ok": true}'}},
    )
    result = ai_gateway.chat_structured(
        messages=[{"role": "user", "content": "test"}],
        schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        model="qwen2.5:7b",
        workload="test",
    )
    assert json.loads(result.content) == {"ok": True}


def test_chat_sends_explicit_think_setting(monkeypatch):
    captured = {}
    monkeypatch.setattr(ai_gateway, "health", lambda **kwargs: {
        "endpoint_reachable": True, "model_available": True,
        "error_message": None, "error_code": None,
    })

    def respond(*args, **kwargs):
        captured.update(json.loads(kwargs["body"]))
        return {"message": {"content": '{"ok": true}'}}

    monkeypatch.setattr(ai_gateway, "_request_json", respond)
    ai_gateway.chat(
        messages=[{"role": "user", "content": "test"}],
        model="qwen3-vl:8b",
        think=False,
    )

    assert captured["think"] is False


def test_chat_reports_the_node_that_served_the_request(monkeypatch):
    monkeypatch.setattr(
        ai_gateway.ollama_nodes,
        "select_available_node",
        lambda **_kwargs: {
            "node_id": "rtx4060",
            "base_url": "http://127.0.0.1:11437",
            "was_primary": True,
            "attempts": [],
        },
    )
    monkeypatch.setattr(ai_gateway, "health", lambda **kwargs: {
        "endpoint_reachable": True, "model_available": True,
        "error_message": None, "error_code": None,
    })
    monkeypatch.setattr(
        ai_gateway,
        "_request_json",
        lambda *args, **kwargs: {"message": {"content": '{"ok": true}'}},
    )

    result = ai_gateway.chat(
        messages=[{"role": "user", "content": "test"}],
        model="qwen3-vl:8b-instruct",
    )

    assert result.node_id == "rtx4060"
    assert result.node_label == "RTX 4060"


def test_structured_chat_rejects_schema_mismatch(monkeypatch):
    monkeypatch.setattr(ai_gateway, "health", lambda **kwargs: {
        "endpoint_reachable": True, "model_available": True,
        "error_message": None, "error_code": None,
    })
    monkeypatch.setattr(
        ai_gateway,
        "_request_json",
        lambda *args, **kwargs: {"message": {"content": '{"ok": "yes"}'}},
    )
    with pytest.raises(ai_gateway.AIGatewayError) as error:
        ai_gateway.chat_structured(
            messages=[{"role": "user", "content": "test"}],
            schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
            model="qwen2.5:7b",
            workload="test",
        )
    assert error.value.code == "OLLAMA_SCHEMA_VALIDATION_FAILED"


# 19 Oct: num_ctx=1536 (13 Oct) could not hold the invite prompt (~1,200 tokens),
# its screenshot and the answer, and Ollama rejected every invite read with
# HTTP 400 (OLLAMA_BAD_REQUEST). The context is now never below MIN_NUM_CTX.
def _capture_options(monkeypatch):
    captured = {}
    monkeypatch.setattr(ai_gateway, "health", lambda **kwargs: {
        "endpoint_reachable": True, "model_available": True,
        "error_message": None, "error_code": None,
    })

    def respond(*args, **kwargs):
        captured.update(json.loads(kwargs["body"]))
        return {"message": {"content": '{"ok": true}'}}

    monkeypatch.setattr(ai_gateway, "_request_json", respond)
    return captured


def test_chat_context_holds_an_invite_prompt_and_its_screenshot(monkeypatch):
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    captured = _capture_options(monkeypatch)
    ai_gateway.chat(messages=[{"role": "user", "content": "test"}], model="qwen3-vl:8b",
                    images=["aGk="], think=False)
    assert captured["options"]["num_ctx"] >= 4096


@pytest.mark.parametrize("configured", ["1536", "2048", "0", "-1", "nonsense"])
def test_a_configured_context_is_never_below_the_minimum(monkeypatch, configured):
    monkeypatch.setenv("OLLAMA_NUM_CTX", configured)
    captured = _capture_options(monkeypatch)
    ai_gateway.chat(messages=[{"role": "user", "content": "test"}], model="qwen3-vl:8b")
    assert captured["options"]["num_ctx"] == ai_gateway.MIN_NUM_CTX


def test_a_larger_configured_context_is_kept(monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
    captured = _capture_options(monkeypatch)
    ai_gateway.chat(messages=[{"role": "user", "content": "test"}], model="qwen3-vl:8b")
    assert captured["options"]["num_ctx"] == 8192
