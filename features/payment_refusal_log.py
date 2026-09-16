"""Why a payment was refused, in a form that survives the request.

A refused upload is never stored -- the payee check fails closed and nothing is
written before it passes -- and the detailed refusal line the code already
writes goes to a module logger that production's container log does not carry.
So a live refusal left nothing behind at all: diagnosing the ₹5,000 Ravinder
receipt meant reconstructing the extraction by probing the engine with the facts
a person had read off the screenshot by eye.

This records the decision, never the receipt. Identifiers are masked to their
provider and last four characters, which is what a registry check needs and what
a support conversation quotes; the screenshot itself is not stored, and nothing
here changes what is accepted.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Keep the file small enough to read and old enough to be useful.
MAX_ENTRIES = 500

_DIGITS = re.compile(r"\D")


def _log_path() -> Path:
    base = os.getenv("PAYMENT_REFUSAL_LOG_FILE")
    if base:
        return Path(base)
    data_dir = os.getenv("OPERATIONS_DATA_DIR") or "/var/lib/teleautomation-operations"
    return Path(data_dir) / "payment_refusals.jsonl"


def mask_identifier(value: Any) -> str:
    """A handle reduced to what a registry check needs: provider and tail.

    `raviarvind1111@ybl` becomes `…1111@ybl`, `+918639074573` becomes `…4573`.
    Enough to tell two accounts apart and to compare against the registry;
    not enough to pay anyone.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if "@" in text:
        local, _, domain = text.partition("@")
        return f"…{local[-4:]}@{domain}" if len(local) > 4 else f"…@{domain}"
    digits = _DIGITS.sub("", text)
    return f"…{digits[-4:]}" if len(digits) >= 4 else "…"


def record_refusal(
    *,
    source_module: str,
    verification_state: str,
    reason_codes: list[str] | tuple[str, ...],
    result: dict[str, Any],
    elapsed_ms: int | None = None,
    evidence_id: str = "",
) -> dict[str, Any] | None:
    """Append one masked decision record. Never raises into the caller."""
    try:
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "source_module": str(source_module or ""),
            "evidence_id": str(evidence_id or result.get("idempotency_key") or ""),
            "verification_state": str(verification_state or ""),
            "reason_codes": list(reason_codes or []),
            "receiver": {
                "type": result.get("receiver_type"),
                "match": result.get("receiver_match"),
                "score": result.get("receiver_match_score"),
                "registry_id": result.get("receiver_registry_id"),
                "name_seen": bool(str(result.get("receiver_name") or "").strip()),
                "upi": mask_identifier(result.get("receiver_upi_id")),
                "phone": mask_identifier(
                    result.get("receiver_phone_number") or result.get("receiver_phone")),
                "account": mask_identifier(
                    result.get("receiver_account") or result.get("receiver_account_identifier")),
                "masked_in_source": bool(result.get("receiver_identifier_masked")),
            },
            "extraction": {
                "amount": result.get("amount"),
                "amount_readable": int(result.get("amount") or 0) > 0,
                "status": str(result.get("status") or ""),
                "has_utr": bool(str(result.get("utr_number") or "").strip()),
                "has_transaction_id": bool(str(result.get("transaction_id") or "").strip()),
                "has_reference": bool(str(result.get("reference_number") or "").strip()),
                "confidence": result.get("confidence_score"),
                "is_payment_screenshot": bool(result.get("is_payment_screenshot")),
            },
            "ai": {
                "model": str(result.get("primary_model") or result.get("model") or ""),
                "node": str(result.get("analysed_by") or result.get("ai_node") or ""),
                "elapsed_ms": elapsed_ms,
            },
        }
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        lines.append(json.dumps(entry, default=str))
        path.write_text("\n".join(lines[-MAX_ENTRIES:]) + "\n", encoding="utf-8")
        return entry
    except Exception:  # diagnostics must never cost a payment decision
        return None


def recent_refusals(limit: int = 20) -> list[dict[str, Any]]:
    """The most recent decisions, newest last, for support and debugging."""
    path = _log_path()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[-max(1, limit):]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows
