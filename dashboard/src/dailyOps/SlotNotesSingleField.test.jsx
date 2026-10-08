/**
 * Edit interview slot — the single NOTES field is the interview's attendance
 * remark (the exact text the roster NOTES column shows), not a separate slot
 * note. What this holds the modal to:
 *   - For a row with an attendance outcome, NOTES prefills with
 *     `interview_attendance_remark` exactly, and is editable.
 *   - Saving an edited note sends ONLY the remark through the attendance
 *     endpoint, re-using the row's current status; it never sends feedback or
 *     a slot `notes` field, so feedback/attendee are preserved server-side.
 *   - `interview_feedback` is shown read-only and never merged into the remark.
 *   - For a pending row (no outcome), the NOTES field is disabled.
 *   - An empty stored remark prefills blank (never shows stale text).
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
const REMARK = 'Candidate needs to read docs before L2'
let calls
let slotPatches
let attendancePosts
let dailyRows

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function mockFetch() {
  calls = []
  slotPatches = []
  attendancePosts = []
  dailyRows = [
    {
      id: 'with-note', name: 'Asha Rao', phone: '9000000001', date: '2026-09-28',
      time: '10:00', time_end: '11:00', technology: 'Python', interview_round: 'L1',
      interview_attendee: 'Bhavana',
      interview_attendance_status: 'attended',
      interview_attendance_status_resolved: 'attended',
      interview_feedback: 'needs_improvement',
      interview_attendance_remark: REMARK,
    },
    {
      id: 'no-outcome', name: 'Vikram Nair', phone: '9000000002', date: '2026-09-28',
      time: '12:00', time_end: '13:00', technology: 'Java Backend', interview_round: 'L1',
      interview_attendee: 'Bhavana',
      interview_attendance_status: '',
      interview_attendance_status_resolved: '',
      interview_feedback: '',
      interview_attendance_remark: '',
    },
  ]
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (/\/candidates\/interviews\/slots\//.test(url.pathname) && init?.method === 'PATCH') {
      slotPatches.push(JSON.parse(init.body))
      return jsonResponse({ status: 'ok' })
    }
    if (/\/candidates\/[^/]+\/interview-attendance$/.test(url.pathname) && init?.method === 'POST') {
      attendancePosts.push(JSON.parse(init.body))
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

function openEditSlot(name) {
  fireEvent.click(screen.getByRole('button', { name: `Actions for ${name}` }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Edit slot' }))
}

function slotDialog() {
  return document.querySelector('.ops-slot-modal')
}

function notesInput() {
  return within(slotDialog()).getByLabelText('Notes')
}

function saveButton() {
  return within(slotDialog()).getByRole('button', { name: 'Save changes' })
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

describe('NOTES prefills from the attendance remark', () => {
  it('shows the exact remark text for a row with an outcome', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')
    expect(slotDialog()).toBeInTheDocument()
    expect(notesInput()).toHaveValue(REMARK)
    expect(notesInput()).toBeEnabled()
  })

  it('is a single NOTES control (no duplicate slot-notes box)', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')
    expect(within(slotDialog()).getAllByText('Notes')).toHaveLength(1)
  })

  it('shows feedback read-only and never inside the editable NOTES value', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')
    // Feedback label is present as a read-only pill, not an input.
    expect(within(slotDialog()).getByText('Needs Improvement')).toBeInTheDocument()
    expect(notesInput().tagName).toBe('INPUT')
    expect(notesInput()).not.toHaveValue('needs_improvement')
    expect(notesInput()).not.toHaveValue('Needs Improvement')
  })
})

describe('pending row (no attendance outcome)', () => {
  it('disables the NOTES field', async () => {
    await renderPanel()
    openEditSlot('Vikram Nair')
    expect(notesInput()).toBeDisabled()
    // No stale text — the empty remark prefills blank, not any slot note.
    expect(notesInput()).toHaveValue('')
  })
})

describe('saving an edited note', () => {
  it('sends only the remark via the attendance endpoint, reusing current status, with no feedback', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')
    fireEvent.change(notesInput(), { target: { value: 'Cleared L2; strong on APIs' } })
    await act(async () => { fireEvent.click(saveButton()) })

    await waitFor(() => expect(attendancePosts.length).toBe(1))
    expect(attendancePosts[0]).toMatchObject({ status: 'attended', remark: 'Cleared L2; strong on APIs' })
    expect(attendancePosts[0]).not.toHaveProperty('feedback')
    expect(attendancePosts[0]).not.toHaveProperty('attendee')
    // The slot PATCH carries slot fields only — never a `notes` field.
    const lastPatch = slotPatches[slotPatches.length - 1]
    expect(lastPatch).not.toHaveProperty('notes')
    expect(lastPatch).not.toHaveProperty('noteRemark')
  })

  it('does not call the attendance endpoint when the note is unchanged', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')
    await act(async () => { fireEvent.click(saveButton()) })
    // Slot PATCH still happens (date/time/round/tech), but no remark write.
    await waitFor(() => expect(slotPatches.length).toBeGreaterThan(0))
    expect(attendancePosts.length).toBe(0)
  })
})

describe('NOTES focus behavior (no auto select-all)', () => {
  it('collapses the caret to the end on focus rather than selecting the whole value', async () => {
    await renderPanel()
    openEditSlot('Asha Rao')
    const input = notesInput()
    expect(input).toHaveValue(REMARK)

    // Simulate the whole value being selected (what the browser did before the fix)...
    input.setSelectionRange(0, REMARK.length)
    // ...then focus should collapse the selection to the end (caret at end).
    fireEvent.focus(input)

    expect(input.selectionStart).toBe(REMARK.length)
    expect(input.selectionEnd).toBe(REMARK.length)
    // Nothing is selected, and the value is preserved exactly.
    expect(input.selectionEnd - input.selectionStart).toBe(0)
    expect(input).toHaveValue(REMARK)
  })
})
