"""Central, bounded gateway for local Ollama requests.

New AI features must use this module rather than calling Ollama from routes,
providers, or persistence code.  It deliberately exposes a small synchronous
API so callers can execute it through the application's background workers.
"""

from __future__ import annotations

import json
import http.client
import logging
import os
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

from jsonschema import ValidationError, validate as validate_json_schema

from core import ai_activity
from core import ollama_status
from core import ollama_nodes

logger = logging.getLogger("teleautomation.ai_gateway")

# Workloads a person is waiting on: a candidate at the booking form uploading a
# payment screenshot or an interview invite, or a resume upload. They go ahead
# of background work (mail analysis, attachments) queued for the same machine.
INTERACTIVE_WORKLOADS = frozenset({
    "payment_screenshot_vision",
    "payment_screenshot_text",
    "interview_screenshot_vision",
    "interview_screenshot_text",
    "resume_vision",
    "resume_text",
})


class _HostQueue:
    """A bounded request queue per inference machine, interactive work first.

    This replaced one process-wide slot. That slot serialised every Ollama call
    in the service, including calls bound for different machines: mail analysis
    runs on one host and the booking-form vision model on another, yet a
    candidate's payment screenshot waited behind a mail analysis it shared no
    hardware with, gave up after the queue wait, and was refused as unreadable.
    On 2026-09-14 every booking-form vision call after 20:56 IST failed that way
    -- two invites and a payment -- and a round-wise candidate could not book an
    interview for the next day.

    `AI_OLLAMA_MAX_CONCURRENCY` now bounds each machine rather than the whole
    service, so no machine ever receives more concurrent requests from here
    than before. On a machine that is busy, a waiting interactive request is
    served before any waiting background request.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = max(1, int(capacity))
        self._lock = threading.Condition()
        self._in_use: dict[str, int] = {}
        self._interactive_waiting: dict[str, int] = {}

    def acquire(self, host: str, *, interactive: bool, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            if interactive:
                self._interactive_waiting[host] = self._interactive_waiting.get(host, 0) + 1
            try:
                while True:
                    free = self._in_use.get(host, 0) < self._capacity
                    # Background work does not take a slot an interactive
                    # request on the same machine is already waiting for.
                    unclaimed = interactive or not self._interactive_waiting.get(host, 0)
                    if free and unclaimed:
                        self._in_use[host] = self._in_use.get(host, 0) + 1
                        return True
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._lock.wait(remaining)
            finally:
                if interactive:
                    self._interactive_waiting[host] -= 1
                    if not self._interactive_waiting[host]:
                        # A background request may have been holding back for
                        # this one; let it look again.
                        self._lock.notify_all()

    def release(self, host: str) -> None:
        with self._lock:
            self._in_use[host] = max(0, self._in_use.get(host, 0) - 1)
            self._lock.notify_all()


_host_slots = _HostQueue(int(os.getenv("AI_OLLAMA_MAX_CONCURRENCY", "1") or "1"))


@dataclass(frozen=True)
class AIResult:
    content: str
    model: str
    duration_ms: int
    node_id: str = ""
    node_label: str = ""


class AIGatewayError(RuntimeError):
    """A classified Ollama failure safe for logs and administrator diagnostics."""

    def __init__(self, message: str, *, code: str = "OLLAMA_INTERNAL_ERROR") -> None:
        super().__init__(message)
        self.code = code


def _base_url() -> str:
    return ollama_nodes.primary_base_url()


def _inference_host_id() -> str:
    return ollama_nodes.inference_host_id()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.error("Invalid numeric Ollama setting %s; using safe default", name)
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.error("Invalid integer Ollama setting %s; using safe default", name)
        return default


def _model_available(configured: str, installed: list[str]) -> bool:
    wanted = configured.removesuffix(":latest")
    return any(str(name or "").removesuffix(":latest") == wanted for name in installed)


def _connection_error_code() -> str:
    expected_tunnel = (os.getenv("OLLAMA_EXPECT_REVERSE_SSH_TUNNEL") or "false").lower() in {"1", "true", "yes"}
    return "REVERSE_SSH_TUNNEL_UNAVAILABLE" if expected_tunnel else "OLLAMA_CONNECTION_FAILED"


def _safe_message(code: str) -> str:
    messages = {
        "REVERSE_SSH_TUNNEL_UNAVAILABLE": "Ollama is running on the laptop, but the VPS reverse SSH tunnel is unavailable.",
        "OLLAMA_CONNECTION_FAILED": "The Ollama endpoint could not be reached.",
        "OLLAMA_REQUEST_TIMEOUT": "The Ollama model response timed out.",
        "OLLAMA_QUEUE_TIMEOUT": "The bounded Ollama request queue timed out.",
        "OLLAMA_MODEL_NOT_FOUND": "The configured Ollama model is not installed.",
        "OLLAMA_OPTIONAL_MODEL_MISSING": "Ollama is reachable, but one or more fallback models are not installed.",
        "OLLAMA_CONFIGURATION_ERROR": "The Ollama endpoint configuration is invalid.",
        "OLLAMA_EMPTY_RESPONSE": "Ollama returned an empty response.",
        "OLLAMA_INVALID_JSON": "Ollama returned invalid JSON.",
        "OLLAMA_SCHEMA_VALIDATION_FAILED": "The Ollama response did not match the required schema.",
        "OLLAMA_MODEL_LOAD_FAILED": "Ollama could not load the configured model within the available resources.",
        "OLLAMA_BAD_REQUEST": "Ollama rejected the model request.",
        "OLLAMA_INTERNAL_ERROR": "Ollama validation failed unexpectedly.",
    }
    return messages.get(code, messages["OLLAMA_INTERNAL_ERROR"])


def _http_error_code(status: int, raw: str) -> str:
    """Classify Ollama HTTP failures without exposing its response to clients."""
    detail = raw.casefold()
    if status == 404 and "model" in detail:
        return "OLLAMA_MODEL_NOT_FOUND"
    if any(token in detail for token in (
        "load model", "loading model", "runner", "memory", "resource", "llama-server",
    )):
        return "OLLAMA_MODEL_LOAD_FAILED"
    if status in {400, 413, 422}:
        return "OLLAMA_BAD_REQUEST"
    return "OLLAMA_INTERNAL_ERROR"


def _request_json(path: str, *, method: str = "GET", body: bytes | None = None, connect_timeout: float, response_timeout: float, base_url: str | None = None) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(base_url or _base_url())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        logger.error("Invalid OLLAMA_BASE_URL configuration")
        raise AIGatewayError(
            _safe_message("OLLAMA_CONFIGURATION_ERROR"),
            code="OLLAMA_CONFIGURATION_ERROR",
        )
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parsed.hostname, parsed.port, timeout=connect_timeout)
    try:
        connection.connect()
        if connection.sock is not None:
            connection.sock.settimeout(response_timeout)
        target = f"{parsed.path.rstrip('/')}{path}" or path
        connection.request(method, target, body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        if response.status >= 400:
            code = _http_error_code(response.status, raw)
            logger.warning("Ollama HTTP failure status=%s code=%s", response.status, code)
            raise AIGatewayError(_safe_message(code), code=code)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AIGatewayError(_safe_message("OLLAMA_INVALID_JSON"), code="OLLAMA_INVALID_JSON") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise AIGatewayError(_safe_message("OLLAMA_REQUEST_TIMEOUT"), code="OLLAMA_REQUEST_TIMEOUT") from exc
    except (ConnectionError, OSError, http.client.HTTPException) as exc:
        code = _connection_error_code()
        raise AIGatewayError(_safe_message(code), code=code) from exc
    finally:
        connection.close()


_NODE_FAILURE_CODES = {
    # The node could not produce an answer: transport, capacity or model load.
    # These name the machine, so the request moves to another one.
    "OLLAMA_CONNECTION_FAILED",
    "OLLAMA_REQUEST_TIMEOUT",
    "OLLAMA_INTERNAL_ERROR",
    "OLLAMA_MODEL_LOAD_FAILED",
    "OLLAMA_MODEL_NOT_FOUND",
}
_MODEL_OUTPUT_CODES = {
    # The node answered and the answer was unusable. That is a property of the
    # model and the prompt, not of the machine, so failing over would only
    # repeat it elsewhere and cool a healthy node on the way.
    "OLLAMA_INVALID_JSON",
    "OLLAMA_SCHEMA_VALIDATION_FAILED",
    "OLLAMA_EMPTY_RESPONSE",
    "OLLAMA_BAD_REQUEST",
}


def configured_models() -> dict[str, str]:
    from core.ai_model_routing import configured_model_routes, guard_text_decision_model

    routes = configured_model_routes()
    # The fallback is what serves mail classification when the primary runner
    # cannot, so it is a mail decision too and takes the same floor. Guarding it
    # here rather than trusting the variable keeps a weak value from arriving
    # through the one path that does not go via model_for().
    fallback = (os.getenv("OLLAMA_FALLBACK_MODEL") or os.getenv("AI_RECRUITMENT_FALLBACK_MODEL") or os.getenv("OLLAMA_REASONING_MODEL") or "qwen2.5:7b").strip()
    return {
        "text": routes["recruitment_email_primary"],
        "primary": routes["recruitment_email_primary"],
        "validator": routes["recruitment_email_validator"],
        "vision": routes["recruitment_document_vision"],
        # Kept for callers that still use the old generic gateway vocabulary.
        "fallback": guard_text_decision_model(fallback, route="gateway_fallback"),
    }


def _remaining(deadline: float | None, requested: float) -> float:
    if deadline is None:
        return requested
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AIGatewayError("The AI job deadline was exceeded.", code="OLLAMA_REQUEST_TIMEOUT")
    return min(requested, remaining)


def chat(
    *,
    messages: list[dict[str, Any]],
    model: str | None = None,
    timeout: float | None = None,
    temperature: float = 0,
    images: list[str] | None = None,
    max_retries: int | None = None,
    schema: dict[str, Any] | None = None,
    workload: str = "generic",
    deadline_monotonic: float | None = None,
    num_predict: int | None = None,
    think: bool | str | None = None,
) -> AIResult:
    """Call Ollama through the single bounded production gateway."""
    # The request is reported as it moves -- waiting, on a node, released,
    # moved to another -- so whatever names the node serving it (the booking
    # page, the AI nodes panel) reads this function's own decisions.
    activity = ai_activity.begin_call(workload)
    try:
        return _chat(
            activity,
            messages=messages, model=model, timeout=timeout, temperature=temperature,
            images=images, max_retries=max_retries, schema=schema, workload=workload,
            deadline_monotonic=deadline_monotonic, num_predict=num_predict, think=think,
        )
    finally:
        activity.end()


def _chat(
    activity: ai_activity.Call,
    *,
    messages: list[dict[str, Any]],
    model: str | None,
    timeout: float | None,
    temperature: float,
    images: list[str] | None,
    max_retries: int | None,
    schema: dict[str, Any] | None,
    workload: str,
    deadline_monotonic: float | None,
    num_predict: int | None,
    think: bool | str | None,
) -> AIResult:
    chosen = (model or configured_models()["text"]).strip()
    if not chosen:
        raise AIGatewayError("No AI model is configured", code="OLLAMA_MODEL_NOT_FOUND")
    # Route to the first node in the preference order that passes its model
    # check, rather than insisting on the configured primary. This only chooses
    # where the request runs; the persisted primary is untouched, so a single
    # unhealthy probe cannot move production's primary around.
    health_timeout = _remaining(
        deadline_monotonic,
        _env_float("OLLAMA_HEALTH_TIMEOUT_SECONDS", 10),
    )
    wait = _env_float("AI_RECRUITMENT_QUEUE_WAIT_SECONDS", 30)
    interactive = workload in INTERACTIVE_WORKLOADS
    started = time.monotonic()
    # Time spent queued for a machine, kept out of duration_ms so the latency
    # it reports is still the model's, as it was when the one slot was taken
    # before `started`.
    queued = 0.0
    prepared_messages = [dict(message) for message in messages]
    if images and prepared_messages:
        prepared_messages[-1]["images"] = images
    request_payload: dict[str, Any] = {
        "model": chosen,
        "messages": prepared_messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if schema is not None:
        request_payload["format"] = schema
    if num_predict is not None:
        request_payload["options"]["num_predict"] = int(num_predict)
    if think is not None:
        request_payload["think"] = think
    keep_alive = (os.getenv("OLLAMA_KEEP_ALIVE") or "5m").strip()
    if keep_alive:
        request_payload["keep_alive"] = keep_alive
    body = json.dumps(request_payload).encode("utf-8")
    connect_timeout = _env_float(
        "OLLAMA_CONNECT_TIMEOUT",
        _env_float("OLLAMA_CONNECT_TIMEOUT_SECONDS", 10),
    )
    response_timeout = timeout or _env_float(
        "OLLAMA_REQUEST_TIMEOUT",
        _env_float("OLLAMA_RESPONSE_TIMEOUT_SECONDS", _env_float("OLLAMA_TIMEOUT", 300)),
    )
    configured_retries = _env_int("OLLAMA_RETRY_COUNT", _env_int("OLLAMA_MAX_RETRIES", 1))
    retry_limit = max(0, min(3, configured_retries if max_retries is None else int(max_retries)))
    retry_delays = (2, 5, 10)
    last_error: AIGatewayError | None = None
    # Nodes this request has already found wanting. The breaker cools a node
    # only after three consecutive failures, which is the right global
    # policy and useless here: without this set the next selection returns
    # the node that just refused this very call, because the preference
    # order is deterministic.
    excluded_nodes: set[str] = set()
    node_budget = max(1, len(ollama_nodes.candidate_order(chosen)))
    for _node_attempt in range(node_budget):
        try:
            chosen_node = ollama_nodes.select_available_node(
                model=chosen,
                timeout=_remaining(deadline_monotonic, health_timeout),
                exclude=excluded_nodes,
            )
            selected_node = chosen_node["node_id"]
        except RuntimeError:
            # Selection could not resolve a node — every health probe
            # failed, or none carries this model. Rather than give up, walk
            # the preference order directly and try the next node this
            # request has not already ruled out. On the first pass that is
            # the configured primary, which is exactly what happened before
            # this change, so the caller still sees the specific health
            # failure from the checks below rather than a generic selection
            # message. On later passes it is what keeps failover working
            # even when the health probes themselves are unavailable.
            remaining = [
                node for node in ollama_nodes.candidate_order(chosen)
                if node not in excluded_nodes
            ]
            if not remaining:
                break
            selected_node = remaining[0]
        selected_base_url = ollama_nodes.base_url_for(selected_node)
        host_id = ollama_nodes.inference_host_id(selected_node)
        status = health(
            model=chosen,
            timeout=_remaining(deadline_monotonic, health_timeout),
            node_id=selected_node,
        )
        if not status["endpoint_reachable"]:
            last_error = AIGatewayError(status["error_message"], code=status["error_code"])
            ollama_nodes.record_failure(
                selected_node, status.get("error_message") or "unreachable"
            )
            excluded_nodes.add(selected_node)
            continue
        if not status["model_available"]:
            # Per model, not per node: a node without this model may serve
            # another one perfectly well, so it is skipped, not blamed.
            last_error = AIGatewayError(status["error_message"], code="OLLAMA_MODEL_NOT_FOUND")
            excluded_nodes.add(selected_node)
            continue
        node_failed = False
        for attempt in range(retry_limit + 1):
            logger.info(
                "Ollama request started workload=%s inference_host=%s model=%s attempt=%s",
                workload, host_id, chosen, attempt + 1,
            )
            # The slot belongs to the machine the request is about to run
            # on, and is held only for the call itself -- never across a
            # retry delay or while another machine is being tried. A queue
            # timeout is raised here, before the node-failure handling
            # below, because waiting for our own queue says nothing about
            # the node and must never cool it.
            queue_started = time.monotonic()
            if not _host_slots.acquire(
                host_id, interactive=interactive,
                timeout=_remaining(deadline_monotonic, wait),
            ):
                raise AIGatewayError(
                    "The Ollama request queue timed out.", code="OLLAMA_QUEUE_TIMEOUT"
                )
            queued += time.monotonic() - queue_started
            # From here until the release below, this node is the one doing
            # the work. Not earlier: selection and the queue only decide
            # where it will run.
            activity.running(selected_node)
            try:
                answered = False
                try:
                    payload = _request_json(
                        "/api/chat", method="POST", body=body,
                        connect_timeout=_remaining(deadline_monotonic, connect_timeout),
                        response_timeout=_remaining(deadline_monotonic, response_timeout),
                        base_url=selected_base_url,
                    )
                    answered = True
                finally:
                    _host_slots.release(host_id)
                    # An answer counts even if it fails validation below: the
                    # node read the input. A transport failure does not, and
                    # the request is about to move to another node.
                    activity.released(answered=answered)
                content = str((payload.get("message") or {}).get("content") or "").strip()
                if not content:
                    raise AIGatewayError(
                        _safe_message("OLLAMA_EMPTY_RESPONSE"), code="OLLAMA_EMPTY_RESPONSE"
                    )
                if schema is not None:
                    try:
                        parsed_content = json.loads(content)
                    except json.JSONDecodeError as exc:
                        raise AIGatewayError(
                            _safe_message("OLLAMA_INVALID_JSON"), code="OLLAMA_INVALID_JSON"
                        ) from exc
                    try:
                        validate_json_schema(instance=parsed_content, schema=schema)
                    except ValidationError as exc:
                        logger.warning(
                            "Ollama schema validation failed workload=%s inference_host=%s "
                            "model=%s path=%s",
                            workload, host_id, chosen, list(exc.absolute_path),
                        )
                        raise AIGatewayError(
                            _safe_message("OLLAMA_SCHEMA_VALIDATION_FAILED"),
                            code="OLLAMA_SCHEMA_VALIDATION_FAILED",
                        ) from exc
                    content = json.dumps(parsed_content, ensure_ascii=False)
                duration_ms = int((time.monotonic() - started - queued) * 1000)
                logger.info(
                    "Ollama response completed workload=%s inference_host=%s model=%s "
                    "attempt=%s duration_ms=%s",
                    workload, host_id, chosen, attempt + 1, duration_ms,
                )
                ollama_status.record_request_success(duration_ms)
                ollama_nodes.record_success(selected_node)
                selected_node_record = ollama_nodes.node(selected_node)
                return AIResult(
                    content=content,
                    model=chosen,
                    duration_ms=duration_ms,
                    node_id=selected_node,
                    node_label=str(selected_node_record.get("label") or selected_node),
                )
            except AIGatewayError as exc:
                last_error = exc
                ollama_status.record_request_failure(exc.code, str(exc))
                logger.warning(
                    "Ollama request failed workload=%s inference_host=%s model=%s "
                    "attempt=%s code=%s elapsed_ms=%s",
                    workload, host_id, chosen, attempt + 1, exc.code,
                    int((time.monotonic() - started) * 1000),
                )
                if exc.code in _MODEL_OUTPUT_CODES:
                    # The node answered; the answer was unusable. Another
                    # node would re-run the same prompt against the same
                    # weights for the same bad output, while blaming a node
                    # that is working.
                    raise
                if exc.code in _NODE_FAILURE_CODES:
                    ollama_nodes.record_failure(selected_node, str(exc))
                    alternatives = [
                        node for node in ollama_nodes.candidate_order(chosen)
                        if node not in excluded_nodes and node != selected_node
                    ]
                    if alternatives:
                        # Somewhere else to go: never spend another attempt
                        # on the machine that just refused this call.
                        excluded_nodes.add(selected_node)
                        node_failed = True
                        break
                    # Nowhere else to go. A timeout under load is often
                    # transient, so on a single-node pool the old
                    # retry-with-backoff is still the best available move -
                    # abandoning after one attempt would be a regression.
                    if attempt < retry_limit:
                        delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                        _remaining(deadline_monotonic, delay)
                        time.sleep(delay)
                    continue
                if attempt < retry_limit:
                    delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                    _remaining(deadline_monotonic, delay)
                    time.sleep(delay)
        if not node_failed:
            # Retries exhausted without a verdict naming the node. Do not
            # spin on it again inside this request.
            excluded_nodes.add(selected_node)
    if last_error is not None:
        raise last_error
    raise AIGatewayError(
        "No Ollama node could serve this request.", code="OLLAMA_CONNECTION_FAILED"
    )


def chat_structured(
    *,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    model: str | None = None,
    timeout: float | None = None,
    temperature: float = 0,
    images: list[str] | None = None,
    max_retries: int | None = None,
    workload: str = "structured",
    deadline_monotonic: float | None = None,
    num_predict: int | None = None,
    think: bool | str | None = None,
) -> AIResult:
    """Return strictly parsed and JSON-schema-validated model output."""
    return chat(
        messages=messages, schema=schema, model=model, timeout=timeout,
        temperature=temperature, images=images, max_retries=max_retries,
        workload=workload, deadline_monotonic=deadline_monotonic,
        num_predict=num_predict, think=think,
    )


def health(*, model: str | None = None, timeout: float | None = None, node_id: str | None = None) -> dict[str, Any]:
    """Can this model be served? Reports on whichever node would actually run it.

    Callers use this as a gate — invite extraction refuses to run when it says
    no — so answering for the configured primary alone made a degraded primary
    look like a total outage. That is exactly what happened: rtx4060 became
    primary carrying the vision model but not the text one, and every invite
    read reported the AI unavailable while jagadeesh sat there able to serve it.

    An explicit node_id still reports on that node, which is what the admin
    screen and the post-selection check in chat() both want.
    """
    configured = (model or configured_models()["text"]).strip()
    if node_id:
        selected_node = node_id
    else:
        try:
            selected_node = ollama_nodes.select_available_node(
                model=configured,
                timeout=timeout or _env_float("OLLAMA_HEALTH_TIMEOUT_SECONDS", 10),
            )["node_id"]
        except RuntimeError:
            # Nothing could serve it; report against the primary so the error
            # describes the node the operator expects to be in charge.
            selected_node = ollama_nodes.primary_node_id()
    selected_base_url = ollama_nodes.base_url_for(selected_node)
    host_id = ollama_nodes.inference_host_id(selected_node)
    started = time.monotonic()
    try:
        payload = _request_json(
            "/api/tags",
            connect_timeout=_env_float(
                "OLLAMA_CONNECT_TIMEOUT",
                _env_float("OLLAMA_CONNECT_TIMEOUT_SECONDS", 10),
            ),
            response_timeout=timeout or _env_float("OLLAMA_HEALTH_TIMEOUT_SECONDS", 10),
            base_url=selected_base_url,
        )
        models = [str(item.get("name") or item.get("model") or "") for item in payload.get("models", [])]
        elapsed = int((time.monotonic() - started) * 1000)
        routes = configured_models()
        required_names = (
            dict.fromkeys((configured,)) if model is not None
            else dict.fromkeys((configured, routes["fallback"], routes["vision"], routes["validator"]))
        )
        required = {name: _model_available(name, models) for name in required_names if name}
        available = required.get(configured, False)
        fallback_models = {
            name: present for name, present in required.items() if name != configured
        }
        optional_missing = [name for name, present in fallback_models.items() if not present]
        diagnostic = (
            "PRIMARY_MODEL_MISSING" if not available
            else "OPTIONAL_MODEL_MISSING" if optional_missing
            else "AVAILABLE"
        )
        error_code = (
            "OLLAMA_MODEL_NOT_FOUND" if not available
            else "OLLAMA_OPTIONAL_MODEL_MISSING" if optional_missing
            else None
        )
        return ollama_status.record_health(
            provider="ollama",
            inference_host=host_id,
            remote_enabled=(os.getenv("OLLAMA_REMOTE_ENABLED") or "false").lower() in {"1", "true", "yes"},
            status="healthy" if available and not optional_missing else "degraded",
            diagnostic_status=diagnostic,
            endpoint_reachable=True,
            service_reachable=True,
            serviceReachable=True,
            configured_model=configured,
            primary_model=configured,
            primaryModel=configured,
            model_available=available,
            primary_model_available=available,
            primaryModelAvailable=available,
            required_models=required,
            fallback_models=fallback_models,
            fallbackModels=fallback_models,
            installed_models=models,
            missing_models=[name for name, present in required.items() if not present],
            response_time_ms=elapsed,
            error_code=error_code,
            error_message=_safe_message(error_code) if error_code else None,
        )
    except AIGatewayError as exc:
        diagnostic = {
            "REVERSE_SSH_TUNNEL_UNAVAILABLE": "TUNNEL_UNREACHABLE",
            "OLLAMA_CONNECTION_FAILED": "SERVICE_UNREACHABLE",
            "OLLAMA_REQUEST_TIMEOUT": "TIMEOUT",
            "OLLAMA_QUEUE_TIMEOUT": "QUEUE_TIMEOUT",
            "OLLAMA_MODEL_NOT_FOUND": "PRIMARY_MODEL_MISSING",
            "OLLAMA_CONFIGURATION_ERROR": "CONFIGURATION_ERROR",
        }.get(exc.code, "INTERNAL_ERROR")
        return ollama_status.record_health(
            provider="ollama", inference_host=host_id,
            remote_enabled=(os.getenv("OLLAMA_REMOTE_ENABLED") or "false").lower() in {"1", "true", "yes"},
            status="unavailable", endpoint_reachable=False,
            service_reachable=False, serviceReachable=False,
            configured_model=configured, primary_model=configured, primaryModel=configured,
            model_available=False, response_time_ms=int((time.monotonic() - started) * 1000),
            primary_model_available=False, primaryModelAvailable=False,
            diagnostic_status=diagnostic, required_models={}, fallback_models={},
            fallbackModels={}, installed_models=[], missing_models=[],
            error_code=exc.code, error_message=str(exc),
        )
