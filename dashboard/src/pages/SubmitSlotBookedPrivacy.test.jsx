/**
 * Signed out, Confirmed slots shows initials and times (11 Oct audit).
 *
 * The server withholds names, clients and technologies from an anonymous
 * caller and says so with `details_withheld`; the page shows what it was given
 * and a one-line note, and nothing for a signed-in operator. Invented values.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

const NOTE = /shown to signed-in staff only/i
const SLOT = {
  name: 'A. F.', interview_round: 'L1', date: '2026-10-20', time: '10:00', time_end: '10:30',
  interview_booking_source: 'candidate_booked', booking_type: 'Interview', slot_phase: 'upcoming',
}

async function openConfirmed(payload) {
  vi.stubGlobal('fetch', vi.fn((url) => {
    const body = String(url).includes('/public/slots/booked') ? { status: 'ok', ...payload } : { status: 'ok', candidates: [] }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  }))
  render(<SubmitSlotPage />)
  fireEvent.click(await screen.findByRole('tab', { name: /confirmed slots/i }))
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('confirmed slots for a signed-out visitor', () => {
  it('lists the slot under its initials and says why the rest is not shown', async () => {
    await openConfirmed({ slots: [SLOT], details_withheld: true })
    expect(await screen.findByText('A. F.')).toBeTruthy()
    expect(screen.getByText(NOTE)).toBeTruthy()
  })

  it('shows no note to a signed-in operator', async () => {
    await openConfirmed({ slots: [{ ...SLOT, name: 'Asha Fakename', company: 'Example Client', technology: 'Java' }] })
    expect(await screen.findByText('Asha Fakename')).toBeTruthy()
    expect(screen.queryByText(NOTE)).toBeNull()
  })
})
