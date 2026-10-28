import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

// These fixtures describe an upcoming 25 Oct invite. Freeze Date only: timers
// remain real, and the existing booking/past-date guards are exercised intact.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-20T06:00:00Z'))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function screenshot(label) {
  return new File([label], `${label}.jpg`, { type: 'image/jpeg' })
}

function attach(input, files) {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  fireEvent.change(input)
}

function stubFetch({ date = '2026-10-25', time = '02:30 PM', confirmReply } = {}) {
  const calls = { confirms: [] }
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const target = String(url)
    const reply = body =>
      Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => 'application/json' },
        json: () => Promise.resolve(body)
      })

    if (target.includes('/public/slots/payment-requirement')) {
      return reply({ status: 'ok', service_type: 'profile_service', amount_due: 0, payment_required: false })
    }
    if (target.includes('/extract-invite-ai')) {
      return reply({
        status: 'ok',
        success: true,
        data: {
          interview_date: date,
          start_time: time,
          company: 'Google',
          interview_round: 'L1',
          confidence_score: 95
        }
      })
    }
    if (target.includes('/bookings/confirm')) {
      calls.confirms.push(options?.body)
      return confirmReply
        ? confirmReply()
        : reply({ status: 'ok', candidate: { name: 'Aniket' } })
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({
      status: 'ok',
      candidates: [{ id: 'c1', name: 'Aniket', needs_payment_proof: false, balance_due: 0 }]
    })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  return calls
}

// Since 19 Oct the detected slot is shown in the Interview date & time field itself; the separate preview above
// Confirm repeated it and is gone.
const whenText = () => document.querySelector('.sbs-when__value').textContent.replace(/\u00a0/g, ' ')

describe('SubmitSlotPage - the detected slot, in the Interview date & time field', () => {
  it('shows no slot before an invite is read, and no separate preview at all', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByRole('button', { name: /Confirm booking/i })
    expect(screen.queryByLabelText('Detected interview slot')).toBeNull()
    expect(whenText()).toBe('Read from the invite')
  })

  it('shows the detected date and time (IST) in the field, above Confirm, after analysis', async () => {
    stubFetch({ date: '2026-10-25', time: '02:30 PM' })
    render(<SubmitSlotPage />)
    const confirmButton = await screen.findByRole('button', { name: /Confirm booking/i })

    const invite = [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)
    expect(invite).toBeDefined()
    attach(invite, [screenshot('invite')])

    await waitFor(() => {
      expect(whenText()).toContain('25 Oct 2026')
      expect(whenText()).toContain('02:30 PM')
    })
    expect(screen.queryByLabelText('Detected interview slot')).toBeNull()

    // The field sits above the Confirm booking button
    const field = document.querySelector('.sbs-when')
    expect(field.compareDocumentPosition(confirmButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('clears the field after a successful booking confirmation', async () => {
    stubFetch({ date: '2026-10-25', time: '02:30 PM' })
    render(<SubmitSlotPage />)
    const confirm = await screen.findByRole('button', { name: /Confirm booking/i })

    const nameBox = document.querySelector('.sbs-field--client input')
    fireEvent.change(nameBox, { target: { value: 'Aniket' } })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L1' } })

    const invite = [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)
    attach(invite, [screenshot('invite')])

    await waitFor(() => expect(whenText()).toContain('02:30 PM'))

    fireEvent.click(confirm)

    await waitFor(() => {
      expect(screen.getByText(/Slot confirmed for Aniket/i)).toBeInTheDocument()
      expect(whenText()).toBe('Read from the invite')
    })
  })
})
