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

import { DailyOpsPanel } from './DailyOpsPanel.jsx'
import { formatDayLabel } from './InterviewRoster.jsx'
import {
  DEFAULT_ROSTER_SORT, ROSTER_SORT_OPTIONS, ROSTER_SORT_STORAGE_KEY, minutesOfDay, readRosterSort, saveRosterSort, sortRosterRows,
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

  it('runs Latest to Oldest by default: date, then time, both newest first', () => {
    expect(DEFAULT_ROSTER_SORT).toBe('date_desc')
    expect(names(sortRosterRows(SERVED))).toBe('bdca')
  })

  it('runs Oldest to Latest when asked: date, then time, both oldest first', () => {
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
  function openFilterPanel() {
    const trigger = screen.getByRole('button', { name: /^Filters/ })
    fireEvent.click(trigger)
  }
  const sortBox = () => {
    let box = screen.queryByRole('combobox', { name: 'Sort by' })
    if (!box) {
      openFilterPanel()
      box = screen.getByRole('combobox', { name: 'Sort by' })
    }
    return box
  }

  function applySort(value) {
    const box = sortBox()
    fireEvent.change(box, { target: { value } })
    fireEvent.click(screen.getByRole('button', { name: /^Apply/ }))
  }

  it('shows Latest to Oldest by default, inside the filters panel', async () => {
    render(<DailyOpsPanel />)
    await waitFor(() => expect(shown()).toHaveLength(4))
    expect(sortBox().value).toBe('date_desc')
    expect([...sortBox().options].map(o => o.textContent)).toEqual([
      'Latest to Oldest', 'Oldest to Latest', 'Time Earliest', 'Time Latest', 'Interview Round', 'Attendance Status',
      'Recently Updated', 'Recently Created', 'Screenshot Available First', 'Missing Company First', 'Missing Round First',
    ])
    expect(shown()).toEqual(['Bala', 'Dev', 'Chitra', 'Asha'])
  })

  it('switches order without asking the server again, and keeps the choice through a reload', async () => {
    const { unmount } = render(<DailyOpsPanel />)
    await waitFor(() => expect(shown()).toHaveLength(4))
    const before = calls.length
    applySort('date_asc')
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
    applySort('date_asc')
    fireEvent.change(screen.getByRole('textbox', { name: 'Candidate search' }), { target: { value: 'fake' } })
    await waitFor(() => expect(calls.some(url => url.searchParams.get('candidate_search') === 'fake' || url.searchParams.get('q') === 'fake' || [...url.searchParams.values()].includes('fake'))).toBe(true))
    await waitFor(() => expect(shown()).toEqual(['Asha', 'Chitra', 'Dev', 'Bala']))
    fireEvent.click(screen.getByRole('button', { name: /Reset all filters/ }))
    await waitFor(() => expect(shown()).toHaveLength(4))
    expect(sortBox().value).toBe('date_asc')
    expect(shown()).toEqual(['Asha', 'Chitra', 'Dev', 'Bala'])
  })
})

describe('the other orders (11 Oct, second request)', () => {
  // Four interviews with every field the orders read. Names are invented.
  const r = (id, over) => ({ id, name: `${id} Fakename`, date: '2026-10-14', time: '10:00', interview_round: 'L1',
    interview_attendance_status: '', interview_company: 'Example Co', created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z', slot_screenshot_proof: { url: `/x/${id}` }, ...over })
  const ROWS = [
    r('p', { date: '2026-10-12', time: '16:00', interview_round: 'L2', interview_attendance_status: 'attended',
             interview_company: '', updated_at: '2026-10-11T09:00:00Z', created_at: '2026-10-02T00:00:00Z' }),
    r('q', { date: '2026-10-15', time: '09:00', interview_round: '', interview_attendance_status: 'cancelled',
             slot_screenshot_proof: null, created_at: '2026-10-09T00:00:00Z' }),
    r('s', { date: '2026-10-13', time: '11:30', interview_round: 'Screening', updated_at: '2026-10-10T00:00:00Z' }),
    r('t', { date: '2026-10-14', time: '14:00', interview_round: 'Final', interview_attendance_status: 'not_attended',
             interview_company: '', slot_screenshot_proof: { url: '' }, created_at: '' }),
  ]
  const ids = order => sortRosterRows(ROWS, order).map(x => x.id).join('')

  it.each([
    ['date_desc', 'qtsp'],              // 15, 14, 13, 12 Oct
    ['date_asc', 'pstq'],
    ['time_asc', 'qstp'],               // 09:00, 11:30, 14:00, 16:00
    ['time_desc', 'ptsq'],
    ['round', 'sptq'],                  // Screening, L2, Final, then no round
    ['status', 'sptq'],                 // Pending, Attended, Not attended, Cancelled
    ['updated_desc', 'psqt'],           // 11 Oct, 10 Oct, then the rest by date
    ['created_desc', 'qpst'],           // 9 Oct, 2 Oct, 1 Oct, then none
    ['screenshot_first', 'spqt'],       // with a file first (by date), then none
    ['missing_company_first', 'tpqs'],  // no company first (by date), then the rest
    ['missing_round_first', 'qtsp'],    // no round first, then the rest by date
  ])('%s -> %s', (order, expected) => {
    expect(ids(order)).toBe(expected)
  })

  it('offers no alphabetical order by candidate, company, technology or attendee', () => {
    expect(ROSTER_SORT_OPTIONS).toHaveLength(11)
    for (const { value, label } of ROSTER_SORT_OPTIONS) {
      expect(`${value} ${label}`).not.toMatch(/name|alpha|a-z|company name|technology|attendee/i)
    }
  })

  it('an unknown saved value falls back to the default order', () => {
    expect(ids('by_candidate_name')).toBe(ids('date_desc'))
  })
})

describe('Latest to Oldest reads the real date, and the label shows the year (11 Oct bug)', () => {
  // Production showed "Thu, 16 Sept" and "Sat, 19 Jun" above "Mon, 12 Oct": they
  // were 2027 rows, newest by date, but the label left the year out.
  const rows = [
    row('oct', 'Oct Fakename', '2026-10-12', '11:00'),
    row('sep', 'Sep Fakename', '2026-09-16', '14:00'),
    row('jun27', 'Jun Fakename', '2027-06-19', '14:00'),
    row('sep27', 'Sep27 Fakename', '2027-09-16', '14:00'),
    row('dec', 'Dec Fakename', '2026-12-10', '16:00'),
  ]
  const ids = order => sortRosterRows(rows, order).map(r => r.id)

  it('orders by the ISO date, years included', () => {
    expect(ids('date_desc')).toEqual(['sep27', 'jun27', 'dec', 'oct', 'sep'])
    expect(ids('date_asc')).toEqual(['sep', 'oct', 'dec', 'jun27', 'sep27'])
  })

  it('on one date: latest time first by default, earliest first oldest-first', () => {
    const day = [row('m', 'M', '2026-10-12', '11:00 AM'), row('e', 'E', '2026-10-12', '17:30'), row('n', 'N', '2026-10-12', '2:30 PM')]
    expect(sortRosterRows(day, 'date_desc').map(r => r.id)).toEqual(['e', 'n', 'm'])
    expect(sortRosterRows(day, 'date_asc').map(r => r.id)).toEqual(['m', 'n', 'e'])
  })

  it('shows the year on a date outside this year, and not on one inside it', () => {
    const year = new Date().getFullYear()
    expect(formatDayLabel(`${year + 1}-06-19`)).toContain(String(year + 1))
    expect(formatDayLabel(`${year - 1}-09-16`)).toContain(String(year - 1))
    expect(formatDayLabel(`${year}-10-12`)).not.toContain(String(year))
  })
})
