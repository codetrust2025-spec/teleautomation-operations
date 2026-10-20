/**
 * Technical issue is an attendance status of its own (21 Oct).
 *
 * A sitting that went ahead but failed for a technical reason (UltraViewer,
 * the microphone, the connection) had nowhere to go in Edit Interview Slot and
 * was recorded as Attended with the problem in the note. What this holds the
 * screen to:
 *   1. The Attendance dropdown offers Technical issue beside every existing
 *      status, Awaiting new slot included.
 *   2. Choosing it asks who attended and for a note saying what went wrong,
 *      and Save sends exactly that status, attendee and note.
 *   3. It has its own counter tab, which filters the roster to it, and its own
 *      badge and row style.
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
import { editDialog, openRowEdit, rowOf, saveButton, setAttendance, setNote } from './rowEditTestHelpers.js'

const NOW = '2026-09-28T06:00:00Z'
let calls
let posted
let dailyRows

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function row(name, status = '') {
  return {
    id: name, name, phone: '9000000001', date: '2026-09-28', time: '10:00', time_end: '11:00',
    technology: 'React JS', interview_round: 'L1', interview_attendee: 'Bhavana',
    interview_attendance_status: status, interview_attendance_remark: status ? 'UltraViewer not working' : '',
  }
}

function mockFetch() {
  calls = []
  posted = []
  dailyRows = [row('Asha Rao')]
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (url.pathname.endsWith('/interview-attendance')) {
      posted.push(JSON.parse(init.body))
      return jsonResponse({ status: 'ok' })
    }
    if (url.pathname.endsWith('/interviews/global')) {
      const technical = dailyRows.filter(r => r.interview_attendance_status === 'technical_issue').length
      return jsonResponse({
        status: 'ok',
        interviews: { count: dailyRows.length, pending_count: dailyRows.length - technical, technical_issue_count: technical },
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

async function renderPanel(firstName = 'Asha Rao') {
  render(<DailyOpsPanel />)
  await waitFor(() => expect(calls.length).toBeGreaterThan(0))
  await act(async () => { await Promise.resolve() })
  fireEvent.click(screen.getByRole('button', { name: 'Filter interviews by date' }))
  const todayBtn = document.querySelector('.ops-datepicker__day--today')
  if (todayBtn) fireEvent.click(todayBtn)
  await waitFor(() => expect(screen.getByText(firstName)).toBeInTheDocument())
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

describe('Edit interview slot', () => {
  it('offers Technical issue beside every existing status', async () => {
    await renderPanel()
    openRowEdit('Asha Rao')
    const select = within(editDialog()).getByLabelText('Attendance')
    expect([...select.options].map(o => o.textContent)).toEqual([
      'Pending', 'Attended', 'Not attended', 'Technical issue', 'Cancelled', 'Rescheduled', 'Awaiting new slot', 'Re-Service',
    ])
  })

  it('asks who attended and what went wrong, and saves exactly that', async () => {
    await renderPanel()
    openRowEdit('Asha Rao')
    setAttendance('technical_issue')

    expect(screen.getByRole('combobox', { name: 'Attendee (who attended the interview?)' })).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Interview feedback' })).toBeNull()
    const notes = within(editDialog()).getByRole('textbox', { name: 'Notes' })
    expect(notes).not.toBeDisabled()
    expect(notes.getAttribute('placeholder')).toMatch(/UltraViewer/)
    expect(within(editDialog()).getByText(/Describe the issue/)).toBeInTheDocument()
    expect(saveButton()).toBeDisabled()

    setNote('UltraViewer not working, mic issue')
    expect(saveButton()).not.toBeDisabled()
    await act(async () => { fireEvent.click(saveButton()) })

    expect(posted).toEqual([{
      status: 'technical_issue', remark: 'UltraViewer not working, mic issue', attendee: 'Bhavana', feedback: '',
    }])
  })
})

describe('the roster', () => {
  it('has a Technical issue tab that counts and filters, and a badge of its own', async () => {
    dailyRows = [row('Ravi Kolla', 'technical_issue'), row('Asha Rao')]
    await renderPanel('Ravi Kolla')

    const tab = screen.getByRole('button', { name: /Technical issue/ })
    await waitFor(() => expect(within(tab).getByText('1')).toBeInTheDocument())
    expect(tab.className).toContain('ops-dash-kpi--orange')

    const badge = rowOf('Ravi Kolla').querySelector('.ops-attendance-status')
    expect(badge.textContent).toBe('Technical issue')
    expect(badge.className).toContain('ops-attendance-status--technical')
    expect(rowOf('Ravi Kolla').className).toContain('ops-interview-row--technical')

    fireEvent.click(tab)
    await waitFor(() => expect(screen.queryByText('Asha Rao')).toBeNull())
    expect(screen.getByText('Ravi Kolla')).toBeInTheDocument()
  })
})
