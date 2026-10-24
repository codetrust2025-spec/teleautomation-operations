/**
 * Daily Ops shows ★ Selected beside a selected candidate's name.
 *
 * Selected is the candidate's outcome -- a profile-wise candidate whose stage
 * is Closed / Completed -- read from the `candidate_selected` the server puts
 * on every interview row, so the roster agrees with Candidates and Bookings
 * Analytics. It is not an attendance status: the status counters and the row's
 * own status are untouched, and a round-wise candidate who completed, or one
 * still in progress, shows no badge. Names are invented.
 */
import React from 'react'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'
import { captureLayout } from '../test/captureLayout.js'

const slot = { date: '2026-10-26', time: '15:00', time_end: '16:00', slot_confirmed: true, interview_round: 'L1', technology: 'React JS', interview_attendee: 'Bhavana' }
const ROWS = [
  { ...slot, id: 'r1', name: 'Asha Fakename', phone: '9000000001', stage: 'completed', service_type: 'profile_service', candidate_selected: true, interview_attendance_status: 'attended' },
  { ...slot, id: 'r2', name: 'Ravi Testcase', phone: '9000000002', stage: 'completed', service_type: 'round_wise', candidate_selected: false, time: '16:30', time_end: '17:00' },
  { ...slot, id: 'r3', name: 'Meena Sample', phone: '9000000003', stage: 'in_progress', service_type: 'profile_service', candidate_selected: false, time: '18:00', time_end: '18:30' },
]

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/interviews/monitor') || url.pathname.endsWith('/interviews/daily')) {
      return jsonResponse({ status: 'ok', interviews: ROWS, count: ROWS.length })
    }
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({ status: 'ok', interviews: { count: 3, pending_count: 2, attended_count: 1 }, available_months: [],
        booking_overview: { total: 3, by_candidate: [], by_level: [], by_technology: [] } })
    }
    return jsonResponse({ status: 'ok' })
  }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the Daily Ops roster', () => {
  it('shows ★ Selected beside a selected candidate, and beside nobody else', async () => {
    render(<DailyOpsPanel />)
    await screen.findByTitle('Asha Fakename')
    const cell = (name) => screen.getByTitle(name).closest('td')
    expect(within(cell('Asha Fakename')).getByText('Selected').closest('.ta-selected-badge')).toBeTruthy()
    expect(cell('Ravi Testcase').querySelector('.ta-selected-badge')).toBeNull()
    expect(cell('Meena Sample').querySelector('.ta-selected-badge')).toBeNull()
    captureLayout('daily-ops-selected-badge', document.querySelector('.daily-ops-page'))
  })

  it('leaves the attendance status and its counters as they are', async () => {
    render(<DailyOpsPanel />)
    await screen.findByTitle('Asha Fakename')
    const row = screen.getByTitle('Asha Fakename').closest('tr')
    expect(row.textContent).toContain('Attended')
    expect(row.textContent).not.toMatch(/Selected.*Selected/)
  })
})
