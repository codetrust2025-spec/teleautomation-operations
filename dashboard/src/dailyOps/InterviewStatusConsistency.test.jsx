/**
 * The same six statuses, everywhere.
 *
 * They were declared twice: the row dropdown listed all six, and the KPI tabs
 * above the table named four of them inline. Cancelled, Rescheduled and
 * Re-Service had no tab — and the tabs are the only way to filter, so three of
 * the six could be set on a row and then never filtered for. The backend was
 * missing the other half of the same seam: `_interview_attendance_counts`
 * counted four statuses and derived Pending by subtracting them, so every
 * Re-Service row was reported as Pending.
 *
 * Both sides now come from one list. These drive the real panel to check that
 * the tabs, the dropdown, the filter and the counters agree, and that they go
 * on agreeing after the table reloads.
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
import {
  INTERVIEW_STATUSES,
  STATUS_OPTIONS,
  STATUS_TABS,
  SCHEDULED_TAB,
  canonicalStatus,
  matchesStatusFilter,
  statusLabel,
} from './interviewStatuses.js'

const NOW = '2026-09-08T06:00:00Z'
const TODAY = '2026-09-08'

/** What the backend actually stores, mirrored from candidate_store.py. */
const BACKEND_STATUSES = ['attended', 'not_attended', 'cancelled', 'rescheduled',
  'released_for_reschedule', 're_service']

let calls
let payload

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function interviewRow(name, status) {
  return {
    id: `${name}-${status || 'pending'}`,
    name,
    phone: '9000000001',
    date: TODAY,
    time: '10:00',
    technology: 'Python',
    interview_round: 'L1',
    interview_attendee: 'Bhavana',
    interview_attendance_status: status,
  }
}

/** One row per status, plus the counts the API would report for them. */
function oneOfEach() {
  const rows = [
    interviewRow('Pending Priya', ''),
    interviewRow('Attended Asha', 'attended'),
    interviewRow('Missed Vikas', 'not_attended'),
    interviewRow('Cancelled Chandra', 'cancelled'),
    interviewRow('Moved Meera', 'rescheduled'),
    interviewRow('Waiting Wasim', 'released_for_reschedule'),
    interviewRow('Repeat Ravi', 're_service'),
  ]
  return {
    status: 'ok',
    interviews: rows,
    count: rows.length,
    pending_count: 1,
    attended_count: 1,
    not_attended_count: 1,
    cancelled_count: 1,
    rescheduled_count: 1,
    released_for_reschedule_count: 1,
    re_service_count: 1,
  }
}

/** How many rows `oneOfEach` holds: one per status, Pending included. */
const EVERY_STATUS = oneOfEach().interviews.length

function mockFetch() {
  calls = []
  payload = oneOfEach()
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (url.pathname.endsWith('/interviews/global')) {
      // The panel prefers these over the roster's own counts, so the tabs read
      // from here. interview_global_summary spreads the same
      // _interview_attendance_counts dict, hence the identical keys.
      const { interviews: _rows, ...counts } = payload
      return jsonResponse({
        status: 'ok',
        interviews: counts,
        available_months: [{ value: '2026-09', label: 'Sep 2026', count: 6 }],
        booking_overview: { total: 0, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (/\/interviews\/(daily|monitor)$/.test(url.pathname)) return jsonResponse(payload)
    return jsonResponse({ status: 'ok' })
  }))
}

async function renderPanel() {
  const view = render(<DailyOpsPanel />)
  await waitFor(() => expect(calls.length).toBeGreaterThan(0))
  await act(async () => { await Promise.resolve() })
  return view
}

const tab = (label) => screen.getByRole('button', { name: new RegExp(`^${label}`) })
const rowNames = () => [...document.querySelectorAll('.ops-interview-row')]
  .map(tr => tr.textContent)

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

describe('the list matches the backend', () => {
  it('offers exactly the statuses the backend stores, plus Pending', () => {
    const values = INTERVIEW_STATUSES.map(s => s.value)
    expect(values).toContain('')
    expect(values.filter(Boolean).sort()).toEqual([...BACKEND_STATUSES].sort())
  })

  it('invents nothing the backend would not store', () => {
    for (const { value } of INTERVIEW_STATUSES) {
      if (value) expect(BACKEND_STATUSES).toContain(value)
    }
  })

  it('names a counter for every status', () => {
    for (const status of INTERVIEW_STATUSES) {
      expect(status.countKey).toBeTruthy()
    }
    expect(INTERVIEW_STATUSES.find(s => s.value === 're_service').countKey).toBe('re_service_count')
  })

  it('keeps the dropdown and the tabs on one list', () => {
    // The drift this whole change is about: two declarations, four vs six.
    const tabValues = STATUS_TABS.filter(t => t.id !== 'scheduled').map(t => t.value)
    expect(tabValues).toEqual(STATUS_OPTIONS.map(o => o.value))
  })
})

describe('every status has a tab', () => {
  it.each(INTERVIEW_STATUSES.map(s => s.label))('shows a %s tab', async (label) => {
    await renderPanel()
    expect(tab(label)).toBeInTheDocument()
  })

  it('shows Cancelled, which had no tab at all', async () => {
    await renderPanel()
    expect(tab('Cancelled')).toBeInTheDocument()
  })

  it('keeps Scheduled first and separate from attendance', async () => {
    await renderPanel()
    expect(tab('Scheduled')).toBeInTheDocument()
    // It is a booking state, so it is not a value any row can be set to.
    expect(STATUS_OPTIONS.map(o => o.value)).not.toContain(SCHEDULED_TAB.id)
    expect(BACKEND_STATUSES).not.toContain('scheduled')
  })
})

describe('the counters come from the API', () => {
  it('shows each status count as the API reported it', async () => {
    await renderPanel()
    for (const status of INTERVIEW_STATUSES) {
      const card = tab(status.label)
      expect(card.textContent).toMatch(new RegExp(`${status.label}\\s*1`))
    }
  })

  it('counts Scheduled as every row in the roster', async () => {
    await renderPanel()
    expect(tab('Scheduled').textContent).toMatch(new RegExp(`Scheduled\s*${EVERY_STATUS}`))
  })

  it('does not fold Re-Service into Pending', async () => {
    await renderPanel()
    expect(tab('Re-Service').textContent).toMatch(/Re-Service\s*1/)
    expect(tab('Pending').textContent).toMatch(/Pending\s*1/)
  })

  it('updates after the table reloads with new counts', async () => {
    await renderPanel()
    expect(tab('Cancelled').textContent).toMatch(/Cancelled\s*1/)

    // The counters are tallied from the rows, so the rows are what changes.
    payload = {
      status: 'ok',
      interviews: [interviewRow('Cancelled Chandra', 'cancelled'), interviewRow('Also Cancelled', 'cancelled')],
    }
    fireEvent.click(tab('Scheduled'))
    fireEvent.click(screen.getByRole('button', { name: 'Filter interviews by date' }))
    const calendar = screen.getByRole('dialog', { name: 'Choose a date' })
    fireEvent.click(within(calendar).getByRole(
      'button', { name: /Monday.*7 September.*2026/ }))

    await waitFor(() => expect(tab('Cancelled').textContent).toMatch(/Cancelled\s*2/))
    expect(tab('Pending').textContent).toMatch(/Pending\s*0/)
  })
})

describe('filtering by a status', () => {
  it('narrows the table to Cancelled rows', async () => {
    await renderPanel()
    expect(rowNames()).toHaveLength(EVERY_STATUS)

    fireEvent.click(tab('Cancelled'))
    await waitFor(() => expect(rowNames()).toHaveLength(1))
    expect(rowNames()[0]).toMatch(/Cancelled Chandra/)
  })

  it.each([
    ['Attended', 'Attended Asha'],
    ['Not attended', 'Missed Vikas'],
    ['Cancelled', 'Cancelled Chandra'],
    ['Rescheduled', 'Moved Meera'],
    ['Re-Service', 'Repeat Ravi'],
    ['Pending', 'Pending Priya'],
  ])('%s shows only its own rows', async (label, expected) => {
    await renderPanel()
    fireEvent.click(tab(label))
    await waitFor(() => expect(rowNames()).toHaveLength(1))
    expect(rowNames()[0]).toMatch(new RegExp(expected))
  })

  it('clicking the active tab again clears the filter', async () => {
    await renderPanel()
    fireEvent.click(tab('Cancelled'))
    await waitFor(() => expect(rowNames()).toHaveLength(1))
    fireEvent.click(tab('Cancelled'))
    await waitFor(() => expect(rowNames()).toHaveLength(EVERY_STATUS))
  })

  it('Scheduled shows everything', async () => {
    await renderPanel()
    fireEvent.click(tab('Cancelled'))
    await waitFor(() => expect(rowNames()).toHaveLength(1))
    fireEvent.click(tab('Scheduled'))
    await waitFor(() => expect(rowNames()).toHaveLength(EVERY_STATUS))
  })

  it('survives a reload of the same filter', async () => {
    await renderPanel()
    fireEvent.click(tab('Cancelled'))
    await waitFor(() => expect(rowNames()).toHaveLength(1))
    fireEvent.click(screen.getByRole('button', { name: 'Filter interviews by date' }))
    const calendar = screen.getByRole('dialog', { name: 'Choose a date' })
    fireEvent.click(within(calendar).getByRole(
      'button', { name: /Monday.*7 September.*2026/ }))
    await waitFor(() => expect(rowNames()).toHaveLength(1))
    expect(rowNames()[0]).toMatch(/Cancelled Chandra/)
  })
})

describe('the row dropdown', () => {
  it('offers all six statuses', async () => {
    await renderPanel()
    const select = document.querySelector('.ops-interview-row select')
    expect(select).not.toBeNull()
    const labels = [...select.options].map(o => o.textContent)
    for (const status of INTERVIEW_STATUSES) expect(labels).toContain(status.label)
  })

  it('offers Cancelled as a settable value', async () => {
    await renderPanel()
    const select = document.querySelector('.ops-interview-row select')
    expect([...select.options].map(o => o.value)).toContain('cancelled')
  })
})

describe('the roster fallback carries every counter', () => {
  it('zeroes a slot for each status rather than only three', async () => {
    const { emptyStatusCounts, readStatusCounts } = await import('./interviewStatuses.js')
    const zeroed = emptyStatusCounts()
    for (const status of INTERVIEW_STATUSES) {
      expect(zeroed).toHaveProperty(status.countKey, 0)
    }
    expect(zeroed).toHaveProperty('count', 0)
  })

  it('reads every counter out of a roster payload', async () => {
    const { readStatusCounts } = await import('./interviewStatuses.js')
    const counts = readStatusCounts(oneOfEach())
    expect(counts.cancelled_count).toBe(1)
    expect(counts.rescheduled_count).toBe(1)
    expect(counts.re_service_count).toBe(1)
    expect(counts.count).toBe(EVERY_STATUS)
  })

  it('shows the roster counts when the global summary is unavailable', async () => {
    // globalStats null -> the panel falls back to the roster's own counts.
    // Those omitted three statuses, so three tabs read 0 with rows on screen.
    calls = []
    vi.stubGlobal('fetch', vi.fn(async (input) => {
      const url = new URL(String(input), 'http://localhost')
      calls.push(url)
      if (url.pathname.endsWith('/interviews/global')) {
        return { ok: false, status: 500, headers: { get: () => 'application/json' }, json: async () => ({ status: 'error' }) }
      }
      if (/\/interviews\/(daily|monitor)$/.test(url.pathname)) return jsonResponse(payload)
      return jsonResponse({ status: 'ok' })
    }))

    await renderPanel()
    await waitFor(() => expect(tab('Cancelled').textContent).toMatch(/Cancelled\s*1/))
    expect(tab('Rescheduled').textContent).toMatch(/Rescheduled\s*1/)
    expect(tab('Re-Service').textContent).toMatch(/Re-Service\s*1/)
  })
})

describe('changing a row refreshes the counters', () => {
  it('sets a row to Cancelled and re-reads the counts', async () => {
    await renderPanel()
    expect(tab('Cancelled').textContent).toMatch(/Cancelled\s*1/)

    const select = document.querySelector('.ops-interview-row select')
    fireEvent.change(select, { target: { value: 'cancelled' } })

    // Choosing a status opens the confirm modal rather than saving silently.
    const form = await waitFor(() => {
      const node = document.querySelector('form.ops-slot-modal')
      if (!node) throw new Error('the confirm modal did not open')
      return node
    })
    // A note is required before a status change saves, so fill it the way the
    // operator would rather than submitting an incomplete form.
    // The remark input carries no `type`, so select it by class: in this mode
    // the form renders only the attendee select and this one input.
    const note = form.querySelector('input.cand-input')
    expect(note).not.toBeNull()
    fireEvent.change(note, { target: { value: 'Candidate cancelled the interview.' } })

    // What the API will report once the change has landed.
    // Chandra was already cancelled; Priya joins her, so two rows are.
    payload = {
      status: 'ok',
      interviews: oneOfEach().interviews.map(row =>
        row.name === 'Pending Priya'
          ? { ...row, interview_attendance_status: 'cancelled' }
          : row,
      ),
    }
    fireEvent.submit(form)

    await waitFor(() => {
      const posted = calls.filter(u => u.pathname.includes('/interview-attendance'))
      expect(posted.length).toBeGreaterThan(0)
    })
    // The roster and the global counts are both re-read after a write.
    await waitFor(() => expect(tab('Cancelled').textContent).toMatch(/Cancelled\s*2/))
  })

  it('re-reads the counts when Refresh is pressed', async () => {
    await renderPanel()
    payload = {
      status: 'ok',
      interviews: Array.from({ length: 3 }, (_, i) => interviewRow(`Pending ${i}`, '')),
    }
    fireEvent.click(screen.getByRole('button', { name: /Refresh|Updating/ }))
    await waitFor(() => expect(tab('Pending').textContent).toMatch(/Pending\s*3/))
    expect(tab('Cancelled').textContent).toMatch(/Cancelled\s*0/)
  })
})

describe('Cancelled is never mapped to something else', () => {
  it.each(['cancelled', 'canceled', 'Cancelled', ' CANCELED '])('%s stays Cancelled', (spelling) => {
    expect(canonicalStatus(spelling)).toBe('cancelled')
    expect(statusLabel(spelling)).toBe('Cancelled')
  })

  it('does not match another status filter', () => {
    expect(matchesStatusFilter('cancelled', 'cancelled')).toBe(true)
    for (const other of ['attended', 'not_attended', 'rescheduled', 're_service', 'pending']) {
      expect(matchesStatusFilter('cancelled', other)).toBe(false)
    }
  })

  it('is not swept into Pending', () => {
    expect(matchesStatusFilter('cancelled', 'pending')).toBe(false)
    expect(matchesStatusFilter('', 'pending')).toBe(true)
  })
})

describe('Pending is the absence of a status', () => {
  it('is submitted as an empty value but filtered by its own token', () => {
    const pending = INTERVIEW_STATUSES.find(s => s.label === 'Pending')
    expect(pending.value).toBe('')
    expect(pending.filterValue).toBe('pending')
  })

  it('treats an unknown status as Pending rather than hiding the row', () => {
    expect(statusLabel('something-else')).toBe('Pending')
    expect(matchesStatusFilter('something-else', 'pending')).toBe(true)
  })
})
