/**
 * A proof that was not verified says why (11 Oct audit).
 *
 * The card only said "Needs review" or "Receiver not recognised". The engine's
 * reason codes are now stored on the proof and turned into plain sentences; a
 * proof saved before that falls back to what its state says.
 */
import { describe, it, expect } from 'vitest'
import { proofPresentation, proofReasons } from './paymentProofs.js'

describe('proofReasons', () => {
  it('turns stored codes into sentences, once each', () => {
    const proof = {
      verification_state: 'PENDING_MANUAL_REVIEW',
      verification_reason_codes: ['TRANSACTION_REFERENCE_MISSING', 'LOW_EXTRACTION_CONFIDENCE', 'TRANSACTION_REFERENCE_MISSING'],
    }
    expect(proofReasons(proof)).toEqual([
      'No UTR or transaction ID could be read.',
      'The screenshot was read with low confidence.',
    ])
  })

  it('falls back to the state for a proof saved before codes were kept', () => {
    expect(proofReasons({ verification_state: 'INCOMPLETE_PAYMENT_EVIDENCE' }))
      .toEqual(["The receiver's UPI ID, phone or account could not be read or checked."])
    expect(proofReasons({ verification_state: 'UNKNOWN_RECEIVER' }))
      .toEqual(['Paid to an account that is not a registered company or referrer account.'])
  })

  it('says nothing for a verified or unread proof', () => {
    expect(proofReasons({ verification_state: 'VERIFIED_COMPANY_PAYMENT', verification_reason_codes: ['X'] })).toEqual([])
    expect(proofReasons({})).toEqual([])
    expect(proofPresentation({ verification_state: 'VERIFIED_COMPANY_PAYMENT', verified_amount: 5000 }).reasons).toEqual([])
  })
})
