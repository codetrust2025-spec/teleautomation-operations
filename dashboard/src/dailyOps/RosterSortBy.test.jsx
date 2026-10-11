/**
 * Daily Ops: Sort By (11 Oct).
 *
 * Latest to Oldest by default, or Oldest to Latest; on one date the day reads
 * earliest first. Works on whatever the filters, search and refresh put on
 * screen, survives a reload, and only changes the order: no request is made
 * for it and no record is touched. Names are invented.
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
vi.mock('./PendingWorksStrip.jsx', () => ({ PendingWorksStrip: () => null }))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'
import {
  DEFAULT_ROSTER_SORT, ROSTER_SORT_STORAGE_KEY, minutesOfDay, readRosterSort, saveRosterSort, sortRosterRows,
} from './rosterSort.js'

const row = (id, name, date, time) => ({
  id, name, phone: '9000000001', date, time, time_end: '', technology: 'Python',
  interview_round: 'L1', interview_attendee: 'Bhavana', interview_attendance_status: '',
})

// Served deliberately out of order.
const SERVED = [
  row('b', 'Bala Fakename', '2026-10-14', '15:00'),
  row('a', 'Asha Fakename', '2026-10-12', '10:00'),
  row('d', 'Dev Fakename', '2026-10-14', '09:30'),
  row('c', 'Chitra Fakename', '2026-10-13', '3:00 PM'),
]

describe('sortRosterRows', () => {
  const names = rows => rows.map(r => r.id).join('')

  it('runs Latest to Oldest by default, each date earliest first', () => {
    expect(DEFAULT_ROSTER_SORT).toBe('date_desc')
    expect(names(sortRosterRows(SERVED))).toBe('dbca')
  })

  it('runs Oldest to Latest when asked, each date still earliest first', () => {
    expect(names(sortRosterRows(SERVED, 'date_asc'))).toBe('acdb')
  })

  it('puts undated rows and unreadable times last, and leaves the input alone', () => {
    const input = [row('x', 'X', '', '10:00'), row('t', 'T', '2026-10-12', 'soon'), row('a', 'A', '2026-10-12', '09:00')]
    expect(names(sortRosterRows(input, 'date_desc'))).toBe('atx')
    expect(names(sortRosterRows(input, 'date_asc'))).toBe('atx')
    expect(names(input)).toBe('xta')
  })

  it.each([
    ['15:00', 900], ['9:05', 545], ['3:00 PM', 900], ['12:15 am', 15], ['12:00 PM', 720], ['11:30am', 690],
    ['', null], ['25:00', null], ['soon', null],
  ])('reads %s as %s minutes', (value, minutes) => {
    expect(minutesOfDay(value)).toBe(minutes)
  })
})

describe('the saved choice', () => {
  beforeEach(() => window.localStorage.clear())

  it('defaults when nothing or something unknown is saved', () => {
    expect(readRosterSort()).toBe('date_desc')
    window.localStorage.setItem(ROSTER_SORT_STORAGE_KEY, 'by_name')
    expect(readRosterSort()).toBe('date_desc')
  })

  it('keeps a valid choice and ignores an invalid one', () => {
    saveRosterSort('date_asc')
    expect(readRosterSort()).toBe('date_asc')
    saveRosterSort('by_name')
    expect(window.localStorage.getItem(ROSTER_SORT_STORAGE_KEY)).toBe('date_asc')
  })

  it('falls back to the default when storage is blocked', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    expect(readRosterSort()).toBe('date_desc')
    spy.mockRestore()
  })
})

describe('Sort By on the Daily Ops roster', () => {
  let calls

  beforeEach(() => {
    window.localStorage.clear()
    calls = []
    vi.stubGlobal('fetch', vi.fn(async input => {
      const url = new URL(String(input), 'http://localhost')
      calls.push(url)
      const reply = body => ({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body })
      if (url.pathname.endsWith('/interviews/global')) {
        return reply({ status: 'ok', interviews: {}, available_months: [], booking_overview: { total: 0, by_candidate: [], by_level: [], by_technology: [] } })
      }
      if (url.pathname.endsWith('/interviews/monitor')) {
        return reply({ status: 'ok', interviews: SERVED, count: SERVED.length, awaiting_interviews: [] })
      }
      return reply({ status: 'ok', attendees: [], rounds: [], technologies: [], candidates: [] })
    }))
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  const shown = () => [...document.querySelectorAll('tbody tr')]
    .map(tr => tr.textContent)
    .map(text => ['Asha', 'Bala', 'Chitra', 'Dev'].find(n => text.includes(`${n} Fakename`)))
    .filter(Boolean)
  const sortBox = () => screen.getByRole('combobox', { name: 'Sort by' })

  it('shows Latest to Oldest by default, beside the filters', async () => {
    render(<DailyOpsPanel />)
    await waitFor(() => expect(shown()).toHaveLength(4))
    expect(sortBox().value).toBe('date_desc')
    expect([...sortBox().options].map(o => o.textContent)).toEqual(['Latest to Oldest', 'Oldest to Latest'])
    expect(sortBox().closest('.ops-roster-controls__filters')).not.toBeNull()
    expect(shown()).toEqual(['Dev', 'Bala', 'Chitra', 'Asha'])
  })

  it('switches order without asking the server again, and keeps the choice through a reload', async () => {
    const { unmount } = render(<DailyOpsPanel />)
    await waitFor(() => expect(shown()).toHaveLength(4))
    const before = calls.length
    fireEvent.change(sortBox(), { target: { value: 'date_asc' } })
    expect(shown()).toEqual(['Asha', 'Chitra', 'Dev', 'Bala'])
    expect(calls.length).toBe(before)
    expect(window.localStorage.getItem(ROSTER_SORT_STORAGE_KEY)).toBe('date_asc')
    expect(calls.every(url => !url.searchParams.has('sort'))).toBe(true)

    unmount()
    render(<DailyOpsPanel />)
    await waitFor(() => expect(shown()).toHaveLength(4))
    expect(sortBox().value).toBe('date_asc')
    expect(shown()).toEqual(['Asha', 'Chitra', 'Dev', 'Bala'])
  })

  it('stays sorted after a search refetches the roster, and Reset leaves the choice alone', async () => {
    render(<DailyOpsPanel />)
    await waitFor(() => expect(shown()).toHaveLength(4))
    fireEvent.change(sortBox(), { target: { value: 'date_asc' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Candidate search' }), { target: { value: 'fake' } })
    await waitFor(() => expect(calls.some(url => url.searchParams.get('candidate_search') === 'fake' || url.searchParams.get('q') === 'fake' || [...url.searchParams.values()].includes('fake'))).toBe(true))
    await waitFor(() => expect(shown()).toEqual(['Asha', 'Chitra', 'Dev', 'Bala']))
    fireEvent.click(screen.getByRole('button', { name: /Reset all filters/ }))
    await waitFor(() => expect(shown()).toHaveLength(4))
    expect(sortBox().value).toBe('date_asc')
    expect(shown()).toEqual(['Asha', 'Chitra', 'Dev', 'Bala'])
  })
})
