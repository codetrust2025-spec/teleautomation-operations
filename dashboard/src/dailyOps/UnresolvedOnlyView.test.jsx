/**
 * Daily Ops — "All unresolved".
 *
 * One list of every interview still waiting for a status update, whatever its
 * date. Today, Upcoming, This week and the calendar all describe the days
 * around now, so an interview booked in May and never closed was invisible in
 * Daily Ops however long it sat there. This view asks the server a different
 * question, and these tests hold it to that: the range must not narrow it, the
 * row must keep its status dropdown, and setting a status must take the row
 * off the list.
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
vi.mock('./PendingWorksStrip.jsx', () => ({
  PendingWorksStrip: () => null,
}))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'

const NOW = '2026-09-27T06:00:00Z'
const TODAY = '2026-09-27'

let calls
let unresolvedRows
let dailyRows
let posted
// A reader a moment behind the write: the row keeps coming back in the list
// even though the update was saved. It is what a poll whose request left
// before the save returns, and the screen must not believe it.
let serverLags
let postFails

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function interviewRow(date, name) {
  return {
    id: `${date}-${name}`, name, phone: '9000000001', date, time: '10:00', time_end: '11:00',
    technology: 'Python', interview_round: 'L1', interview_attendee: 'Bhavana',
    interview_attendance_status: '',
  }
}

function mockFetch() {
  calls = []
  posted = []
  serverLags = false
  postFails = false
  unresolvedRows = [interviewRow('2026-05-04', 'Asha Rao'), interviewRow('2026-06-18', 'Vikram Devi')]
  dailyRows = [interviewRow(TODAY, 'Meera Iyer')]
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (url.pathname.endsWith('/interview-attendance')) {
      posted.push({ url, body: JSON.parse(init.body) })
      if (postFails) {
        return { ok: false, status: 500, headers: { get: () => 'application/json' },
                 json: async () => ({ status: 'error', message: 'Update failed' }) }
      }
      const id = decodeURIComponent(url.pathname.split('/').at(-2))
      // The server drops it from the unresolved list the moment it has a
      // status -- unless this test is playing a reader that has not caught up.
      if (!serverLags) unresolvedRows = unresolvedRows.filter(row => row.id !== id)
      dailyRows = dailyRows.map(row => row.id === id
        ? { ...row, interview_attendance_status: JSON.parse(init.body).status } : row)
      return jsonResponse({ status: 'ok' })
    }
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok',
        interviews: { count: 0, attended_count: 0, pending_count: 0, not_attended_count: 0 },
        available_months: [{ value: '2026-09', label: 'Sep 2026', count: 4 }],
        booking_overview: { total: 0, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (url.pathname.endsWith('/interviews/monitor')) {
      const rows = url.searchParams.get('unresolved_only') === 'true' ? unresolvedRows : []
      return jsonResponse({
        status: 'ok', interviews: rows, count: rows.length, pending_count: rows.length,
        awaiting_interviews: [], awaiting_count: 0, unresolved_only: true,
      })
    }
    if (url.pathname.endsWith('/interviews/daily')) {
      return jsonResponse({ status: 'ok', interviews: dailyRows, count: dailyRows.length })
    }
    return jsonResponse({ status: 'ok' })
  }))
}

function rosterCalls() {
  return calls.filter(url => /\/interviews\/(daily|monitor)$/.test(url.pathname))
}

function lastRosterCall() {
  return rosterCalls().at(-1)
}

function lastGlobalCall() {
  return calls.filter(url => url.pathname.endsWith('/interviews/global')).at(-1)
}

async function renderPanel() {
  const view = render(<DailyOpsPanel />)
  await waitFor(() => expect(calls.length).toBeGreaterThan(0))
  await act(async () => { await Promise.resolve() })
  return view
}

function unresolvedTab() {
  return screen.getByRole('tab', { name: 'All unresolved' })
}

async function openUnresolved() {
  fireEvent.click(unresolvedTab())
  await waitFor(() => expect(lastRosterCall().searchParams.get('unresolved_only')).toBe('true'))
  await act(async () => { await Promise.resolve() })
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

describe('the tab', () => {
  it('sits with the periods and starts unselected', async () => {
    await renderPanel()

    expect(unresolvedTab()).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByRole('tab', { name: 'Upcoming' })).toHaveAttribute('aria-selected', 'true')
    expect(lastRosterCall().searchParams.get('unresolved_only')).toBeNull()
  })

  it('becomes the selected view, and no period stays selected with it', async () => {
    await renderPanel()
    await openUnresolved()

    expect(unresolvedTab()).toHaveAttribute('aria-selected', 'true')
    for (const label of ['Today', 'Upcoming', 'This week', 'Last 7 days']) {
      expect(screen.getByRole('tab', { name: label })).toHaveAttribute('aria-selected', 'false')
    }
    expect(screen.getByRole('status').textContent).toMatch(/still waiting for a status update/)
  })
})

describe('what it asks the server for', () => {
  it('asks for every date, not the period on screen', async () => {
    await renderPanel()
    await openUnresolved()

    const request = lastRosterCall()
    expect(request.pathname).toMatch(/\/interviews\/monitor$/)
    expect(request.searchParams.get('from')).toBe('2000-01-01')
    expect(request.searchParams.get('to')).toBe('2100-12-31')
    // Upcoming is a window around today; this view must not be narrowed by it.
    expect(request.searchParams.get('upcoming_only')).toBeNull()
  })

  it('puts the counters above the table on the same question', async () => {
    await renderPanel()
    await openUnresolved()

    expect(lastGlobalCall().searchParams.get('unresolved_only')).toBe('true')
    expect(lastGlobalCall().searchParams.get('upcoming_only')).toBeNull()
  })

  it('goes back to the dated view when a period is picked', async () => {
    await renderPanel()
    await openUnresolved()

    fireEvent.click(screen.getByRole('tab', { name: 'Today' }))

    await waitFor(() => expect(lastRosterCall().searchParams.get('unresolved_only')).toBeNull())
    expect(lastRosterCall().searchParams.get('date')).toBe(TODAY)
    expect(unresolvedTab()).toHaveAttribute('aria-selected', 'false')
  })
})

describe('the list', () => {
  it('shows interviews from months ago', async () => {
    await renderPanel()
    await openUnresolved()

    expect(screen.getByText('Asha Rao')).toBeInTheDocument()
    expect(screen.getByText('Vikram Devi')).toBeInTheDocument()
    expect(screen.getByText('Mon, 4 May')).toBeInTheDocument()
  })

  it('keeps the status dropdown on every row', async () => {
    await renderPanel()
    await openUnresolved()

    const select = screen.getByLabelText('Attendance for Asha Rao')
    expect([...select.options].map(option => option.textContent)).toEqual([
      'Pending', 'Attended', 'Not attended', 'Cancelled', 'Rescheduled', 'Awaiting new slot', 'Re-Service',
    ])
  })

  it('counts what is on screen', async () => {
    await renderPanel()
    await openUnresolved()

    const pending = screen.getByRole('button', { name: /^Pending/ })
    await waitFor(() => expect(pending.textContent).toBe('Pending2'))
  })

  it('drops a row as soon as its status is set', async () => {
    await renderPanel()
    await openUnresolved()

    fireEvent.change(screen.getByLabelText('Attendance for Asha Rao'), { target: { value: 'cancelled' } })
    fireEvent.change(screen.getByLabelText(/Note \/ remark/), { target: { value: 'client called it off' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Cancelled' })) })

    await waitFor(() => expect(screen.queryByText('Asha Rao')).toBeNull())
    expect(posted[0].body.status).toBe('cancelled')
    expect(screen.getByText('Vikram Devi')).toBeInTheDocument()
  })

  it('says so plainly when nothing is outstanding', async () => {
    unresolvedRows = []
    await renderPanel()
    await openUnresolved()

    expect(screen.getByText('Nothing is waiting for a status update')).toBeInTheDocument()
    // Never "No interviews on <date>": this list is not about a date.
    expect(screen.queryByText(/No interviews (on|between)/)).toBeNull()
  })
})

describe('a recorded outcome lands immediately', () => {
  /** Pick an option in a DarkSelect custom dropdown by its trigger aria-label. */
  function pickFromDarkSelect(triggerName, optionText) {
    const trigger = screen.getByRole('combobox', { name: triggerName })
    fireEvent.click(trigger)
    const option = screen.getByRole('option', { name: optionText })
    fireEvent.mouseDown(option)
  }

  /** Attended needs an attendee, feedback, result and a note before it saves. */
  async function markAttended(name) {
    fireEvent.change(screen.getByLabelText(`Attendance for ${name}`), { target: { value: 'attended' } })
    pickFromDarkSelect('Interview feedback', 'Good')
    pickFromDarkSelect('Interview result', 'Next round')
    fireEvent.change(screen.getByLabelText(/Note \/ remark/), { target: { value: 'went ahead, cleared' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Attended', hidden: false })) })
  }

  function pendingCard() {
    return screen.getByRole('button', { name: /^Pending/ })
  }

  it('takes the row off the list and drops the count by one, with no reload', async () => {
    // The bug: the row only left when a fetch said so, and a fetch that had
    // left before the save said the opposite. Here the server keeps returning
    // the row, so nothing but the save itself can remove it from the screen.
    serverLags = true
    await renderPanel()
    await openUnresolved()
    await waitFor(() => expect(pendingCard().textContent).toBe('Pending2'))

    await markAttended('Asha Rao')

    expect(screen.queryByText('Asha Rao')).toBeNull()
    expect(pendingCard().textContent).toBe('Pending1')
    expect(screen.getByText('Vikram Devi')).toBeInTheDocument()
    expect(posted[0].body.status).toBe('attended')
  })

  it('does not let a lagging reload put the row back', async () => {
    serverLags = true
    await renderPanel()
    await openUnresolved()
    await markAttended('Asha Rao')

    // Several poll cycles of a server still listing it.
    await act(async () => { await vi.advanceTimersByTimeAsync(16000) })

    expect(screen.queryByText('Asha Rao')).toBeNull()
    expect(pendingCard().textContent).toBe('Pending1')
  })

  it('keeps the row and shows the error when the update fails', async () => {
    postFails = true
    await renderPanel()
    await openUnresolved()

    await markAttended('Asha Rao')

    expect(await screen.findByRole('alert')).toHaveTextContent('Update failed')
    expect(screen.getByText('Asha Rao')).toBeInTheDocument()
    expect(pendingCard().textContent).toBe('Pending2')
  })

  it('leaves a dated roster showing the row with its new status', async () => {
    // Only a list of rows that have no outcome loses the row. Today's roster
    // is a list of that day's interviews, so it keeps it and shows the status.
    await renderPanel()
    fireEvent.click(screen.getByRole('tab', { name: 'Today' }))
    await waitFor(() => expect(screen.getByText('Meera Iyer')).toBeInTheDocument())

    await markAttended('Meera Iyer')

    expect(screen.getByText('Meera Iyer')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Attended/ }).textContent).toBe('Attended1')
  })
})

describe('the sidebar badge', () => {
  it('is asked to re-read rather than handed an all-time number', async () => {
    // The badge counts pending interviews in its own seven-day window. Handing
    // it every unresolved interview ever would make it disagree with itself.
    const seen = []
    const listener = event => seen.push(event.detail)
    window.addEventListener('teleautomation:pending-work-changed', listener)
    try {
      await renderPanel()
      await openUnresolved()

      await waitFor(() => expect(seen.length).toBeGreaterThan(0))
      expect(seen.at(-1)?.pendingCount).toBeUndefined()
    } finally {
      window.removeEventListener('teleautomation:pending-work-changed', listener)
    }
  })
})
