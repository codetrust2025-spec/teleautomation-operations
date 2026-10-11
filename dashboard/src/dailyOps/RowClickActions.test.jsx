/**
 * Daily Ops: a row click opens its Edit dialog directly (12 Oct).
 *
 * The three-dot Actions button, its column and the one-item menu between the
 * row and Edit are gone. Every control inside a row keeps its own behaviour
 * without opening Edit: the screenshot View, the attendance select, any
 * button. The Edit dialog itself -- fields, Save, Cancel, Remove slot -- is
 * the existing one. Names are invented.
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

import { InterviewRoster } from './InterviewRoster.jsx'

const TODAY = '2026-09-15'

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function row(name, extra) {
  return {
    id: name, name, phone: '9000000001', date: TODAY, time: '19:05', time_end: '20:05',
    technology: 'Automation', interview_round: 'L1', interview_attendee: 'Bhavana',
    interview_attendance_status: '', interview_booking_source: 'ai_auto_booked',
    slot_screenshot_proof: { url: '/proof/' + name + '.png', original_name: name + '.png' },
    ...extra,
  }
}

let interviews = []

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  interviews = [row('Row A Candidate'), row('Row B Candidate')]
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/interviews/daily') || url.pathname.endsWith('/interviews/monitor')) {
      return jsonResponse({ status: 'ok', interviews, count: interviews.length, pending_count: 2 })
    }
    if (url.pathname.endsWith('/interviews/filter-options')) {
      return jsonResponse({ status: 'ok', attendees: [], rounds: [], technologies: [], candidates: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function rowEl(name) {
  return screen.getByText(name).closest('tr')
}
const editOpen = () => screen.queryByText('Edit interview slot')

describe('a row opens its Edit dialog', () => {
  it('opens Edit directly when the row is clicked, with no menu in between', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')
    fireEvent.click(rowEl('Row A Candidate').querySelector('td[data-label="Date"]'))
    expect(await screen.findByText('Edit interview slot')).toBeTruthy()
    expect(screen.getByText('Technology *')).toBeTruthy()
    expect(screen.queryByRole('menuitem')).toBeNull()
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('has no three-dot button, no Actions column and no menu', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')
    expect(screen.queryByRole('button', { name: /Actions for/ })).toBeNull()
    expect(document.querySelector('.ops-row-menu, .ops-row-menu__trigger, col.ops-col--actions')).toBeNull()
    expect([...document.querySelectorAll('thead th')].map(th => th.textContent)).not.toContain('Actions')
    expect(rowEl('Row A Candidate').getAttribute('aria-haspopup')).toBe('dialog')
  })

  it('opens the clicked row, not another one', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row B Candidate')
    fireEvent.click(rowEl('Row B Candidate').querySelector('td[data-label="Time"]'))
    await screen.findByText('Edit interview slot')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(editOpen()).toBeNull())
    fireEvent.click(rowEl('Row A Candidate').querySelector('td[data-label="Candidate"]'))
    expect(await screen.findByText('Edit interview slot')).toBeTruthy()
  })

  it('shows attendance read-only on the row; it is changed in Edit', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')
    const cell = rowEl('Row A Candidate').querySelector('td[data-label="Status"]')
    expect(cell.querySelector('select')).toBeNull()
    fireEvent.click(cell.querySelector('.ops-attendance-status'))
    await screen.findByText('Edit interview slot')
    expect(screen.getByLabelText('Attendance').value).toBe('')
  })

  it('leaves the screenshot View button to itself: it opens the screenshot, not Edit', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')
    fireEvent.click(within(rowEl('Row A Candidate')).getByRole('button', { name: 'View' }))
    await waitFor(() => expect(document.querySelector('.ops-slot-shot-modal')).not.toBeNull())
    expect(editOpen()).toBeNull()
  })

  it('does not open while text is being selected in the row', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')
    const spy = vi.spyOn(window, 'getSelection').mockReturnValue({ toString: () => 'Row A' })
    fireEvent.click(rowEl('Row A Candidate').querySelector('td[data-label="Candidate"]'))
    expect(editOpen()).toBeNull()
    spy.mockRestore()
  })

  it('does not stack a second dialog when the open one is clicked', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')
    fireEvent.click(rowEl('Row A Candidate').querySelector('td[data-label="Date"]'))
    const title = await screen.findByText('Edit interview slot')
    fireEvent.click(title)
    expect(screen.getAllByText('Edit interview slot')).toHaveLength(1)
  })

  it('Cancel closes it without saving anything', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')
    const before = fetch.mock.calls.length
    fireEvent.click(rowEl('Row A Candidate').querySelector('td[data-label="Date"]'))
    await screen.findByText('Edit interview slot')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(editOpen()).toBeNull())
    const writes = fetch.mock.calls.slice(before).filter(([, init]) => init && init.method && init.method !== 'GET')
    expect(writes).toEqual([])
  })

  it('keeps Remove slot in the dialog', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')
    fireEvent.click(rowEl('Row A Candidate').querySelector('td[data-label="Date"]'))
    await screen.findByText('Edit interview slot')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove slot' })).toBeTruthy())
  })

  it('opens from the keyboard: Enter on the focused row, not on a control inside it', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')
    fireEvent.keyDown(within(rowEl('Row A Candidate')).getByRole('button', { name: 'View' }), { key: 'Enter' })
    expect(editOpen()).toBeNull()
    fireEvent.keyDown(rowEl('Row A Candidate'), { key: 'Enter' })
    expect(await screen.findByText('Edit interview slot')).toBeTruthy()
  })
})

describe('attendance is set in the same Edit dialog, with one Save', () => {
  const writes = () => fetch.mock.calls
    .filter(([, init]) => init && init.method && init.method !== 'GET')
    .map(([url, init]) => [init.method, decodeURIComponent(new URL(String(url), 'http://localhost').pathname), init.body ? JSON.parse(init.body) : null])

  async function openA() {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')
    fireEvent.click(rowEl('Row A Candidate').querySelector('td[data-label="Date"]'))
    await screen.findByText('Edit interview slot')
  }

  it('a new outcome alone sends only the attendance request -- the slot is not rewritten', async () => {
    await openA()
    fireEvent.change(screen.getByLabelText('Attendance'), { target: { value: 'cancelled' } })
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()   // a note is required
    fireEvent.change(screen.getByRole('textbox', { name: 'Notes' }), { target: { value: 'Client called it off' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save changes' })) })
    await waitFor(() => expect(writes().map(([m, path]) => `${m} ${path}`)).toEqual([
      'POST /candidates/Row A Candidate/interview-attendance',
    ]))
    expect(writes()[0][2]).toEqual({ status: 'cancelled', remark: 'Client called it off', feedback: '' })
  })

  it('a moved slot and a new outcome save together: the slot first, then the outcome', async () => {
    await openA()
    fireEvent.change(screen.getByLabelText('Start time'), { target: { value: '18:30' } })
    fireEvent.change(screen.getByLabelText('Attendance'), { target: { value: 'not_attended' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Notes' }), { target: { value: 'Did not join' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save changes' })) })
    await waitFor(() => expect(writes().map(([m, path]) => `${m} ${path}`)).toEqual([
      'PATCH /candidates/interviews/slots/Row A Candidate',
      'POST /candidates/Row A Candidate/interview-attendance',
    ]))
    expect(writes()[0][2]).toEqual({ date: TODAY, time: '18:30', time_end: '20:05', interview_round: 'L1', technology: 'Automation' })
    expect(writes()[1][2]).toEqual({ status: 'not_attended', remark: 'Did not join', attendee: 'Bhavana', feedback: '' })
  })

  it('an outcome on a row with no end time is not held up by the slot fields', async () => {
    interviews = [row('Row A Candidate', { time_end: '' })]
    await openA()
    expect(screen.getByLabelText('End time')).toBeRequired()          // a slot edit still needs it
    fireEvent.change(screen.getByLabelText('Attendance'), { target: { value: 'cancelled' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Notes' }), { target: { value: 'Postponed by the client' } })
    expect(screen.getByLabelText('End time')).not.toBeRequired()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save changes' })) })
    await waitFor(() => expect(writes().map(([m]) => m)).toEqual(['POST']))
  })

  it('asks for feedback and a note before Attended saves, and sends who attended', async () => {
    await openA()
    fireEvent.change(screen.getByLabelText('Attendance'), { target: { value: 'attended' } })
    expect(screen.getByRole('combobox', { name: 'Attendee (who attended the interview?)' })).toBeTruthy()
    const save = screen.getByRole('button', { name: 'Save changes' })
    expect(save).toBeDisabled()
    fireEvent.click(screen.getByRole('combobox', { name: 'Interview feedback' }))
    fireEvent.mouseDown(screen.getByRole('option', { name: 'Good' }))
    expect(save).toBeDisabled()
    fireEvent.change(screen.getByRole('textbox', { name: 'Notes' }), { target: { value: 'Went well' } })
    expect(save).toBeEnabled()
    await act(async () => { fireEvent.click(save) })
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0][2]).toEqual({ status: 'attended', remark: 'Went well', attendee: 'Bhavana', feedback: 'good' })
  })

  it('a slot edit with the status left as it was sends only the slot', async () => {
    await openA()
    fireEvent.change(screen.getByLabelText('Date'), { target: { value: '2026-09-16' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save changes' })) })
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0][0]).toBe('PATCH')
    expect(writes()[0][2]).not.toHaveProperty('notes')
  })

  it('Save with nothing changed writes nothing and closes', async () => {
    await openA()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save changes' })) })
    await waitFor(() => expect(document.querySelector('.ops-slot-modal')).toBeNull())
    expect(writes()).toEqual([])
  })
})
