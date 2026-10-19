import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
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

describe('SubmitSlotPage - Detected Interview Slot Preview', () => {
  it('does not display slot preview when no file or slot is detected', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByRole('button', { name: /Confirm booking/i })
    expect(screen.queryByLabelText('Detected interview slot')).toBeNull()
  })

  it('displays detected interview date and time (IST) above Confirm Booking button after analysis', async () => {
    stubFetch({ date: '2026-10-25', time: '02:30 PM' })
    render(<SubmitSlotPage />)
    const confirmButton = await screen.findByRole('button', { name: /Confirm booking/i })

    const invite = [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)
    expect(invite).toBeDefined()
    attach(invite, [screenshot('invite')])

    await waitFor(() => {
      const preview = screen.getByLabelText('Detected interview slot')
      expect(preview).toBeInTheDocument()
      // The date and times are held together with no-break spaces (a phone
      // wraps the line only at its separators); read them as plain spaces.
      const text = preview.textContent.replace(/ /g, ' ')
      expect(text).toContain('Detected slot (IST)')
      expect(text).toContain('25 Oct 2026')
      expect(text).toContain('02:30 PM')
    })

    // Verify it is located above the Confirm booking button
    const preview = screen.getByLabelText('Detected interview slot')
    expect(preview.compareDocumentPosition(confirmButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('hides detected slot preview after successful booking confirmation', async () => {
    stubFetch({ date: '2026-10-25', time: '02:30 PM' })
    render(<SubmitSlotPage />)
    const confirm = await screen.findByRole('button', { name: /Confirm booking/i })

    const nameBox = document.querySelector('.sbs-field--client input')
    fireEvent.change(nameBox, { target: { value: 'Aniket' } })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L1' } })

    const invite = [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)
    attach(invite, [screenshot('invite')])

    await waitFor(() => {
      expect(screen.getByLabelText('Detected interview slot')).toBeInTheDocument()
    })

    fireEvent.click(confirm)

    await waitFor(() => {
      expect(screen.getByText(/Slot confirmed for Aniket/i)).toBeInTheDocument()
      expect(screen.queryByLabelText('Detected interview slot')).toBeNull()
    })
  })
})
