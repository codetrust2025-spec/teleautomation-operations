"""Configuration, routing and administration for the Ollama inference pool."""

from __future__ import annotations

import http.client
import json
import os
import socket
import tempfile
import threading
import time
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()

#: One decision's node, for the thread making that decision.
#:
#: A mail's relevance, classification and validation are three calls that are
#: only comparable if they ran in the same place. The model is not random --
#: pinned to one warm node it repeated the identical answer 9 times out of 9 --
#: but it answers differently on different hardware, so a mid-analysis failover
#: silently turns the validator into a second opinion about the machine.
_decision = threading.local()
# Normal priority is the order of `configured_nodes()`: RTX 4060, then
# Jagadeesh, then Praveen. This name is only the last resort for when every
# node is cooling off and one still has to be quoted.
_DEFAULT_PRIMARY = "rtx4060"

# How long a hand-picked primary outranks normal priority before the pool
# returns to choosing for itself. An override is for working through an
# incident, not a permanent re-pin, and one left in place silently became the
# configuration nobody remembered making.
_OVERRIDE_SECONDS = 3600.0


def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


def configured_nodes() -> list[dict[str, str]]:
    """The inference pool, in failover order.

    Order is preference, not health: selection walks this list and skips nodes
    that fail their model check. Ids are persisted in runtime state and quoted
    in the admin API, so they never change — ``our_machine`` stays
    ``our_machine`` even though the machine is Praveen's laptop. Business logic
    keys on the id; the label is only ever displayed.
    """
    nodes = [
        {
            "id": "rtx4060",
            "label": "RTX 4060",
            "base_url": (
                os.getenv("OLLAMA_NODE_RTX4060_URL")
                or "http://127.0.0.1:11437"
            ).rstrip("/"),
        },
        {
            "id": "jagadeesh",
            "label": "Jagadeesh",
            # Deliberately does NOT fall back to OLLAMA_BASE_URL. That variable
            # names "some Ollama", not this node, and the two drifted apart in
            # production: a stale OLLAMA_BASE_URL of 11434 resolved `jagadeesh`
            # to the VPS's own CPU Ollama while the Jagadeesh laptop sat idle on
            # 11435. Every text request ran on four shared cores and nothing
            # failed loudly, because 11434 happens to have qwen2.5:7b installed
            # so the model check passed. A node id resolves from its own
            # variable or the documented default — never from a generic one.
            "base_url": (
                os.getenv("OLLAMA_NODE_JAGADEESH_URL")
                or "http://127.0.0.1:11435"
            ).rstrip("/"),
        },
        {
            "id": "our_machine",
            "label": "Praveen",
            "base_url": (
                os.getenv("OLLAMA_NODE_OUR_MACHINE_URL")
                or "http://127.0.0.1:11436"
            ).rstrip("/"),
        },
    ]
    # The VPS runs its own Ollama, but on the same four CPU cores that serve the
    # web application and with no GPU. Measured CPU-only inference on an
    # identical job took 171s against 4.7s on a GPU node, so this is an
    # emergency fallback that has to be turned on deliberately, never a default.
    if _env_flag("OLLAMA_ENABLE_VPS_LOCAL"):
        nodes.append(
            {
                "id": "vps_local",
                "label": "VPS Local",
                "base_url": (
                    os.getenv("OLLAMA_NODE_VPS_LOCAL_URL")
                    or "http://127.0.0.1:11434"
                ).rstrip("/"),
            }
        )
    return nodes


def _state_path() -> Path:
    """Where the pool's runtime state lives.

    On the data volume, not under the application directory. `/app/data` comes
    from the image, so a deploy replaced it and took the state with it -- a
    one-hour override could be silently discarded ten minutes in by an
    unrelated release.
    """
    configured = (os.getenv("OLLAMA_NODE_STATE_FILE") or "").strip()
    if configured:
        return Path(configured)
    try:
        from core.config import DATA_DIR

        return Path(DATA_DIR) / "ollama_nodes_state.json"
    except Exception:  # pragma: no cover - configuration unavailable
        return Path(__file__).resolve().parents[1] / "data" / "ollama_nodes_state.json"


def _valid_node_ids() -> set[str]:
    return {node["id"] for node in configured_nodes()}


def _read_state() -> dict[str, Any]:
    with _LOCK:
        try:
            saved = json.loads(_state_path().read_text(encoding="utf-8"))
            return saved if isinstance(saved, dict) else {}
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return {}


def _write_state(update: dict[str, Any]) -> None:
    """Merge into the state file rather than replacing it.

    The file holds more than the primary now, so writing a fresh object would
    silently drop whatever else was in it.
    """
    path = _state_path()
    with _LOCK:
        current = _read_state()
        current.update(update)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(current, indent=2, sort_keys=True) + "\n"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink(missing_ok=True)


def model_node_preference() -> dict[str, str]:
    """Which node should serve a given model first, ahead of the primary.

    A node with 8 GB of VRAM cannot hold both the text and vision models at
    once: measured on the RTX 4060, alternating between them reloaded on every
    single request and took vision from 2.4s to 11.5s. Making that node primary
    therefore *loses* to leaving it alone, because primary attracts both routes.

    Pinning a model to a node instead keeps each machine holding one model
    resident, which is where the speed actually comes from. It is a preference
    and nothing more: if the pinned node cannot serve, selection carries on down
    the normal order.
    """
    preference: dict[str, str] = {}
    raw = (os.getenv("OLLAMA_MODEL_NODE_PREFERENCE") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                preference.update({str(k): str(v) for k, v in parsed.items()})
        except ValueError:
            pass
    saved = _read_state().get("model_nodes")
    if isinstance(saved, dict):
        preference.update({str(k): str(v) for k, v in saved.items()})
    valid = _valid_node_ids()
    return {model: node for model, node in preference.items() if node in valid}


def set_model_node(model: str, node_id: str | None, *, force: bool = False) -> None:
    """Pin a model to a node, or pass node_id=None to unpin it."""
    wanted = str(model or "").strip()
    if not wanted:
        raise ValueError("A model name is required")
    saved = dict(_read_state().get("model_nodes") or {})
    if node_id is None:
        saved.pop(wanted, None)
    else:
        selected = str(node_id).strip()
        if selected not in _valid_node_ids():
            raise ValueError("Unknown Ollama node")
        if not force:
            status = node_health(selected, model=wanted, timeout=10)
            if not status["endpoint_reachable"]:
                raise RuntimeError(f"{selected} is not reachable")
            if not status["model_available"]:
                raise ValueError(f"{selected} does not have {wanted} installed")
        saved[wanted] = selected
    _write_state({"model_nodes": saved})


def _healthy(node_id: str) -> bool:
    """Usable right now, judged from the breaker the request path maintains.

    Deliberately not a live probe: this is called on the way to every inference
    and a network round trip per call would cost more than the failover it
    guards against. `record_failure` already knows when a node stopped
    answering.
    """
    return not in_cooldown(node_id)


def primary_override() -> dict[str, Any]:
    """The hand-picked primary, if one is still in force.

    Returns the node and the seconds remaining, or an empty mapping. An expired
    override is reported as absent rather than cleared here, so a read stays a
    read; `set_primary_node` overwrites it and nothing else needs to.
    """
    state = _read_state()
    node_id = str(state.get("primary_node") or "")
    if node_id not in _valid_node_ids():
        return {}
    expires_at = float(state.get("primary_expires_at") or 0.0)
    remaining = expires_at - time.time()
    if remaining <= 0:
        return {}
    return {
        "node_id": node_id,
        "expires_at": expires_at,
        "expires_in_s": int(remaining),
        "healthy": _healthy(node_id),
    }


def primary_node_id() -> str:
    """The node production routes to, decided fresh on every call.

    Normal priority is the configured order -- RTX 4060, Jagadeesh, Praveen --
    and the highest node that is not cooling off takes it. A hand-picked
    primary outranks that for an hour, but only while it is actually up: an
    override on a node that has gone down is not a reason to keep sending work
    to it, so failover is immediate and needs no one to cancel anything.
    """
    override = primary_override()
    if override and override["healthy"]:
        return str(override["node_id"])

    configured = (os.getenv("OLLAMA_PRIMARY_NODE") or "").strip()
    order = [node["id"] for node in configured_nodes()]
    if configured in _valid_node_ids():
        order = [configured] + [node_id for node_id in order if node_id != configured]

    for node_id in order:
        if _healthy(node_id):
            return node_id
    # Everything is cooling off. Something still has to be named, and naming the
    # top of the order means recovery is tried there first.
    return order[0] if order else _DEFAULT_PRIMARY


def required_models() -> list[str]:
    """Every model the application needs, not just the vision one.

    The pool serves text and vision from different models, so a node carrying
    only one of them is useless as primary however fast it is.
    """
    try:
        from core.ai_gateway import configured_models

        models = configured_models()
    except Exception:  # pragma: no cover - configuration unavailable
        return []
    ordered = (models.get("text"), models.get("validator"), models.get("vision"))
    return list(dict.fromkeys(name for name in ordered if name))


def missing_models(node_id: str, *, timeout: float = 10) -> list[str]:
    """Which required models this node does not have installed."""
    wanted = required_models()
    if not wanted:
        return []
    status = node_health(node_id, model=wanted[0], timeout=timeout)
    if not status["endpoint_reachable"]:
        raise RuntimeError(f"{node_id} is not reachable")
    installed = status.get("installed_models") or []
    return [name for name in wanted if not _model_available(name, installed)]


def set_primary_node(node_id: str, *, force: bool = False) -> str:
    """Point production at a node.

    Verifies the node actually carries every required model first. The HTTP
    layer already refused an unready node, but a direct call could bypass that
    — and did: rtx4060 was promoted on the strength of the vision model alone
    while lacking the text model, which took invite extraction down until the
    primary was reverted. The check belongs here, next to the write.
    """
    selected = str(node_id or "").strip()
    if selected not in _valid_node_ids():
        raise ValueError("Unknown Ollama node")
    if not force:
        absent = missing_models(selected)
        if absent:
            raise ValueError(
                f"{selected} is missing required model(s): {', '.join(absent)}. "
                "Install them, or pass force=True to accept a degraded primary."
            )
    _write_state({
        "primary_node": selected,
        "primary_expires_at": time.time() + _OVERRIDE_SECONDS,
    })
    return selected


def clear_primary_override() -> None:
    """Hand the choice back to normal priority immediately."""
    _write_state({"primary_node": "", "primary_expires_at": 0.0})


def node(node_id: str) -> dict[str, str]:
    selected = str(node_id or "").strip()
    for item in configured_nodes():
        if item["id"] == selected:
            return item
    raise ValueError("Unknown Ollama node")


def base_url_for(node_id: str) -> str:
    return node(node_id)["base_url"]


def primary_base_url() -> str:
    return base_url_for(primary_node_id())


def inference_host_id(node_id: str | None = None) -> str:
    return f"{node_id or primary_node_id()}-ollama"


def _model_available(configured: str, installed: list[str]) -> bool:
    wanted = configured.removesuffix(":latest")
    return any(name.removesuffix(":latest") == wanted for name in installed)


def _request(
    node_id: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 5,
) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(base_url_for(node_id))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Invalid Ollama node URL")
    connection_type = (
        http.client.HTTPSConnection
        if parsed.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
    try:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        target = f"{parsed.path.rstrip('/')}{path}" or path
        connection.request(
            method,
            target,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        if response.status >= 400:
            raise RuntimeError(f"Ollama returned HTTP {response.status}")
        return json.loads(raw) if raw else {}
    finally:
        connection.close()


def node_health(
    node_id: str, *, model: str, timeout: float = 5, deep: bool = True
) -> dict[str, Any]:
    """Probe a node. ``deep`` adds GPU and version detail for the admin screen.

    Routing calls this on every request, so the selection path passes
    deep=False and pays for one HTTP round trip rather than three.
    """
    item = node(node_id)
    started = time.monotonic()
    result: dict[str, Any] = {
        "id": item["id"],
        "label": item["label"],
        "primary": item["id"] == primary_node_id(),
        "status": "offline",
        "endpoint_reachable": False,
        "model": model,
        "model_available": False,
        "model_loaded": False,
        "response_time_ms": None,
        "error": None,
    }
    try:
        tags = _request(node_id, "/api/tags", timeout=timeout)
        installed = [
            str(entry.get("name") or entry.get("model") or "")
            for entry in tags.get("models", [])
        ]
        loaded: list[str] = []
        processes: dict[str, Any] = {}
        try:
            processes = (
                _request(node_id, "/api/ps", timeout=timeout) if deep else {}
            )
            loaded = [
                str(entry.get("name") or entry.get("model") or "")
                for entry in processes.get("models", [])
            ]
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            # Older Ollama builds can be healthy without exposing /api/ps.
            loaded = []
        result.update(
            {
                "status": "online" if _model_available(model, installed) else "degraded",
                "endpoint_reachable": True,
                "model_available": _model_available(model, installed),
                "model_loaded": _model_available(model, loaded),
                "response_time_ms": int((time.monotonic() - started) * 1000),
                "installed_models": installed,
                "loaded_models": loaded,
            }
        )
        # How much of the loaded model sits in VRAM is the only reliable signal
        # from the Ollama API that a node is really using its GPU. A node
        # reporting 0 here is running on CPU however good its spec sheet is.
        try:
            for entry in (processes or {}).get("models", []):
                total = int(entry.get("size") or 0)
                vram = int(entry.get("size_vram") or 0)
                if not total:
                    continue
                result["gpu"] = {
                    "size_bytes": total,
                    "size_vram_bytes": vram,
                    "gpu_fraction": round(vram / total, 3),
                    "accelerated": vram > 0,
                }
                break
        except (AttributeError, TypeError, ValueError):
            pass
        if deep:
            try:
                result["ollama_version"] = str(
                    _request(node_id, "/api/version", timeout=timeout).get("version")
                    or ""
                )
            except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
                result["ollama_version"] = ""
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, socket.timeout) as exc:
        result.update(
            {
                "response_time_ms": int((time.monotonic() - started) * 1000),
                "error": type(exc).__name__,
            }
        )
    result["breaker"] = breaker_state(node_id)
    # Reachable plus the model present is the most this cheap probe can claim.
    # Only verify_inference() proves the node can actually generate.
    result["available"] = bool(
        result["endpoint_reachable"]
        and result["model_available"]
        and not result["breaker"]["in_cooldown"]
    )
    return result


# ── circuit breaker ─────────────────────────────────────────────────────────
#
# Kept in memory rather than in the state file. Persisting it would rewrite the
# file on every failed probe, and a breaker that survives a restart would keep a
# node out of service for reasons nobody can see. A fresh process starts by
# probing everything, which is the safe direction.
_BREAKERS: dict[str, dict[str, Any]] = {}


def _failure_threshold() -> int:
    try:
        return max(1, int(os.getenv("OLLAMA_NODE_FAILURE_THRESHOLD") or 3))
    except ValueError:
        return 3


def _cooldown_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("OLLAMA_NODE_COOLDOWN_SECONDS") or 120))
    except ValueError:
        return 120.0


def _breaker(node_id: str) -> dict[str, Any]:
    with _LOCK:
        return dict(
            _BREAKERS.setdefault(
                node_id,
                {
                    "consecutive_failures": 0,
                    "last_success_at": None,
                    "last_failure_at": None,
                    "last_error": None,
                    "cooling_until": 0.0,
                },
            )
        )


def record_success(node_id: str) -> None:
    with _LOCK:
        state = _BREAKERS.setdefault(node_id, {})
        state.update(
            {
                "consecutive_failures": 0,
                "last_success_at": time.time(),
                "last_error": None,
                "cooling_until": 0.0,
            }
        )


def record_failure(node_id: str, error: str = "") -> None:
    """One timeout is not a dead node; a run of them is."""
    with _LOCK:
        state = _BREAKERS.setdefault(node_id, {"consecutive_failures": 0})
        failures = int(state.get("consecutive_failures") or 0) + 1
        state["consecutive_failures"] = failures
        state["last_failure_at"] = time.time()
        state["last_error"] = error or state.get("last_error")
        if failures >= _failure_threshold():
            state["cooling_until"] = time.monotonic() + _cooldown_seconds()


def in_cooldown(node_id: str) -> bool:
    return float(_breaker(node_id).get("cooling_until") or 0.0) > time.monotonic()


def reset_breakers() -> None:
    with _LOCK:
        _BREAKERS.clear()


def breaker_state(node_id: str) -> dict[str, Any]:
    state = _breaker(node_id)
    cooling = float(state.get("cooling_until") or 0.0)
    return {
        "consecutive_failures": int(state.get("consecutive_failures") or 0),
        "last_success_at": state.get("last_success_at"),
        "last_failure_at": state.get("last_failure_at"),
        "last_error": state.get("last_error"),
        "in_cooldown": cooling > time.monotonic(),
        "cooldown_remaining_s": max(0, int(cooling - time.monotonic())) if cooling else 0,
    }


def verify_inference(
    node_id: str, *, model: str, timeout: float = 60
) -> dict[str, Any]:
    """Prove the node can actually run the model.

    An open port says nothing: a node can accept TCP, list the model in
    /api/tags and still fail every generation. Health that matters is a
    completed response.
    """
    started = time.monotonic()
    try:
        payload = _request(
            node_id,
            "/api/generate",
            method="POST",
            payload={
                "model": model,
                "prompt": "Reply with the single word: ok",
                "stream": False,
                "options": {"temperature": 0, "num_predict": 4},
            },
            timeout=timeout,
        )
        text = str(payload.get("response") or "").strip()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not text:
            record_failure(node_id, "empty response")
            return {"ok": False, "latency_ms": elapsed_ms, "error": "empty response"}
        eval_count = int(payload.get("eval_count") or 0)
        eval_ns = int(payload.get("eval_duration") or 0)
        record_success(node_id)
        return {
            "ok": True,
            "latency_ms": elapsed_ms,
            "tokens_per_second": (
                round(eval_count / (eval_ns / 1e9), 2) if eval_ns else None
            ),
            "load_duration_s": round(int(payload.get("load_duration") or 0) / 1e9, 2),
        }
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, socket.timeout) as exc:
        record_failure(node_id, type(exc).__name__)
        return {
            "ok": False,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": type(exc).__name__,
        }


def decision_node() -> str | None:
    """The node this thread's decision session has already committed to."""
    return getattr(_decision, "node_id", None)


def decision_session_active() -> bool:
    return bool(getattr(_decision, "active", False))


def _remember_decision_node(node_id: str) -> None:
    """First node a session resolves becomes the session's node."""
    if decision_session_active() and not decision_node():
        _decision.node_id = node_id


@contextmanager
def decision_session():
    """Hold every model call for one decision on a single node.

    The first call resolves a node normally, honouring the model pin, health
    and cooldown. Every later call in the same session is restricted to that
    node. If it stops being able to serve, selection fails and the caller sees
    the gateway error -- which parks the mail for retry -- rather than
    quietly finishing the decision somewhere else.

    Nested sessions restore the outer session's node on exit, so a helper that
    opens one cannot strand its caller on the wrong node.
    """
    previous_active = getattr(_decision, "active", False)
    previous_node = getattr(_decision, "node_id", None)
    _decision.active = True
    _decision.node_id = None
    try:
        yield
    finally:
        _decision.active = previous_active
        _decision.node_id = previous_node


def candidate_order(model: str | None = None) -> list[str]:
    """Routing order: a node pinned to this model, then the primary, then rest.

    Inside a decision session the order collapses to the one node that session
    already used. Measured on the same mail, byte-identical input: rtx4060
    answered INTERVIEW_UPDATE and jagadeesh INTERVIEW_SHORTLISTED, each of them
    repeatably. Letting one analysis take its classifier from one node and its
    validator from another therefore compares two machines rather than two
    readings, and that is what produced MODEL_DISAGREEMENT on a genuine Karat
    interview reminder at confidence 1.0.
    """
    pinned = decision_node()
    if pinned:
        return [pinned]
    ordered = [item["id"] for item in configured_nodes()]
    head: list[str] = []
    pinned = model_node_preference().get(str(model or "")) if model else None
    if not pinned and model and "model_nodes" not in _read_state():
        # RTX 4060 (8 GB VRAM) is dedicated to GPU vision extraction.
        # Text requests route to Jagadeesh to avoid alternating model unloads/reloads in VRAM.
        m = str(model).lower()
        if "vl" in m or "vision" in m:
            pinned = "rtx4060"
        elif "7b" in m or "qwen2.5" in m:
            pinned = "jagadeesh"
    if pinned and pinned in ordered:
        head.append(pinned)
    primary = primary_node_id()
    if primary not in head:
        head.append(primary)
    return head + [item for item in ordered if item not in head]


def select_available_node(
    *,
    model: str,
    timeout: float = 5,
    require_inference: bool = False,
    exclude: set[str] | frozenset[str] | None = None,
    node_order: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Pick where this request should run.

    Walks the preference order and returns the first node whose model check
    passes, skipping any that is cooling off. This never rewrites the persisted
    primary: routing around a sick node is a per-request decision, so a blip
    cannot move production's configured primary. Changing that stays an
    explicit admin action.

    `exclude` is the caller's request-scoped set of nodes already known to have
    failed this request. The breaker needs three consecutive failures before it
    cools a node, which is right for the global view but useless inside one
    request: the node that just refused this exact call would be chosen again
    immediately. Excluding it is per-request and leaves the breaker's own
    counting untouched.
    """
    attempts: list[dict[str, Any]] = []
    excluded = {str(node) for node in (exclude or ())}
    # A workload may narrow its own pool without changing shared preferences,
    # persisted primary selection, or another in-flight decision session.
    order = [nid for nid in (node_order if node_order is not None else candidate_order(model)) if nid not in excluded]
    if not order:
        raise RuntimeError(
            "No Ollama node is left for this request; already tried: "
            + ", ".join(sorted(excluded))
        )
    cooling = [nid for nid in order if in_cooldown(nid)]
    # If every node is cooling there is nothing to be gained by refusing; try
    # them anyway so a total outage still gets probed rather than hard-failing.
    usable = [nid for nid in order if nid not in cooling] or order

    for node_id in usable:
        status = node_health(node_id, model=model, timeout=timeout, deep=False)
        if not status["endpoint_reachable"]:
            record_failure(node_id, status.get("error") or "unreachable")
            attempts.append({"node": node_id, "reason": "unreachable"})
            continue
        if not status["model_available"]:
            # Not a health failure. The breaker is per node while this is per
            # model, so counting it would let text requests cool down a node
            # that serves vision perfectly well — and a node carrying only the
            # vision model would be dropped from vision work too, which is
            # exactly backwards. Skip it for this model and leave it healthy.
            attempts.append({"node": node_id, "reason": "model missing"})
            continue
        if require_inference:
            proof = verify_inference(node_id, model=model, timeout=max(timeout, 30))
            if not proof["ok"]:
                attempts.append({"node": node_id, "reason": proof.get("error")})
                continue
        else:
            record_success(node_id)
        _remember_decision_node(node_id)
        return {
            "node_id": node_id,
            "base_url": base_url_for(node_id),
            "was_primary": node_id == primary_node_id(),
            "attempts": attempts,
        }

    raise RuntimeError(
        "No Ollama node could serve this request: "
        + "; ".join(f"{a['node']} ({a['reason']})" for a in attempts)
    )


def unload_model(node_id: str, *, model: str, timeout: float = 30) -> dict[str, Any]:
    before = node_health(node_id, model=model, timeout=min(timeout, 5))
    if not before["endpoint_reachable"]:
        raise RuntimeError("The selected Ollama node is offline")
    if not before["model_available"]:
        raise RuntimeError("The configured model is not installed on this node")
    _request(
        node_id,
        "/api/generate",
        method="POST",
        payload={"model": model, "keep_alive": 0},
        timeout=timeout,
    )
    deadline = time.monotonic() + min(timeout, 3)
    after = node_health(node_id, model=model, timeout=min(timeout, 5))
    while after["model_loaded"] and time.monotonic() < deadline:
        time.sleep(0.25)
        after = node_health(node_id, model=model, timeout=min(timeout, 5))
    return {"node": after, "unloaded": not after["model_loaded"]}
