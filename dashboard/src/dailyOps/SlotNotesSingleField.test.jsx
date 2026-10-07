/**
 * The "Edit interview slot" modal shows exactly ONE notes control: the editable
 * slot NOTES field, bound to `row.notes`. The earlier read-only "Attendance
 * note / feedback" recap was removed as duplicative of the roster's own Notes
 * column, so the modal must no longer render it.
 *
 * What this holds the modal to:
 *   - An existing slot note prefills the editable NOTES input, exactly.
 *   - There is no "Attendance note / feedback" recap section in the modal.
 *   - The attendance remark/feedback is NOT shown anywhere in the slot modal,
 *     and is never merged into the editable NOTES value.
 *   - Saving sends `notes` (possibly edited) and never the attendance fields.
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
      // Attendance outcome — a separate record that must NOT appear in the modal.
      interview_feedback: 'needs_improvement',
      interview_attendance_remark: REMARK,
    },
    {
      id: 'no-outcome', name: 'Vikram Nair', phone: '9000000002', date: '2026-09-28',
      time: '12:00', time_end: '13:00', technology: 'Java Backend', interview_round: 'L1',
      interview_attendee: 'Bhavana', interview_attendance_status: '',
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

describe('only one notes control (no attendance recap)', () => {
  it('does not render an Attendance note / feedback recap section', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')

    expect(slotDialog()).toBeInTheDocument()
    expect(slotDialog().querySelector('.ops-slot-attendance-note')).toBeNull()
    expect(within(slotDialog()).queryByText('Attendance note / feedback')).toBeNull()
    expect(within(slotDialog()).queryByText(/Recorded from attendance/)).toBeNull()
  })

  it('does not show the attendance remark or feedback anywhere in the modal', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')

    expect(within(slotDialog()).queryByText(REMARK)).toBeNull()
    expect(within(slotDialog()).queryByText('Needs Improvement')).toBeNull()
    // The editable NOTES input holds only the slot note, never the remark.
    expect(notesInput()).toHaveValue(SLOT_NOTE)
    expect(notesInput()).not.toHaveValue(REMARK)
  })

  it('has exactly one Notes label in the slot form', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')

    expect(within(slotDialog()).getAllByText('Notes')).toHaveLength(1)
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
