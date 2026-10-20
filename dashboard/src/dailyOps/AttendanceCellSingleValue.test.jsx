/**
 * The Attendance column says each row's status once: in the dropdown.
 *
 * A status pill under the dropdown repeated its value -- "Pending" under
 * "Pending", "Attended" under "Attended" -- in every row of the desktop table
 * (phones already hid it). The pill is gone from the markup; the dropdown still
 * shows, and still changes, the stored status.
 */
import React from 'react'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'

const TODAY = '2026-10-06'

const row = (id, name, status) => ({
  id, name, phone: '9000000000', date: TODAY, time: '10:00', time_end: '10:30',
  technology: 'Python', interview_round: 'L1', interview_attendee: 'Bhavana',
  interview_attendance_status: status, interview_booking_source: 'candidate_booked',
})

const ROWS = [
  row('p', 'Asha Rao', ''),
  row('a', 'Vikram Devi', 'attended'),
  row('n', 'Meera Iyer', 'not_attended'),
  row('r', 'Ravi Kumar', 're_service'),
]

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({ status: 'ok', interviews: {}, available_months: [], booking_overview: { total: 0, by_candidate: [], by_level: [], by_technology: [] } })
    }
    if (/\/interviews\/(daily|monitor)$/.test(url.pathname)) {
      return jsonResponse({ status: 'ok', interviews: ROWS, count: ROWS.length, awaiting_interviews: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function renderPanel() {
  render(<DailyOpsPanel />)
  await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())
  await act(async () => { await Promise.resolve() })
}

function attendanceCell(name) {
  return screen.getByText(name).closest('tr').querySelector('td[data-label="Status"]')
}

describe('the Attendance column', () => {
  it.each([
    ['Asha Rao', '', 'Pending'],
    ['Vikram Devi', 'attended', 'Attended'],
    ['Meera Iyer', 'not_attended', 'Not attended'],
    ['Ravi Kumar', 're_service', 'Re-Service'],
  ])('shows %s\'s status once, read-only (changed in Edit)', async (name, value, label) => {
    await renderPanel()
    const cell = attendanceCell(name)
    expect(cell.textContent.trim()).toBe(label)
    expect(cell.querySelector('select, input, button')).toBeNull()
    expect(cell.querySelector('.ops-status-pill')).toBeNull()
    const status = cell.querySelector('.ops-attendance-status')
    expect(status.getAttribute('aria-label')).toBe(`Attendance: ${label}`)
    expect(value === '' ? 'pending' : value).toBeTruthy()
  })

  it('still colours the row by its status', async () => {
    await renderPanel()
    const rowOf = name => screen.getByText(name).closest('tr')
    expect(rowOf('Vikram Devi').className).toContain('ops-interview-row--done')
    expect(rowOf('Meera Iyer').className).toContain('ops-interview-row--missed')
  })
})
