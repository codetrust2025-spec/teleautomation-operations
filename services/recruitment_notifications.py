"""Internal notifications for recruitment detections and mailbox failures.

These used to import `features.web_push`, a module that does not exist in this
repository, inside a bare `except Exception: pass`. Every call therefore did
nothing, silently: a mailbox that failed to sync three times (or hit the
dead-letter queue) and an offer/selection detection never reached anyone.

They now go through the same durable path as every other notification
(`cross_project_outbox`, delivered to Marketing by the outbox dispatcher), which
is synchronous to enqueue and so safe to call from the mail worker's threads.
A failure to enqueue is logged, never swallowed.
"""
from __future__ import annotations

import hashlib
import logging

from core.ist_time import ist_now
from services import cross_project_outbox as outbox

logger = logging.getLogger(__name__)

EVENT_TYPE = "marketing.notification.v1"


def _enqueue(title: str, body: str, tag: str) -> None:
    payload = {"title": title, "body": body, "tag": tag, "whatsapp_text": ""}
    # Same key recipe as services.messaging_client.send_notification, so an
    # identical notification is one outbox row however it was raised.
    key = hashlib.sha256(f"notification|{tag}|{title}|{body}|".encode()).hexdigest()
    outbox.enqueue(event_type=EVENT_TYPE, idempotency_key=key, payload=payload)


def notify_detection(event: dict) -> None:
    from core.recruitment_offer_visibility import should_show_in_selection_offer_review

    if not should_show_in_selection_offer_review(event):
        return
    try:
        title = str(event["primary_status"]).replace("_", " ").title()
        confidence = f"{round(float(event.get('confidence') or 0) * 100)}% confidence"
        body = " · ".join(x for x in [event.get("company_name"), event.get("job_title"), confidence] if x)
        _enqueue(title, body, f"recruitment:{event['id']}")
    except Exception:  # noqa: BLE001 - a notification must never break mail processing
        logger.warning("Recruitment detection notification could not be queued", exc_info=True)


def notify_system(title: str, body: str, tag: str) -> None:
    """One notification per tag per IST day.

    The tag used to be constant per mailbox, and the outbox de-duplicates on it,
    so a mailbox that failed again after being fixed would never notify twice.
    """
    try:
        _enqueue(title, body, f"{tag}:{ist_now():%Y-%m-%d}")
    except Exception:  # noqa: BLE001
        logger.warning("System notification could not be queued: %s", title, exc_info=True)
