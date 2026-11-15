/**
 * Daily Ops Filters take several values per field (Nov 2026).
 *
 * - Rounds HR + L2 + L3 (ticked in Profile + Round, the one place for rounds
 *   and profiles since 15 Nov) go to the server as `round=HR&round=L2&round=L3`,
 *   on the table's request and on the counters' request alike. The server ORs
 *   values within a field and ANDs the fields
 *   (tests/test_daily_ops_multi_select_filters.py).
 * - The Filters button shows the count and a summary of what is applied.
 * - Reset's badge counts fields in use, and Reset clears every value.
 * - With two attendees chosen the Attendee column stays: it tells their rows apart.
 */
import React from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'
import { applyFilters, closeMenus, openFilters, tickProfileRound, tickedProfileRound, toggleFilterValue } from './filterMenuTestHelpers.js'

const TODAY = '2026-10-06'
let calls

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

const ROWS = [
  { id: 'a', name: 'Asha Varma', phone: '9000000101', date: TODAY, time: '10:00', time_end: '10:30',
    technology: 'Python', interview_round: 'HR', interview_attendee: 'Bhavana', interview_attendance_status: '' },
  { id: 'b', name: 'Bala Kumar', phone: '9000000102', date: TODAY, time: '11:00', time_end: '11:30',
    technology: 'Java', interview_round: 'L2', interview_attendee: 'Nikhila', interview_attendance_status: '' },
  { id: 'c', name: 'Chitra Rao', phone: '9000000103', date: TODAY, time: '12:00', time_end: '12:30',
    technology: 'Java', interview_round: 'L3', interview_attendee: 'Bhavana', interview_attendance_status: '' },
  { id: 'd', name: 'Dev Prasad', phone: '9000000104', date: TODAY, time: '13:00', time_end: '13:30',
    technology: 'Python', interview_round: 'L1', interview_attendee: 'Tool', interview_attendance_status: '' },
]

/** Stands in for the server's rule: OR within a field, AND across fields. */
function serverRows(url) {
  const pick = (key, field) => {
    const wanted = url.searchParams.getAll(key)
    return row => wanted.length === 0 || wanted.includes(row[field])
  }
  return ROWS
    .filter(pick('round', 'interview_round'))
    .filter(pick('attendee', 'interview_attendee'))
    .filter(pick('technology', 'technology'))
    .filter(pick('candidate', 'name'))
}

function mockFetch() {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok',
        interviews: {
          by_candidate: ROWS.map(r => ({ name: r.name, scheduled: 1 })),
          by_technology: [{ name: 'Java' }, { name: 'Python' }],
        },
        booking_overview: { total: ROWS.length, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (/\/interviews\/(daily|monitor)$/.test(url.pathname)) {
      const rows = serverRows(url)
      return jsonResponse({ status: 'ok', interviews: rows, count: rows.length, awaiting_interviews: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
}

const globalCalls = () => calls.filter(u => u.pathname.endsWith('/interviews/global'))
const rosterCalls = () => calls.filter(u => /\/interviews\/(daily|monitor)$/.test(u.pathname))
const inTable = name => [...document.querySelectorAll('.ops-interview-table tbody tr')]
  .some(tr => tr.textContent.includes(name))

async function renderPanel() {
  const view = render(<DailyOpsPanel />)
  await waitFor(() => expect(inTable('Asha Varma')).toBe(true))
  await act(async () => { await Promise.resolve() })
  return view
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  mockFetch()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('several levels at once', () => {
  it('sends every ticked level to the table and the counters, and shows those rows', async () => {
    await renderPanel()
    tickProfileRound('Interview rounds', 'HR')
    tickProfileRound('Interview rounds', 'L2')
    tickProfileRound('Interview rounds', 'L3')

    // Applied as ticked, in interview order.
    await waitFor(() => expect(rosterCalls().at(-1).searchParams.getAll('round')).toEqual(['L2', 'L3', 'HR']))
    await waitFor(() => expect(globalCalls().at(-1).searchParams.getAll('round')).toEqual(['L2', 'L3', 'HR']))
    await waitFor(() => expect(inTable('Dev Prasad')).toBe(false))
    expect(inTable('Asha Varma')).toBe(true)
    expect(inTable('Bala Kumar')).toBe(true)
    expect(inTable('Chitra Rao')).toBe(true)

    expect(screen.getByRole('button', { name: 'Profile and round (3 selected)' })).toBeInTheDocument()
    // The Filters button does not count rounds any more.
    expect(screen.getByRole('button', { name: 'Filters' })).toBeInTheDocument()
  })

  it('combines fields with AND: levels HR + L3 and attendee Bhavana', async () => {
    await renderPanel()
    tickProfileRound('Interview rounds', 'HR')
    tickProfileRound('Interview rounds', 'L2')
    closeMenus()
    openFilters()
    toggleFilterValue('Attendee filter', 'Bhavana')
    applyFilters()

    await waitFor(() => {
      const last = rosterCalls().at(-1).searchParams
      expect(last.getAll('round')).toEqual(['L2', 'HR'])
      expect(last.getAll('attendee')).toEqual(['Bhavana'])
    })
    await waitFor(() => expect(inTable('Bala Kumar')).toBe(false))
    expect(inTable('Asha Varma')).toBe(true)
  })

  it('says which values narrow an empty table, joined with "or"', async () => {
    await renderPanel()
    tickProfileRound('Interview rounds', 'Final')
    tickProfileRound('Interview rounds', 'Screening')
    await waitFor(() => expect(screen.getByText(/level Screening or Final/)).toBeInTheDocument())
  })
})

describe('attendees and candidates', () => {
  it('keeps the Attendee column for two attendees and drops it for one', async () => {
    await renderPanel()
    const heads = () => [...document.querySelectorAll('.ops-interview-table thead th')].map(th => th.textContent)

    openFilters()
    toggleFilterValue('Attendee filter', 'Bhavana')
    toggleFilterValue('Attendee filter', 'Nikhila')
    applyFilters()
    await waitFor(() => expect(rosterCalls().at(-1).searchParams.getAll('attendee')).toEqual(['Bhavana', 'Nikhila']))
    expect(heads()).toContain('Attendee')

    openFilters()
    toggleFilterValue('Attendee filter', 'Nikhila')
    applyFilters()
    await waitFor(() => expect(rosterCalls().at(-1).searchParams.getAll('attendee')).toEqual(['Bhavana']))
    await waitFor(() => expect(heads()).not.toContain('Attendee'))
  })

  it('sends several candidates as exact choices and clears the search box', async () => {
    await renderPanel()
    fireEvent.change(screen.getByLabelText('Candidate search'), { target: { value: 'asha' } })
    openFilters()
    toggleFilterValue('Candidate filter', 'Asha Varma')
    toggleFilterValue('Candidate filter', 'Dev Prasad')
    applyFilters()
    await waitFor(() => expect(rosterCalls().at(-1).searchParams.getAll('candidate')).toEqual(['Asha Varma', 'Dev Prasad']))
    expect(rosterCalls().at(-1).searchParams.get('search')).toBeNull()
    expect(screen.getByLabelText('Candidate search')).toHaveValue('')
  })
})

describe('Reset', () => {
  it('counts fields in use and clears every value', async () => {
    await renderPanel()
    tickProfileRound('Interview rounds', 'HR')
    tickProfileRound('Interview rounds', 'L2')
    // Round-wise unticked: profile bookings only (sent as channel=profile).
    tickProfileRound('Booking type', 'Round-wise')
    closeMenus()
    // Two fields, three values: Reset puts back two things.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reset all filters (2 set)' })).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: /^Reset all filters/ }))
    await waitFor(() => {
      const last = rosterCalls().at(-1).searchParams
      expect(last.getAll('round')).toEqual([])
      expect(last.get('channel')).toBeNull()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Profile and round' }))
    expect(tickedProfileRound('Booking type')).toEqual(['Profile-wise', 'Round-wise'])
    expect(tickedProfileRound('Interview rounds')).toEqual([])
  })
})
