"""Typed client for Operations-to-Marketing notification delivery."""

from __future__ import annotations

import asyncio
import os
import hashlib
import httpx
import logging

from services import cross_project_outbox as outbox

logger = logging.getLogger(__name__)
_dispatcher_task: asyncio.Task | None = None


# fetch_crm_operational_summary() was removed with Daily Briefing, its only
# caller. It read Marketing's `/internal/v1/operational-summary` projection.
# That Marketing endpoint is untouched and still serves the contract in
# docs/migration/cross-project-contracts.md — only this consumer is gone, so a
# future Operations feature can call it again without a Marketing change.


async def send_notification(*, title: str, body: str, tag: str, whatsapp_text: str = "") -> None:
    payload = {"title": title, "body": body, "tag": tag, "whatsapp_text": whatsapp_text}
    idempotency_key = hashlib.sha256(f"notification|{tag}|{title}|{body}|{whatsapp_text}".encode()).hexdigest()
    outbox.enqueue(event_type="marketing.notification.v1", idempotency_key=idempotency_key, payload=payload)
    await dispatch_notifications_once()


async def dispatch_notifications_once() -> int:
    """Best-effort delivery; failures remain durable for bounded retries."""
    base = os.getenv("MESSAGING_INTERNAL_URL", "http://127.0.0.1:8000").rstrip("/")
    token = os.getenv("INTERNAL_SERVICE_TOKEN", "").strip()
    delivered = 0
    if not token:
        return delivered
    async with httpx.AsyncClient(timeout=30.0) as client:
        for row in outbox.due(event_type="marketing.notification.v1"):
            key = row["idempotency_key"]
            try:
                response = await client.post(
                    f"{base}/internal/v1/notifications",
                    json=row["payload"],
                    headers={"X-Internal-Service-Token": token, "X-Idempotency-Key": key},
                )
                response.raise_for_status()
                outbox.mark_delivered(key)
                delivered += 1
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                outbox.mark_failed(key, f"{type(exc).__name__}: {exc}")
    return delivered


DISPATCH_INTERVAL_SEC = 30


async def _dispatcher_loop() -> None:
    # The body used to be unguarded: one unexpected error (an unreadable outbox
    # file, a client that could not be built) ended the task, and every later
    # notification stayed "pending" forever with nothing in the log.
    from core.job_heartbeats import beat

    while True:
        try:
            await dispatch_notifications_once()
            beat("outbox_dispatcher", interval_sec=DISPATCH_INTERVAL_SEC)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Outbox dispatch pass failed; will retry")
            beat("outbox_dispatcher", interval_sec=DISPATCH_INTERVAL_SEC, ok=False, error=repr(exc))
        await asyncio.sleep(DISPATCH_INTERVAL_SEC)


def start_outbox_dispatcher() -> None:
    global _dispatcher_task
    if _dispatcher_task is None or _dispatcher_task.done():
        _dispatcher_task = asyncio.create_task(_dispatcher_loop(), name="operations-marketing-outbox")


async def stop_outbox_dispatcher() -> None:
    global _dispatcher_task
    if _dispatcher_task is None:
        return
    _dispatcher_task.cancel()
    try:
        await _dispatcher_task
    except asyncio.CancelledError:
        pass
    _dispatcher_task = None
