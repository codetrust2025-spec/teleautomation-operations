/**
 * Daily Ops on a desktop: company, column widths, one Reset, quiet booking chips.
 *
 * - Every row says which company the interview is with, or that none was
 *   recorded, rather than leaving the reader to open the booking.
 * - The table is fixed-layout. Its headers carried no width, so all ten
 *   columns came out equal; a <colgroup> now gives each one its share, and it
 *   has to stay one <col> per <th> in the same order.
 * - The ⋮ column has a real "Actions" heading.
 * - Reset puts back the period, date, search, candidate, attendee, level,
 *   profile and status tab together, and reloads the counters and the table
 *   even when nothing was set.
 * - The booking source is a small chip instead of a third line of
 *   "Candidate booked" in every row, still named on hover and to a reader.
 * - A screenshot whose file is gone shows an icon, not a broken image.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))
vi.mock('./PendingWorksStrip.jsx', () => ({ PendingWorksStrip: () => null }))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, '..', 'dailyOps.css'), 'utf-8').replace(/\r\n/g, '\n')
const ROSTER = readFileSync(join(here, 'InterviewRoster.jsx'), 'utf-8').replace(/\r\n/g, '\n')

const TODAY = '2026-10-06'
let calls

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

const ROWS = [
  {
    id: 'a', name: 'Asha Rao', phone: '9000000001', date: TODAY, time: '10:00', time_end: '10:30',
    technology: 'Python', interview_round: 'L1', interview_attendee: 'Bhavana', interview_attendance_status: '',
    interview_booking_source: 'ai_auto_booked', interview_company: 'Example Systems Pvt Ltd',
    interview_role: 'Senior Test Engineer', slot_screenshot_proof: { url: '/files/a.png' },
  },
  {
    id: 'b', name: 'Vikram Devi', phone: '9000000002', date: TODAY, time: '12:00', time_end: '12:30',
    technology: 'React JS', interview_round: 'L2', interview_attendee: 'Bhavana', interview_attendance_status: '',
    interview_booking_source: 'candidate_booked',
  },
  {
    id: 'c', name: 'Meera Iyer', phone: '9000000003', date: TODAY, time: '15:00', time_end: '15:30',
    technology: 'Java', interview_round: 'L1', interview_attendee: 'Tool', interview_attendance_status: '',
    interview_role: 'Java Developer',
  },
]

function mockFetch() {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok',
        interviews: {
          count: 3, pending_count: 3,
          by_candidate: ROWS.map(r => ({ name: r.name, scheduled: 1 })),
          by_technology: [{ name: 'Java' }, { name: 'Python' }, { name: 'React JS' }],
        },
        available_months: [{ value: '2026-10', label: 'Oct 2026', count: 3 }],
        booking_overview: { total: 3, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (/\/interviews\/(daily|monitor)$/.test(url.pathname)) {
      return jsonResponse({ status: 'ok', interviews: ROWS, count: ROWS.length, awaiting_interviews: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
}

const globalCalls = () => calls.filter(u => u.pathname.endsWith('/interviews/global'))
const rosterCalls = () => calls.filter(u => /\/interviews\/(daily|monitor)$/.test(u.pathname))

async function renderPanel() {
  const view = render(<DailyOpsPanel />)
  await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())
  await act(async () => { await Promise.resolve() })
  return view
}

const rowOf = name => screen.getByText(name).closest('tr')
const cellOf = (name, label) => rowOf(name).querySelector(`td[data-label="${label}"]`)

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

describe('company', () => {
  it('names the company, and the role under it, on the row', async () => {
    await renderPanel()
    const company = cellOf('Asha Rao', 'Company')
    expect(company.querySelector('.ops-interview-company__name').textContent).toBe('Example Systems Pvt Ltd')
    expect(company.querySelector('.ops-interview-company__role').textContent).toBe('Senior Test Engineer')
  })

  it('says so when no company was recorded, instead of leaving the cell blank', async () => {
    await renderPanel()
    expect(cellOf('Vikram Devi', 'Company').textContent).toBe('Not recorded')
  })

  it('still shows the role when only the role was recorded', async () => {
    // In production the role is on far more rows than the company is.
    await renderPanel()
    const company = cellOf('Meera Iyer', 'Company')
    expect(company.querySelector('.ops-interview-company__none').textContent).toBe('Not recorded')
    expect(company.querySelector('.ops-interview-company__role').textContent).toBe('Java Developer')
  })

  it('sits in its own column right after the candidate', async () => {
    await renderPanel()
    const heads = [...document.querySelectorAll('.ops-interview-table thead th')].map(th => th.textContent)
    expect(heads.slice(0, 4)).toEqual(['Date', 'Time', 'Candidate', 'Company'])
  })
})

describe('columns', () => {
  it('names the menu column Actions', async () => {
    await renderPanel()
    const heads = [...document.querySelectorAll('.ops-interview-table thead th')]
    expect(heads.at(-1).textContent).toBe('Actions')
  })

  it('gives every column a <col>, in the order of the headers', async () => {
    await renderPanel()
    const cols = [...document.querySelectorAll('.ops-interview-table colgroup col')].map(c => c.className.replace('ops-col ops-col--', ''))
    const heads = [...document.querySelectorAll('.ops-interview-table thead th')].map(th => th.textContent)
    expect(cols).toEqual(['date', 'time', 'candidate', 'company', 'tech', 'round', 'attendee', 'attendance', 'screenshot', 'notes', 'actions'])
    expect(cols).toHaveLength(heads.length)
  })

  it('drops the attendee <col> with the attendee column', async () => {
    await renderPanel()
    fireEvent.change(screen.getByLabelText('Attendee filter'), { target: { value: 'Bhavana' } })
    await waitFor(() => expect(rosterCalls().at(-1).searchParams.get('attendee')).toBe('Bhavana'))
    await waitFor(() => expect(document.querySelectorAll('.ops-interview-table thead th')).toHaveLength(10))
    expect(document.querySelectorAll('.ops-interview-table colgroup col')).toHaveLength(10)
    expect(document.querySelector('col.ops-col--attendee')).toBeNull()
  })

  it('sizes them so Notes no longer matches Candidate and the fixed-size cells fit', () => {
    const width = col => new RegExp(`col\\.ops-col--${col}\\{width:([^}]+)\\}`).exec(CSS)?.[1]
    const pct = col => Number.parseFloat(width(col))
    expect(pct('notes')).toBeLessThan(pct('candidate'))
    expect(pct('round')).toBeLessThan(pct('time'))
    // The thumbnail is 72px plus padding and the menu button 28px: fixed sizes.
    expect(width('screenshot')).toBe('88px')
    expect(width('actions')).toBe('64px')
    for (const col of ['date', 'time', 'candidate', 'company', 'tech', 'round', 'attendee', 'attendance', 'notes']) {
      expect(width(col), `${col} has no width`).toMatch(/%$/)
    }
  })
})

describe('booking source', () => {
  it('is a chip that names itself, not a line of text', async () => {
    await renderPanel()
    const candidate = cellOf('Vikram Devi', 'Candidate')
    const chip = candidate.querySelector('.ops-booking-source--candidate')
    expect(chip.getAttribute('aria-label')).toBe('Candidate booked')
    expect(chip.getAttribute('title')).toMatch(/^Candidate booked:/)
    // No visible "Candidate booked" text in the row any more.
    expect(candidate.textContent).not.toContain('Candidate booked')
    expect(chip.querySelector('svg')).not.toBeNull()
  })

  it('keeps a visible AI mark on an automatic booking', async () => {
    await renderPanel()
    const chip = cellOf('Asha Rao', 'Candidate').querySelector('.ops-booking-source--auto')
    expect(chip.getAttribute('aria-label')).toBe('AI Auto-booked')
    expect(chip.textContent).toBe('AI')
  })

  it('shows nothing for a row whose source was never recorded', async () => {
    await renderPanel()
    expect(cellOf('Meera Iyer', 'Candidate').querySelector('.ops-booking-source')).toBeNull()
    expect(cellOf('Meera Iyer', 'Candidate').textContent).not.toContain('Booked')
  })
})

describe('icons', () => {
  it('draws the row menu, search and refresh marks as icons, not font glyphs', async () => {
    await renderPanel()
    const trigger = screen.getByRole('button', { name: 'Actions for Asha Rao' })
    expect(trigger.querySelector('svg.ta-icon--more-vertical')).not.toBeNull()
    expect(trigger.textContent).toBe('')
    expect(document.querySelector('.ops-roster-search__icon svg.ta-icon--search')).not.toBeNull()
    expect(screen.getByRole('button', { name: /Refresh/ }).querySelector('svg.ta-icon--refresh')).not.toBeNull()
    expect(ROSTER).not.toContain('>⋮<')
  })

  it('replaces a screenshot that will not load with an icon, still opening the viewer', async () => {
    await renderPanel()
    const shot = cellOf('Asha Rao', 'Screenshot')
    fireEvent.error(shot.querySelector('img'))
    await waitFor(() => expect(shot.querySelector('img')).toBeNull())
    const thumb = shot.querySelector('button.ops-slot-shot-thumb--missing')
    expect(thumb.querySelector('svg.ta-icon--image-off')).not.toBeNull()
    expect(thumb.getAttribute('title')).toMatch(/could not be loaded/)
  })
})

describe('Reset', () => {
  it('is always offered, and counts what it will put back', async () => {
    await renderPanel()
    const reset = screen.getByRole('button', { name: 'Reset all filters' })
    expect(reset).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Candidate interview level filter'), { target: { value: 'L1' } })
    fireEvent.click(screen.getByRole('tab', { name: 'Today' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reset all filters (2 set)' })).toBeInTheDocument())
  })

  it('puts back period, date, search, candidate, attendee, level, profile and status in one click', async () => {
    await renderPanel()
    fireEvent.click(screen.getByRole('tab', { name: 'Today' }))
    fireEvent.change(screen.getByLabelText('Candidate search'), { target: { value: 'asha' } })
    fireEvent.change(screen.getByLabelText('Candidate filter'), { target: { value: 'Asha Rao' } })
    fireEvent.change(screen.getByLabelText('Attendee filter'), { target: { value: 'Bhavana' } })
    fireEvent.change(screen.getByLabelText('Candidate interview level filter'), { target: { value: 'L1' } })
    fireEvent.change(screen.getByLabelText('Technology filter'), { target: { value: 'Python' } })
    fireEvent.click(screen.getByRole('button', { name: /^Attended/ }))
    await waitFor(() => expect(globalCalls().at(-1).searchParams.get('technology')).toBe('Python'))

    fireEvent.click(screen.getByRole('button', { name: /^Reset all filters/ }))

    await waitFor(() => {
      const last = globalCalls().at(-1).searchParams
      for (const key of ['search', 'candidate', 'attendee', 'round', 'technology']) expect(last.get(key), key).toBeNull()
      expect(last.get('upcoming_only')).toBe('true')
    })
    await waitFor(() => {
      const last = rosterCalls().at(-1).searchParams
      for (const key of ['search', 'candidate', 'attendee', 'round', 'technology']) expect(last.get(key), key).toBeNull()
      expect(last.get('upcoming_only')).toBe('true')
    })
    expect(screen.getByRole('tab', { name: 'Upcoming' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByLabelText('Candidate search')).toHaveValue('')
    expect(screen.getByLabelText('Candidate filter')).toHaveValue('')
    expect(screen.getByLabelText('Attendee filter')).toHaveValue('')
    expect(screen.getByLabelText('Candidate interview level filter')).toHaveValue('')
    expect(screen.getByLabelText('Technology filter')).toHaveValue('')
    expect(screen.getByRole('button', { name: /^Scheduled/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Reset all filters' })).toBeInTheDocument()
  })

  it('reloads the counters and the table even when nothing was set', async () => {
    await renderPanel()
    const before = { global: globalCalls().length, roster: rosterCalls().length }
    fireEvent.click(screen.getByRole('button', { name: 'Reset all filters' }))
    await waitFor(() => expect(globalCalls().length).toBeGreaterThan(before.global))
    await waitFor(() => expect(rosterCalls().length).toBeGreaterThan(before.roster))
  })
})
