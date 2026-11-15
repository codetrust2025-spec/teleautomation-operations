/**
 * The Edit dialog's Interview round list offers every round a booking can
 * store (VALID_INTERVIEW_ROUNDS): the six standard rounds and Assessment.
 *
 * L3 was missing (15 Nov): an L3 interview opened with "Select round" shown,
 * and no interview could be set to L3 here. Names are invented.
 */
import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

import { InterviewRoster } from './InterviewRoster.jsx'

const TODAY = '2026-09-15'
let writes

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function row(name, round) {
  return {
    id: name, name, phone: '9000000001', date: TODAY, time: '19:05', time_end: '20:05',
    technology: 'Automation', interview_round: round, interview_attendee: 'Bhavana',
    interview_attendance_status: '', interview_booking_source: 'ai_auto_booked',
  }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  writes = []
  const interviews = [row('Third Round Person', 'L3'), row('First Round Person', 'L1')]
  vi.stubGlobal('fetch', vi.fn(async (input, init = {}) => {
    const url = new URL(String(input), 'http://localhost')
    if (init.method && init.method !== 'GET') writes.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : null })
    if (url.pathname.endsWith('/interviews/daily') || url.pathname.endsWith('/interviews/monitor')) {
      return jsonResponse({ status: 'ok', interviews, count: interviews.length })
    }
    return jsonResponse({ status: 'ok' })
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

async function openEdit(name) {
  render(<InterviewRoster />)
  await screen.findByText(name)
  fireEvent.click(screen.getByText(name).closest('tr').querySelector('td[data-label="Date"]'))
  await screen.findByText('Edit interview slot')
  return screen.getByLabelText('Interview round')
}

describe('the Edit dialog round list', () => {
  it('offers the six standard rounds and Assessment', async () => {
    const select = await openEdit('First Round Person')
    expect([...select.options].map(o => o.value))
      .toEqual(['', 'L1', 'L2', 'L3', 'HR', 'Final', 'Screening', 'Assessment'])
  })

  it('opens an L3 interview showing L3', async () => {
    const select = await openEdit('Third Round Person')
    expect(select.value).toBe('L3')
  })

  it('saves a change to L3', async () => {
    const select = await openEdit('First Round Person')
    fireEvent.change(select, { target: { value: 'L3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(writes.some(w => w.body?.interview_round === 'L3')).toBe(true))
  })
})
