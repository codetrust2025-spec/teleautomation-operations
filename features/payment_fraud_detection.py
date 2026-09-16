"""Deterministic duplicate and risk checks for candidate payment proofs."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

PAYMENT_REUSE_ALLOWED_MESSAGE = "Previous booking cancelled — payment can be reused."
PAYMENT_REUSE_BLOCKED_MESSAGE = (
    "This payment is already linked to an active or completed booking."
)


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def payment_transaction_identities(extraction: dict[str, Any] | None) -> set[str]:
    details = extraction or {}
    return {
        identity
        for key in ("utr_number", "transaction_id", "reference_number")
        if (identity := _norm(details.get(key)))
        and len(identity) >= 8
    }


def payment_transaction_identity(extraction: dict[str, Any] | None) -> str:
    identities = payment_transaction_identities(extraction)
    return sorted(identities, key=lambda value: (-len(value), value))[0] if identities else ""


def _booking_status(candidate_store, row: dict[str, Any]) -> str:
    attendance = candidate_store.row_interview_attendance_status(row)
    if attendance:
        return attendance
    if str(row.get("stage") or "").strip().lower() == "completed":
        return "completed"
    if row.get("slot_confirmed"):
        return "confirmed"
    return "active"


def assess_payment_proof(
    raw: bytes,
    extraction: dict[str, Any] | None,
    *,
    candidate_id: str = "",
    candidate_name: str = "",
    candidate_phone: str = "",
) -> dict[str, Any]:
    from features import candidate_store

    digest = hashlib.sha256(raw).hexdigest()
    extracted = extraction or {}
    transaction_identities = payment_transaction_identities(extracted)
    transaction_identity = payment_transaction_identity(extracted)
    status = str(extracted.get("status") or "").strip().lower()
    matches = []
    reasons = []
    if not transaction_identities:
        reasons.append(
            "The transaction or UTR reference is not visible. Open the payment in PhonePe / Google Pay, tap View Details, and upload that screenshot -- a payment summary does not show the UTR or Transaction ID."
        )
    for row in candidate_store._load().get("candidates") or []:
        for proof in candidate_store.list_attachments(
            str(row.get("id") or ""), "payment_proof"
        ) or []:
            matching_identities = transaction_identities.intersection(
                payment_transaction_identities(proof)
            )
            if not matching_identities:
                continue
            matched_identity = sorted(matching_identities, key=lambda value: (-len(value), value))[0]
            row_id = str(row.get("id") or "")
            requested_phone = candidate_store.candidate_phone_identity(candidate_phone)
            stored_phone = candidate_store.candidate_phone_identity(row.get("phone"))
            same_candidate = bool(
                (candidate_id and row_id == str(candidate_id))
                or (requested_phone and stored_phone == requested_phone)
            )
            booking_status = _booking_status(candidate_store, row)
            already_rebooked = bool(
                row.get("paymentReusedByBookingId")
                or row.get("payment_reused_by_booking_id")
            )
            matches.append({
                "candidate_id": row_id,
                "candidate_name": row.get("name"),
                "candidate_phone": row.get("phone") or "",
                "proof_id": proof.get("id"),
                "match": "transaction",
                "transaction_identity": matched_identity,
                "booking_status": booking_status,
                "same_candidate": same_candidate,
                "already_rebooked": already_rebooked,
                "reuse_allowed": bool(
                    same_candidate
                    and booking_status in {"cancelled", "not_attended"}
                    and not already_rebooked
                ),
            })
    reuse_allowed = bool(matches) and all(match["reuse_allowed"] for match in matches)
    if matches and not reuse_allowed:
        reasons.append(PAYMENT_REUSE_BLOCKED_MESSAGE)
    if status in {"failed", "failure", "declined", "reversed"}:
        reasons.append(f"The transaction status is {status}.")
    warnings = list(extracted.get("warnings") or [])
    if status in {"pending", "processing"}:
        warnings.append(f"Transaction status is {status}; manual confirmation is required.")
    confidence = int(extracted.get("confidence_score") or 0)
    if extracted and confidence < 55:
        warnings.append("Payment details have low extraction confidence.")
    decision = "rejected" if reasons else "reusable" if reuse_allowed else "needs_review" if warnings else "verified"
    reuse_match = matches[0] if reuse_allowed else {}
    return {
        "decision": decision,
        "verified": decision in {"verified", "reusable"},
        "reasons": reasons,
        "warnings": list(dict.fromkeys(warnings)),
        "duplicate_matches": matches,
        "sha256": digest,
        "utr_number": transaction_identity,
        "transaction_identity": transaction_identity,
        "reuse_allowed": reuse_allowed,
        "message": PAYMENT_REUSE_ALLOWED_MESSAGE if reuse_allowed else "",
        "previousBookingId": reuse_match.get("candidate_id") or "",
        "reusedPaymentId": reuse_match.get("proof_id") or "",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "candidate_id": candidate_id,
        "candidate_name": candidate_name,
        "candidate_phone": candidate_phone,
    }
