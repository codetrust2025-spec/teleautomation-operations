/**
 * Confirmed slots read earliest first (29 Oct), by each slot's IST start and
 * with the same helper Daily Ops sorts with, whatever order the payload holds:
 * days in calendar order, and inside a day the clock in order however it was
 * written. Display only -- the page asks for nothing new. Names are invented.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

const slot = (name, date, time, phase = 'upcoming') => ({
  name, date, time, time_end: '', technology: 'Java', interview_round: 'L1',
  interview_booking_source: 'candidate_booked', slot_phase: phase,
})

// Served deliberately out of order: a later day first, PM before AM, a 2027 row
// ahead of 2026 ones, and the ended-without-outcome section shuffled too.
const SERVED = [
  slot('Dev Fakename', '2026-11-02', '10:00'),
  slot('Chitra Fakename', '2026-10-31', '3:00 PM'),
  slot('Gita Fakename', '2027-01-05', '09:00'),
  slot('Asha Fakename', '2026-10-31', '09:30'),
  slot('Bala Fakename', '2026-10-31', '11:00'),
  slot('Eshan Fakename', '2026-10-30', '16:00', 'needs_status_update'),
  slot('Farah Fakename', '2026-10-29', '17:00', 'needs_status_update'),
]

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function openConfirmed() {
  const calls = []
  vi.stubGlobal('fetch', vi.fn((url, init = {}) => {
    calls.push({ url: String(url), method: (init.method || 'GET').toUpperCase() })
    const body = String(url).includes('/public/slots/booked') ? { status: 'ok', slots: SERVED } : { status: 'ok', candidates: [] }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  }))
  render(<SubmitSlotPage />)
  fireEvent.click(await screen.findByRole('tab', { name: /confirmed slots/i }))
  await waitFor(() => expect(document.querySelectorAll('.sbs-confirmed-card')).toHaveLength(SERVED.length))
  return calls
}

const namesIn = section => [...section.querySelectorAll('.sbs-slot-card__name')].map(el => el.textContent.split(' ')[0])
const daysIn = section => [...section.querySelectorAll('.sbs-date-group__label')].map(el => el.textContent.replace(/^(Today|Tomorrow) · /, ''))

describe('Confirmed slots order', () => {
  it('lists upcoming slots earliest first, days in calendar order', async () => {
    const calls = await openConfirmed()
    const upcoming = screen.getByRole('heading', { name: 'Upcoming slots' }).closest('section')
    expect(namesIn(upcoming)).toEqual(['Asha', 'Bala', 'Chitra', 'Dev', 'Gita'])
    expect(daysIn(upcoming)).toEqual(['Sat, 31 Oct 2026', 'Mon, 2 Nov 2026', 'Tue, 5 Jan 2027'])
    expect(calls.every(call => call.method === 'GET')).toBe(true)
  })

  it('lists the ended-without-outcome slots earliest first too', async () => {
    await openConfirmed()
    const awaiting = screen.getByRole('heading', { name: 'Needs status update' }).closest('section')
    expect(namesIn(awaiting)).toEqual(['Farah', 'Eshan'])
  })
})
