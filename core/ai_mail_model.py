"""Admin-tested, mail-only model selection. No mailbox or candidate side effects."""
from __future__ import annotations

import json
import logging
import http.client
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from core import ai_gateway, ollama_nodes
from core.config import DATA_DIR

_LOCK = threading.RLock()


def _path():
    return Path(os.getenv("AI_MAIL_MODEL_STATE_FILE") or
                Path(DATA_DIR) / "ai_mail_model.json")


@contextmanager
def _transaction():
    """Coordinate API and background processes as well as threads."""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, open(str(path) + ".lock", "a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            if not lock.read(1):
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def state():
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except FileNotFoundError:
        return {}
    # Corrupt/unreadable configuration must not silently select another model.


def _write(value):
    path = _path()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False,
                                         encoding="utf-8") as handle:
            temporary = Path(handle.name)
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def selection():
    saved = state()
    if saved.get("model"):
        _check_model(saved["model"])
    return {"model": saved.get("model") or ai_gateway.configured_models()["primary"],
            "revision": saved.get("revision", 0), "custom": bool(saved.get("model")),
            "last_used": saved.get("last_used"),
            "validated_nodes": saved.get("validated_nodes", [])}


def _inventory(node):
    try:
        return {"id": node["id"], "label": node["label"], "available": True,
                "models": ollama_nodes.installed_model_inventory(node["id"], timeout=5)}
    except (OSError, RuntimeError, ValueError, http.client.HTTPException):
        return {"id": node["id"], "label": node["label"], "available": False, "models": []}


def catalog():
    nodes = ollama_nodes.configured_nodes()
    with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
        inventory = list(pool.map(_inventory, nodes))
    return {"selection": selection(), "nodes": inventory,
            "primary_node": ollama_nodes.primary_node_id()}


def _check_model(model):
    if not isinstance(model, str) or not model.strip() or len(model) > 200 or any(c.isspace() for c in model):
        raise ValueError("Select an installed model from the list.")
    from core.ai_model_routing import is_weak_text_model
    if is_weak_text_model(model):
        raise ValueError("Mail decisions require a model of at least 7B parameters.")
    return model.strip()


def _infer(node_id, **kwargs):
    # Reuse the gateway's existing affinity, queue, activity and failover gates.
    # A temporary nested session pins a probe without changing the caller.
    with ollama_nodes.decision_session():
        ollama_nodes._remember_decision_node(node_id)
        return ai_gateway.chat_structured(**kwargs)


def test_model(model):
    """Exercise the real mail JSON schema on two distinct nodes before activation."""
    from services.recruitment_mail_agent import SCHEMA, CLASSIFIER_PROMPT, RELEVANCE_SCHEMA, RELEVANCE_PROMPT

    model = _check_model(model)
    validator = ai_gateway.configured_models()["validator"]
    inventory = catalog()["nodes"]
    candidates = [n for n in inventory if n["available"] and
                  all(ollama_nodes._model_available(required, [m["name"] for m in n["models"]])
                      for required in {model, validator})]
    if len(candidates) < 2:
        raise ValueError("Model needs two reachable nodes for verified failover. Previous model retained.")
    proofs = []
    for node in candidates:
        try:
            result = _infer(node["id"],
                messages=[{"role": "system", "content": CLASSIFIER_PROMPT},
                          {"role": "user", "content": json.dumps({
                              "subject": "Synthetic service notice", "body": "Synthetic test: no job outcome.",
                              "sender_email": "test@example.invalid", "recipient_email": "lab@example.invalid"})}],
                schema=SCHEMA, model=model, timeout=60,
                deadline_monotonic=time.monotonic() + 65, max_retries=0,
                workload="recruitment_mail_model_validation")
            _infer(node["id"], messages=[{"role": "system", "content": RELEVANCE_PROMPT},
                   {"role": "user", "content": "Synthetic service notice. No personal hiring process or job outcome."}],
                   schema=RELEVANCE_SCHEMA, model=model, timeout=60, max_retries=0,
                   deadline_monotonic=time.monotonic() + 65, workload="recruitment_mail_model_validation")
            if validator != model:
                _infer(node["id"], messages=[{"role": "system", "content": CLASSIFIER_PROMPT},
                       {"role": "user", "content": "Synthetic service notice; no candidate hiring outcome."}],
                       schema=SCHEMA, model=validator, timeout=60, max_retries=0,
                       deadline_monotonic=time.monotonic() + 65, workload="recruitment_mail_model_validation")
            if result.node_id != node["id"] or result.model != model:
                raise ValueError("Unexpected inference route")
            proofs.append({"node_id": result.node_id, "node_label": result.node_label,
                           "duration_ms": result.duration_ms, "ok": True})
        except (ai_gateway.AIGatewayError, ValueError):
            proofs.append({"node_id": node["id"], "ok": False})
    if sum(p["ok"] for p in proofs) < 2:
        raise ValueError("Structured mail JSON or failover validation failed. Previous model retained.")
    return {"model": model, "ok": True, "nodes": proofs}


def activate(model, revision, actor):
    # No write until all probes pass; compare revision again after slow inference.
    if type(revision) is not int or revision != selection()["revision"]:
        raise ValueError("AI Mail model changed. Refresh before saving.")
    proof = test_model(model)
    with _transaction():
        saved = state()
        if revision != saved.get("revision", 0):
            raise ValueError("AI Mail model changed. Refresh before saving.")
        _write({**saved, "model": proof["model"], "revision": revision + 1,
                "previous_model": selection()["model"], "updated_by": actor,
                "updated_at": time.time(),
                "validated_nodes": [p["node_id"] for p in proof["nodes"] if p["ok"]]})
    return selection()


def request(*, selected, **kwargs):
    """Mail-only node failover; global primary and other workload routes stay intact."""
    eligible = set(selected["validated_nodes"])
    order = [node for node in ollama_nodes.candidate_order(kwargs["model"]) if node in eligible]
    order = [node for node in order if not ollama_nodes.in_cooldown(node)] or order
    last_error = None
    for node in order:
        if node not in eligible:
            continue
        try:
            result = _infer(node, **kwargs)
        except ai_gateway.AIGatewayError as exc:
            # The shared gateway owns breaker accounting. Counting this again
            # would turn one failed inference into two consecutive failures.
            last_error = exc
            continue
        # Commit the outer mail session only after its first call succeeds.
        # Later calls cannot move to another node mid-decision.
        ollama_nodes._remember_decision_node(result.node_id)
        remember(result)
        return result
    raise last_error or ai_gateway.AIGatewayError("No validated AI Mail node is available.", code="OLLAMA_CONNECTION_FAILED")


def remember(result):
    if not getattr(result, "node_id", ""):
        return
    try:
        with _transaction():
            saved = state()
            _write({**saved, "last_used": {"model": result.model, "node_id": result.node_id,
                                           "node_label": result.node_label, "at": time.time()}})
    except (OSError, ValueError):
        # Observability failure must not discard a successfully classified mail.
        logging.getLogger(__name__).warning("AI Mail last-used status could not be persisted")
