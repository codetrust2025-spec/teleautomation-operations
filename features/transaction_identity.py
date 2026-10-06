"""One real-world transaction must affect balances only once.

The platform records money in several places — candidate payments, referrer
recoveries, handler expenses, payouts — and until now nothing tied those
records to the underlying bank transaction. Ledger rows carried no UPI
reference, no screenshot hash and no payer, so the same receipt could be filed
twice in two modules and deducted twice, which is exactly what happened when a
candidate's payment into a referrer's own account was auto-posted as a recovery
and then re-filed by hand as an expense.

This module gives every financial record a comparable identity:

  external id   the UPI / bank reference, when the receipt carried one
  fingerprint   normalized amount + date + payer + receiver, for the common
                case where no reference was captured
  screenshot    sha256 of the proof image, used as a supporting signal only

Two records are one transaction when they share ANY of these -- the engine's
payment id, the bank reference, or (for the same amount) the screenshot --
whichever identifier each happens to rank strongest. A stored expense and the
same payment filed again often differ in which they carry, and comparing only
the strongest of each let one payment be saved three times.

The external id is authoritative. The fingerprint is a strong hint but not
proof: two genuine payments of the same amount on the same day between the same
parties are possible, so a fingerprint match is reported for review rather than
silently blocked. A screenshot hash never establishes identity on its own — the
same image can legitimately document one transaction in two places — but it
does confirm a match found another way.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

# Fields that may carry a bank or UPI reference, in preference order.
EXTERNAL_ID_FIELDS = (
    "upi_transaction_id",
    "utr",
    "transaction_ref",
    "transaction_id",
    "bank_reference",
    "rrn",
    "external_transaction_id",
)

# Records that move money. Anything not in this set is ignored by the scan.
MONEY_KINDS = frozenset(
    {"candidate_payment", "recovery", "expense", "payout", "refund", "adjustment"}
)


class DuplicateTransactionError(ValueError):
    """Raised instead of recording a second money effect for one transaction."""

    def __init__(self, message: str, *, existing: dict | None = None) -> None:
        super().__init__(message)
        self.existing = existing or {}


def normalize_external_id(value: Any) -> str:
    """UPI/UTR references vary in case, spacing and punctuation between apps."""
    raw = re.sub(r"[^A-Za-z0-9]", "", str(value or ""))
    # A reference shorter than six characters is not identifying.
    return raw.upper() if len(raw) >= 6 else ""


def normalize_party(value: Any) -> str:
    """Payer/receiver as printed on a receipt: case and spacing vary."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    # UPI handles identify a party more precisely than the printed name.
    handle = re.search(r"[a-z0-9._-]+@[a-z]{2,}", text)
    if handle:
        return handle.group(0)
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 10:
        return digits[-10:]
    return text


def normalize_amount(value: Any) -> int:
    try:
        return abs(int(round(float(str(value).replace(",", "").strip() or 0))))
    except (TypeError, ValueError):
        return 0


def normalize_date(value: Any) -> str:
    """Compare on the calendar day; receipts and entries disagree on time."""
    text = str(value or "").strip()
    match = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return text[:10]


def screenshot_hash(data: bytes | None) -> str:
    return hashlib.sha256(data).hexdigest() if data else ""


def external_id_of(record: dict) -> str:
    for field in EXTERNAL_ID_FIELDS:
        found = normalize_external_id(record.get(field))
        if found:
            return found
    return ""


def fingerprint(record: dict) -> str:
    """Stable identity for a transaction with no usable external reference.

    Deliberately excludes the module that recorded it: the whole point is to
    match the same transaction across modules.
    """
    amount = normalize_amount(record.get("amount"))
    date = normalize_date(record.get("payment_date") or record.get("date"))
    # Without both an amount and a day there is nothing to identify: matching on
    # the parties alone would collapse every payment between two people.
    if not amount or not date:
        return ""
    parts = (
        str(amount),
        date,
        normalize_party(record.get("payer") or record.get("sender_name") or record.get("from")),
        normalize_party(
            record.get("receiver")
            or record.get("receiver_registry_name")
            or record.get("referrer")
            or record.get("to")
        ),
    )
    return "fp:" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def identity_of(record: dict) -> dict[str, str]:
    """Everything comparable about one financial record.

    `payment_id` outranks everything else. The verification engine assigns one
    payment record per real transaction and reuses it when a second screenshot
    of the same transfer is uploaded, so two records sharing a payment_id are
    the same money by the engine's own reckoning — no inference needed. That
    matters most for historical rows, where the operator's typed date can
    differ from the date on the receipt and the fingerprint therefore misses.
    """
    payment_id = str(record.get("payment_id") or "").strip()
    external = external_id_of(record)
    return {
        "payment_id": payment_id,
        "external_id": external,
        "fingerprint": fingerprint(record),
        "screenshot_hash": str(record.get("screenshot_hash") or record.get("proof_sha256") or ""),
        # Every digest of an image documenting it: an expense can carry more
        # than one proof, and any of them can be the one filed again.
        "screenshot_hashes": sorted({
            str(h) for h in [record.get("screenshot_hash"), record.get("proof_sha256"),
                             *(record.get("screenshot_hashes") or [])] if h
        }),
        # The strongest identity this record can offer.
        "identity": (f"pay:{payment_id}" if payment_id else "") or external or fingerprint(record),
    }


def _conflicting(a: dict, b: dict) -> bool:
    """Two money effects on the same transaction, rather than one effect."""
    kind_a, kind_b = a.get("kind"), b.get("kind")
    if kind_a not in MONEY_KINDS or kind_b not in MONEY_KINDS:
        return False
    if a.get("voided") or b.get("voided"):
        return False
    # A candidate payment and the recovery it triggers are one intended chain,
    # not a duplicate: the recovery exists precisely because of that payment.
    pair = {kind_a, kind_b}
    if pair == {"candidate_payment", "recovery"}:
        return False
    return True


def find_duplicates(records: Iterable[dict]) -> list[dict]:
    """Group money records that describe the same real-world transaction.

    Each group names the canonical record (the earliest automatic one, else the
    earliest recorded) and the duplicates that double-count it.
    """
    by_identity: dict[str, list[dict]] = {}
    by_screenshot: dict[str, list[dict]] = {}
    for record in records:
        ident = identity_of(record)
        row = {**record, **ident}
        if row["identity"]:
            by_identity.setdefault(row["identity"], []).append(row)
        if row["screenshot_hash"]:
            by_screenshot.setdefault(row["screenshot_hash"], []).append(row)

    groups: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()

    def _emit(rows: list[dict], basis: str) -> None:
        # Only records that move the balance belong in a group. A ledger mirror
        # shares the payment but is the audit copy of an expense, so listing it
        # as a duplicate would double the reported financial impact.
        active = [
            r for r in rows if not r.get("voided") and r.get("kind") in MONEY_KINDS
        ]
        if len(active) < 2:
            return
        conflicts = [
            (a, b)
            for i, a in enumerate(active)
            for b in active[i + 1 :]
            if _conflicting(a, b)
        ]
        if not conflicts:
            return
        key = tuple(sorted(str(r.get("record_id")) for r in active))
        if key in seen_pairs:
            return
        seen_pairs.add(key)
        # Canonical: prefer an automatically verified record, then the earliest.
        ordered = sorted(
            active,
            key=lambda r: (
                0 if str(r.get("source_module") or "").startswith("public_slot") else 1,
                str(r.get("created_at") or ""),
            ),
        )
        canonical, *duplicates = ordered
        # Only an engine-assigned payment or a bank reference, backed by
        # matching amounts, is safe to correct without a human looking. A
        # shared screenshot with differing amounts usually means a proof was
        # attached to the wrong record, not that money moved twice; and a
        # fingerprint match alone cannot rule out two genuine payments of the
        # same amount between the same parties on the same day.
        amounts_agree = len({normalize_amount(r.get("amount")) for r in active}) == 1
        confidence = (
            "high" if basis in {"payment_id", "external_id"} and amounts_agree else "review"
        )
        groups.append(
            {
                "basis": basis,
                "confidence": confidence,
                "identity": canonical.get("identity"),
                "canonical": canonical,
                "duplicates": duplicates,
                "handler": canonical.get("handler") or canonical.get("referrer"),
                "amount": normalize_amount(canonical.get("amount")),
                "month": normalize_date(
                    canonical.get("payment_date") or canonical.get("date")
                )[:7],
                "financial_impact": sum(
                    normalize_amount(d.get("amount")) for d in duplicates
                ),
            }
        )

    for identity, rows in by_identity.items():
        if identity.startswith("pay:"):
            basis = "payment_id"
        elif identity.startswith("fp:"):
            basis = "fingerprint"
        else:
            basis = "external_id"
        _emit(rows, basis)
    for _hash, rows in by_screenshot.items():
        _emit(rows, "screenshot_hash")
    return groups


def shared_signal(a: dict, b: dict) -> str:
    """What two records have in common that says they are one transaction.

    Compares every identifier each record carries, not only the one it ranks
    strongest. The old check compared a single "identity" per record, and a
    stored expense that had inherited the engine's payment id (identity
    `pay:...`) never equalled the same payment filed again with only its bank
    reference (identity `UTR...`) -- so one payment was saved three times.

    Returns the basis, strongest first, or "" when nothing is shared:

      payment_id       the engine matched both to one payment
      external_id      the same UPI / bank reference
      fingerprint      the same amount, day, payer and receiver, when neither
                       has a stronger identity (unchanged from before)
      screenshot_hash  the same image, filed for the same amount. An image
                       alone proves nothing -- it can document one transaction
                       in two places -- but the same image for the same amount
                       is the same payment filed twice.
    """
    ia = a if "payment_id" in a and "identity" in a else {**a, **identity_of(a)}
    ib = b if "payment_id" in b and "identity" in b else {**b, **identity_of(b)}
    if ia["payment_id"] and ia["payment_id"] == ib["payment_id"]:
        return "payment_id"
    if ia["external_id"] and ia["external_id"] == ib["external_id"]:
        return "external_id"
    if ia["identity"] and ia["identity"] == ib["identity"]:
        return "fingerprint"
    if set(ia["screenshot_hashes"]) & set(ib["screenshot_hashes"]) and (
        normalize_amount(ia.get("amount")) == normalize_amount(ib.get("amount"))
    ):
        return "screenshot_hash"
    return ""


def duplicates_of(candidate: dict, existing: Iterable[dict]) -> list[dict]:
    """Every already-recorded transaction this one would double-count.

    Each record returned says what matched (`matched_on`).
    """
    incoming = {**candidate, **identity_of(candidate)}
    found: list[dict] = []
    for record in existing:
        row = {**record, **identity_of(record)}
        if row.get("voided"):
            continue
        if not _conflicting(incoming, row):
            continue
        basis = shared_signal(incoming, row)
        if basis:
            found.append({**row, "matched_on": basis})
    return found


def duplicate_of(candidate: dict, existing: Iterable[dict]) -> dict | None:
    """The first already-recorded transaction this one would double-count.

    Used before saving, so a second money effect is never posted silently.
    """
    found = duplicates_of(candidate, existing)
    return found[0] if found else None


def duplicate_message(existing: dict) -> str:
    """What to tell the operator instead of creating a second effect."""
    kind = str(existing.get("kind") or "transaction")
    labels = {
        "recovery": "a candidate-payment recovery",
        "candidate_payment": "a candidate payment",
        "expense": "a handler expense",
        "payout": "a payout",
        "refund": "a refund",
        "adjustment": "an adjustment",
    }
    matched = {
        "payment_id": " (same payment)",
        "external_id": " (same bank reference)",
        "screenshot_hash": " (same screenshot and amount)",
    }.get(str(existing.get("matched_on") or ""), "")
    return (
        f"This transaction is already recorded as {labels.get(kind, kind)}"
        f"{' on ' + normalize_date(existing.get('payment_date') or existing.get('date')) if (existing.get('payment_date') or existing.get('date')) else ''}"
        f"{matched}."
        " Reclassify the existing record instead of adding a second one."
    )
