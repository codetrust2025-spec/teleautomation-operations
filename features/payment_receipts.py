"""Proof-derived received totals.

The money a candidate has actually paid is whatever their verified payment
proofs add up to — not whatever someone typed into the Received field. This
module owns that derivation: which proof states count, how two uploads of the
same transaction collapse into one, and what the resulting figures are.

Two deliberate boundaries:

* Only VERIFIED proofs contribute. Pending, failed, duplicate and rejected
  evidence is visible but worth ₹0 until a reviewer verifies it.
* A row with no proof evidence at all keeps its recorded amount. Most of the
  historical roster predates proof capture, and deriving those from an empty
  proof list would silently erase real payments. Those rows are reported as
  unevidenced instead — see `mismatch_report`.
"""
from __future__ import annotations

from typing import Any, Iterable

# Engine states that represent money confirmed as received.
VERIFIED_PROOF_STATES = frozenset({
    "VERIFIED_COMPANY_PAYMENT",
    "VERIFIED_REFERRER_PAYMENT",
})

# Explicit lifecycle shown to reviewers, mapped from the verification engine's
# internal states. Anything unrecognised is treated as needing review rather
# than silently counting or silently vanishing.
PROOF_STATUS_PENDING_EXTRACTION = "PENDING_EXTRACTION"
PROOF_STATUS_EXTRACTION_FAILED = "EXTRACTION_FAILED"
PROOF_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
PROOF_STATUS_VERIFIED = "VERIFIED"
PROOF_STATUS_DUPLICATE = "DUPLICATE"
PROOF_STATUS_REJECTED = "REJECTED"

PROOF_STATUSES = (
    PROOF_STATUS_PENDING_EXTRACTION,
    PROOF_STATUS_EXTRACTION_FAILED,
    PROOF_STATUS_NEEDS_REVIEW,
    PROOF_STATUS_VERIFIED,
    PROOF_STATUS_DUPLICATE,
    PROOF_STATUS_REJECTED,
)

# File availability, mirrored from the verification engine so the receipt layer
# can refuse evidence it cannot re-read without importing the engine.
FILE_AVAILABLE = "AVAILABLE"
FILE_MISSING = "MISSING_FILE"
FILE_CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
FILE_UNREADABLE = "UNREADABLE"
FILE_ARCHIVED = "ARCHIVED"

FILE_STATES_BLOCKING_VERIFICATION = frozenset({
    FILE_MISSING,
    FILE_CHECKSUM_MISMATCH,
    FILE_UNREADABLE,
})

_ENGINE_STATE_TO_STATUS = {
    "AMOUNT_EXTRACTION_REVIEW_REQUIRED": "NEEDS_REVIEW",
    "UPLOADED": PROOF_STATUS_PENDING_EXTRACTION,
    "EXTRACTION_IN_PROGRESS": PROOF_STATUS_PENDING_EXTRACTION,
    "EXTRACTED": PROOF_STATUS_NEEDS_REVIEW,
    "EXTRACTION_FAILED": PROOF_STATUS_EXTRACTION_FAILED,
    "VERIFIED_COMPANY_PAYMENT": PROOF_STATUS_VERIFIED,
    "VERIFIED_REFERRER_PAYMENT": PROOF_STATUS_VERIFIED,
    "PENDING_MANUAL_REVIEW": PROOF_STATUS_NEEDS_REVIEW,
    "INCOMPLETE_PAYMENT_EVIDENCE": PROOF_STATUS_NEEDS_REVIEW,
    "UNKNOWN_RECEIVER": PROOF_STATUS_NEEDS_REVIEW,
    "DUPLICATE_PAYMENT": PROOF_STATUS_DUPLICATE,
    "FAILED_PAYMENT": PROOF_STATUS_REJECTED,
    "REJECTED": PROOF_STATUS_REJECTED,
    "REVERSED": PROOF_STATUS_REJECTED,
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _coerce_bool_like(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _clean(value).lower() in {"true", "yes", "1"}


def _norm_ref(value: Any) -> str:
    """Fold a transaction reference for comparison — case and spacing vary
    between the bank app, the OCR pass and manual entry."""
    return "".join(_clean(value).lower().split())


def file_availability(proof: dict[str, Any]) -> str:
    if not isinstance(proof, dict):
        return FILE_AVAILABLE
    return _clean(proof.get("file_availability")).upper() or FILE_AVAILABLE


def proof_status(proof: dict[str, Any]) -> str:
    """Explicit lifecycle status for one proof."""
    if not isinstance(proof, dict):
        return PROOF_STATUS_NEEDS_REVIEW
    if file_availability(proof) in FILE_STATES_BLOCKING_VERIFICATION:
        # The evidence cannot be re-read, so whatever verdict it carries can no
        # longer be relied on. It is not rejected — the payment may well be
        # real — it simply needs a human and a replacement file.
        return PROOF_STATUS_NEEDS_REVIEW
    if _coerce_bool_like(proof.get("blocks_automatic_reconciliation")):
        return PROOF_STATUS_NEEDS_REVIEW
    state = _clean(proof.get("verification_state")).upper()
    if not state:
        # No engine verdict recorded at all: either it never ran or this is a
        # legacy upload. Either way a human decides, and it counts for nothing.
        return PROOF_STATUS_PENDING_EXTRACTION
    return _ENGINE_STATE_TO_STATUS.get(state, PROOF_STATUS_NEEDS_REVIEW)


def is_verified(proof: dict[str, Any]) -> bool:
    return proof_status(proof) == PROOF_STATUS_VERIFIED


def proof_amount(proof: dict[str, Any]) -> int:
    """Rupees this proof contributes. Only a verified proof is ever worth
    anything, so a low-confidence extraction cannot inflate the total before a
    reviewer confirms it."""
    if not is_verified(proof):
        return 0
    return max(0, int(proof.get("verified_amount") or 0))


def proof_identity(proof: dict[str, Any]) -> tuple[str, str]:
    """Identity of the underlying transaction, most authoritative first.

    Returns (kind, value). Two proofs sharing an identity are the same payment
    and must be counted once, however many times the screenshot was uploaded.
    """
    utr = _norm_ref(proof.get("utr_number"))
    if utr:
        return ("utr", utr)
    txn = _norm_ref(proof.get("transaction_id"))
    if txn:
        return ("transaction_id", txn)
    reference = _norm_ref(proof.get("reference_number"))
    if reference:
        return ("reference", reference)
    digest = _norm_ref(proof.get("sha256"))
    if digest:
        return ("screenshot_sha256", digest)
    # Nothing identifying survived extraction. Fall back to the attachment id so
    # the proof still counts once, but it can never merge with another record.
    return ("attachment_id", _norm_ref(proof.get("id")))


def _secondary_identity(proof: dict[str, Any]) -> tuple[str, ...]:
    """Every identity this proof carries, so a UTR-keyed record and a
    checksum-keyed record of the same upload still collapse together."""
    keys = []
    for kind, value in (
        ("utr", _norm_ref(proof.get("utr_number"))),
        ("transaction_id", _norm_ref(proof.get("transaction_id"))),
        ("reference", _norm_ref(proof.get("reference_number"))),
        ("screenshot_sha256", _norm_ref(proof.get("sha256"))),
    ):
        if value:
            keys.append(f"{kind}:{value}")
    if not keys:
        keys.append(f"attachment_id:{_norm_ref(proof.get('id'))}")
    return tuple(keys)


def unique_verified_proofs(proofs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Verified proofs with same-transaction duplicates removed.

    Deduplication is by any shared identity — UTR, transaction id, reference or
    screenshot checksum. Payer, receiver and timestamp are compared too, but
    only to catch a re-upload that lost its reference during extraction; a
    shared identifier alone is already conclusive.
    """
    seen: dict[str, dict[str, Any]] = {}
    kept: list[dict[str, Any]] = []
    for proof in proofs or []:
        if not isinstance(proof, dict) or not is_verified(proof):
            continue
        keys = list(_secondary_identity(proof))
        match = next((seen[key] for key in keys if key in seen), None)
        if match is None:
            match = _match_by_transaction_shape(proof, kept)
        if match is not None:
            # Same payment seen again — keep the first record and let the
            # duplicate contribute nothing.
            for key in keys:
                seen.setdefault(key, match)
            continue
        for key in keys:
            seen[key] = proof
        kept.append(proof)
    return kept


def _match_by_transaction_shape(
    proof: dict[str, Any], kept: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Catch a duplicate whose identifiers did not survive extraction by
    comparing the transaction itself: same amount, payer, receiver and moment."""
    amount = proof_amount(proof)
    if amount <= 0:
        return None
    payer = _norm_ref(proof.get("payer_name") or proof.get("payer"))
    receiver = _norm_ref(proof.get("receiver_name") or proof.get("receiver"))
    stamp = _norm_ref(
        f"{_clean(proof.get('transaction_date') or proof.get('payment_date'))}"
        f"{_clean(proof.get('transaction_time') or proof.get('payment_time'))}"
    )
    if not (payer and receiver and stamp):
        return None
    for other in kept:
        if proof_amount(other) != amount:
            continue
        if _norm_ref(other.get("payer_name") or other.get("payer")) != payer:
            continue
        if _norm_ref(other.get("receiver_name") or other.get("receiver")) != receiver:
            continue
        other_stamp = _norm_ref(
            f"{_clean(other.get('transaction_date') or other.get('payment_date'))}"
            f"{_clean(other.get('transaction_time') or other.get('payment_time'))}"
        )
        if other_stamp == stamp:
            return other
    return None


def verified_proof_total(proofs: Iterable[dict[str, Any]]) -> int:
    """Sum of every unique verified proof, in rupees. Never capped."""
    return sum(proof_amount(proof) for proof in unique_verified_proofs(proofs))


def collect_proofs(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every payment proof across an identity group.

    A profile's interview slots are stored as cloned candidate rows that each
    carry a copy of the same proof, so the group is the correct unit and
    deduplication is what stops one ₹20,000 payment reading as ₹120,000.
    """
    proofs: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for proof in row.get("payment_proofs") or []:
            if isinstance(proof, dict):
                proofs.append(proof)
    return proofs


def has_proof_evidence(proofs: Iterable[dict[str, Any]]) -> bool:
    """True when the verification engine has adjudicated at least one proof.

    A legacy upload carrying no `verification_state` is an unprocessed artifact,
    not evidence. Treating it as proof control would read "no verified proofs"
    as "paid ₹0" and wipe out payments that predate the engine.
    """
    return any(
        isinstance(proof, dict) and _clean(proof.get("verification_state"))
        for proof in (proofs or [])
    )


def status_counts(proofs: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in PROOF_STATUSES}
    for proof in proofs or []:
        if isinstance(proof, dict):
            counts[proof_status(proof)] += 1
    return counts


def api_summary(row: dict[str, Any]) -> dict[str, Any]:
    """The authoritative payment figures for a computed candidate row.

    Returned alongside every proof mutation so the editor can show the new
    total the moment a proof is verified, instead of waiting for a save round
    trip. The browser renders these numbers; it never recomputes them.
    """
    row = row or {}
    received = max(0, int(row.get("payment") or 0))
    expected = max(0, int(row.get("expected_minimum") or row.get("expected_payment") or 0))
    status = str(row.get("payment_status") or "").upper()
    return {
        "verified_proof_total": max(0, int(row.get("verified_proof_total") or 0)),
        "received_total": received,
        "expected_amount": expected,
        "outstanding_amount": max(0, int(row.get("balance_due") or 0)),
        "above_minimum_amount": max(0, int(row.get("above_minimum") or 0)),
        "verified_proof_count": max(0, int(row.get("verified_proof_count") or 0)),
        "payment_status": status or ("UNPAID" if received <= 0 else "PAID"),
        "proof_derived": bool(row.get("payment_is_proof_derived")),
        "unevidenced": bool(row.get("payment_unevidenced")),
        "proof_count": max(0, int(row.get("proof_count") or 0)),
        "needs_reconciliation": bool(row.get("payment_needs_reconciliation")),
        "reconciliation_gap": max(0, int(row.get("payment_reconciliation_gap") or 0)),
        # Referral share of this payment, so the editor never recomputes it.
        # Deliberately excludes closure complimentary amounts, which are earned
        # separately and must not be folded into the payment commission.
        "referrer": str(row.get("reference") or ""),
        "referral_percentage": max(0, int(row.get("referral_percentage") or 0)),
        "referral_commission": max(0, int(row.get("referral_commission") or 0)),
        "referral_basis": max(0, int(row.get("referral_basis") or 0)),
        "referrer_complimentary_amount": max(
            0, int(row.get("referrer_complimentary_amount") or 0)
        ),
    }


def receipt_summary(
    *,
    expected: int,
    recorded: int,
    proofs: Iterable[dict[str, Any]],
    proof_controlled: bool = False,
) -> dict[str, Any]:
    """Everything the payment panel needs, derived in one place.

    The proof total is authoritative and is never capped at `expected` — the
    expected figure is a minimum, so paying above it is legitimate and must
    show. Where it is *lower* than the recorded amount there are two possible
    causes, and they need opposite treatment:

    * proof capture is incomplete (someone paid ₹20,000 but only a ₹2,000
      receipt was ever uploaded), or
    * the recorded amount was overstated.

    Nothing in the data distinguishes them, so a shortfall does not silently
    reduce the total. It keeps the recorded amount and raises
    `needs_reconciliation` for review. Once a row is genuinely under proof
    control — every proof adjudicated and accounted for, which
    `recalculate_received_total` asserts by setting `proof_controlled` — the
    proof total wins outright, including reductions from a rejected proof.
    """
    proofs = [proof for proof in (proofs or []) if isinstance(proof, dict)]
    unique = unique_verified_proofs(proofs)
    proof_total = sum(proof_amount(proof) for proof in unique)
    adjudicated = has_proof_evidence(proofs)
    expected = max(0, int(expected or 0))
    recorded = max(0, int(recorded or 0))

    shortfall = adjudicated and proof_total < recorded
    if proof_controlled or (adjudicated and not shortfall):
        received = proof_total
        derived = True
    else:
        received = recorded
        derived = False
    return {
        "expected_minimum": expected,
        "verified_received": received,
        "proof_derived": derived,
        "recorded_amount": recorded,
        "verified_proof_total": proof_total,
        "outstanding": max(0, expected - received),
        "above_minimum": max(0, received - expected),
        "verified_proof_count": len(unique),
        "proof_count": len(proofs),
        "status_counts": status_counts(proofs),
        "unevidenced": (not adjudicated) and recorded > 0,
        "needs_reconciliation": bool(shortfall and not proof_controlled),
        "reconciliation_gap": max(0, recorded - proof_total) if shortfall else 0,
    }
