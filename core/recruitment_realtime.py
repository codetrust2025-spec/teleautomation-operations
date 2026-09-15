"""Authenticated, replayable real-time transport for mail monitoring.

Operations code publishes small events here only after durable database writes.
The full email body is never included in a WebSocket payload.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("teleautomation.recruitment_realtime")

_connections: dict[WebSocket, dict[str, Any]] = {}
_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def configure_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def connection_count() -> int:
    return len(_connections)


async def connect(websocket: WebSocket, profile: dict[str, Any]) -> None:
    await websocket.accept()
    _connections[websocket] = profile


async def disconnect(websocket: WebSocket) -> None:
    _connections.pop(websocket, None)


async def _broadcast(payload: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    for websocket, profile in list(_connections.items()):
        if not profile.get("username"):
            dead.append(websocket)
            continue
        try:
            await websocket.send_json(payload)
        except Exception:
            dead.append(websocket)
    for websocket in dead:
        await disconnect(websocket)


async def _broadcast_recorded(event: dict[str, Any]) -> None:
    event_id = str(event["id"])
    await _broadcast({
        "event": event["event_type"],
        "event_id": event_id,
        **(event.get("payload") or {}),
        "created_at": str(event.get("created_at") or ""),
        "delivery_source": "live",
    })
    # Mark only after the coroutine actually ran. Previously publish() marked
    # the row before scheduling this work, so a closing loop could make the
    # durable tailer skip an event that had never reached a socket.
    _mark_delivered(event_id)
    logger.info(
        "Mail realtime event delivered event_id=%s notification_id=%s type=%s clients=%s",
        event_id, (event.get("payload") or {}).get("notification_id"),
        event.get("event_type"), connection_count(),
    )


def deliver_persisted(event: dict[str, Any]) -> None:
    """Fan out a row already committed to the durable realtime log."""
    loop = _loop
    if not loop or not loop.is_running():
        logger.info(
            "Mail realtime event awaits API tailer event_id=%s notification_id=%s type=%s",
            event.get("id"), (event.get("payload") or {}).get("notification_id"),
            event.get("event_type"),
        )
        return
    try:
        future = asyncio.run_coroutine_threadsafe(_broadcast_recorded(event), loop)

        def report_failure(done: Any) -> None:
            try:
                done.result()
            except Exception:
                logger.exception(
                    "Mail realtime delivery failed; durable tailer will retry event_id=%s",
                    event.get("id"),
                )

        future.add_done_callback(report_failure)
    except RuntimeError:
        logger.info(
            "Mail WebSocket loop closed before delivery; durable tailer will retry event_id=%s",
            event.get("id"),
        )


def publish(event_type: str, **payload: Any) -> dict[str, Any]:
    """Persist an event, then fan it out when a server loop is available."""
    from core import recruitment_mail_store as store

    safe = {
        key: value
        for key, value in payload.items()
        if key not in {"body", "html_body", "raw_email", "credential_ciphertext"}
    }
    event = store.record_realtime_event(event_type, safe)
    deliver_persisted(event)
    return {
        "event": event_type,
        "event_id": event["id"],
        **safe,
        "created_at": str(event.get("created_at") or safe.get("created_at") or ""),
    }


# Events this process has already put on the wire. The tailer reads the same
# durable table every publisher writes to, so without this it would send a
# second copy of everything published locally.
_delivered: OrderedDict[str, None] = OrderedDict()
_DELIVERED_LIMIT = 2000


def _mark_delivered(event_id: str) -> None:
    with _lock:
        _delivered[event_id] = None
        while len(_delivered) > _DELIVERED_LIMIT:
            _delivered.popitem(last=False)


def _already_delivered(event_id: str) -> bool:
    with _lock:
        return event_id in _delivered


async def _tail_events(poll_seconds: float = 2.0) -> None:
    """Deliver events written by any process to the clients held by this one.

    `publish` can only broadcast from the process that owns the WebSockets.
    Everything else - the recovery scripts used for the August rescan, a cron,
    a second uvicorn worker - wrote a durable row that no connected browser
    ever saw. This closes that gap without new infrastructure: the table is
    already the replay log clients use on reconnect, so tailing it is the same
    mechanism, read continuously instead of once.
    """
    from core import recruitment_mail_store as store

    cursor: str | None = None
    cursor_ready = False
    try:
        # Start at the present. Older events are already the client's business
        # via the last_event_id replay it performs on connect.
        cursor = await asyncio.to_thread(store.latest_realtime_event_id)
        cursor_ready = True
    except Exception:
        logger.exception("Mail event tailer could not read its starting position")

    while True:
        try:
            await asyncio.sleep(poll_seconds)
            if not cursor_ready:
                # Fail closed. A transient bootstrap failure must not turn
                # after_id=None into a replay of the whole durable table.
                cursor = await asyncio.to_thread(store.latest_realtime_event_id)
                cursor_ready = True
                continue
            if not _connections:
                # Keep the cursor moving so a client connecting later is not
                # met with a backlog it has already replayed for itself.
                try:
                    cursor = await asyncio.to_thread(store.latest_realtime_event_id)
                except Exception:
                    cursor_ready = False
                    logger.exception("Mail event tailer could not advance its cursor")
                continue
            rows = await asyncio.to_thread(
                store.list_realtime_events, after_id=cursor, limit=200,
            )
            for row in rows:
                cursor = row["id"]
                if _already_delivered(row["id"]):
                    continue
                await _broadcast({
                    "event": row["event_type"],
                    "event_id": row["id"],
                    **(row.get("payload") or {}),
                    "created_at": str(row.get("created_at") or ""),
                    "delivery_source": "live",
                })
                _mark_delivered(row["id"])
        except asyncio.CancelledError:
            raise
        except Exception:
            # A tailer that dies takes live delivery with it and says nothing.
            logger.exception("Mail event tailer iteration failed; continuing")


async def _push_node_activity(poll_seconds: float = 0.5) -> None:
    """Tell connected dashboards what each AI node is serving, as it changes.

    Node activity is live state, not history: it is sent to the sockets open
    now and never written to the durable event log, so a reconnecting client
    is not replayed a stale "busy" and reads the current snapshot instead. The
    whole snapshot goes out each time, so one missed push corrects itself on
    the next.
    """
    from core import ai_activity

    sent = ai_activity.version()
    while True:
        try:
            await asyncio.sleep(poll_seconds)
            if not _connections:
                # Nobody to tell. A client that connects later reads the
                # snapshot itself rather than being sent one unasked, so the
                # socket's opening frames stay exactly what they were.
                sent = ai_activity.version()
                continue
            if ai_activity.version() == sent:
                continue
            snapshot = ai_activity.node_snapshot()
            await _broadcast({"event": "ai_node_activity", **snapshot})
            sent = snapshot["version"]
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AI node activity push failed; continuing")


_tailer: asyncio.Task | None = None
_activity_pusher: asyncio.Task | None = None


def start_activity_pusher() -> None:
    """Start the node activity push on the running loop, once per process."""
    global _activity_pusher
    if _activity_pusher is not None and not _activity_pusher.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.info("No running loop; AI node activity push not started")
        return
    _activity_pusher = loop.create_task(_push_node_activity())


async def stop_activity_pusher() -> None:
    global _activity_pusher
    task, _activity_pusher = _activity_pusher, None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


def start_tailer() -> None:
    """Start the tailer on the running loop, once per process."""
    global _tailer
    if _tailer is not None and not _tailer.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.info("No running loop; mail event tailer not started")
        return
    configure_loop(loop)
    _tailer = loop.create_task(_tail_events())
    logger.info("Mail event tailer started")


async def stop_tailer() -> None:
    global _tailer
    task, _tailer = _tailer, None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
