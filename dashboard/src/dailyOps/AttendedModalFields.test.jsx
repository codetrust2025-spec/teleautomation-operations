/**
 * The "Mark as Attended" modal: dark custom dropdowns, a required result, a
 * gated button and a footer that stays put.
 *
 * What this holds the modal to:
 *   1. Feedback is the app's dark custom dropdown (a combobox + listbox), not a
 *      native <select> whose OS popup overlapped the Note field and footer, and
 *      it offers the five grades.
 *   2. There is a separate Interview result dropdown with its four values.
 *   3. The Attended button is disabled until attendee, feedback, result and a
 *      note are all present; filling them enables it.
 *   4. Saving sends both feedback and result to the server.
 *   5. The footer (Cancel + Attended) stays in the document while a dropdown is
 *      open — the menu is portaled out of the scrolling body, so it cannot push
 *      or hide the footer.
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
let posted
let dailyRows

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function row(name, date) {
  return {
    id: `${name}`, name, phone: '9000000001', date, time: '10:00', time_end: '11:00',
    technology: 'Python', interview_round: 'L1', interview_attendee: 'Bhavana',
    interview_attendance_status: '',
  }
}

function mockFetch() {
  calls = []
  posted = []
  dailyRows = [row('Asha Rao', '2026-09-28')]
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (url.pathname.endsWith('/interview-attendance')) {
      posted.push(JSON.parse(init.body))
      return jsonResponse({ status: 'ok' })
    }
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok',
        interviews: { count: 1, attended_count: 0, pending_count: 1 },
        available_months: [{ value: '2026-09', label: 'Sep 2026', count: 1 }],
        booking_overview: { total: 1, by_candidate: [], by_level: [], by_technology: [] },
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

async function renderPanel() {
  render(<DailyOpsPanel />)
  await waitFor(() => expect(calls.length).toBeGreaterThan(0))
  await act(async () => { await Promise.resolve() })
  // Land on Today so the dated roster lists our row.
  fireEvent.click(screen.getByRole('tab', { name: 'Today' }))
  await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())
}

/** Set the row's attendance status to Attended, which opens the modal. */
function openAttendedModal(name = 'Asha Rao') {
  fireEvent.change(screen.getByLabelText(`Attendance for ${name}`), { target: { value: 'attended' } })
}

function submitButton() {
  // The modal footer's submit is labelled with the target status ("Attended").
  // Scope to the dialog's footer so the KPI tab of the same name is excluded.
  const dialog = document.querySelector('.ops-slot-modal')
  return within(dialog).getByRole('button', { name: 'Attended' })
}

function pick(triggerName, optionText) {
  fireEvent.click(screen.getByRole('combobox', { name: triggerName }))
  fireEvent.mouseDown(screen.getByRole('option', { name: optionText }))
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

describe('the feedback control', () => {
  it('is a custom combobox, not a native select', async () => {
    await renderPanel()
    openAttendedModal()

    const feedback = screen.getByRole('combobox', { name: 'Interview feedback' })
    expect(feedback.tagName).not.toBe('SELECT')
    expect(feedback).toHaveAttribute('aria-haspopup', 'listbox')
  })

  it('offers the five grades', async () => {
    await renderPanel()
    openAttendedModal()
    fireEvent.click(screen.getByRole('combobox', { name: 'Interview feedback' }))

    const listbox = screen.getByRole('listbox', { name: 'Interview feedback' })
    expect(within(listbox).getAllByRole('option').map(o => o.textContent.replace('✓', '').trim())).toEqual([
      'Excellent', 'Good', 'Average', 'Needs Improvement', 'Negative',
    ])
  })
})

describe('the result control', () => {
  it('exists as its own dropdown with the four values', async () => {
    await renderPanel()
    openAttendedModal()
    fireEvent.click(screen.getByRole('combobox', { name: 'Interview result' }))

    const listbox = screen.getByRole('listbox', { name: 'Interview result' })
    expect(within(listbox).getAllByRole('option').map(o => o.textContent.replace('✓', '').trim())).toEqual([
      'Awaiting result', 'Next round', 'Selected', 'Rejected',
    ])
  })
})

describe('the Attended button', () => {
  it('is disabled until attendee, feedback, result and a note are all set', async () => {
    await renderPanel()
    openAttendedModal()

    // Attendee defaults to Bhavana, so only feedback, result and note are missing.
    expect(submitButton()).toBeDisabled()

    pick('Interview feedback', 'Good')
    expect(submitButton()).toBeDisabled()

    pick('Interview result', 'Next round')
    expect(submitButton()).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/Note \/ remark/), { target: { value: 'cleared the round' } })
    expect(submitButton()).toBeEnabled()
  })

  it('is dimmed while disabled', async () => {
    await renderPanel()
    openAttendedModal()
    expect(submitButton().className).toContain('cand-btn--disabled')
  })
})

describe('saving', () => {
  it('sends both feedback and result to the server', async () => {
    await renderPanel()
    openAttendedModal()
    pick('Interview feedback', 'Excellent')
    pick('Interview result', 'Selected')
    fireEvent.change(screen.getByLabelText(/Note \/ remark/), { target: { value: 'strong hire' } })

    await act(async () => { fireEvent.click(submitButton()) })

    await waitFor(() => expect(posted.length).toBe(1))
    expect(posted[0]).toMatchObject({
      status: 'attended', feedback: 'excellent', result: 'selected', remark: 'strong hire',
    })
  })
})

describe('the footer', () => {
  it('stays in the document while a dropdown is open', async () => {
    await renderPanel()
    openAttendedModal()

    // Both footer buttons present before opening a dropdown.
    const dialog = document.querySelector('.ops-slot-modal')
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(submitButton()).toBeInTheDocument()

    // Open the feedback dropdown; the menu is portaled to <body>, so the
    // footer buttons must still be exactly where they were.
    fireEvent.click(screen.getByRole('combobox', { name: 'Interview feedback' }))
    expect(screen.getByRole('listbox', { name: 'Interview feedback' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Attended' })).toBeInTheDocument()
    // The menu is not inside the scrolling modal body — it is a child of <body>.
    const menu = screen.getByRole('listbox', { name: 'Interview feedback' })
    expect(dialog.contains(menu)).toBe(false)
  })
})
