/**
 * Upcoming shows what is still to come, and nothing else.
 *
 * It used to carry a second table underneath it — "Awaiting outcome" — holding
 * pending interviews whose time had passed, reaching 30 days back. That made
 * two places to chase an outcome from, and the older ones were in neither: the
 * section could only ever show the last 30 days. All unresolved is that list
 * now, at any age, so the section is gone and a sitting whose end time has
 * passed simply leaves Upcoming.
 *
 * The server stopped sending `awaiting_interviews` in the same change, but a
 * payload that still carries some must not put that table back, so the test
 * below hands the panel exactly that.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
const roster = readFileSync(join(here, 'InterviewRoster.jsx'), 'utf-8')
const provider = readFileSync(join(here, 'PendingWorksProvider.jsx'), 'utf-8')

const NOW = '2026-09-28T06:00:00Z'
let calls

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function interviewRow(id, name, date, time) {
  return {
    id, name, phone: '9000000001', date, time, time_end: '11:00', technology: 'Python',
    interview_round: 'L1', interview_attendee: 'Bhavana', interview_attendance_status: '',
  }
}

const STILL_TO_COME = interviewRow('future', 'Asha Rao', '2026-09-30', '10:00')
const TIME_HAS_PASSED = interviewRow('overdue', 'Vikram Devi', '2026-09-02', '10:00')

/** A server that still splits its rows, which the screen must not act on. */
function mockFetch() {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok', interviews: {}, available_months: [],
        booking_overview: { total: 0, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (url.pathname.endsWith('/interviews/monitor')) {
      if (url.searchParams.get('unresolved_only') === 'true') {
        return jsonResponse({ status: 'ok', interviews: [TIME_HAS_PASSED, STILL_TO_COME], count: 2,
                              awaiting_interviews: [] })
      }
      return jsonResponse({ status: 'ok', interviews: [STILL_TO_COME], count: 1,
                            awaiting_interviews: [TIME_HAS_PASSED], awaiting_count: 1 })
    }
    return jsonResponse({ status: 'ok', interviews: [], count: 0 })
  }))
}

async function renderPanel() {
  const view = render(<DailyOpsPanel />)
  await waitFor(() => expect(calls.length).toBeGreaterThan(0))
  await act(async () => { await Promise.resolve() })
  return view
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

describe('Upcoming', () => {
  it('lists what is still to come and nothing that is past', async () => {
    await renderPanel()

    expect(await screen.findByText('Asha Rao')).toBeInTheDocument()
    expect(screen.queryByText('Vikram Devi')).toBeNull()
  })

  it('has no Awaiting outcome section, even when the payload offers one', async () => {
    await renderPanel()
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())

    expect(screen.queryByText('Awaiting outcome')).toBeNull()
    expect(document.querySelector('.ops-awaiting-outcome-section')).toBeNull()
    expect(document.querySelectorAll('table')).toHaveLength(1)
  })

  it('counts only the rows it shows', async () => {
    await renderPanel()
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())

    // 1, not 2: the awaiting row used to be added to the Pending total.
    expect(screen.getByRole('button', { name: /^Pending/ }).textContent).toBe('Pending1')
  })
})

describe('the interview whose time has passed', () => {
  it('is under All unresolved instead', async () => {
    await renderPanel()
    fireEvent.click(screen.getByRole('tab', { name: 'All unresolved' }))

    expect(await screen.findByText('Vikram Devi')).toBeInTheDocument()
    expect(screen.getByText('Asha Rao')).toBeInTheDocument()
  })
})

describe('the section is gone from the component, not hidden', () => {
  it('leaves no second table, badge or awaiting state behind', () => {
    for (const trace of ['ops-awaiting-outcome-section', 'ops-awaiting-badge', 'Awaiting outcome',
                         'awaitingRows', 'awaiting_interviews', 'ops-interview-row--awaiting']) {
      expect(roster, `${trace} is still in the roster`).not.toContain(trace)
    }
  })

  it('publishes the pending count it displays', () => {
    expect(roster).toMatch(/const totalPending = nextCounts\.pending_count/)
    expect(roster).toContain('publishPendingWorkChanged(totalPending)')
  })

  it('leaves the sidebar badge reading its own 30-day window', () => {
    // Untouched by this change: the badge asks interview_upcoming, which still
    // knows both phases.
    expect(provider).toMatch(/usePendingInterviewsQuery\(\{\s*enabled: authReady,\s*deferMs: 8000,\s*days: 30,\s*\}\)/)
  })
})
