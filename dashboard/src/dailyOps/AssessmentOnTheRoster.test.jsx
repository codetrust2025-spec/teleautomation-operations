/**
 * An assessment sits on the roster, and says so.
 *
 * It occupies a slot exactly as an interview does -- the operator plans around
 * it the same way -- but it is a test the candidate sits alone inside a window,
 * not a meeting anyone attends. A row that does not distinguish them reads as
 * an interview nobody scheduled.
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
vi.mock('./PendingWorksStrip.jsx', () => ({ PendingWorksStrip: () => null }))

import { InterviewRoster } from './InterviewRoster.jsx'

const TODAY = '2026-09-15'

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function row(name, extra) {
  return {
    id: name, name, phone: '9000000001', date: TODAY, time: '19:05', time_end: '20:05',
    technology: 'Automation', interview_attendee: 'Bhavana', interview_attendance_status: '',
    interview_booking_source: 'ai_auto_booked', ...extra,
  }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  const interviews = [
    row('Assessment Candidate', { booking_type: 'Assessment', interview_role: 'Automation' }),
    row('Interview Candidate', { booking_type: 'Interview', interview_round: 'L1' }),
  ]
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/interviews/daily') || url.pathname.endsWith('/interviews/monitor')) {
      return jsonResponse({ status: 'ok', interviews, count: interviews.length, pending_count: 2 })
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

describe('an assessment on the roster', () => {
  it('is marked as an assessment, and an interview is not', async () => {
    render(<InterviewRoster />)

    const assessment = await screen.findByText('Assessment Candidate')
    const interview = await screen.findByText('Interview Candidate')

    await waitFor(() => {
      expect(within(assessment.closest('td')).getByText('Assessment')).toBeTruthy()
    })
    expect(within(interview.closest('td')).queryByText('Assessment')).toBeNull()
  })

  it('keeps the slot it was booked for', async () => {
    render(<InterviewRoster />)

    const assessment = await screen.findByText('Assessment Candidate')
    const cells = assessment.closest('tr').querySelectorAll('td')

    expect(cells[1].textContent.replace(/\s/g, '')).toContain('7:05')
  })
})
