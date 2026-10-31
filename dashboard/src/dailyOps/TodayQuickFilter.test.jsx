/**
 * Daily Ops: the Today button beside Filters (29 Oct).
 *
 * One click shows only the interviews scheduled on the current IST date,
 * earliest first; the button stays lit while that is the view, and Reset, a
 * second click, the calendar's Upcoming or any other period turns it off.
 * Search and the status tabs keep narrowing within it. Only reads are made:
 * the day roster and the day summary the calendar's Today preset already uses.
 * Names are invented.
 */
import React from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'

// 01:30 IST on 30 October is still the 29th in UTC: "today" has to be the 30th.
const NOW = '2026-10-29T20:00:00Z'
const TODAY_IST = '2026-10-30'

const row = (id, time, status = '') => ({
  id, name: `${id} Fakename`, phone: '9000000001', date: TODAY_IST, time, time_end: '',
  technology: 'Python', interview_round: 'L1', interview_attendee: 'Bhavana', interview_attendance_status: status,
})
// Served out of order, with the clock written both ways.
const TODAY_ROWS = [row('Chitra', '2:30 PM', 'attended'), row('Asha', '09:00'), row('Bala', '11:00', 'attended')]

let calls
const writes = () => calls.filter(call => call.method !== 'GET')

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(NOW))
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (input, init = {}) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push({ url, method: (init.method || 'GET').toUpperCase() })
    const reply = body => ({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body })
    if (url.pathname.endsWith('/interviews/global')) {
      return reply({ status: 'ok', interviews: {}, booking_overview: { total: 0, by_candidate: [], by_level: [], by_technology: [] } })
    }
    if (url.pathname.endsWith('/interviews/daily')) {
      const rows = url.searchParams.get('date') === TODAY_IST ? TODAY_ROWS : []
      const search = (url.searchParams.get('search') || '').toLowerCase()
      const matched = search ? rows.filter(r => r.name.toLowerCase().includes(search)) : rows
      return reply({ status: 'ok', interviews: matched, count: matched.length })
    }
    if (url.pathname.endsWith('/interviews/monitor')) return reply({ status: 'ok', interviews: [], count: 0 })
    return reply({ status: 'ok' })
  }))
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const todayButton = () => screen.getByRole('button', { name: 'Today' })
const rosterCalls = () => calls.filter(call => /\/interviews\/(daily|monitor)$/.test(call.url.pathname)).map(call => call.url)
const lastRosterCall = () => rosterCalls().at(-1)
const lastGlobalCall = () => calls.filter(call => call.url.pathname.endsWith('/interviews/global')).map(call => call.url).at(-1)
const shown = () => [...document.querySelectorAll('tbody tr')]
  .map(tr => ['Asha', 'Bala', 'Chitra'].find(n => tr.textContent.includes(`${n} Fakename`)))
  .filter(Boolean)

async function renderPanel() {
  render(<DailyOpsPanel />)
  await waitFor(() => expect(lastRosterCall()).toBeTruthy())
  await act(async () => { await Promise.resolve() })
}

async function turnTodayOn() {
  fireEvent.click(todayButton())
  await waitFor(() => expect(lastRosterCall().pathname).toMatch(/\/interviews\/daily$/))
  await waitFor(() => expect(shown()).toHaveLength(3))
}

async function expectUpcoming() {
  await waitFor(() => expect(lastRosterCall().pathname).toMatch(/\/interviews\/monitor$/))
  expect(lastRosterCall().searchParams.get('upcoming_only')).toBe('true')
  expect(todayButton()).toHaveAttribute('aria-pressed', 'false')
  expect(todayButton().className).not.toContain('ops-roster-today--active')
}

describe('the Today button', () => {
  it('sits immediately beside the Filters button, off while Upcoming is showing', async () => {
    await renderPanel()
    const filters = screen.getByRole('button', { name: /^Filters/ })
    expect(filters.closest('.ops-filter-dropdown-wrap').nextElementSibling).toBe(todayButton())
    await expectUpcoming()
  })

  it('shows only the current IST date, earliest first, and stays highlighted', async () => {
    await renderPanel()
    await turnTodayOn()
    expect(lastRosterCall().searchParams.get('date')).toBe(TODAY_IST)
    expect(lastRosterCall().searchParams.has('upcoming_only')).toBe(false)
    await waitFor(() => expect(lastGlobalCall().searchParams.get('from')).toBe(TODAY_IST))
    expect(lastGlobalCall().searchParams.get('to')).toBe(TODAY_IST)
    expect(shown()).toEqual(['Asha', 'Bala', 'Chitra'])
    expect(todayButton()).toHaveAttribute('aria-pressed', 'true')
    expect(todayButton().className).toContain('ops-roster-today--active')
    expect(screen.getByRole('button', { name: 'Filter interviews by date' })).toHaveTextContent('Today')
    expect(writes()).toEqual([])
  })

  it('goes back to Upcoming on a second click', async () => {
    await renderPanel()
    await turnTodayOn()
    fireEvent.click(todayButton())
    await expectUpcoming()
  })

  it('is cleared by Reset', async () => {
    await renderPanel()
    await turnTodayOn()
    fireEvent.click(screen.getByRole('button', { name: /Reset all filters \(1 set\)/ }))
    await expectUpcoming()
  })

  it("is cleared by the calendar's Upcoming, replaced by another period, and lit by today's date off the grid", async () => {
    await renderPanel()
    await turnTodayOn()
    const openCalendar = () => {
      fireEvent.click(screen.getByRole('button', { name: 'Filter interviews by date' }))
      return screen.getByRole('dialog', { name: 'Choose a date' })
    }
    fireEvent.click(within(openCalendar()).getByRole('button', { name: 'Upcoming' }))
    await expectUpcoming()

    fireEvent.click(within(openCalendar()).getByRole('button', { name: 'This Week' }))
    await waitFor(() => expect(lastRosterCall().searchParams.get('from')).toBe('2026-10-26'))
    expect(todayButton()).toHaveAttribute('aria-pressed', 'false')

    fireEvent.click(within(openCalendar()).getByRole('button', { name: /Friday.*30.*October.*2026/ }))
    await waitFor(() => expect(lastRosterCall().searchParams.get('date')).toBe(TODAY_IST))
    expect(todayButton()).toHaveAttribute('aria-pressed', 'true')
  })

  it('keeps search and the status tabs narrowing within today, in order', async () => {
    await renderPanel()
    await turnTodayOn()

    fireEvent.click(screen.getByRole('button', { name: /^Attended/ }))
    await waitFor(() => expect(shown()).toEqual(['Bala', 'Chitra']))
    expect(todayButton()).toHaveAttribute('aria-pressed', 'true')

    fireEvent.change(screen.getByRole('textbox', { name: 'Candidate search' }), { target: { value: 'chitra' } })
    await waitFor(() => expect(lastRosterCall().searchParams.get('search')).toBe('chitra'))
    expect(lastRosterCall().searchParams.get('date')).toBe(TODAY_IST)
    await waitFor(() => expect(shown()).toEqual(['Chitra']))
    expect(todayButton()).toHaveAttribute('aria-pressed', 'true')
  })

  it('Refresh reads today again without turning it off', async () => {
    await renderPanel()
    await turnTodayOn()
    const before = rosterCalls().length
    fireEvent.click(screen.getByRole('button', { name: /^Refresh$/ }))
    await waitFor(() => expect(rosterCalls().length).toBeGreaterThan(before))
    expect(lastRosterCall().searchParams.get('date')).toBe(TODAY_IST)
    expect(todayButton()).toHaveAttribute('aria-pressed', 'true')
    expect(shown()).toEqual(['Asha', 'Bala', 'Chitra'])
  })
})
