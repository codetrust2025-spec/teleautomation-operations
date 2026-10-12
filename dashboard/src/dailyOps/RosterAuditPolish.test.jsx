/**
 * Daily Ops roster, 12 Oct audit. Names are invented.
 *
 * - Two standing bookings for one person at one date and start time are
 *   marked on both rows ("Same slot ×2"). On 12 Oct one invite had been
 *   submitted twice and the roster showed two identical rows with nothing to
 *   tell them apart. Nothing is changed: the rows are only marked.
 * - A clickable row answers the pointer, desktop text reads at 12-13px, long
 *   names wrap before they are cut (full text in the tooltip), and a narrow
 *   desktop scrolls the table inside its frame instead of overlapping columns.
 */
import React from 'react'
import { cleanup, render, screen, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

import { InterviewRoster, sameSlotKey } from './InterviewRoster.jsx'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, '..', 'dailyOps.css'), 'utf-8').replace(/\r\n/g, '\n')
const TODAY = '2026-09-15'

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function row(id, name, extra) {
  return {
    id, name, phone: '9000000111', date: TODAY, time: '16:00', time_end: '16:30',
    technology: 'React JS', interview_round: 'L1', interview_company: 'Example Co', interview_attendee: 'Bhavana',
    interview_attendance_status: '', interview_booking_source: 'candidate_booked',
    ...extra,
  }
}

let interviews = []

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/interviews/daily') || url.pathname.endsWith('/interviews/monitor')) {
      return jsonResponse({ status: 'ok', interviews, count: interviews.length, pending_count: interviews.length })
    }
    return jsonResponse({ status: 'ok', attendees: [], rounds: [], technologies: [], candidates: [] })
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const rowsOf = name => screen.getAllByText(name, { selector: 'strong' }).map(el => el.closest('tr'))
const badgeIn = tr => tr.querySelector('.ops-same-slot')

describe('same slot booked twice', () => {
  it('marks both rows of one person booked twice at one date and time, and no other row', async () => {
    interviews = [
      row('a1', 'Asha Fakename'),
      row('a2', 'Asha Fakename', { interview_company: 'Example Co' }),
      row('b1', 'Bala Fakename', { phone: '9000000222' }),                  // same time, another person
      row('c1', 'Chitra Fakename', { phone: '9000000333', time: '16:00' }),
      row('c2', 'Chitra Fakename', { phone: '9000000333', time: '17:00' }),  // same person, another time
    ]
    render(<InterviewRoster />)
    await screen.findAllByText('Asha Fakename', { selector: 'strong' })

    const twins = rowsOf('Asha Fakename')
    expect(twins).toHaveLength(2)
    for (const tr of twins) {
      expect(badgeIn(tr)).not.toBeNull()
      expect(badgeIn(tr).textContent).toBe('Same slot ×2')
      expect(badgeIn(tr).getAttribute('title')).toMatch(/Check that one is not a duplicate/)
    }
    for (const tr of [...rowsOf('Bala Fakename'), ...rowsOf('Chitra Fakename')]) {
      expect(badgeIn(tr)).toBeNull()
    }
  })

  it('does not count a cancelled or moved booking as a second one', async () => {
    interviews = [
      row('a1', 'Asha Fakename'),
      row('a2', 'Asha Fakename', { interview_attendance_status: 'cancelled' }),
      row('a3', 'Asha Fakename', { interview_attendance_status: 'rescheduled' }),
    ]
    render(<InterviewRoster />)
    await screen.findAllByText('Asha Fakename', { selector: 'strong' })
    expect(document.querySelectorAll('.ops-same-slot')).toHaveLength(0)
  })

  it('matches the person by phone, else by name, and the start by clock time', () => {
    expect(sameSlotKey(row('x', 'Asha', { phone: '+91 90000 00111', time: '4:00 PM' })))
      .toBe(sameSlotKey(row('y', 'Asha', { phone: '9000000111', time: '16:00' })))
    expect(sameSlotKey(row('x', 'Asha Fakename', { phone: '' }))).toBe(sameSlotKey(row('y', ' asha fakename ', { phone: '' })))
    expect(sameSlotKey(row('x', 'Asha', { time: '' }))).toBe('')
    expect(sameSlotKey(row('x', 'Asha', { date: '' }))).toBe('')
  })
})

describe('long values', () => {
  it('carry their full text in a tooltip', async () => {
    const long = 'Yamini Venkataramanan Subrahmanyam'
    interviews = [row('a1', long, { interview_company: 'A Very Long Company Name Private Limited' })]
    render(<InterviewRoster />)
    const name = await screen.findByText(long, { selector: 'strong' })
    expect(name.getAttribute('title')).toBe(long)
    const tr = name.closest('tr')
    expect(within(tr).getByText('A Very Long Company Name Private Limited').getAttribute('title')).toBe('A Very Long Company Name Private Limited')
  })
})

describe('the stylesheet', () => {
  const block = marker => {
    const at = CSS.indexOf(marker)
    expect(at, `missing: ${marker}`).toBeGreaterThan(-1)
    return CSS.slice(at, CSS.indexOf('}\n}', at) + 3)
  }

  it('lights a clickable row under the pointer, only where there is a pointer', () => {
    const hover = block('@media (min-width: 901px) and (hover: hover)')
    expect(hover).toMatch(/tr\.ops-interview-row--actionable:hover > td \{ box-shadow: inset/)
    expect(CSS).toMatch(/tr\.ops-interview-row--actionable:active > td \{ box-shadow: inset/)
  })

  it('reads at 12px on a narrow desktop and 13px from 1181px', () => {
    expect(block('@media (min-width: 901px) {\n  .ops-dash-table--v3 thead th')).toMatch(/tbody td \{ font-size: 12px;/)
    expect(block('@media (min-width: 1181px)')).toMatch(/tbody td \{ font-size: 13px; \}/)
  })

  it('keeps the table wide enough on a narrow desktop and scrolls it, but leaves the phone cards alone', () => {
    const desktop = block('@media (min-width: 901px) {\n  /* !important')
    expect(desktop).toMatch(/\.ta-table-responsive--cards \.ops-interview-table\.ops-dash-table--v3 \{ min-width: 940px !important; \}/)
    // The only place the minimum is set: never outside the desktop query.
    expect(CSS.match(/min-width: 940px/g)).toHaveLength(1)
  })

  it('gives Attendance, which holds only a pill, less room than Candidate and Notes', () => {
    const width = col => Number.parseFloat(new RegExp(`col\\.ops-col--${col}\\{width:([^}]+)\\}`).exec(CSS)?.[1])
    expect(width('attendance')).toBeLessThan(width('candidate'))
    expect(width('attendance')).toBeLessThan(width('notes'))
  })
})
