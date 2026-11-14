/**
 * No specified round, no status update (owner, 14 Nov).
 *
 * Rows read "Round not specified" and could still be marked Attended, Not
 * attended, Rescheduled or Cancelled. The owner's rule, held here on the real
 * Daily Ops panel at a desktop width and at a phone width:
 *   1. On a row with no round, Edit interview slot blocks the status: the
 *      Attendance control is disabled, the owner's message is shown, Notes
 *      (which save through the same route) are disabled too, and nothing is
 *      sent to the attendance route.
 *   2. It does not ask for a round as part of the status change: there is no
 *      "Required" round and no hint to pick one.
 *   3. The round is corrected separately, in the same edit, by an admin only:
 *      it saves with the slot, the table shows it at once, and only then can
 *      the status change.
 *   4. Rows that carry a round change status exactly as before.
 *   5. The round's change history is listed in the dialog.
 * The API side of the same rule is tests/test_interview_round_required.py.
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

const MESSAGE = 'Interview round is missing. Update the round before changing the status.'
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
      if (url.pathname.includes('/interviews/slots/')) {
        const id = decodeURIComponent(url.pathname.split('/').at(-1))
        dailyRows = dailyRows.map(r => r.id === id ? { ...r, interview_round: body.interview_round } : r)
        return jsonResponse({ status: 'ok', candidate: dailyRows.find(r => r.id === id) })
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
const attendanceSelect = () => within(editDialog()).getByLabelText('Attendance')
const attendanceCalls = () => sent.filter(call => call.path.endsWith('/interview-attendance'))

function setWidth(width) {
  window.innerWidth = width
  window.matchMedia = query => {
    const max = /max-width:\s*(\d+)px/.exec(query)
    const min = /min-width:\s*(\d+)px/.exec(query)
    const matches = (!max || width <= Number(max[1])) && (!min || width >= Number(min[1]))
    return { matches, media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }
  }
  window.dispatchEvent(new Event('resize'))
}

const ORIGINAL_MATCH_MEDIA = window.matchMedia
const ORIGINAL_WIDTH = window.innerWidth

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(NOW))
  auth.role = 'admin'
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  window.matchMedia = ORIGINAL_MATCH_MEDIA
  window.innerWidth = ORIGINAL_WIDTH
})

describe.each([['desktop', 1440], ['mobile', 440]])('No round, no status update (%s)', (label, width) => {
  beforeEach(() => setWidth(width))

  it.each(['admin', 'handler'])('blocks the status on a roundless row for a %s, without asking for a round', async who => {
    auth.role = who
    mockFetch([row('Zara Testname', { interview_attendance_status: 'attended', interview_attendance_remark: 'went well' })])
    await renderPanel('Zara Testname')
    expect(within(rowOf('Zara Testname')).getByText('Round not specified')).toBeInTheDocument()
    openRowEdit('Zara Testname')

    expect(attendanceSelect()).toBeDisabled()
    expect(within(editDialog()).getByRole('alert')).toHaveTextContent(MESSAGE)
    expect(within(editDialog()).getByRole('textbox', { name: 'Notes' })).toBeDisabled()
    // Not a prompt: nothing calls the round required or asks to pick one now.
    expect(within(editDialog()).queryByText('Required')).toBeNull()
    expect(roundSelect()).not.toBeRequired()
    if (label === 'mobile' && who === 'admin') captureLayout('round-missing-modal-mobile')
    if (label === 'desktop' && who === 'admin') captureLayout('round-missing-modal-desktop')

    // Forcing the control does not get a status through either.
    fireEvent.change(attendanceSelect(), { target: { value: 'cancelled' } })
    expect(saveButton()).toBeDisabled()
    await act(async () => { fireEvent.submit(editDialog()) })
    expect(within(editDialog()).getAllByText(MESSAGE).length).toBeGreaterThan(0)
    expect(attendanceCalls()).toEqual([])
  })

  it('lets an admin set the round in the edit; the table shows it and the status then saves', async () => {
    mockFetch([row('Zara Testname')])
    await renderPanel('Zara Testname')
    openRowEdit('Zara Testname')
    fireEvent.change(roundSelect(), { target: { value: 'L2' } })
    await act(async () => { fireEvent.click(saveButton()) })
    await waitFor(() => expect(sent.length).toBe(1))
    expect(sent[0].path).toMatch(/\/interviews\/slots\/Zara%20Testname$/)
    expect(sent[0].body.interview_round).toBe('L2')
    expect(attendanceCalls()).toEqual([])
    await waitFor(() => expect(rowOf('Zara Testname').querySelector('td[data-label="Round"]').textContent).toBe('L2'))

    openRowEdit('Zara Testname')
    expect(attendanceSelect()).not.toBeDisabled()
    setAttendance('not_attended')
    setNote('No show')
    await act(async () => { fireEvent.click(saveButton()) })
    await waitFor(() => expect(attendanceCalls().length).toBe(1))
    expect(attendanceCalls()[0].body.status).toBe('not_attended')
  })

  it.each(['attended', 'not_attended', 'rescheduled', 'cancelled'])('saves %s on a row with a round as before', async status => {
    mockFetch([row('Kiran Testname', { interview_round: 'L1' })])
    await renderPanel('Kiran Testname')
    openRowEdit('Kiran Testname')
    expect(within(editDialog()).queryByRole('alert')).toBeNull()
    setAttendance(status)
    if (status === 'attended') pick('Interview feedback', 'Good')
    setNote('Recorded')
    await act(async () => { fireEvent.click(saveButton()) })
    await waitFor(() => expect(attendanceCalls().length).toBe(1))
    expect(attendanceCalls()[0].body.status).toBe(status)
    expect(sent.some(call => call.path.includes('/interviews/slots/'))).toBe(false)
  })

  it('locks the round for a non-admin', async () => {
    auth.role = 'handler'
    mockFetch([row('Kiran Testname', { interview_round: 'L1' })])
    await renderPanel('Kiran Testname')
    openRowEdit('Kiran Testname')
    expect(roundSelect()).toBeDisabled()
    expect(within(editDialog()).getByText('Only an admin can set or change the interview round.')).toBeInTheDocument()
  })
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
