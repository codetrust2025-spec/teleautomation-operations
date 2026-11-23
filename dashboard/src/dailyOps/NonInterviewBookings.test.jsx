/**
 * A career consultation or BGV call is a booking, not an interview (23 Nov).
 *
 * The mail agent books them from their invitations. They keep their slot on
 * the roster, say what they are, and have no interview round -- so they read
 * "Not an interview", never "Needs Review", which no evidence could ever clear.
 */
import React from 'react'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

import { InterviewRoster } from './InterviewRoster.jsx'

const TODAY = '2026-09-15'

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function row(name, extra) {
  return {
    id: name, name, phone: '9000000001', date: TODAY, time: '19:05', time_end: '20:05',
    technology: 'Automation', interview_attendee: 'Bhavana', interview_attendance_status: '',
    interview_booking_source: 'ai_auto_booked', interview_round: '', ...extra,
  }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  const interviews = [
    row('Consultation Candidate', { booking_type: 'Career Consultation' }),
    row('Verification Candidate', { booking_type: 'BGV Verification' }),
    row('Roundless Candidate', { booking_type: 'Interview' }),
  ]
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/interviews/daily') || url.pathname.endsWith('/interviews/monitor')) {
      return jsonResponse({ status: 'ok', interviews, count: interviews.length, pending_count: 3 })
    }
    if (url.pathname.endsWith('/interviews/filter-options')) {
      return jsonResponse({ status: 'ok', attendees: [], rounds: [], technologies: [], candidates: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('bookings that are not interviews', () => {
  it('are labelled by type and are not Needs Review; a roundless interview still is', async () => {
    render(<InterviewRoster />)

    for (const [name, kind] of [['Consultation Candidate', 'Career Consultation'], ['Verification Candidate', 'BGV Verification']]) {
      const cell = await screen.findByText(name)
      await waitFor(() => expect(within(cell.closest('td')).getByText(kind)).toBeTruthy())
      const rowEl = cell.closest('tr')
      expect(within(rowEl).getByText('Not an interview')).toBeTruthy()
      expect(within(rowEl).queryByText('Needs Review')).toBeNull()
    }
    const plain = (await screen.findByText('Roundless Candidate')).closest('tr')
    expect(within(plain).getByText('Needs Review')).toBeTruthy()
  })
})
