/**
 * The "Edit interview slot" modal keeps two notes apart:
 *
 *   1. The editable NOTES input is the slot's own `row.notes`. It must prefill
 *      with whatever note the slot already carries, and save back under `notes`
 *      through PATCH /candidates/interviews/slots/{id} — unchanged by this work.
 *   2. A read-only "Attendance note / feedback" recap shows `interview_feedback`
 *      and `interview_attendance_remark`. Those are written through the
 *      attendance endpoint, so the slot form shows them for context but never
 *      edits or saves them.
 *
 * What this holds the modal to:
 *   - An existing slot note prefills the editable NOTES input, exactly.
 *   - The read-only recap shows the feedback label and the remark text.
 *   - The recap is a different field from the editable input: the recap text is
 *     not an editable control, and editing NOTES leaves the recap untouched.
 *   - Saving sends the (possibly edited) `notes` and never the attendance
 *     feedback/remark.
 *   - A slot with no recorded attendance outcome shows no recap at all.
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
const SLOT_NOTE = 'Bring laptop charger; parking in Lot B'
const REMARK = 'Candidate needs to read docs before L2'
let calls
let patched
let dailyRows

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function mockFetch() {
  calls = []
  patched = []
  dailyRows = [
    {
      id: 'with-note', name: 'Asha Rao', phone: '9000000001', date: '2026-09-28',
      time: '10:00', time_end: '11:00', technology: 'Python', interview_round: 'L1',
      interview_attendee: 'Bhavana', interview_attendance_status: 'attended',
      // The slot's own editable note.
      notes: SLOT_NOTE,
      // The attendance outcome — a separate record, shown read-only.
      interview_feedback: 'needs_improvement',
      interview_attendance_remark: REMARK,
    },
    {
      id: 'no-outcome', name: 'Vikram Nair', phone: '9000000002', date: '2026-09-28',
      time: '12:00', time_end: '13:00', technology: 'Java Backend', interview_round: 'L1',
      interview_attendee: 'Bhavana', interview_attendance_status: '',
      // A slot note but no attendance feedback/remark yet.
      notes: 'Reschedule candidate from last week',
      interview_feedback: '',
      interview_attendance_remark: '',
    },
  ]
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (/\/candidates\/interviews\/slots\//.test(url.pathname) && init?.method === 'PATCH') {
      patched.push(JSON.parse(init.body))
      return jsonResponse({ status: 'ok' })
    }
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok',
        interviews: { count: 2, attended_count: 1, pending_count: 1 },
        available_months: [{ value: '2026-09', label: 'Sep 2026', count: 2 }],
        booking_overview: { total: 2, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (url.pathname.endsWith('/interviews/daily')) {
      return jsonResponse({ status: 'ok', interviews: dailyRows, count: dailyRows.length })
    }
    if (url.pathname.endsWith('/interviews/monitor')) {
      return jsonResponse({ status: 'ok', interviews: [], count: 0, awaiting_interviews: [] })
    }
    if (url.pathname.endsWith('/interviews/filter-options')) {
      return jsonResponse({ status: 'ok', options: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
}

async function renderPanel() {
  render(<DailyOpsPanel />)
  await waitFor(() => expect(calls.length).toBeGreaterThan(0))
  await act(async () => { await Promise.resolve() })
  // Land on Today so the dated roster lists our rows.
  fireEvent.click(screen.getByRole('tab', { name: 'Today' }))
  await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())
}

/** Open the row's Actions menu and click "Edit slot" to open the slot modal. */
function openEditSlot(name) {
  fireEvent.click(screen.getByRole('button', { name: `Actions for ${name}` }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Edit slot' }))
}

function slotDialog() {
  return document.querySelector('.ops-slot-modal')
}

function notesInput() {
  // The editable slot NOTES input is the text field inside the "Notes" label.
  const dialog = slotDialog()
  const label = within(dialog).getByText('Notes').closest('label')
  return label.querySelector('input')
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

describe('the editable slot NOTES field', () => {
  it('prefills with the slot\'s existing saved note, exactly', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')

    expect(slotDialog()).toBeInTheDocument()
    expect(notesInput()).toHaveValue(SLOT_NOTE)
  })

  it('prefills the second row\'s own note, not the first row\'s', async () => {
    await renderPanel()
    openEditSlot('Vikram Nair')

    expect(notesInput()).toHaveValue('Reschedule candidate from last week')
  })
})

describe('the read-only Attendance note / feedback recap', () => {
  it('shows the feedback label and the attendance remark', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')

    const recap = slotDialog().querySelector('.ops-slot-attendance-note')
    expect(recap).not.toBeNull()
    expect(within(recap).getByText('Needs Improvement')).toBeInTheDocument()
    expect(within(recap).getByText(REMARK)).toBeInTheDocument()
    expect(within(recap).getByText(/Read-only here/)).toBeInTheDocument()
  })

  it('is absent when the slot has no attendance feedback or remark', async () => {
    await renderPanel()
    openEditSlot('Vikram Nair')

    expect(slotDialog()).toBeInTheDocument()
    expect(slotDialog().querySelector('.ops-slot-attendance-note')).toBeNull()
  })
})

describe('field separation between the two data models', () => {
  it('renders the remark as static text, not an editable control', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')

    const recap = slotDialog().querySelector('.ops-slot-attendance-note')
    const remarkEl = within(recap).getByText(REMARK)
    // The remark lives in a <p>, not an <input>/<textarea>/<select>.
    expect(remarkEl.tagName).toBe('P')
    expect(recap.querySelector('input, textarea, select')).toBeNull()
    // The editable NOTES input never holds the attendance remark.
    expect(notesInput()).not.toHaveValue(REMARK)
  })

  it('leaves the recap untouched when the editable note is edited', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')

    fireEvent.change(notesInput(), { target: { value: 'Updated slot note' } })

    const recap = slotDialog().querySelector('.ops-slot-attendance-note')
    expect(within(recap).getByText(REMARK)).toBeInTheDocument()
    expect(within(recap).getByText('Needs Improvement')).toBeInTheDocument()
    expect(notesInput()).toHaveValue('Updated slot note')
  })
})

describe('saving the slot', () => {
  it('round-trips an unchanged note under `notes` and sends no attendance fields', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')

    await act(async () => {
      fireEvent.click(within(slotDialog()).getByRole('button', { name: 'Save changes' }))
    })

    await waitFor(() => expect(patched.length).toBe(1))
    expect(patched[0].notes).toBe(SLOT_NOTE)
    expect(patched[0]).not.toHaveProperty('interview_feedback')
    expect(patched[0]).not.toHaveProperty('interview_attendance_remark')
    expect(patched[0]).not.toHaveProperty('remark')
    expect(patched[0]).not.toHaveProperty('feedback')
  })

  it('saves an edited note under `notes`', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')

    fireEvent.change(notesInput(), { target: { value: 'Updated slot note' } })
    await act(async () => {
      fireEvent.click(within(slotDialog()).getByRole('button', { name: 'Save changes' }))
    })

    await waitFor(() => expect(patched.length).toBe(1))
    expect(patched[0].notes).toBe('Updated slot note')
  })
})
