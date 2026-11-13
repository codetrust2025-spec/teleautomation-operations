"""Read-only reconciliation of recorded received amounts against proof evidence.

This never writes. It exists so the mismatch between what was typed and what the
proofs show can be reviewed before any record is corrected.

Rows are split into two kinds, because they need different decisions:

* `evidenced` — the candidate has payment proofs, so the proof total is
  authoritative and a difference is a defect to correct.
* `unevidenced` — money was recorded but no proof was ever captured. Most of the
  historical roster is in this state. These are listed for visibility only;
  treating an empty proof list as ₹0 would erase real payments.
"""
from __future__ import annotations

from typing import Any

from features import candidate_store, payment_receipts
from features.candidate_attachments import partition_candidate_attachments


def _group_key(row: dict[str, Any]) -> str:
    """Slot rows are clones of one profile carrying the same proof, so the
    profile is the unit of reconciliation."""
    name = candidate_store._normalise_candidate_name_key(row.get("name") or "")
    phone = candidate_store.candidate_phone_identity(row.get("phone"))
    return f"{name}|{phone}"


def mismatch_report() -> dict[str, Any]:
    from features import test_record_exclusions

    rows = test_record_exclusions.without_test_records(candidate_store._load().get("candidates") or [])
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_group_key(row), []).append(row)

    evidenced: list[dict[str, Any]] = []
    unevidenced: list[dict[str, Any]] = []
    needs_reconciliation: list[dict[str, Any]] = []
    for key, members in groups.items():
        # Run each member through the attachment partition so legacy uploads
        # stored under `proofs` are included alongside typed `payment_proofs`.
        proofs: list[dict[str, Any]] = []
        for member in members:
            proofs.extend(partition_candidate_attachments(member)["payment_proofs"])

        recorded = max(int(member.get("payment") or 0) for member in members)
        representative = max(
            members, key=lambda member: int(member.get("payment") or 0)
        )
        expected = candidate_store.effective_expected_payment(representative)
        summary = payment_receipts.receipt_summary(
            expected=expected, recorded=recorded, proofs=proofs
        )
        unique = payment_receipts.unique_verified_proofs(proofs)
        record = {
            "candidate_id": str(representative.get("id") or ""),
            "candidate_name": str(representative.get("name") or ""),
            "phone": str(representative.get("phone") or ""),
            "row_ids": [str(member.get("id") or "") for member in members],
            "recorded_amount": recorded,
            "verified_proof_total": summary["verified_proof_total"],
            "difference": summary["verified_proof_total"] - recorded,
            "expected_minimum": expected,
            "verified_proof_count": summary["verified_proof_count"],
            "proof_count": summary["proof_count"],
            "status_counts": summary["status_counts"],
            "proof_ids": [str(proof.get("id") or "") for proof in unique],
            "utrs": [
                str(proof.get("utr_number") or proof.get("transaction_id") or "")
                for proof in unique
            ],
        }
        if summary["needs_reconciliation"]:
            # Proofs exist and are adjudicated, but they total less than the
            # recorded amount. Almost always incomplete capture rather than
            # overstated revenue, so it is never auto-applied.
            record["reconciliation_gap"] = summary["reconciliation_gap"]
            record["recommended_correction"] = (
                f"Proofs account for only ₹{summary['verified_proof_total']:,} of the "
                f"₹{recorded:,} recorded (gap ₹{summary['reconciliation_gap']:,}). "
                "Upload the missing receipts, or confirm the recorded amount was "
                "overstated before reducing it."
            )
            needs_reconciliation.append(record)
            continue
        if not summary["proof_derived"]:
            if recorded > 0:
                record["recommended_correction"] = (
                    "No payment proof on file. Leave the recorded amount as is and "
                    "upload the original receipt to bring this row under proof control."
                )
                unevidenced.append(record)
            continue
        if record["difference"] == 0:
            continue
        direction = "increase" if record["difference"] > 0 else "reduce"
        record["recommended_correction"] = (
            f"{direction.capitalize()} recorded received from "
            f"₹{recorded:,} to ₹{summary['verified_proof_total']:,} "
            f"({len(unique)} verified proof(s))."
        )
        evidenced.append(record)

    evidenced.sort(key=lambda item: abs(item["difference"]), reverse=True)
    unevidenced.sort(key=lambda item: item["recorded_amount"], reverse=True)
    needs_reconciliation.sort(key=lambda item: item["reconciliation_gap"], reverse=True)
    return {
        "evidenced_mismatches": evidenced,
        "needs_reconciliation": needs_reconciliation,
        "unevidenced_rows": unevidenced,
        "evidenced_mismatch_count": len(evidenced),
        "needs_reconciliation_count": len(needs_reconciliation),
        "unevidenced_count": len(unevidenced),
        "net_evidenced_delta": sum(item["difference"] for item in evidenced),
        "reconciliation_gap_total": sum(
            item["reconciliation_gap"] for item in needs_reconciliation
        ),
        "unevidenced_recorded_total": sum(
            item["recorded_amount"] for item in unevidenced
        ),
    }
