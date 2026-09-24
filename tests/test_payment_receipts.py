"""Received amount is derived from verified payment proofs, never typed."""
import pytest

from features import payment_receipts as receipts


def proof(
    amount,
    *,
    pid="p1",
    state="VERIFIED_COMPANY_PAYMENT",
    utr="",
    txn="",
    sha="",
    payer="",
    receiver="",
    date="",
    time="",
):
    return {
        "id": pid,
        "verified_amount": amount,
        "verification_state": state,
        "utr_number": utr,
        "transaction_id": txn,
        "sha256": sha,
        "payer_name": payer,
        "receiver_name": receiver,
        "transaction_date": date,
        "transaction_time": time,
    }


def test_one_seven_thousand_proof_gives_seven_thousand_received():
    assert receipts.verified_proof_total([proof(7000, utr="U1")]) == 7000


def test_two_unique_proofs_sum():
    total = receipts.verified_proof_total(
        [proof(5000, pid="a", utr="U1"), proof(7000, pid="b", utr="U2")]
    )
    assert total == 12000


def test_received_is_not_capped_at_expected():
    summary = receipts.receipt_summary(
        expected=5000, recorded=5000, proofs=[proof(7000, utr="U1")]
    )
    assert summary["verified_received"] == 7000
    assert summary["above_minimum"] == 2000
    assert summary["outstanding"] == 0


def test_duplicate_utr_is_counted_once():
    total = receipts.verified_proof_total(
        [proof(5000, pid="a", utr="U1"), proof(5000, pid="b", utr="U1")]
    )
    assert total == 5000


def test_duplicate_utr_counted_once_even_with_different_amounts():
    """Same transaction re-extracted with a different reading is still one
    payment; the first verified record stands."""
    total = receipts.verified_proof_total(
        [proof(5000, pid="a", utr="U1"), proof(7000, pid="b", utr="U1")]
    )
    assert total == 5000


def test_duplicate_screenshot_checksum_is_counted_once():
    total = receipts.verified_proof_total(
        [proof(5000, pid="a", sha="abc"), proof(5000, pid="b", sha="abc")]
    )
    assert total == 5000


def test_utr_and_checksum_records_of_one_upload_collapse():
    total = receipts.verified_proof_total([
        proof(5000, pid="a", utr="U1", sha="abc"),
        proof(5000, pid="b", sha="abc"),
    ])
    assert total == 5000


def test_same_transaction_without_identifiers_is_counted_once():
    same = dict(
        amount=5000, payer="Ravi", receiver="Company", date="2026-08-05", time="17:34"
    )
    total = receipts.verified_proof_total([
        proof(same["amount"], pid="a", payer=same["payer"], receiver=same["receiver"],
              date=same["date"], time=same["time"]),
        proof(same["amount"], pid="b", payer=same["payer"], receiver=same["receiver"],
              date=same["date"], time=same["time"]),
    ])
    assert total == 5000


def test_distinct_payments_from_same_payer_both_count():
    total = receipts.verified_proof_total([
        proof(5000, pid="a", utr="U1", payer="Ravi", receiver="Company",
              date="2026-08-05", time="17:34"),
        proof(7000, pid="b", utr="U2", payer="Ravi", receiver="Company",
              date="2026-08-05", time="19:10"),
    ])
    assert total == 12000


@pytest.mark.parametrize("state", [
    "UPLOADED", "EXTRACTION_IN_PROGRESS", "EXTRACTED", "PENDING_MANUAL_REVIEW",
    "INCOMPLETE_PAYMENT_EVIDENCE", "UNKNOWN_RECEIVER",
])
def test_pending_proof_is_excluded(state):
    assert receipts.verified_proof_total([proof(7000, utr="U1", state=state)]) == 0


@pytest.mark.parametrize("state", ["REJECTED", "FAILED_PAYMENT", "REVERSED"])
def test_rejected_proof_is_excluded(state):
    assert receipts.verified_proof_total([proof(7000, utr="U1", state=state)]) == 0


def test_duplicate_state_proof_is_excluded():
    assert receipts.verified_proof_total(
        [proof(7000, utr="U1", state="DUPLICATE_PAYMENT")]
    ) == 0


def test_extraction_failure_is_excluded_and_labelled():
    row = proof(7000, utr="U1", state="EXTRACTION_FAILED")
    assert receipts.proof_status(row) == receipts.PROOF_STATUS_EXTRACTION_FAILED
    assert receipts.proof_amount(row) == 0


def test_low_confidence_ocr_needs_review_and_contributes_nothing():
    """A low-confidence extraction lands in PENDING_MANUAL_REVIEW. It must not
    be guessed from the expected amount, and must not reach the total until a
    reviewer verifies it."""
    pending = proof(0, utr="U1", state="PENDING_MANUAL_REVIEW")
    summary = receipts.receipt_summary(expected=5000, recorded=0, proofs=[pending])
    assert receipts.proof_status(pending) == receipts.PROOF_STATUS_NEEDS_REVIEW
    assert summary["verified_received"] == 0
    assert summary["outstanding"] == 5000

    verified = proof(6000, utr="U1", state="VERIFIED_COMPANY_PAYMENT")
    after = receipts.receipt_summary(expected=5000, recorded=0, proofs=[verified])
    assert after["verified_received"] == 6000


def test_rejecting_one_of_two_proofs_recalculates():
    both = [proof(5000, pid="a", utr="U1"), proof(7000, pid="b", utr="U2")]
    assert receipts.verified_proof_total(both) == 12000
    both[0]["verification_state"] = "REJECTED"
    assert receipts.verified_proof_total(both) == 7000


def test_deleting_a_proof_recalculates():
    both = [proof(5000, pid="a", utr="U1"), proof(7000, pid="b", utr="U2")]
    del both[0]
    assert receipts.verified_proof_total(both) == 7000


def test_replacing_a_proof_recalculates():
    proofs = [proof(5000, pid="a", utr="U1")]
    proofs[0] = proof(9000, pid="a2", utr="U9")
    assert receipts.verified_proof_total(proofs) == 9000


def test_row_without_any_proof_keeps_its_recorded_amount():
    """Most of the historical roster predates proof capture. Deriving those
    from an empty proof list would erase real payments."""
    summary = receipts.receipt_summary(expected=20000, recorded=20000, proofs=[])
    assert summary["verified_received"] == 20000
    assert summary["proof_derived"] is False
    assert summary["unevidenced"] is True


def test_unverified_proof_on_a_row_with_nothing_recorded_reads_zero():
    summary = receipts.receipt_summary(
        expected=5000, recorded=0,
        proofs=[proof(5000, utr="U1", state="PENDING_MANUAL_REVIEW")],
    )
    assert summary["proof_derived"] is True
    assert summary["verified_received"] == 0
    assert summary["outstanding"] == 5000


def test_unverified_proof_never_wipes_an_amount_already_recorded():
    summary = receipts.receipt_summary(
        expected=5000, recorded=5000,
        proofs=[proof(5000, utr="U1", state="PENDING_MANUAL_REVIEW")],
    )
    assert summary["verified_received"] == 5000
    assert summary["needs_reconciliation"] is True
    assert summary["reconciliation_gap"] == 5000


def test_summary_reports_every_display_figure():
    summary = receipts.receipt_summary(
        expected=5000,
        recorded=5000,
        proofs=[proof(5000, pid="a", utr="U1"), proof(7000, pid="b", utr="U2")],
    )
    assert summary["expected_minimum"] == 5000
    assert summary["verified_received"] == 12000
    assert summary["above_minimum"] == 7000
    assert summary["outstanding"] == 0
    assert summary["verified_proof_count"] == 2


def test_status_counts_cover_every_explicit_status():
    summary = receipts.receipt_summary(
        expected=0, recorded=0,
        proofs=[
            proof(1, pid="a", utr="U1"),
            proof(1, pid="b", utr="U2", state="PENDING_MANUAL_REVIEW"),
            proof(1, pid="c", utr="U3", state="DUPLICATE_PAYMENT"),
            proof(1, pid="d", utr="U4", state="REJECTED"),
            proof(1, pid="e", utr="U5", state="EXTRACTION_FAILED"),
            proof(1, pid="f", utr="U6", state="UPLOADED"),
        ],
    )
    counts = summary["status_counts"]
    assert counts[receipts.PROOF_STATUS_VERIFIED] == 1
    assert counts[receipts.PROOF_STATUS_NEEDS_REVIEW] == 1
    assert counts[receipts.PROOF_STATUS_DUPLICATE] == 1
    assert counts[receipts.PROOF_STATUS_REJECTED] == 1
    assert counts[receipts.PROOF_STATUS_EXTRACTION_FAILED] == 1
    assert counts[receipts.PROOF_STATUS_PENDING_EXTRACTION] == 1
    assert set(counts) == set(receipts.PROOF_STATUSES)


def test_group_proofs_from_slot_clones_collapse_to_one_payment():
    """One profile's interview slots are stored as cloned rows each carrying a
    copy of the same proof. Six clones of one ₹20,000 payment is ₹20,000."""
    shared = proof(20000, pid="shared", utr="T123")
    clones = [{"payment_proofs": [dict(shared)]} for _ in range(6)]
    assert receipts.verified_proof_total(receipts.collect_proofs(clones)) == 20000


def test_shortfall_against_recorded_does_not_silently_reduce():
    """Production has rows where ₹20,000 was received but only a ₹2,000 receipt
    was ever captured. Deriving those from the proofs would delete real money,
    so a shortfall keeps the recorded amount and asks for reconciliation."""
    summary = receipts.receipt_summary(
        expected=20000, recorded=20000, proofs=[proof(2000, utr="U1")]
    )
    assert summary["verified_received"] == 20000
    assert summary["proof_derived"] is False
    assert summary["needs_reconciliation"] is True
    assert summary["reconciliation_gap"] == 18000
    assert summary["verified_proof_total"] == 2000


def test_legacy_proof_without_a_verification_state_is_not_proof_control():
    """Uploads that predate the verification engine carry no state. They are
    unprocessed artifacts, not evidence of a ₹0 balance."""
    legacy = {"id": "old", "filename": "old.png"}
    summary = receipts.receipt_summary(expected=50000, recorded=50000, proofs=[legacy])
    assert summary["verified_received"] == 50000
    assert summary["proof_derived"] is False
    assert summary["unevidenced"] is True
    assert receipts.has_proof_evidence([legacy]) is False


def test_proof_total_above_recorded_is_adopted_without_reconciliation():
    summary = receipts.receipt_summary(
        expected=5000, recorded=5000, proofs=[proof(6000, utr="U1")]
    )
    assert summary["verified_received"] == 6000
    assert summary["proof_derived"] is True
    assert summary["needs_reconciliation"] is False


def test_proof_controlled_row_accepts_a_reduction_from_a_rejected_proof():
    """Once a row is under proof control, rejecting the ₹5,000 proof of a
    ₹5,000 + ₹7,000 pair must drop the total to ₹7,000."""
    proofs = [
        proof(5000, pid="a", utr="U1", state="REJECTED"),
        proof(7000, pid="b", utr="U2"),
    ]
    summary = receipts.receipt_summary(
        expected=5000, recorded=12000, proofs=proofs, proof_controlled=True
    )
    assert summary["verified_received"] == 7000
    assert summary["proof_derived"] is True
    assert summary["needs_reconciliation"] is False


def test_api_summary_matches_the_documented_response_shape():
    """The editor renders these figures verbatim, so the contract is fixed."""
    row = {
        "payment": 6000, "expected_minimum": 5000, "verified_proof_total": 6000,
        "balance_due": 0, "above_minimum": 1000, "verified_proof_count": 1,
        "payment_status": "paid", "payment_is_proof_derived": True,
        "reference": "Pavan Kalyan", "referral_commission": 3000,
        "referral_percentage": 50, "referral_basis": 6000,
    }
    assert receipts.api_summary(row) == {
        "verified_proof_total": 6000,
        "received_total": 6000,
        "expected_amount": 5000,
        "outstanding_amount": 0,
        "above_minimum_amount": 1000,
        "verified_proof_count": 1,
        "payment_status": "PAID",
        "proof_derived": True,
        "unevidenced": False,
        "proof_count": 0,
        "needs_reconciliation": False,
        "reconciliation_gap": 0,
        "referrer": "Pavan Kalyan",
        "referral_percentage": 50,
        "referral_commission": 3000,
        "referral_basis": 6000,
        "referrer_complimentary_amount": 0,
    }


def test_api_summary_reports_a_partial_payment():
    row = {
        "payment": 3000, "expected_minimum": 5000, "verified_proof_total": 3000,
        "balance_due": 2000, "above_minimum": 0, "verified_proof_count": 1,
        "payment_status": "partial", "payment_is_proof_derived": True,
    }
    out = receipts.api_summary(row)
    assert out["payment_status"] == "PARTIAL"
    assert out["outstanding_amount"] == 2000
    assert out["above_minimum_amount"] == 0


def test_api_summary_survives_a_row_with_nothing_set():
    out = receipts.api_summary({})
    assert out["received_total"] == 0
    assert out["payment_status"] == "UNPAID"
    assert out["verified_proof_count"] == 0
    assert out["unevidenced"] is False
    assert out["proof_count"] == 0


def test_api_summary_flags_unevidenced_payment():
    row = {
        "payment": 20000,
        "expected_minimum": 20000,
        "payment_status": "paid",
        "payment_is_proof_derived": False,
        "payment_unevidenced": True,
        "proof_count": 0,
        "verified_proof_count": 0,
    }
    out = receipts.api_summary(row)
    assert out["unevidenced"] is True
    assert out["proof_derived"] is False
    assert out["proof_count"] == 0
    assert out["received_total"] == 20000


def test_receipt_summary_unevidenced_full_and_partial():
    # Full amount recorded with no proofs
    full = receipts.receipt_summary(expected=20000, recorded=20000, proofs=[])
    assert full["unevidenced"] is True
    assert full["proof_derived"] is False
    assert full["verified_received"] == 20000
    assert full["outstanding"] == 0

    # Partial amount recorded with no proofs
    partial = receipts.receipt_summary(expected=20000, recorded=10000, proofs=[])
    assert partial["unevidenced"] is True
    assert partial["proof_derived"] is False
    assert partial["verified_received"] == 10000
    assert partial["outstanding"] == 10000

    # Zero recorded with no proofs
    zero = receipts.receipt_summary(expected=20000, recorded=0, proofs=[])
    assert zero["unevidenced"] is False
    assert zero["verified_received"] == 0

    # Recorded with verified proof
    verified_res = receipts.receipt_summary(
        expected=20000, recorded=20000, proofs=[proof(20000, utr="U1")]
    )
    assert verified_res["unevidenced"] is False
    assert verified_res["proof_derived"] is True
    assert verified_res["verified_received"] == 20000

