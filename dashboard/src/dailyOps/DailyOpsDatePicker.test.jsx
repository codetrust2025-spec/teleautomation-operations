/**
 * Daily Ops — picking one calendar day.
 *
 * The control that used to offer whole months now opens a calendar, and the
 * point of these tests is that the calendar is wired to the data rather than
 * merely drawn: choosing Yesterday has to reach the API as that exact day, and
 * the day has to still be on screen after the popover closes and after the
 * table reloads. The period presets keep their own tests here too, because the
 * new control shares the range they set.
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
vi.mock('./PendingWorksStrip.jsx', () => ({
  PendingWorksStrip: () => null,
}))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'

// 11:30 IST on 8 September 2026 — the day in the brief, so "Yesterday" is the
// 7th. The label reads "7 Sept 2026": en-IN abbreviates September as "Sept",
// and the roster's own Date column renders it the same way through the same
// shared formatter, so the control and the table never disagree on a month.
const NOW = '2026-09-08T06:00:00Z'
const TODAY = '2026-09-08'
const YESTERDAY = '2026-09-07'

let calls
let rowsByDate

function jsonResponse(body) {
  return {
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    json: async () => body,
  }
}

function interviewRow(date, name) {
  return {
    id: `${date}-${name}`,
    name,
    phone: '9000000001',
    date,
    time: '10:00',
    technology: 'Python',
    interview_round: 'L1',
    interview_attendee: 'Bhavana',
  }
}

function mockFetch() {
  calls = []
  rowsByDate = {}
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok',
        interviews: { count: 0, attended_count: 0, pending_count: 0, not_attended_count: 0 },
        available_months: [
          { value: '2026-09', label: 'Sep 2026', count: 4 },
          { value: '2026-08', label: 'Aug 2026', count: 2 },
        ],
        booking_overview: { total: 0, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (url.pathname.endsWith('/interviews/daily')) {
      const day = url.searchParams.get('date')
      return jsonResponse({ status: 'ok', interviews: rowsByDate[day] || [], count: (rowsByDate[day] || []).length })
    }
    if (url.pathname.endsWith('/interviews/monitor')) {
      return jsonResponse({ status: 'ok', interviews: [], count: 0 })
    }
    return jsonResponse({ status: 'ok' })
  }))
}

/** Every request the roster table made, in order. */
function rosterCalls() {
  return calls.filter(url => /\/interviews\/(daily|monitor)$/.test(url.pathname))
}

function lastRosterCall() {
  const made = rosterCalls()
  return made[made.length - 1]
}

function lastGlobalCall() {
  const made = calls.filter(url => url.pathname.endsWith('/interviews/global'))
  return made[made.length - 1]
}

async function renderPanel() {
  const view = render(<DailyOpsPanel />)
  await waitFor(() => expect(calls.length).toBeGreaterThan(0))
  await act(async () => { await Promise.resolve() })
  return view
}

function dateTrigger() {
  return screen.getByRole('button', { name: 'Filter interviews by date' })
}

async function openCalendar() {
  fireEvent.click(dateTrigger())
  return screen.getByRole('dialog', { name: 'Choose a date' })
}

/**
 * A day cell in the open calendar, found by the parts of its accessible name.
 *
 * Not one exact string: ICU moves the comma between engines — "Thursday,
 * 3 September 2026" under Node, "Thursday 3 September, 2026" under Chrome —
 * and these tests are about which day was clicked, not about punctuation.
 */
function dayCell(calendar, ...parts) {
  return within(calendar).getByRole('button', { name: new RegExp(parts.join('.*')) })
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(NOW))
  mockFetch()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the date control replaces the month dropdown', () => {
  it('offers a calendar rather than a list of months', async () => {
    await renderPanel()

    expect(screen.queryByLabelText('Filter interviews by month')).toBeNull()
    expect(screen.queryByText('Select month')).toBeNull()
    expect(dateTrigger()).toBeInTheDocument()
  })

  it('opens a calendar on click and closes it on Escape', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    expect(within(calendar).getByRole('grid')).toBeInTheDocument()
    expect(within(calendar).getByLabelText('Month')).toBeInTheDocument()
    expect(within(calendar).getByLabelText('Year')).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Choose a date' })).toBeNull()
  })

  it('offers no quick picks that repeat the Period row', async () => {
    // Today sat in both places, and Yesterday/Tomorrow repeated two cells of
    // the grid directly underneath them. The calendar is the date control; the
    // Period row is the range control.
    await renderPanel()
    const calendar = await openCalendar()

    for (const label of ['Yesterday', 'Today', 'Tomorrow']) {
      expect(within(calendar).queryByRole('button', { name: label })).toBeNull()
    }
    expect(within(calendar).getByRole('grid')).toBeInTheDocument()
  })

  it('still reaches those days through the grid', async () => {
    await renderPanel()
    const calendar = await openCalendar()

    for (const parts of [['Monday', '7 September'], ['Tuesday', '8 September'],
                         ['Wednesday', '9 September']]) {
      expect(dayCell(calendar, ...parts, '2026')).toBeInTheDocument()
    }
  })
})

describe('choosing a day filters the table by that exact day', () => {
  it('applies Yesterday as 7 September 2026, not the day before or after', async () => {
    rowsByDate[YESTERDAY] = [interviewRow(YESTERDAY, 'Asha Rao')]
    await renderPanel()

    const calendar = await openCalendar()
    fireEvent.click(dayCell(calendar, 'Monday', '7 September', '2026'))

    await waitFor(() => expect(lastRosterCall().searchParams.get('date')).toBe(YESTERDAY))
    expect(lastRosterCall().pathname).toMatch(/\/interviews\/daily$/)
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())
  })

  it('shows the chosen day in the control as "7 Sept 2026"', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    fireEvent.click(dayCell(calendar, 'Monday', '7 September', '2026'))

    await waitFor(() => expect(dateTrigger()).toHaveTextContent('7 Sept 2026'))
  })

  it('narrows the KPI summary to the same single day', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    fireEvent.click(dayCell(calendar, 'Monday', '7 September', '2026'))

    await waitFor(() => expect(lastGlobalCall().searchParams.get('from')).toBe(YESTERDAY))
    expect(lastGlobalCall().searchParams.get('to')).toBe(YESTERDAY)
    // A single chosen day is never an "upcoming only" view, or picking
    // yesterday would filter its own records away.
    expect(lastGlobalCall().searchParams.get('upcoming_only')).toBeNull()
  })

  it('applies any day clicked in the grid', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    fireEvent.click(dayCell(calendar, 'Thursday', '3 September', '2026'))

    await waitFor(() => expect(lastRosterCall().searchParams.get('date')).toBe('2026-09-03'))
    expect(dateTrigger()).toHaveTextContent('3 Sept 2026')
  })

  it('closes the calendar once a day is chosen', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    fireEvent.click(dayCell(calendar, 'Tuesday', '8 September', '2026'))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Choose a date' })).toBeNull())
  })
})

describe('navigating to another month and year', () => {
  it('reaches a day in a different month through the arrows', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    fireEvent.click(within(calendar).getByRole('button', { name: 'Previous month' }))
    fireEvent.click(dayCell(calendar, 'Wednesday', '12 August', '2026'))

    await waitFor(() => expect(lastRosterCall().searchParams.get('date')).toBe('2026-08-12'))
    expect(dateTrigger()).toHaveTextContent('12 Aug 2026')
  })

  it('reaches a day in a different year through the month and year selects', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    fireEvent.change(within(calendar).getByLabelText('Year'), { target: { value: '2027' } })
    fireEvent.change(within(calendar).getByLabelText('Month'), { target: { value: '11' } })
    fireEvent.click(dayCell(calendar, '25 December', '2027'))

    await waitFor(() => expect(lastRosterCall().searchParams.get('date')).toBe('2027-12-25'))
    expect(dateTrigger()).toHaveTextContent('25 Dec 2027')
  })

  it('walks days with the arrow keys and commits with Enter', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    const grid = within(calendar).getByRole('grid')
    fireEvent.keyDown(grid, { key: 'ArrowLeft' })
    fireEvent.keyDown(grid, { key: 'ArrowLeft' })
    fireEvent.click(dayCell(calendar, 'Sunday', '6 September', '2026'))

    await waitFor(() => expect(lastRosterCall().searchParams.get('date')).toBe('2026-09-06'))
  })
})

describe('the chosen day survives', () => {
  it('stays in the control after the calendar closes and the table reloads', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    fireEvent.click(dayCell(calendar, 'Monday', '7 September', '2026'))
    await waitFor(() => expect(dateTrigger()).toHaveTextContent('7 Sept 2026'))

    fireEvent.click(screen.getByRole('button', { name: /Refresh|Updating/ }))
    await act(async () => { await Promise.resolve() })

    expect(dateTrigger()).toHaveTextContent('7 Sept 2026')
    expect(lastRosterCall().searchParams.get('date')).toBe(YESTERDAY)
  })

  it('reopens the calendar on the chosen month with the day still marked', async () => {
    await renderPanel()

    let calendar = await openCalendar()
    fireEvent.change(within(calendar).getByLabelText('Month'), { target: { value: '7' } })
    fireEvent.click(dayCell(calendar, 'Wednesday', '12 August', '2026'))
    await waitFor(() => expect(dateTrigger()).toHaveTextContent('12 Aug 2026'))

    calendar = await openCalendar()
    expect(within(calendar).getByLabelText('Month')).toHaveValue('7')
    expect(dayCell(calendar, 'Wednesday', '12 August', '2026'))
      .toHaveAttribute('aria-pressed', 'true')
  })
})

describe('the period presets keep working alongside it', () => {
  it('still offers Today, Upcoming, This week and Last 7 days', async () => {
    await renderPanel()

    for (const label of ['Today', 'Upcoming', 'This week', 'Last 7 days']) {
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument()
    }
  })

  it('sends a multi-day range for Last 7 days and empties the date control', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    fireEvent.click(dayCell(calendar, 'Monday', '7 September', '2026'))
    await waitFor(() => expect(dateTrigger()).toHaveTextContent('7 Sept 2026'))

    fireEvent.click(screen.getByRole('tab', { name: 'Last 7 days' }))

    await waitFor(() => expect(lastRosterCall().pathname).toMatch(/\/interviews\/monitor$/))
    expect(lastRosterCall().searchParams.get('from')).toBe('2026-09-01')
    expect(lastRosterCall().searchParams.get('to')).toBe(TODAY)
    // No single day is in force, so the control must not claim one.
    expect(dateTrigger()).toHaveTextContent('Select date')
  })

  it('marks the Today preset when today is picked from the calendar', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    fireEvent.click(dayCell(calendar, 'Tuesday', '8 September', '2026'))

    await waitFor(() => expect(lastRosterCall().searchParams.get('date')).toBe(TODAY))
    expect(screen.getByRole('tab', { name: 'Today' })).toHaveAttribute('aria-selected', 'true')
    expect(dateTrigger()).toHaveTextContent('8 Sept 2026')
  })

  it('still filters by a whole month, first day to last', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    fireEvent.click(within(calendar).getByRole('button', { name: /Whole month/ }))

    await waitFor(() => expect(lastRosterCall().searchParams.get('from')).toBe('2026-09-01'))
    expect(lastRosterCall().searchParams.get('to')).toBe('2026-09-30')
  })

  it('still filters across all time', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    fireEvent.click(within(calendar).getByRole('button', { name: 'All time' }))

    await waitFor(() => expect(lastRosterCall().searchParams.get('from')).toBe('2000-01-01'))
    expect(lastRosterCall().searchParams.get('to')).toBe('2100-12-31')
  })
})

describe('a day with nothing on it', () => {
  it('names the day it found nothing for', async () => {
    await renderPanel()

    const calendar = await openCalendar()
    fireEvent.click(dayCell(calendar, 'Monday', '7 September', '2026'))

    await waitFor(() => expect(screen.getByText(/No interviews on/)).toBeInTheDocument())
    expect(screen.getByText(/No interviews on/)).toHaveTextContent('7 Sep')
  })

  it('says which filters are narrowing the day when some are set', async () => {
    await renderPanel()

    fireEvent.change(screen.getByLabelText('Attendee filter'), { target: { value: 'Nikhila' } })
    const calendar = await openCalendar()
    fireEvent.click(dayCell(calendar, 'Monday', '7 September', '2026'))

    await waitFor(() => expect(screen.getByText(/attendee Nikhila/)).toBeInTheDocument())
  })

  it('goes back to rows when a day that has them is chosen', async () => {
    rowsByDate[YESTERDAY] = [interviewRow(YESTERDAY, 'Asha Rao')]
    await renderPanel()

    let calendar = await openCalendar()
    fireEvent.click(dayCell(calendar, 'Wednesday', '9 September', '2026'))
    await waitFor(() => expect(screen.getByText(/No interviews on/)).toBeInTheDocument())

    calendar = await openCalendar()
    fireEvent.click(dayCell(calendar, 'Monday', '7 September', '2026'))
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())
    expect(screen.queryByText(/No interviews on/)).toBeNull()
  })
})
