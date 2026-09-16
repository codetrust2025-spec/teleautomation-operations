"""Attaching a proof that cannot be verified must not erase a recorded payment.

`lavanya` had a recorded 20,000 and no proofs. Two genuine PhonePe screenshots
were then uploaded. The engine read both, priced both at 10,000, found the
transaction successful and the receiver name known -- and still withheld credit,
because the payee handle in the screenshot is masked (``XXXXXX4573@ybl``). That
refusal is deliberate: a mask matches no registry entry and every mask from the
same bank looks alike, so accepting one would let any payment to any similar
handle count.

The damage was on the save path. It forced ``payment`` to
``verified_proof_total`` whenever proof evidence merely *existed*, and
``has_proof_evidence`` is true from the moment a proof is attached, not once one
is verified. Two unverifiable screenshots therefore rewrote 20,000 to 0.

``recalculate_received_total`` already refuses that reduction in as many words.
These tests hold both paths to the same rule.
"""

from __future__ import annotations

import pytest

from features import payment_receipts as pr


MASKED_PROOF = {
    "id": "dad103acc40a",
    "attachment_type": "payment_proof",
    "verified_amount": 10000,
    "utr_number": "T2608201546305273525731",
    "receiver_name": "JOLLU RAVINDER",
    "receiver_upi_id": "XXXXXX4573@ybl",
    "payment_status": "success",
    "fraud_decision": "verified",
    "verification_state": "INCOMPLETE_PAYMENT_EVIDENCE",
}
SECOND_MASKED_PROOF = {
    **MASKED_PROOF,
    "id": "d50005bead15",
    "utr_number": "T2608071210007608760634",
}
VERIFIED_PROOF = {
    "id": "ok-1",
    "attachment_type": "payment_proof",
    "verified_amount": 10000,
    "utr_number": "T9999999999999999999999",
    "verification_state": "VERIFIED_COMPANY_PAYMENT",
}


class TestTheEngineVerdictIsRespected:
    def test_a_masked_receiver_proof_is_not_verified(self):
        """Her real proofs. Read, priced and fraud-checked, but the payee handle
        is a mask, so the engine will not confirm the receiver."""
        assert pr.proof_status(MASKED_PROOF) == pr.PROOF_STATUS_NEEDS_REVIEW
        assert pr.is_verified(MASKED_PROOF) is False
        assert pr.proof_amount(MASKED_PROOF) == 0

    def test_both_of_her_proofs_contribute_nothing(self):
        proofs = [MASKED_PROOF, SECOND_MASKED_PROOF]
        summary = pr.receipt_summary(expected=20000, recorded=20000, proofs=proofs)
        assert summary["verified_proof_count"] == 0
        assert summary["proof_count"] == 2
        assert summary["verified_received"] == 20000, (
            "the recorded amount stands; the proofs simply add nothing to it"
        )

    def test_the_shortfall_is_reported_rather_than_applied(self):
        proofs = [MASKED_PROOF, SECOND_MASKED_PROOF]
        summary = pr.receipt_summary(expected=20000, recorded=20000, proofs=proofs)
        assert summary["needs_reconciliation"] is True
        assert summary["reconciliation_gap"] == 20000

    def test_a_verified_proof_still_counts(self):
        summary = pr.receipt_summary(expected=20000, recorded=0, proofs=[VERIFIED_PROOF])
        assert summary["verified_proof_count"] == 1
        assert summary["verified_received"] == 10000


class TestTheSavePathRuleMatchesTheRecalculationRule:
    """Both paths decide the same thing: proofs may raise the recorded total, and
    may only lower it once the row is genuinely under proof control."""

    @staticmethod
    def decide(proof_total: int, recorded: int, controlled: bool, typed: int | None = None) -> int:
        """The rule as written in update_candidate.

        `typed` is what the operator submitted; it stands only when no proof has
        been verified, because a total of zero verified rupees is the absence of
        a statement about the amount, not a statement that zero arrived.
        """
        if controlled or proof_total > 0:
            return proof_total if (controlled or proof_total >= recorded) else recorded
        return max(typed if typed is not None else recorded, recorded)

    def test_unverified_proofs_do_not_erase_the_recorded_amount(self):
        # lavanya: two attached proofs, zero verified, 20,000 recorded.
        assert self.decide(proof_total=0, recorded=20000, controlled=False) == 20000

    def test_proofs_still_raise_the_total(self):
        assert self.decide(proof_total=25000, recorded=20000, controlled=False) == 25000

    def test_proofs_may_lower_it_once_the_row_is_proof_controlled(self):
        # A rejected proof on an adjudicated row is a real reduction.
        assert self.decide(proof_total=10000, recorded=20000, controlled=True) == 10000

    def test_an_equal_total_is_unchanged(self):
        assert self.decide(proof_total=20000, recorded=20000, controlled=False) == 20000

    def test_a_recorded_amount_can_still_be_corrected_while_proofs_are_unverified(self):
        """The first attempt at this fix stopped the erasure and also froze the
        field: with the recorded figure already zero, `proof_total >= recorded`
        forced zero straight back, so the destroyed amount could not be put
        right. Nothing verified means no opinion, not an opinion of zero."""
        assert self.decide(proof_total=0, recorded=0, controlled=False, typed=20000) == 20000

    def test_no_caller_can_reduce_it_while_proofs_are_unverified(self):
        """The edit form already omits a proof-derived amount, so the erasure
        did not come from there. Leaving the reduction open to any other caller
        is what made it possible, so the floor is enforced here rather than
        trusted to each caller."""
        assert self.decide(proof_total=0, recorded=20000, controlled=False, typed=0) == 20000
        assert self.decide(proof_total=0, recorded=20000, controlled=False, typed=5000) == 20000
        assert self.decide(proof_total=0, recorded=20000, controlled=False, typed=25000) == 25000

    def test_the_rule_is_the_one_in_the_source(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "features" / "candidate_store.py").read_text(
            encoding="utf-8"
        )
        assert "if controlled or proof_total > 0:" in source
        assert 'allowed_patch["payment"] = max(typed, recorded)' in source
        assert "if controlled or proof_total >= recorded" in source


class TestOtherCandidatesAreCoveredToo:
    @pytest.mark.parametrize("state,why", [
        (None, "legacy upload the engine never adjudicated"),
        ("UPLOADED", "queued, extraction has not run"),
        ("EXTRACTION_IN_PROGRESS", "still being read"),
        ("PENDING_MANUAL_REVIEW", "waiting on a human"),
        ("UNKNOWN_RECEIVER", "receiver not in the registry"),
        ("INCOMPLETE_PAYMENT_EVIDENCE", "masked payee handle"),
    ])
    def test_no_unverified_state_can_erase_a_recorded_amount(self, state, why):
        """22 of 39 candidates hold proofs with none verified, most of them
        legacy uploads with no engine verdict at all. Every one of those states
        would have zeroed the recorded amount on the next save."""
        proof = {**MASKED_PROOF, "verification_state": state}
        assert pr.is_verified(proof) is False, why
        proof_total = pr.verified_proof_total([proof])
        assert TestTheSavePathRuleMatchesTheRecalculationRule.decide(
            proof_total=proof_total, recorded=20000, controlled=False
        ) == 20000
