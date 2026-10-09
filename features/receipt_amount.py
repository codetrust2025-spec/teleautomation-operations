"""Which amount a payment screenshot establishes: one clear figure, or none.

An expense used to be saved with whatever amount the operator typed, checked only
for being covered by the receipt. The amount is now read off the screenshot and
that is the amount saved. A figure is accepted only when the screenshot gives one
clear answer; otherwise the save is blocked and the reason is returned, because a
guessed amount is worse than none -- the same screenshot has been read as a tenth
of what it said, with full confidence, before.

Pure and side-effect free: it only reads the verification the payment engine
produced.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReceiptAmount:
    """`amount` is whole rupees, 0 whenever `problem` says why it cannot be used."""

    amount: int
    problem: str = ""

    @property
    def ok(self) -> bool:
        return self.amount > 0 and not self.problem


def _blocked(problem: str) -> ReceiptAmount:
    return ReceiptAmount(amount=0, problem=problem)


def read_amount(verification: dict) -> ReceiptAmount:
    """The amount the screenshot establishes, or why it establishes none.

    Judged on the engine's own findings, most specific first:
    an amount the receipt contradicts itself on, an amount reached by arithmetic
    or one a digit may have been dropped from, an amount that was not read at
    all, and any other reason the receipt cannot be accepted (not a payment, not
    successful, not to this referrer, no transaction reference).
    """
    verification = verification or {}
    try:
        amount = int(verification.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0

    review = str(verification.get("amount_review_reason") or "").strip()
    mismatch = str(verification.get("amount_mismatch_reason") or "").strip()

    if verification.get("amount_extraction_review_required"):
        return _blocked(
            (review or "The amount on the screenshot is not clear enough to use.")
            + " Attach a clearer screenshot."
        )
    if mismatch or verification.get("amount_crosscheck") == "mismatch":
        return _blocked(
            (mismatch or "The amounts visible on the screenshot disagree.")
            + " Attach a clearer screenshot."
        )
    if amount <= 0:
        return _blocked("The payment amount could not be read from this screenshot. Attach a clearer one.")
    if not verification.get("deterministic_verified"):
        reasons = " ".join(str(reason) for reason in (verification.get("deterministic_reasons") or []) if reason)
        return _blocked(reasons or "Payment screenshot could not be verified.")
    return ReceiptAmount(amount=amount)
