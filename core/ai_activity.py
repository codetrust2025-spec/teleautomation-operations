"""Which inference node is doing what, right now.

The gateway is the one place that knows where a request really runs: it picks
the node, waits for that machine's slot, and moves the request on when a node
refuses it. It reports each of those moments here, and everything that names a
node reads this record -- the booking page and the dashboard uploads saying
which node is reading a screenshot or a resume, and the AI nodes panel marking
that node busy. None of them infers the node from configuration or the routing
table, so they cannot disagree with each other or with the machine that
actually did the work.

Nothing here chooses a node or changes how one is chosen.

State is held in memory. uvicorn runs a single worker, so the booking
endpoints, their worker threads and the mail worker all report into this one
process; a separate process -- a `docker exec` probe -- has its own empty copy.
"""

from __future__ import annotations

import contextvars
import itertools
import json
import logging
import re
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Iterator

from core import ollama_nodes

logger = logging.getLogger("teleautomation.ai_activity")

BOOKING_ANALYSIS = "booking_analysis"
PAYMENT_ANALYSIS = "payment_analysis"
RESUME_ANALYSIS = "resume_analysis"

_KIND_LABELS = {
    BOOKING_ANALYSIS: "Booking analysis",
    PAYMENT_ANALYSIS: "Payment analysis",
    "invite_analysis": "Invite analysis",
    RESUME_ANALYSIS: "Resume analysis",
    "mail_analysis": "Mail analysis",
    "ai_request": "AI request",
}

# Work that is not inside an analysis of a named kind is named from its
# workload, first matching prefix wins. A payment screenshot read from the admin
# dashboard is a payment analysis, not a booking one: only the booking endpoints
# say "booking".
_WORKLOAD_KINDS = (
    ("payment_screenshot", "payment_analysis"),
    ("interview_screenshot", "invite_analysis"),
    ("resume", "resume_analysis"),
    ("recruitment_", "mail_analysis"),
    ("calendar_invite", "mail_analysis"),
)

# A request back in the queue this long is waiting for a node, not between two
# steps. Shorter than this, it is choosing one -- a health probe per candidate
# node -- and the page keeps naming the node that served it last rather than
# flickering to "waiting" for the length of a probe.
QUEUE_GRACE_SECONDS = 1.5

# Finished analyses are kept long enough for a slow page to read the result,
# and never grow without bound: ids arrive from the public booking page.
ANALYSIS_TTL_SECONDS = 15 * 60
ANALYSIS_LIMIT = 500

# Live status streams held open at once, across every visitor.
MAX_STREAMS = 64

_ANALYSIS_ID = re.compile(r"[0-9a-f]{32}")

# Names this process. The activity counter starts again at zero on every
# restart, so a client comparing counters needs to know when it has.
BOOT_ID = uuid.uuid4().hex

_lock = threading.Lock()
_call_ids = itertools.count(1)
_calls: dict[int, dict[str, Any]] = {}
_analyses: OrderedDict[str, dict[str, Any]] = OrderedDict()
_version = 0
_streams = 0

_current_analysis: contextvars.ContextVar[str] = contextvars.ContextVar(
    "booking_analysis_id", default=""
)


def kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, _KIND_LABELS["ai_request"])


def kind_for(workload: str, analysis_kind: str = "") -> str:
    if analysis_kind:
        return analysis_kind
    name = str(workload or "")
    for prefix, kind in _WORKLOAD_KINDS:
        if name.startswith(prefix):
            return kind
    return "ai_request"


def _node_label(node_id: str) -> str:
    """The registry's display name, so a node added there is named here too."""
    try:
        return str(ollama_nodes.node(node_id).get("label") or node_id)
    except ValueError:
        return node_id


def _bump() -> None:
    global _version
    _version += 1


def version() -> int:
    """Changes whenever any node starts or stops serving a request."""
    return _version


# ── gateway calls ────────────────────────────────────────────────────────────


class Call:
    """One gateway request, from arrival until it returns or raises."""

    __slots__ = ("id",)

    def __init__(self, call_id: int) -> None:
        self.id = call_id

    def running(self, node_id: str) -> None:
        """The request holds this node's slot and the model call is in flight."""
        label = _node_label(node_id)
        with _lock:
            call = _calls.get(self.id)
            if call is None:
                return
            call.update(
                state="running",
                node_id=node_id,
                node_label=label,
                since=time.monotonic(),
                started_at=time.time(),
            )
            _bump()

    def released(self, *, answered: bool) -> None:
        """The node let go: it answered, or it failed and the request moves on."""
        now = time.monotonic()
        with _lock:
            call = _calls.get(self.id)
            if call is None or call["state"] != "running":
                return
            node = (call["node_id"], call["node_label"])
            call.update(state="waiting", node_id="", node_label="", since=now)
            record = _analyses.get(call["analysis_id"]) if call["analysis_id"] else None
            if record is not None:
                if answered:
                    if node not in record["served"]:
                        record["served"].append(node)
                    record["served_at"] = now
                else:
                    record["failed"].append(node)
                    record["failed_at"] = now
            _bump()

    def end(self) -> None:
        with _lock:
            call = _calls.pop(self.id, None)
            if call is not None and call["state"] == "running":
                _bump()


def begin_call(workload: str) -> Call:
    """Register a request as it reaches the gateway, before any node is chosen."""
    analysis_id = _current_analysis.get()
    call_id = next(_call_ids)
    with _lock:
        record = _analyses.get(analysis_id) if analysis_id else None
        _calls[call_id] = {
            "workload": str(workload or ""),
            "analysis_id": analysis_id if record is not None else "",
            "kind": kind_for(workload, record["kind"] if record is not None else ""),
            "state": "waiting",
            "node_id": "",
            "node_label": "",
            "since": time.monotonic(),
            "started_at": 0.0,
        }
    return Call(call_id)


def node_snapshot() -> dict[str, Any]:
    """What every node is serving at this moment, keyed by node id.

    Only requests that hold a node's slot count. A request queued for a node
    is not using it, and calling that node busy would name the wrong cause.
    """
    with _lock:
        nodes: dict[str, list[dict[str, Any]]] = {}
        for call in sorted(_calls.values(), key=lambda item: item["started_at"]):
            if call["state"] != "running":
                continue
            nodes.setdefault(call["node_id"], []).append({
                "kind": call["kind"],
                "label": kind_label(call["kind"]),
                "workload": call["workload"],
                "started_at": call["started_at"],
            })
        return {"boot": BOOT_ID, "version": _version, "nodes": nodes}


# ── analyses: one upload, followed by the page that sent it ──────────────────


def valid_analysis_id(value: Any) -> bool:
    return bool(_ANALYSIS_ID.fullmatch(str(value or "")))


def _expire(now: float) -> None:
    for analysis_id in list(_analyses):
        record = _analyses[analysis_id]
        reference = record["finished_at"] or record["created"]
        if now - reference > ANALYSIS_TTL_SECONDS:
            del _analyses[analysis_id]
    while len(_analyses) > ANALYSIS_LIMIT:
        _analyses.popitem(last=False)


@contextmanager
def analysis(analysis_id: str = "", kind: str = "") -> Iterator[str]:
    """Attribute every model call made inside this block to one upload.

    The id comes from the page, which generates it before uploading so it can
    follow the analysis while the upload is still in flight. A missing or
    malformed one is replaced: the analysis is still tracked, and the node still
    shows as busy with it, the page simply cannot follow it live.

    `kind` is how the AI nodes panel names the work ("Payment analysis"). Left
    empty, each call is named from its own workload, as work outside any
    analysis is.

    The context variable is copied into `asyncio.to_thread` workers, which is
    how the gateway, running in a worker thread, knows which upload a call
    belongs to without that being threaded through every extractor.
    """
    analysis_id = analysis_id if valid_analysis_id(analysis_id) else uuid.uuid4().hex
    now = time.monotonic()
    with _lock:
        _expire(now)
        _analyses[analysis_id] = {
            "kind": kind,
            "created": now,
            "finished_at": 0.0,
            "served": [],
            "failed": [],
            "served_at": 0.0,
            "failed_at": 0.0,
        }
        _analyses.move_to_end(analysis_id)
    token = _current_analysis.set(analysis_id)
    try:
        yield analysis_id
    finally:
        _current_analysis.reset(token)
        with _lock:
            record = _analyses.get(analysis_id)
            if record is not None:
                record["finished_at"] = time.monotonic()
                served = ",".join(node_id for node_id, _ in record["served"]) or "-"
                failed = ",".join(node_id for node_id, _ in record["failed"]) or "-"
                seconds = record["finished_at"] - record["created"]
        if record is not None:
            # Production filters ordinary INFO traffic, and this line is the
            # server's own account of which machine read the upload -- the
            # thing to compare with what the page displayed.
            logger.warning(
                "%s finished analysis_id=%s analysed_by=%s failed_on=%s seconds=%.1f",
                kind_label(kind) if kind else "AI analysis",
                analysis_id, served, failed, seconds,
            )


def booking_analysis(analysis_id: str = ""):
    """An analysis of one upload from the public booking page."""
    return analysis(analysis_id, kind=BOOKING_ANALYSIS)


def _failed_elsewhere(record: dict[str, Any], answering: list[str]) -> list[str]:
    """Nodes that failed this analysis and did not go on to serve it.

    A node that failed one attempt and answered a later one did not hand the
    work to another machine, so it is not reported as a failover.
    """
    labels: list[str] = []
    for _, label in record["failed"]:
        if label not in answering and label not in labels:
            labels.append(label)
    return labels


def analysis_status(analysis_id: str) -> dict[str, Any] | None:
    """What the page shows for one upload, or None if it is unknown.

    `running` names the node serving it now; between two model calls it keeps
    naming the node that served the last one. `waiting` covers the time before
    any node has taken it, a request queued behind other work, and a node that
    has just failed while the request moves elsewhere. `done` lists every node
    that answered, in order. `failed_on` names the nodes the request moved away
    from, so a failover can be shown as one rather than as a change of name.
    """
    now = time.monotonic()
    with _lock:
        _expire(now)
        record = _analyses.get(analysis_id)
        if record is None:
            return None
        served = [label for _, label in record["served"]]
        status: dict[str, Any] = {"analysis_id": analysis_id, "analysed_by": served}
        if record["finished_at"]:
            return {**status, "state": "done", "node": served[-1] if served else None,
                    "failed_on": _failed_elsewhere(record, served)}
        mine = [call for call in _calls.values() if call["analysis_id"] == analysis_id]
        running = sorted(
            (call for call in mine if call["state"] == "running"),
            key=lambda call: call["since"],
        )
        if running:
            node = running[-1]["node_label"]
            return {**status, "state": "running", "node": node,
                    "failed_on": _failed_elsewhere(record, [*served, node])}
        reassigning = bool(mine) and record["failed_at"] > record["served_at"]
        queued = any(now - call["since"] >= QUEUE_GRACE_SECONDS for call in mine)
        if served and not reassigning and not queued:
            return {**status, "state": "running", "node": served[-1],
                    "failed_on": _failed_elsewhere(record, served)}
        return {**status, "state": "waiting", "node": None,
                "failed_on": _failed_elsewhere(record, served)}


def analysis_view(analysis_id: str) -> dict[str, Any]:
    """The status of one analysis; an id not known (yet) reads as waiting.

    The upload it belongs to may still be arriving, and saying "unknown" would
    only tell a caller which ids exist.
    """
    return analysis_status(analysis_id) or {
        "analysis_id": analysis_id, "state": "waiting", "node": None,
        "analysed_by": [], "failed_on": [],
    }


def with_analysis(response: Any, analysis_id: str) -> Any:
    """Add the finished analysis -- which nodes read the upload -- to a response.

    The page follows the analysis live, but the upload's own response is the
    last word: it arrives after every model call has returned, so the node
    named as having analysed the upload can never lag behind the work. Refusals
    carry it too, since a refused upload was still read by a node.
    """
    view = analysis_view(analysis_id)
    if isinstance(response, dict):
        return {**response, "analysis": view}
    # Imported here so the record stays free of the web layer for everything
    # else that reports into it.
    from fastapi.responses import JSONResponse

    if isinstance(response, JSONResponse):
        try:
            payload = json.loads(response.body)
        except ValueError:
            return response
        if isinstance(payload, dict):
            payload["analysis"] = view
            return JSONResponse(payload, status_code=response.status_code)
    return response


def open_stream() -> bool:
    global _streams
    with _lock:
        if _streams >= MAX_STREAMS:
            return False
        _streams += 1
        return True


def close_stream() -> None:
    global _streams
    with _lock:
        _streams = max(0, _streams - 1)


def reset() -> None:
    """Test seam: forget every call, analysis and stream."""
    global _version, _streams
    with _lock:
        _calls.clear()
        _analyses.clear()
        _version = 0
        _streams = 0
