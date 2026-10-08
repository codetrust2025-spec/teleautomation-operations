/**
 * The whole interview row is an actions trigger, not just the kebab.
 *
 * Clicking anywhere on a row opens that exact row's Actions menu (single "Edit"
 * item that opens the existing slot modal). The kebab still works. Interactive
 * controls inside the row — the attendance select, the screenshot View button,
 * links/buttons/inputs — keep their own behaviour and must NOT open the row
 * menu. Only one row's menu is open at a time; an outside click closes it and
 * never opens another.
 */
import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  const interviews = [row('Row A Candidate'), row('Row B Candidate')]
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

function kebabFor(name) {
  return screen.getByRole('button', { name: `Actions for ${name}` })
}
function rowEl(name) {
  // The row is labelled for screen readers; grab the <tr> via that label.
  return screen.getByText(name).closest('tr')
}

describe('whole-row actions trigger', () => {
  it('1. clicking the row opens that row\'s menu', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.click(rowEl('Row A Candidate'))

    const items = screen.getAllByRole('menuitem')
    expect(items).toHaveLength(1)
    expect(items[0].textContent).toBe('Edit')
    expect(kebabFor('Row A Candidate').getAttribute('aria-expanded')).toBe('true')
  })

  it('2. clicking a different row switches the menu to it', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.click(rowEl('Row A Candidate'))
    expect(kebabFor('Row A Candidate').getAttribute('aria-expanded')).toBe('true')

    fireEvent.mouseDown(rowEl('Row B Candidate'))
    fireEvent.click(rowEl('Row B Candidate'))

    expect(screen.getAllByRole('menuitem')).toHaveLength(1)
    expect(kebabFor('Row A Candidate').getAttribute('aria-expanded')).toBe('false')
    expect(kebabFor('Row B Candidate').getAttribute('aria-expanded')).toBe('true')
  })

  it('3. clicking the kebab still opens the menu', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.click(kebabFor('Row A Candidate'))
    expect(screen.getAllByRole('menuitem')).toHaveLength(1)
    expect(kebabFor('Row A Candidate').getAttribute('aria-expanded')).toBe('true')
  })

  it('4. clicking outside closes the menu', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.click(rowEl('Row A Candidate'))
    expect(screen.getByRole('menuitem')).toBeTruthy()

    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('menuitem')).toBeNull()
  })

  it('5. clicking outside never opens a menu', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.mouseDown(document.body)
    fireEvent.click(document.body)
    expect(screen.queryByRole('menuitem')).toBeNull()
  })

  it('6. clicking the Attendance select does NOT open the row menu', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    const select = screen.getByRole('combobox', { name: 'Attendance for Row A Candidate' })
    fireEvent.click(select)
    expect(screen.queryByRole('menuitem')).toBeNull()
    expect(kebabFor('Row A Candidate').getAttribute('aria-expanded')).toBe('false')
  })

  it('7. clicking the Screenshot View button does NOT open the row menu', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    // The screenshot thumb is a <button> labelled "View" inside row A.
    const rowA = rowEl('Row A Candidate')
    const shot = within(rowA).getByRole('button', { name: 'View' })
    fireEvent.click(shot)
    expect(screen.queryByRole('menuitem')).toBeNull()
    expect(kebabFor('Row A Candidate').getAttribute('aria-expanded')).toBe('false')
  })

  it('8. clicking a button/control inside the row does NOT open the row menu', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    // A generic interactive descendant: the screenshot "View" <button>. The
    // row-activation path must be suppressed because closest('button') matches.
    const rowA = rowEl('Row A Candidate')
    const innerButton = within(rowA).getByRole('button', { name: 'View' })
    fireEvent.click(innerButton)
    expect(screen.queryByRole('menuitem')).toBeNull()
  })

  it('9. only one row menu is open at a time', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.click(rowEl('Row A Candidate'))
    expect(screen.getAllByRole('menuitem')).toHaveLength(1)

    fireEvent.mouseDown(rowEl('Row B Candidate'))
    fireEvent.click(rowEl('Row B Candidate'))
    expect(screen.getAllByRole('menuitem')).toHaveLength(1)
  })

  it('10. Edit from a row-opened menu opens the existing slot modal', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.click(rowEl('Row A Candidate'))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit' }))

    expect(await screen.findByText('Edit interview slot')).toBeTruthy()
    expect(screen.getByText('Technology *')).toBeTruthy()
  })

  it('11. Remove slot remains available from the modal opened via the row', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.click(rowEl('Row A Candidate'))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit' }))
    await screen.findByText('Edit interview slot')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove slot' })).toBeTruthy())
  })

  it('keyboard: Enter on a focused row opens its menu', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.keyDown(rowEl('Row A Candidate'), { key: 'Enter' })
    expect(screen.getAllByRole('menuitem')).toHaveLength(1)
    expect(kebabFor('Row A Candidate').getAttribute('aria-expanded')).toBe('true')
  })
})
