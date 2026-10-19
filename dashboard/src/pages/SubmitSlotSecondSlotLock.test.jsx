/**
 * Booking a profile candidate's interview while money is owed (11 Oct).
 *
 * First slot: may be booked with the balance pending, which is shown as a
 * reminder. Second and later slots: refused while anything is outstanding --
 * "₹X payment pending. Clear the balance to book another interview slot." --
 * and allowed once verified payments clear it. The server decides which
 * (payment_blocked, first_slot) and refuses at /bookings/confirm too; the page
 * shows it and does not send a booking it would refuse. Invented names.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const ROSTER = [
  { id: 'a1', name: 'Asha Fakename', needs_payment_proof: false, payment_blocked: false, first_slot: true,
    balance_due: 20000, payment_message: '₹20,000 payment pending.' },
  { id: 'r1', name: 'Ravi Kolla', needs_payment_proof: true, payment_blocked: true, first_slot: false,
    balance_due: 10000, payment_message: '₹10,000 payment pending. Clear the balance to book another interview slot.' },
  { id: 'm1', name: 'Manu Sample', needs_payment_proof: false, payment_blocked: false, first_slot: false, balance_due: 0 },
]

function upcomingDate() {
  const date = new Date()
  date.setDate(date.getDate() + 7)
  return date.toISOString().slice(0, 10)
}

function stubFetch() {
  const calls = { confirms: [] }
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const target = String(url)
    const reply = body => Promise.resolve({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: () => Promise.resolve(body) })
    if (target.includes('/public/slots/payment-requirement')) return reply({ status: 'ok', service_type: 'profile_service', amount_due: 0, payment_required: false })
    if (target.includes('/extract-invite-ai')) {
      return reply({ status: 'ok', success: true, data: { interview_date: upcomingDate(), start_time: '04:00 PM', company: 'Contoso', confidence_score: 95 } })
    }
    if (target.includes('/bookings/confirm')) {
      calls.confirms.push(options?.body)
      return reply({ status: 'ok', candidate: { name: 'x' } })
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: ROSTER })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  return calls
}

const inviteInput = () => [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)

async function completedForm(name) {
  const calls = stubFetch()
  render(<SubmitSlotPage />)
  const confirm = await screen.findByRole('button', { name: /Confirm booking/i })
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled())
  fireEvent.change(document.querySelector('.sbs-field--client input'), { target: { value: name } })
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L1' } })
  const invite = inviteInput()
  Object.defineProperty(invite, 'files', { value: [new File(['x'], 'invite.jpg', { type: 'image/jpeg' })], configurable: true })
  fireEvent.change(invite)
  await waitFor(() => expect(document.querySelector('.ai-node-progress')?.textContent || '').toMatch(/^✓/))
  return { confirm, calls }
}

describe('the first interview', () => {
  it('books with the balance pending, which is shown as a reminder', async () => {
    const { confirm, calls } = await completedForm('Asha Fakename')
    expect(screen.getByText('₹20,000 payment pending. You can book this first interview now.')).toBeInTheDocument()
    expect(screen.queryByText(/Clear the balance to book another/)).toBeNull()
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
  })
})

describe('a later interview', () => {
  it('is not booked while anything is owed, and says so', async () => {
    const { confirm, calls } = await completedForm('Ravi Kolla')
    expect(screen.getByText('₹10,000 payment pending. Clear the balance to book another interview slot.', { selector: '[role="status"]' })).toBeInTheDocument()
    // Confirm waits for verified payment screenshots that clear the balance.
    expect(confirm).toBeDisabled()
    fireEvent.click(confirm)
    await new Promise(resolve => setTimeout(resolve, 50))
    expect(calls.confirms).toHaveLength(0)
  })

  it('books once the balance is cleared', async () => {
    const { confirm, calls } = await completedForm('Manu Sample')
    expect(screen.queryByText(/payment pending/)).toBeNull()
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
  })
})
