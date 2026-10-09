/**
 * The slot kebab is one action, and it only opens when you ask it to.
 *
 * In production the three-item menu ('Edit attendee' / 'Edit slot' /
 * 'Remove slot') would appear on clicks elsewhere on screen because the
 * open-state was local to each row with no propagation guard. The menu now
 * shows a single 'Edit' item, its open-state is lifted to the roster keyed by
 * row id (single-open-at-a-time), and a stray click can only ever close it.
 * 'Edit' opens the existing Edit interview slot modal; removal moved into that
 * modal's footer so it is never a dropdown item.
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
    interview_attendance_status: '', interview_booking_source: 'ai_auto_booked', ...extra,
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

describe('slot actions menu', () => {
  it('a. kebab click opens exactly one "Edit" action', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.click(kebabFor('Row A Candidate'))

    const items = screen.getAllByRole('menuitem')
    expect(items).toHaveLength(1)
    expect(items[0].textContent).toBe('Edit')
  })

  it('b. clicking outside closes the menu', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.click(kebabFor('Row A Candidate'))
    expect(screen.getByRole('menuitem')).toBeTruthy()

    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('menuitem')).toBeNull()
  })

  it('c. clicking elsewhere does NOT open the menu (regression for the reported bug)', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    // No kebab was clicked — a stray click must never conjure a menu.
    fireEvent.mouseDown(document.body)
    fireEvent.click(document.body)
    expect(screen.queryByRole('menuitem')).toBeNull()

    // Open, close via an outside click, then a stray click must not reopen it.
    fireEvent.click(kebabFor('Row A Candidate'))
    expect(screen.getByRole('menuitem')).toBeTruthy()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('menuitem')).toBeNull()
    fireEvent.click(document.body)
    expect(screen.queryByRole('menuitem')).toBeNull()
  })

  it('d. clicking "Edit" opens the existing Edit interview slot modal', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.click(kebabFor('Row A Candidate'))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit' }))

    // The SAME existing slot-mode modal: its heading and the required
    // Technology field prove it, not a new/duplicate edit UI.
    expect(await screen.findByText('Edit interview slot')).toBeTruthy()
    expect(screen.getByText('Technology *')).toBeTruthy()
  })

  it('e. only one row\'s menu is open at a time', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.click(kebabFor('Row A Candidate'))
    expect(screen.getAllByRole('menuitem')).toHaveLength(1)

    fireEvent.click(kebabFor('Row B Candidate'))
    // Row A's menu closed; exactly one menu remains and it belongs to row B.
    const items = screen.getAllByRole('menuitem')
    expect(items).toHaveLength(1)
    expect(kebabFor('Row A Candidate').getAttribute('aria-expanded')).toBe('false')
    expect(kebabFor('Row B Candidate').getAttribute('aria-expanded')).toBe('true')
  })

  it('f. the old 3-item menu is gone; removal is reachable from the edit modal', async () => {
    render(<InterviewRoster />)
    await screen.findByText('Row A Candidate')

    fireEvent.click(kebabFor('Row A Candidate'))
    const menu = screen.getByRole('menu')
    expect(within(menu).queryByText('Edit attendee')).toBeNull()
    expect(within(menu).queryByText('Edit slot')).toBeNull()
    expect(within(menu).queryByText('Remove slot')).toBeNull()
    expect(within(menu).getAllByRole('menuitem')).toHaveLength(1)

    // Removal still exists — now inside the Edit interview slot modal footer.
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Edit' }))
    await screen.findByText('Edit interview slot')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove slot' })).toBeTruthy())
  })
})
