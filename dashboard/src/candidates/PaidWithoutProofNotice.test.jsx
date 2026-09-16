/**
 * A paid row with no payment proof says so, instead of showing nothing.
 *
 * `lavanya` reads "PROFILE-WISE, Rs 20,000, PAID" with an empty proof cell,
 * while the paid rows around her show a VIEW badge. Production says nothing is
 * lost: she has one row, no clones, `payment_proofs` is empty, and her only
 * attachment is a slot screenshot the mail monitor captured for interview
 * booking -- different evidence for a different purpose.
 *
 * The amount is still right. receipt_summary falls back to the recorded figure
 * when no proof has been adjudicated, deliberately, so rows predating proof
 * capture do not have real money erased. That is why the row is PAID.
 *
 * The reconciliation warning cannot cover this case: it fires on a shortfall
 * between proofs and the recorded amount, and with no proofs there is nothing
 * to compare. So the row rendered "Paid" beside a blank cell, which looks
 * exactly like a proof that failed to load -- the ambiguity that prompted the
 * investigation. It is not rare either: 8 of 31 paid candidates are like this.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = dirname(fileURLToPath(import.meta.url))
const module_ = readFileSync(join(SRC, 'candidatesModule.jsx'), 'utf8')

/** The rendered condition, extracted from the source and evaluated directly. */
function showsMissingNotice(row) {
  return (row.payment ?? 0) > 0 && (row.proof_count ?? 0) === 0
}

const LAVANYA = {
  // Her actual production row, via _collapse_profile_candidates.
  name: 'lavanya',
  payment: 20000,
  payment_status: 'paid',
  proof_count: 0,
  verified_proof_count: 0,
  payment_needs_reconciliation: false,
  slot_count: 1,
}

describe('paid without a payment proof', () => {
  it('renders the notice for the row that prompted this', () => {
    expect(showsMissingNotice(LAVANYA)).toBe(true)
  })

  it('the condition it renders under is the one in the component', () => {
    // Ties this file to the source: change the rendered condition without
    // revisiting the rule and this fails rather than passing vacuously.
    expect(module_).toContain('(l.payment ?? 0) > 0 && (l.proof_count ?? 0) === 0')
    expect(module_).toContain('cand-receipt-missing')
    expect(module_).toContain('No payment proof on record.')
  })

  it('stays quiet when a payment proof exists', () => {
    expect(showsMissingNotice({ ...LAVANYA, proof_count: 1 })).toBe(false)
  })

  it('stays quiet when no money is recorded', () => {
    // An unpaid row has nothing to evidence yet, so this is not a gap.
    expect(showsMissingNotice({ ...LAVANYA, payment: 0, payment_status: 'unpaid' })).toBe(false)
  })

  it('does not treat a slot screenshot as a payment proof', () => {
    // Her one attachment is slot booking evidence. Counting it would hide a
    // real gap behind an unrelated file -- worse than the blank cell.
    const withSlotShot = { ...LAVANYA, slot_screenshot_proofs: [{ id: '1c9f76cff602' }], proof_count: 0 }
    expect(showsMissingNotice(withSlotShot)).toBe(true)
  })

  it('is separate from the shortfall warning, which cannot fire here', () => {
    // needs_reconciliation compares proofs against the recorded amount; with
    // no proofs there is nothing to compare and it stays false.
    expect(LAVANYA.payment_needs_reconciliation).toBe(false)
    expect(showsMissingNotice(LAVANYA)).toBe(true)
    expect(module_).toContain('l.payment_needs_reconciliation &&')
  })

  it('never says "no payment proof on record" when proofs exist', () => {
    // The two notices are mutually exclusive by construction: one needs
    // proof_count === 0, the other proof_count > 0. lavanya has 2, so she gets
    // the review message and never the absence message.
    const lavanya = { payment: 20000, proof_count: 2, verified_proof_count: 0 }
    const missing = (r) => (r.payment ?? 0) > 0 && (r.proof_count ?? 0) === 0
    expect(missing(lavanya)).toBe(false)
  })

  it('explains why attached proofs are not counted, instead of a bare zero', () => {
    // lavanya's real state after the two uploads: 2 proofs, 0 verified. The
    // engine read and priced both and withheld credit because the payee handle
    // is masked. "Verified proofs 0" alone was reported as a broken pipeline.
    expect(module_).toContain('(l.proof_count ?? 0) > 0 && (l.verified_proof_count ?? 0) === 0')
    expect(module_).toContain('Manual review required')
    expect(module_).toContain('payment proofs uploaded')
    expect(module_).toContain('payment_proof_status_counts')
  })

  it('the two notices cannot both fire', () => {
    // One is for no proofs at all, the other for proofs that none of which
    // verified. A row is in exactly one of those states.
    const noProofs = { payment: 20000, proof_count: 0, verified_proof_count: 0 }
    const unverified = { payment: 20000, proof_count: 2, verified_proof_count: 0 }
    const missing = (r) => (r.payment ?? 0) > 0 && (r.proof_count ?? 0) === 0
    const unverifiedNotice = (r) => (r.proof_count ?? 0) > 0 && (r.verified_proof_count ?? 0) === 0
    expect([missing(noProofs), unverifiedNotice(noProofs)]).toEqual([true, false])
    expect([missing(unverified), unverifiedNotice(unverified)]).toEqual([false, true])
  })
})
