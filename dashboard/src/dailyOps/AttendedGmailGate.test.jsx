/**
 * Profile-wise L2, HR and Final: Attended needs one of the candidate's Gmail
 * accounts with Monitoring Active in AI Mail Review (owner, 26 Nov). On the real
 * Daily Ops panel, at a desktop width and at a phone width:
 *   1. With a monitored Gmail, Attended saves -- no reconnection asked for, even
 *      when the candidate has other Gmail accounts needing it.
 *   2. With none, the dialog says so, Save stays disabled and nothing is sent.
 *   3. Round-wise bookings, other rounds and other outcomes never ask.
 *   4. The server's refusal is shown if it disagrees with the dialog.
 * The API side is tests/test_attended_gmail_gate.py.
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
import { GMAIL_MONITORING_REQUIRED_MESSAGE, requiresMonitoredGmail } from './attendedGmailGate.js'
import { editDialog, openRowEdit, pick, saveButton, setAttendance, setNote } from './rowEditTestHelpers.js'

const NOW = '2026-09-28T06:00:00Z'
let sent
let gmailAsks
let gmail
let attendanceReply

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function row(name, over = {}) {
  return {
    id: name, name, phone: '9000000001', date: '2026-09-28', time: '10:00', time_end: '11:00',
    technology: 'React JS', interview_round: 'L2', service_type: 'profile_service', interview_attendee: 'Bhavana',
    interview_attendance_status: '', interview_attendance_remark: '', ...over,
  }
}

function mockFetch(rows) {
  sent = []
  gmailAsks = []
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/attended-gmail')) {
      gmailAsks.push(decodeURIComponent(url.pathname.split('/').at(-2)))
      return jsonResponse({ status: 'ok', required: true, ...gmail })
    }
    if (init?.method === 'PATCH' || init?.method === 'POST') {
      sent.push({ path: url.pathname, method: init.method, body: JSON.parse(init.body) })
      return jsonResponse(url.pathname.endsWith('/interview-attendance') ? attendanceReply : { status: 'ok' })
    }
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok', interviews: { count: rows.length, pending_count: rows.length },
        available_months: [{ value: '2026-09', label: 'Sep 2026', count: rows.length }],
        booking_overview: { total: rows.length, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (url.pathname.endsWith('/interviews/daily')) return jsonResponse({ status: 'ok', interviews: rows, count: rows.length })
    if (url.pathname.endsWith('/interviews/monitor')) return jsonResponse({ status: 'ok', interviews: [], count: 0, awaiting_interviews: [] })
    return jsonResponse({ status: 'ok' })
  }))
}

async function renderPanel(firstName) {
  render(<DailyOpsPanel />)
  await act(async () => { await Promise.resolve() })
  fireEvent.click(screen.getByRole('button', { name: 'Filter interviews by date' }))
  const todayBtn = document.querySelector('.ops-datepicker__day--today')
  if (todayBtn) fireEvent.click(todayBtn)
  await waitFor(() => expect(screen.getByText(firstName)).toBeInTheDocument())
}

const attendanceCalls = () => sent.filter(call => call.path.endsWith('/interview-attendance'))

async function chooseAttended() {
  setAttendance('attended')
  pick('Interview feedback', 'Good')
  setNote('Joined on time')
  await act(async () => { await Promise.resolve() })
}

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
  gmail = { active: true, monitored: ['asha@example.test'] }
  attendanceReply = { status: 'ok' }
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  window.matchMedia = ORIGINAL_MATCH_MEDIA
  window.innerWidth = ORIGINAL_WIDTH
})

describe('which bookings the rule covers', () => {
  it.each([
    [{ service_type: 'profile_service' }, 'L2', 'attended', true],
    [{ service_type: 'profile_service' }, 'HR', 'attended', true],
    [{ service_type: 'profile_service' }, 'Final', 'attended', true],
    [{}, 'Final', 'attended', true],
    [{ service_type: 'round_wise' }, 'L2', 'attended', false],
    [{ service_type: 'profile_service' }, 'L1', 'attended', false],
    [{ service_type: 'profile_service' }, 'Screening', 'attended', false],
    [{ service_type: 'profile_service' }, 'HR', 'not_attended', false],
  ])('%j round %s status %s -> %s', (r, round, status, expected) => {
    expect(requiresMonitoredGmail(r, { status, round })).toBe(expected)
  })
})

describe.each([['desktop', 1440], ['mobile', 440]])('Attended needs a monitored Gmail (%s)', (label, width) => {
  beforeEach(() => setWidth(width))

  it.each(['L2', 'HR', 'Final'])('saves Attended on a Profile-wise %s when one Gmail is monitored', async round => {
    mockFetch([row('Asha Testname', { interview_round: round })])
    await renderPanel('Asha Testname')
    openRowEdit('Asha Testname')
    await chooseAttended()
    await waitFor(() => expect(saveButton()).not.toBeDisabled())
    expect(gmailAsks).toEqual(['Asha Testname'])
    expect(within(editDialog()).queryByText(GMAIL_MONITORING_REQUIRED_MESSAGE)).toBeNull()
    await act(async () => { fireEvent.click(saveButton()) })
    await waitFor(() => expect(attendanceCalls()).toHaveLength(1))
    expect(attendanceCalls()[0].body.status).toBe('attended')
  })

  it('blocks Attended with no monitored Gmail: says why, Save disabled, nothing sent', async () => {
    gmail = { active: false, monitored: [] }
    mockFetch([row('Asha Testname', { interview_round: 'Final' })])
    await renderPanel('Asha Testname')
    openRowEdit('Asha Testname')
    await chooseAttended()
    await waitFor(() => expect(within(editDialog()).getByText(GMAIL_MONITORING_REQUIRED_MESSAGE)).toBeInTheDocument())
    expect(saveButton()).toBeDisabled()
    await act(async () => { fireEvent.submit(editDialog()) })
    expect(attendanceCalls()).toEqual([])
    // Another outcome on the same booking is not gated.
    setAttendance('not_attended')
    await waitFor(() => expect(saveButton()).not.toBeDisabled())
    await act(async () => { fireEvent.click(saveButton()) })
    await waitFor(() => expect(attendanceCalls()).toHaveLength(1))
    expect(attendanceCalls()[0].body.status).toBe('not_attended')
  })

  it.each([
    ['a Round-wise L2', { service_type: 'round_wise', interview_round: 'L2' }],
    ['a Profile-wise L1', { interview_round: 'L1' }],
  ])('never asks for %s', async (_, over) => {
    gmail = { active: false, monitored: [] }
    mockFetch([row('Kiran Testname', over)])
    await renderPanel('Kiran Testname')
    openRowEdit('Kiran Testname')
    await chooseAttended()
    await act(async () => { fireEvent.click(saveButton()) })
    await waitFor(() => expect(attendanceCalls()).toHaveLength(1))
    expect(gmailAsks).toEqual([])
  })

  it("shows the server's refusal when it disagrees", async () => {
    attendanceReply = { status: 'error', code: 'gmail_monitoring_required', message: GMAIL_MONITORING_REQUIRED_MESSAGE }
    mockFetch([row('Asha Testname', { interview_round: 'HR' })])
    await renderPanel('Asha Testname')
    openRowEdit('Asha Testname')
    await chooseAttended()
    await waitFor(() => expect(saveButton()).not.toBeDisabled())
    await act(async () => { fireEvent.click(saveButton()) })
    await waitFor(() => expect(within(editDialog()).getByText(GMAIL_MONITORING_REQUIRED_MESSAGE)).toBeInTheDocument())
  })
})
