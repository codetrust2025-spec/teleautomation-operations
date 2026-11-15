/**
 * Interview Levels: a filter of its own beside Filters (Nov 2026).
 *
 * - Six checkboxes in interview order: Screening, L1, L2, L3, HR, Final.
 * - Every tick applies at once: the table and the counters re-read with the
 *   ticked levels (`round=HR&round=L2`), and there is no Apply button.
 * - It shares one selection with the Filters panel's Level field, so the two
 *   never disagree; the other Filters fields are untouched by it.
 * - "All levels" and the page's Reset clear it.
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
import { INTERVIEW_LEVELS, InterviewLevelsFilter } from './InterviewLevelsFilter.jsx'
import { applyFilters, checkedFilterValues, openFilters, toggleFilterValue } from './filterMenuTestHelpers.js'

const TODAY = '2026-10-06'
let calls

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

const ROWS = [
  ['Asha Varma', 'HR', 'Bhavana'], ['Bala Kumar', 'L2', 'Nikhila'],
  ['Chitra Rao', 'L3', 'Bhavana'], ['Dev Prasad', 'L1', 'Tool'], ['Esha Pillai', 'Screening', 'Tool'],
].map(([name, round, attendee], i) => ({
  id: `r${i}`, name, phone: `90000002${String(i).padStart(2, '0')}`, date: TODAY, time: `${10 + i}:00`,
  time_end: `${10 + i}:30`, technology: 'Java', interview_round: round, interview_attendee: attendee,
  interview_attendance_status: '',
}))

function serverRows(url) {
  const pick = (key, field) => {
    const wanted = url.searchParams.getAll(key)
    return row => wanted.length === 0 || wanted.includes(row[field])
  }
  return ROWS.filter(pick('round', 'interview_round')).filter(pick('attendee', 'interview_attendee'))
}

function mockFetch() {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok',
        interviews: { by_candidate: ROWS.map(r => ({ name: r.name, scheduled: 1 })), by_technology: [{ name: 'Java' }] },
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
const levelsGroup = () => screen.getByRole('group', { name: 'Interview levels' })
const tickLevel = level => fireEvent.click(within(levelsGroup()).getByRole('checkbox', { name: level }))
const openLevels = () => fireEvent.click(screen.getByRole('button', { name: /^Interview levels/ }))

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

describe('the Interview Levels control', () => {
  it('offers the six levels in interview order, with no Apply button', () => {
    render(<InterviewLevelsFilter />)
    openLevels()
    expect(within(levelsGroup()).getAllByRole('checkbox').map(b => b.getAttribute('aria-label')))
      .toEqual(['Screening', 'L1', 'L2', 'L3', 'HR', 'Final'])
    expect(INTERVIEW_LEVELS).toEqual(['Screening', 'L1', 'L2', 'L3', 'HR', 'Final'])
    expect(screen.queryByRole('button', { name: /^Apply/ })).toBeNull()
  })

  it('reports each tick at once, in interview order, and unticks on a second click', () => {
    const onChange = vi.fn()
    const { rerender } = render(<InterviewLevelsFilter value={[]} onChange={onChange} />)
    openLevels()
    tickLevel('HR')
    expect(onChange).toHaveBeenLastCalledWith(['HR'])
    rerender(<InterviewLevelsFilter value={['HR']} onChange={onChange} />)
    tickLevel('L2')
    expect(onChange).toHaveBeenLastCalledWith(['L2', 'HR'])
    rerender(<InterviewLevelsFilter value={['L2', 'HR']} onChange={onChange} />)
    tickLevel('HR')
    expect(onChange).toHaveBeenLastCalledWith(['L2'])
    // The menu stays open between ticks.
    expect(levelsGroup()).toBeInTheDocument()
  })

  it('shows what is chosen on the button, and All levels clears it', () => {
    const onChange = vi.fn()
    render(<InterviewLevelsFilter value={['L2', 'L3', 'HR']} onChange={onChange} />)
    const trigger = screen.getByRole('button', { name: 'Interview levels (3 selected: L2, L3, HR)' })
    expect(trigger).toHaveTextContent('Levels(3)')
    expect(trigger.getAttribute('title')).toBe('Interview levels: L2, L3, HR')
    openLevels()
    fireEvent.click(screen.getByRole('button', { name: 'Show all interview levels' }))
    expect(onChange).toHaveBeenLastCalledWith([])
  })

  it('closes on Escape and on a click outside', () => {
    render(<div><span data-testid="out">x</span><InterviewLevelsFilter /></div>)
    openLevels()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('group', { name: 'Interview levels' })).toBeNull()
    openLevels()
    fireEvent.mouseDown(screen.getByTestId('out'))
    expect(screen.queryByRole('group', { name: 'Interview levels' })).toBeNull()
  })
})

describe('on Daily Ops', () => {
  it('re-reads the table and the counters on every tick, without Apply', async () => {
    await renderPanel()
    openLevels()

    tickLevel('HR')
    await waitFor(() => expect(rosterCalls().at(-1).searchParams.getAll('round')).toEqual(['HR']))
    await waitFor(() => expect(globalCalls().at(-1).searchParams.getAll('round')).toEqual(['HR']))
    await waitFor(() => expect(inTable('Bala Kumar')).toBe(false))

    tickLevel('L2')
    tickLevel('L3')
    await waitFor(() => expect(rosterCalls().at(-1).searchParams.getAll('round')).toEqual(['L2', 'L3', 'HR']))
    await waitFor(() => expect(globalCalls().at(-1).searchParams.getAll('round')).toEqual(['L2', 'L3', 'HR']))
    await waitFor(() => expect(inTable('Chitra Rao')).toBe(true))
    expect(inTable('Asha Varma')).toBe(true)
    expect(inTable('Bala Kumar')).toBe(true)
    expect(inTable('Dev Prasad')).toBe(false)
    expect(inTable('Esha Pillai')).toBe(false)
  })

  it('shares one selection with the Filters panel Level field', async () => {
    await renderPanel()
    openLevels()
    tickLevel('Screening')
    tickLevel('Final')
    fireEvent.keyDown(document, { key: 'Escape' })

    openFilters()
    expect(checkedFilterValues('Candidate interview level filter')).toEqual(['Screening', 'Final'])
    toggleFilterValue('Candidate interview level filter', 'Final')
    toggleFilterValue('Candidate interview level filter', 'L1')
    applyFilters()

    await waitFor(() => expect(screen.getByRole('button', { name: 'Interview levels (2 selected: Screening, L1)' })).toBeInTheDocument())
  })

  it('leaves the other Filters fields as they were', async () => {
    await renderPanel()
    openFilters()
    toggleFilterValue('Attendee filter', 'Bhavana')
    applyFilters()
    await waitFor(() => expect(rosterCalls().at(-1).searchParams.getAll('attendee')).toEqual(['Bhavana']))

    openLevels()
    tickLevel('L3')
    await waitFor(() => {
      const last = rosterCalls().at(-1).searchParams
      expect(last.getAll('round')).toEqual(['L3'])
      expect(last.getAll('attendee')).toEqual(['Bhavana'])
    })
    await waitFor(() => expect(inTable('Asha Varma')).toBe(false))
    expect(inTable('Chitra Rao')).toBe(true)
  })

  it('is cleared by the page Reset', async () => {
    await renderPanel()
    openLevels()
    tickLevel('HR')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reset all filters (1 set)' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Reset all filters/ }))
    await waitFor(() => expect(rosterCalls().at(-1).searchParams.getAll('round')).toEqual([]))
    expect(screen.getByRole('button', { name: 'Interview levels' })).toBeInTheDocument()
  })
})
