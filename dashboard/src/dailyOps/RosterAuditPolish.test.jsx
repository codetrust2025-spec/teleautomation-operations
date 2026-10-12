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

import { InterviewRoster } from './InterviewRoster.jsx'

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

describe('one interview booked twice', () => {
  // The server decides, by the one rule every booking path uses, and marks the
  // rows of a duplicate with duplicate_count / duplicate_rank. The roster only
  // shows it: a different company, round or invitation at one hour is a
  // genuine second interview and the server leaves it unmarked.
  it('flags every row the server marks as one interview held twice, and no other row', async () => {
    interviews = [
      row('a1', 'Asha Fakename', { duplicate_count: 2, duplicate_rank: 1 }),
      row('a2', 'Asha Fakename', { duplicate_count: 2, duplicate_rank: 2, duplicate_of: 'a1' }),
      row('b1', 'Bala Fakename', { phone: '9000000222' }),
      row('c1', 'Chitra Fakename', { phone: '9000000333', interview_company: 'First Client' }),
      row('c2', 'Chitra Fakename', { phone: '9000000333', interview_company: 'Second Client' }),   // same hour, different interview
    ]
    render(<InterviewRoster />)
    await screen.findAllByText('Asha Fakename', { selector: 'strong' })

    const twins = rowsOf('Asha Fakename')
    expect(twins).toHaveLength(2)
    for (const tr of twins) {
      expect(badgeIn(tr).textContent).toBe('Duplicate ×2')
      expect(badgeIn(tr).getAttribute('title')).toMatch(/booked 2 times \(the same invitation\)/)
    }
    for (const tr of [...rowsOf('Bala Fakename'), ...rowsOf('Chitra Fakename')]) {
      expect(badgeIn(tr)).toBeNull()
    }
  })

  it('shows two genuinely different interviews at one hour as two plain rows', async () => {
    interviews = [
      row('c1', 'Chitra Fakename', { interview_round: 'L1' }),
      row('c2', 'Chitra Fakename', { interview_round: 'HR' }),
    ]
    render(<InterviewRoster />)
    await screen.findAllByText('Chitra Fakename', { selector: 'strong' })
    expect(rowsOf('Chitra Fakename')).toHaveLength(2)
    expect(document.querySelectorAll('.ops-duplicate-booking')).toHaveLength(0)
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
