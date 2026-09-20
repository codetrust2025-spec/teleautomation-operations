"""Typed candidate attachment metadata and legacy classification helpers."""
from __future__ import annotations

from enum import Enum
from typing import Any


class AttachmentType(str, Enum):
    PAYMENT_PROOF = "payment_proof"
    SLOT_SCREENSHOT_PROOF = "slot_screenshot_proof"
    PROFILE_PHOTO = "profile_photo"


ATTACHMENT_FIELDS = {
    AttachmentType.PAYMENT_PROOF: "payment_proofs",
    AttachmentType.SLOT_SCREENSHOT_PROOF: "slot_screenshot_proofs",
    AttachmentType.PROFILE_PHOTO: "profile_photo",
}


def parse_attachment_type(value: Any) -> AttachmentType:
    if isinstance(value, AttachmentType):
        return value
    raw = str(value or "").strip().lower()
    if not raw:
        raise ValueError("attachment_type is required")
    try:
        return AttachmentType(raw)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in AttachmentType)
        raise ValueError(f"Invalid attachment_type. Expected one of: {allowed}") from exc


def _combined_context(proof: dict, candidate: dict | None = None) -> str:
    metadata = proof.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    values = [
        proof.get("source_module"),
        proof.get("source_endpoint"),
        proof.get("upload_context"),
        proof.get("note"),
        proof.get("original_name"),
        metadata.get("source_module"),
        metadata.get("source_endpoint"),
        metadata.get("upload_context"),
        metadata.get("kind"),
        (candidate or {}).get("source_module"),
    ]
    return " ".join(str(value or "").strip().lower() for value in values)


def classify_legacy_attachment(
    proof: dict, candidate: dict | None = None
) -> AttachmentType | None:
    """Classify only records with affirmative metadata; ambiguity stays reviewable."""
    if not isinstance(proof, dict):
        return None
    explicit = proof.get("attachment_type")
    if explicit:
        try:
            return parse_attachment_type(explicit)
        except ValueError:
            return None

    metadata = proof.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    context = _combined_context(proof, candidate)

    if proof.get("payment_id") or metadata.get("payment_id"):
        return AttachmentType.PAYMENT_PROOF
    if any(
        proof.get(key) or metadata.get(key)
        for key in ("transaction_id", "utr_number", "ledger_entry_id")
    ):
        return AttachmentType.PAYMENT_PROOF
    if proof.get("booking_id") or metadata.get("booking_id"):
        return AttachmentType.SLOT_SCREENSHOT_PROOF
    if proof.get("slot_screenshot_proof_id") or metadata.get("slot_screenshot_proof_id"):
        return AttachmentType.SLOT_SCREENSHOT_PROOF
    payment_markers = (
        "payment proof",
        "payment receipt",
        "payment_upload",
        "/proofs",
        "whatsapp_payment",
        "candidate_payment",
    )
    slot_markers = (
        "slot screenshot",
        "interview screenshot",
        "interview invite",
        "slot-screenshot",
        "slot_booking",
        "interview_evidence",
    )
    profile_markers = ("profile photo", "profile_photo", "candidate avatar")
    if any(marker in context for marker in profile_markers):
        return AttachmentType.PROFILE_PHOTO
    if any(marker in context for marker in slot_markers):
        return AttachmentType.SLOT_SCREENSHOT_PROOF
    if any(marker in context for marker in payment_markers):
        return AttachmentType.PAYMENT_PROOF
    if (
        candidate
        and int(candidate.get("payment") or 0) > 0
        and str(proof.get("id") or "")
        != str(candidate.get("slot_screenshot_proof_id") or "")
    ):
        # Historical payment uploads had no metadata, but the owning ledger row
        # recorded money. Slot uploads always carried a slot note/id.
        return AttachmentType.PAYMENT_PROOF
    return None


def partition_candidate_attachments(
    candidate: dict, *, context: dict | None = None
) -> dict:
    """Return isolated typed collections without mutating the source record.

    `context` is the record legacy attachments are judged against, defaulting
    to the candidate itself. A profile candidate's interview slots are stored
    as separate cloned rows and the payment is recorded on only one of them,
    so a caller holding the whole identity group passes the group's view here.
    Judging a clone in isolation asks "did *this row* take money?" when the
    question is "did this candidate?", and answers no for every clone but one.
    """
    judged_against = context if isinstance(context, dict) else candidate
    payment = list(candidate.get("payment_proofs") or [])
    slots = list(candidate.get("slot_screenshot_proofs") or [])
    profile = candidate.get("profile_photo")
    review: list[dict] = []

    seen: set[str] = {
        str(item.get("id"))
        for item in payment + slots + ([profile] if isinstance(profile, dict) else [])
        if isinstance(item, dict) and item.get("id")
    }

    def place(entry: dict, kind: AttachmentType | None) -> None:
        nonlocal profile
        if kind == AttachmentType.PAYMENT_PROOF:
            payment.append(entry)
        elif kind == AttachmentType.SLOT_SCREENSHOT_PROOF:
            slots.append(entry)
        elif kind == AttachmentType.PROFILE_PHOTO:
            profile = entry
        else:
            review.append({**entry, "review_reason": "legacy_attachment_type_uncertain"})

    # Anything parked as uncertain is re-judged rather than stranded there for
    # good. The pass that parked it — the one-shot migration, or an earlier
    # read of a clone that had no payment of its own — saw less than this one
    # does. An attachment that still classifies as nothing stays in the queue.
    for queued in candidate.get("attachment_review_queue") or []:
        if not isinstance(queued, dict):
            continue
        queued_id = str(queued.get("id") or "")
        if queued_id and queued_id in seen:
            continue
        entry = {key: value for key, value in queued.items() if key != "review_reason"}
        kind = classify_legacy_attachment(entry, judged_against)
        if kind:
            entry["attachment_type"] = kind.value
        place(entry, kind)
        if queued_id:
            seen.add(queued_id)

    for legacy in candidate.get("proofs") or []:
        if not isinstance(legacy, dict):
            continue
        legacy_id = str(legacy.get("id") or "")
        if legacy_id and legacy_id in seen:
            continue
        kind = classify_legacy_attachment(legacy, judged_against)
        migrated = {**legacy, "legacy_storage": True}
        if kind:
            migrated["attachment_type"] = kind.value
        place(migrated, kind)
        if legacy_id:
            seen.add(legacy_id)

    return {
        "payment_proofs": payment,
        "slot_screenshot_proofs": slots,
        "profile_photo": profile if isinstance(profile, dict) else None,
        "attachment_review_queue": review,
    }
