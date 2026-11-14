/**
 * A status never saves on an interview with no round (14 Nov).
 *
 * Rows read "Round not specified" and could still be marked Attended, Not
 * attended, Rescheduled or Cancelled. What this holds Edit interview slot to:
 *   1. On a row with no round, choosing a status marks the round Required and
 *      Save stays disabled until one is picked; nothing is sent before that.
 *   2. Picking it saves the round (its own audited request) before the status,
 *      and the table shows it straight away.
 *   3. A round already set is locked for a non-admin and open to an admin.
 *   4. The round's change history is listed in the dialog.
 */
import React from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({ role: 'admin', reference: '', enabled: true }))
vi.mock('../context/AuthContext.jsx', () => ({ useAuth: () => auth }))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'
import { captureLayout } from '../test/captureLayout.js'
import { editDialog, openRowEdit, pick, rowOf, saveButton, setAttendance, setNote } from './rowEditTestHelpers.js'

const NOW = '2026-09-28T06:00:00Z'
let calls
let sent
let dailyRows

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function row(name, over = {}) {
  return {
    id: name, name, phone: '9000000001', date: '2026-09-28', time: '10:00', time_end: '11:00',
    technology: 'React JS', interview_round: '', interview_attendee: 'Bhavana',
    interview_attendance_status: '', interview_attendance_remark: '', ...over,
  }
}

function mockFetch(rows) {
  calls = []
  sent = []
  dailyRows = rows
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (init?.method === 'PATCH' || init?.method === 'POST') {
      const body = JSON.parse(init.body)
      sent.push({ path: url.pathname, method: init.method, body })
      if (url.pathname.endsWith('/interview-round')) {
        const id = decodeURIComponent(url.pathname.split('/').at(-2))
        dailyRows = dailyRows.map(r => r.id === id ? { ...r, interview_round: body.interview_round } : r)
        return jsonResponse({
          status: 'ok',
          candidate: { interview_round: body.interview_round, interview_round_history: [{ from: '', to: body.interview_round, by: 'admin1', at: '2026-09-28T06:00:00Z' }] },
        })
      }
      return jsonResponse({ status: 'ok' })
    }
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok',
        interviews: { count: dailyRows.length, pending_count: dailyRows.length },
        available_months: [{ value: '2026-09', label: 'Sep 2026', count: dailyRows.length }],
        booking_overview: { total: dailyRows.length, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (url.pathname.endsWith('/interviews/daily')) {
      return jsonResponse({ status: 'ok', interviews: dailyRows, count: dailyRows.length })
    }
    if (url.pathname.endsWith('/interviews/monitor')) {
      return jsonResponse({ status: 'ok', interviews: [], count: 0, awaiting_interviews: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
}

async function renderPanel(firstName) {
  render(<DailyOpsPanel />)
  await waitFor(() => expect(calls.length).toBeGreaterThan(0))
  await act(async () => { await Promise.resolve() })
  fireEvent.click(screen.getByRole('button', { name: 'Filter interviews by date' }))
  const todayBtn = document.querySelector('.ops-datepicker__day--today')
  if (todayBtn) fireEvent.click(todayBtn)
  await waitFor(() => expect(screen.getByText(firstName)).toBeInTheDocument())
}

const roundSelect = () => within(editDialog()).getByLabelText('Interview round')

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(NOW))
  auth.role = 'admin'
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('Interview round on a status update', () => {
  it.each(['attended', 'not_attended', 'rescheduled', 'cancelled'])(
    'requires the round before %s saves, then saves round first and shows it in the table',
    async status => {
      mockFetch([row('Gangadhar Testname')])
      await renderPanel('Gangadhar Testname')
      expect(within(rowOf('Gangadhar Testname')).getByText('Round not specified')).toBeInTheDocument()
      openRowEdit('Gangadhar Testname')
      setAttendance(status)
      if (status === 'attended') pick('Interview feedback', 'Good')
      setNote('Recorded')
      expect(within(editDialog()).getByText('Select the round before saving this status.')).toBeInTheDocument()
      expect(saveButton()).toBeDisabled()
      expect(sent).toEqual([])
      if (status === 'attended') captureLayout('round-required-modal')

      fireEvent.change(roundSelect(), { target: { value: 'L2' } })
      expect(saveButton()).not.toBeDisabled()
      await act(async () => { fireEvent.click(saveButton()) })

      await waitFor(() => expect(sent.length).toBe(2))
      expect(sent[0].path).toMatch(/\/candidates\/Gangadhar%20Testname\/interview-round$/)
      expect(sent[0]).toMatchObject({ method: 'PATCH', body: { interview_round: 'L2' } })
      expect(sent[1].path).toMatch(/\/interview-attendance$/)
      expect(sent[1].body.status).toBe(status)
      // The slot itself was not touched.
      expect(sent.some(call => call.path.includes('/interviews/slots/'))).toBe(false)
      await waitFor(() => expect(within(rowOf('Gangadhar Testname')).queryByText('Round not specified')).toBeNull())
      expect(rowOf('Gangadhar Testname').querySelector('td[data-label="Round"]').textContent).toBe('L2')
      if (status === 'attended') captureLayout('round-required-table')
    },
  )

  it('lets a row without a status stay roundless (Pending is not a status update)', async () => {
    mockFetch([row('Asha Testname')])
    await renderPanel('Asha Testname')
    openRowEdit('Asha Testname')
    expect(within(editDialog()).queryByText('Select the round before saving this status.')).toBeNull()
    expect(roundSelect()).not.toBeRequired()
  })

  it('locks a round already set for a non-admin, and opens it to an admin', async () => {
    auth.role = 'handler'
    mockFetch([row('Kiran Testname', { interview_round: 'L1' })])
    await renderPanel('Kiran Testname')
    openRowEdit('Kiran Testname')
    expect(roundSelect()).toBeDisabled()
    expect(within(editDialog()).getByText('Only an admin can change a round that is already set.')).toBeInTheDocument()
    cleanup()

    auth.role = 'admin'
    mockFetch([row('Kiran Testname', { interview_round: 'L1', interview_attendance_status: 'attended', interview_feedback: 'good', interview_attendance_remark: 'went well' })])
    await renderPanel('Kiran Testname')
    openRowEdit('Kiran Testname')
    expect(roundSelect()).not.toBeDisabled()
    fireEvent.change(roundSelect(), { target: { value: 'HR' } })
    await act(async () => { fireEvent.click(saveButton()) })
    await waitFor(() => expect(sent.length).toBe(1))
    expect(sent[0].path).toMatch(/\/interview-round$/)
    expect(sent[0].body).toEqual({ interview_round: 'HR' })
  })

  it('offers L3 and lists who changed the round and when', async () => {
    mockFetch([row('Ravi Testname', {
      interview_round: 'L2',
      interview_round_history: [
        { from: '', to: 'L1', by: 'handler1', at: '2026-09-27T05:00:00Z' },
        { from: 'L1', to: 'L2', by: 'admin1', at: '2026-09-28T05:00:00Z' },
      ],
    })])
    await renderPanel('Ravi Testname')
    openRowEdit('Ravi Testname')
    expect([...roundSelect().options].map(o => o.value)).toEqual(['', 'L1', 'L2', 'L3', 'HR', 'Final', 'Screening'])
    captureLayout('round-history-modal')
    const items = within(within(editDialog()).getByRole('list', { name: 'Round history' })).getAllByRole('listitem')
    expect(items.map(item => item.textContent)).toEqual([
      expect.stringMatching(/^L1 → L2 admin1 · /),
      expect.stringMatching(/^Not specified → L1 handler1 · /),
    ])
  })
})
