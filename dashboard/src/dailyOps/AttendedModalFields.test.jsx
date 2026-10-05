/**
 * The "Mark as Attended" modal: dark custom dropdowns, a gated button and a
 * footer that stays put.
 *
 * The required fields are Attendee, Interview feedback and Note / remark —
 * there is no Interview result field (it was removed). What this holds the
 * modal to:
 *   1. Feedback is the app's dark custom dropdown (a combobox + listbox), not a
 *      native <select> whose OS popup overlapped the Note field and footer, and
 *      it offers the five grades.
 *   2. There is NO Interview result control anywhere in the modal.
 *   3. The Attended button is disabled until attendee, feedback and a note are
 *      all present; filling them enables it.
 *   4. Saving sends feedback (and never a result) to the server.
 *   5. The footer (Cancel + Attended) stays in the document while a dropdown is
 *      open — the menu is portaled out of the scrolling body, so it cannot push
 *      or hide the footer — and the menu is sized to its options, not the whole
 *      viewport, so it covers the Note field no more than necessary.
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

describe('the removed Interview result field', () => {
  it('is nowhere in the modal', async () => {
    await renderPanel()
    openAttendedModal()

    expect(screen.queryByRole('combobox', { name: 'Interview result' })).toBeNull()
    expect(screen.queryByText('Interview result')).toBeNull()
  })

  it('leaves exactly two dropdowns in the modal: attendee and feedback', async () => {
    await renderPanel()
    openAttendedModal()

    const dialog = document.querySelector('.ops-slot-modal')
    const comboboxes = within(dialog).getAllByRole('combobox').map(c => c.getAttribute('aria-label'))
    expect(comboboxes).toEqual([
      'Attendee (who attended the interview?)', 'Interview feedback',
    ])
  })
})

describe('the Attended button', () => {
  it('is disabled until attendee, feedback and a note are all set', async () => {
    await renderPanel()
    openAttendedModal()

    // Attendee defaults to Bhavana, so only feedback and note are missing.
    expect(submitButton()).toBeDisabled()

    pick('Interview feedback', 'Good')
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
  it('sends feedback and never a result to the server', async () => {
    await renderPanel()
    openAttendedModal()
    pick('Interview feedback', 'Excellent')
    fireEvent.change(screen.getByLabelText(/Note \/ remark/), { target: { value: 'strong hire' } })

    await act(async () => { fireEvent.click(submitButton()) })

    await waitFor(() => expect(posted.length).toBe(1))
    expect(posted[0]).toMatchObject({
      status: 'attended', feedback: 'excellent', remark: 'strong hire',
    })
    expect(posted[0]).not.toHaveProperty('result')
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

  it('opens a menu sized to its options, not the whole viewport', async () => {
    await renderPanel()
    openAttendedModal()
    fireEvent.click(screen.getByRole('combobox', { name: 'Interview feedback' }))

    const menu = screen.getByRole('listbox', { name: 'Interview feedback' })
    // Five options at ~36px plus padding is well under 240px; the compact cap
    // keeps the menu from spanning the viewport and burying the Note/footer.
    const cap = parseFloat(menu.style.maxHeight)
    expect(cap).toBeGreaterThan(0)
    expect(cap).toBeLessThanOrEqual(240)
  })
})
