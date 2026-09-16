/**
 * The payment field has to accept a split payment.
 *
 * A candidate who paid ₹5,000 as ₹2,000 + ₹1,000 + ₹2,000 has three
 * screenshots and one booking. The field previously took a single file, so the
 * second and third transfers had nowhere to go and the booking was
 * unreachable. These render the real form and drive the real file input: the
 * multi-select attribute, the running total the server reports, and the
 * comma-separated proof ids the confirmation carries.
 *
 * The invite field deliberately stays single-file and is asserted here so a
 * later change cannot quietly make it accept a set.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

const CANDIDATE = {
  id: 'cand-1', name: 'Aniket', needs_payment_proof: true, balance_due: 5000,
}

function screenshot(label) {
  return new File([label], `${label}.jpg`, { type: 'image/jpeg' })
}

function stubFetch(uploadReplies) {
  const calls = { uploads: [], confirms: [] }
  const fetchStub = vi.fn((url, options) => {
    const target = String(url)
    if (target.includes('/public/slots/payment-proof')) {
      calls.uploads.push(options.body)
      const body = uploadReplies[calls.uploads.length - 1]
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
    }
    if (target.includes('/extract-invite-ai')) {
      const soon = new Date(); soon.setDate(soon.getDate() + 7)
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ status: 'ok', success: true, data: {
          interview_date: soon.toISOString().slice(0, 10), start_time: '03:00 PM', confidence_score: 92,
        } }),
      })
    }
    if (target.includes('/bookings/confirm')) {
      calls.confirms.push(options.body)
      return Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ status: 'ok', candidate: { name: 'Aniket' } }),
      })
    }
    const body = target.includes('/public/slots/booked')
      ? { status: 'ok', slots: [] }
      : { status: 'ok', candidates: [CANDIDATE] }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  })
  vi.stubGlobal('fetch', fetchStub)
  return calls
}

async function pickCandidate() {
  render(<SubmitSlotPage />)
  const name = await screen.findByPlaceholderText(/choose or type your name/i)
  fireEvent.change(name, { target: { value: 'Aniket' } })
  fireEvent.click(await screen.findByRole('option', { name: 'Aniket' }))
  return screen.findByText(/payment due/i)
}

function paymentInput() {
  // The payment drop is the multi-select one; the invite drop is not.
  return document.querySelectorAll('input[type="file"]')[0]
}

function attach(input, files) {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  fireEvent.change(input)
}

describe('Submit slot — split payment screenshots', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    // jsdom has no object URLs; the thumbnails need them.
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(f => `blob:${f.name}`),
      revokeObjectURL: vi.fn(),
    })
  })
  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it('accepts several payment screenshots and books once they cover the fee', async () => {
    const calls = stubFetch([{
      status: 'ok',
      proof_ids: ['proof-a', 'proof-b', 'proof-c'],
      verified_total: 5000, remaining_due: 0, amount_due: 5000,
      payment_complete: true, rejected: [],
      ai_extractions: [
        { is_payment_screenshot: true, amount: 2000, verified: true, utr_number: '700000000001' },
        { is_payment_screenshot: true, amount: 1000, verified: true, utr_number: '700000000002' },
        { is_payment_screenshot: true, amount: 2000, verified: true, utr_number: '700000000003' },
      ],
    }])
    await pickCandidate()

    const input = paymentInput()
    expect(input.multiple).toBe(true)
    attach(input, [screenshot('pay-1'), screenshot('pay-2'), screenshot('pay-3')])

    // Attaching is the upload: all three go in one request, with nothing to press.
    await waitFor(() => expect(calls.uploads).toHaveLength(1))
    expect(calls.uploads[0].getAll('files')).toHaveLength(3)
    expect(await screen.findByText(/payment verified/i)).toBeTruthy()
    expect(document.querySelectorAll('.sbs-pay-result .sbs-pay-item')).toHaveLength(3)
    // The fee is covered, so the upload control retires.
    expect(document.querySelector('.sbs-pay-card input[type="file"]')).toBeNull()
  })

  it('reports the shortfall and keeps collecting while instalments are short', async () => {
    stubFetch([
      {
        status: 'ok', proof_ids: ['proof-a'], verified_total: 2000,
        remaining_due: 3000, amount_due: 5000, payment_complete: false,
        rejected: [], ai_extractions: [{ is_payment_screenshot: true, amount: 2000, verified: true }],
      },
      {
        status: 'ok', proof_ids: ['proof-a', 'proof-b'], verified_total: 5000,
        remaining_due: 0, amount_due: 5000, payment_complete: true,
        rejected: [], ai_extractions: [{ is_payment_screenshot: true, amount: 3000, verified: true }],
      },
    ])
    await pickCandidate()

    attach(paymentInput(), [screenshot('pay-1')])

    expect(await screen.findByText(/₹2,000 verified so far · ₹3,000 still to upload/)).toBeTruthy()
    // Still short, so the drop stays open for the rest of the payment, and the
    // next instalment is analysed the moment it is attached.
    attach(paymentInput(), [screenshot('pay-2')])

    expect(await screen.findByText(/payment verified/i)).toBeTruthy()
    expect(document.querySelectorAll('.sbs-pay-result .sbs-pay-item')).toHaveLength(2)
  })

  it('sends every proof id to the booking boundary', async () => {
    const calls = stubFetch([{
      status: 'ok', proof_ids: ['proof-a', 'proof-b', 'proof-c'],
      verified_total: 5000, remaining_due: 0, amount_due: 5000,
      payment_complete: true, rejected: [], ai_extractions: [],
    }])
    await pickCandidate()

    attach(paymentInput(), [screenshot('pay-1'), screenshot('pay-2'), screenshot('pay-3')])
    await screen.findByText(/payment verified/i)

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L1' } })
    // The invite field is the remaining single-file input.
    const invite = document.querySelector('input[type="file"]')
    expect(invite.multiple).toBe(false)
    attach(invite, [screenshot('invite')])

    await waitFor(() => expect(screen.getByRole('button', { name: /confirm booking/i })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /confirm booking/i }))

    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('payment_proof_ids')).toBe('proof-a,proof-b,proof-c')
  })

  it('names the screenshots it could not verify', async () => {
    stubFetch([{
      status: 'ok', proof_ids: ['proof-a'], verified_total: 2000,
      remaining_due: 3000, amount_due: 5000, payment_complete: false,
      ai_extractions: [{ is_payment_screenshot: true, amount: 2000, verified: true }],
      rejected: [{ filename: 'pay-2.jpg', message: 'Receiver is not registered.' }],
    }])
    await pickCandidate()

    attach(paymentInput(), [screenshot('pay-1'), screenshot('pay-2')])

    // By position in the upload, never by file name.
    expect(await screen.findByText('Screenshot 2: Receiver is not registered.')).toBeTruthy()
    expect(document.body.textContent).not.toContain('pay-2.jpg')
  })

  it('never holds screenshots waiting to be saved', async () => {
    // A candidate once attached both receipts, never pressed "Save 2", and was
    // told to attach the payment screenshot beside the two they had attached.
    const calls = stubFetch([{
      status: 'ok', proof_ids: ['proof-a', 'proof-b'], verified_total: 5000, remaining_due: 0,
      amount_due: 5000, payment_complete: true, rejected: [], ai_extractions: [],
    }])
    await pickCandidate()

    attach(paymentInput(), [screenshot('pay-1'), screenshot('pay-2')])

    await waitFor(() => expect(calls.uploads).toHaveLength(1))
    expect(screen.queryByText(/screenshots? ready/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /save/i })).toBeNull()
    expect(await screen.findByText(/payment verified/i)).toBeTruthy()
  })
})
