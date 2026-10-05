/**
 * The Candidate dropdown is a selection, not a search.
 *
 * Picking "Ram Charan M S" from the dropdown must scope the whole view to that
 * one person. The screen used to fold the dropdown choice into the same loose
 * `search` the free-text box uses, so "Ram Charan M S" matched "Rama Krishna"
 * on the shared word "ram" and the table listed both. The two are separate
 * wires now: a dropdown choice goes out as `candidate` (one person, matched
 * exactly on the server), the typed box goes out as `search` (any word), and
 * they never collapse into each other.
 *
 * The server owns the exact-vs-loose matching; this test owns the contract the
 * screen must honour -- which parameter each control sends -- because that is
 * the half that broke.
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
vi.mock('./PendingWorksStrip.jsx', () => ({ PendingWorksStrip: () => null }))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'

const NOW = '2026-09-28T06:00:00Z'
let calls

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function interviewRow(id, name, date, time) {
  return {
    id, name, phone: '9000000001', date, time, time_end: '11:00', technology: 'Angular',
    interview_round: 'L2', interview_attendee: 'Bhavana', interview_attendance_status: '',
  }
}

// Two people whose names share "ram" -- the collision the old loose match hit.
const RAM = interviewRow('ram', 'Ram Charan M S', '2026-09-20', '10:00')
const RAMA = interviewRow('rama', 'Rama Krishna', '2026-09-21', '17:00')

// The dropdown is built from the global summary's interviews.by_candidate, so
// both people have to be offered there for the option to exist to pick.
const BY_CANDIDATE = [
  { name: 'Ram Charan M S', scheduled: 1 },
  { name: 'Rama Krishna', scheduled: 1 },
]

function monitorRowsFor(url) {
  // Stand in for the server's exact candidate match so the table reflects what
  // a correct backend would return for the parameters the screen sent.
  const candidate = url.searchParams.get('candidate')
  const search = url.searchParams.get('search')
  let rows = [RAM, RAMA]
  if (candidate) rows = rows.filter(r => r.name === candidate)
  else if (search) rows = rows.filter(r => r.name.toLowerCase().includes(search.toLowerCase()))
  return rows
}

function mockFetch() {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok',
        interviews: { by_candidate: BY_CANDIDATE, by_technology: [{ name: 'Angular' }] },
        available_months: [],
        booking_overview: { total: 2, by_candidate: BY_CANDIDATE, by_level: [], by_technology: [] },
      })
    }
    if (url.pathname.endsWith('/interviews/monitor') || url.pathname.endsWith('/interviews/daily')) {
      const rows = monitorRowsFor(url)
      return jsonResponse({ status: 'ok', interviews: rows, count: rows.length, awaiting_interviews: [] })
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

function lastRosterCall() {
  // The table reads /daily or /monitor; the pie/options read /global.
  const roster = calls.filter(u => u.pathname.endsWith('/interviews/monitor') || u.pathname.endsWith('/interviews/daily'))
  return roster[roster.length - 1]
}

function lastGlobalCall() {
  const global = calls.filter(u => u.pathname.endsWith('/interviews/global'))
  return global[global.length - 1]
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

describe('the Candidate dropdown', () => {
  it('sends the choice as an exact candidate, never as a loose search', async () => {
    await renderPanel()
    fireEvent.change(screen.getByLabelText('Candidate filter'), { target: { value: 'Ram Charan M S' } })

    await waitFor(() => expect(lastRosterCall()?.searchParams.get('candidate')).toBe('Ram Charan M S'))
    const roster = lastRosterCall()
    expect(roster.searchParams.get('candidate')).toBe('Ram Charan M S')
    expect(roster.searchParams.get('search')).toBeNull()
  })

  it('passes the choice to the global summary too, so the counters and pie match', async () => {
    await renderPanel()
    fireEvent.change(screen.getByLabelText('Candidate filter'), { target: { value: 'Ram Charan M S' } })

    await waitFor(() => expect(lastGlobalCall()?.searchParams.get('candidate')).toBe('Ram Charan M S'))
    expect(lastGlobalCall().searchParams.get('search')).toBeNull()
  })

  it('shows only the chosen person in the table, not a name that merely shares a word', async () => {
    // The pie legend also prints candidate names, so scope every check to the
    // table itself -- that is the list the bug was about.
    const inTable = name => within(document.querySelector('table')).queryAllByText(name).length > 0

    await renderPanel()
    await waitFor(() => expect(inTable('Rama Krishna')).toBe(true))
    expect(inTable('Ram Charan M S')).toBe(true)

    fireEvent.change(screen.getByLabelText('Candidate filter'), { target: { value: 'Ram Charan M S' } })

    await waitFor(() => expect(inTable('Rama Krishna')).toBe(false))
    expect(inTable('Ram Charan M S')).toBe(true)
  })
})

describe('the search box', () => {
  it('still sends typed text as a loose search, not as an exact candidate', async () => {
    await renderPanel()
    fireEvent.change(screen.getByLabelText('Candidate search'), { target: { value: 'ram' } })

    await waitFor(() => expect(lastRosterCall()?.searchParams.get('search')).toBe('ram'))
    expect(lastRosterCall().searchParams.get('candidate')).toBeNull()
  })
})
