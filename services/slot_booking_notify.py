"""Notify team when a slot is booked via submit-slot (web push + optional WhatsApp)."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _slot_summary(row: dict) -> str:
    name = (row.get("name") or "Candidate").strip()
    day = (row.get("date") or "")[:10]
    start = (row.get("time") or "").strip()[:5]
    end = (row.get("time_end") or "").strip()[:5]
    tech = (row.get("technology") or "").strip()
    rnd = (row.get("interview_round") or "").strip()
    parts = [name]
    if day and start:
        parts.append(f"{day} {start}" + (f"–{end}" if end else ""))
    if rnd:
        parts.append(rnd)
    if tech and tech not in {"", "Unspecified"}:
        parts.append(tech)
    return " · ".join(parts)


def _wa_group_message(row: dict) -> str:
    """Copy-paste friendly line for the Interview slots WhatsApp group."""
    name = (row.get("name") or "Candidate").strip()
    day = (row.get("date") or "")[:10]
    start = (row.get("time") or "").strip()[:5]
    end = (row.get("time_end") or "").strip()[:5]
    tech = (row.get("technology") or "").strip()
    rnd = (row.get("interview_round") or "").strip()
    lines = [f"📅 Interview slot — {name}"]
    if day and start:
        slot = f"{day} {start}"
        if end:
            slot += f" – {end}"
        lines.append(slot)
    if rnd:
        lines.append(f"Round: {rnd}")
    if tech and tech not in {"", "Unspecified"}:
        lines.append(f"Tech: {tech}")
    public_url = (os.environ.get("OPERATIONS_PUBLIC_URL") or "").strip().rstrip("/")
    if public_url:
        lines.append(f"(booked via {public_url}/submit-slot)")
    return "\n".join(lines)


async def notify_slot_booked(row: dict, *, action: str = "assigned") -> None:
    if action in {"skip_exists"}:
        return
    summary = _slot_summary(row)
    title = "Interview slot booked"
    body = summary
    wa_text = _wa_group_message(row)

    from services.messaging_client import send_notification

    try:
        await send_notification(
            title=title,
            body=body,
            tag=f"slot:{row.get('id') or summary}",
            whatsapp_text=wa_text,
        )
    except Exception as exc:
        logger.warning("Slot-booked notification could not be queued: %s", exc, exc_info=True)


def format_slot_reminder(row: dict, *, minutes: int = 30) -> tuple[str, str]:
    name = (row.get("name") or "Candidate").strip()
    start = (row.get("time") or "").strip()[:5]
    title = f"Interview in {minutes} min — {name}"
    body = f"{(row.get('date') or '')[:10]} {start} · {(row.get('technology') or '').strip() or 'Interview'}"
    return title, body
