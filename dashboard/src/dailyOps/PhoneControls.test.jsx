/**
 * Daily Ops on an iPhone (owner, 16 Nov): Status is a section of Profile +
 * Round instead of a button of its own, and the controls sit in three even
 * rows (search · Reset · Refresh / date · Today / Profile + Round · Filters).
 * Wider screens keep the separate Status button. Names are invented.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'

const CSS = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'dailyOps.css'), 'utf-8').replace(/\r\n/g, '\n')
const TODAY = '2026-10-06'

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

const ROWS = [
  ['Asha Varma', 'good', 'L1'], ['Bala Kumar', 'good', 'L2'], ['Dev Prasad', 'excellent', 'L2'],
  ['Esha Pillai', 'average', 'L1'], ['Hema Das', '', 'L1'],
].map(([name, feedback, round], i) => ({
  id: `r${i}`, name, phone: `90000006${String(i).padStart(2, '0')}`, date: TODAY, time: `${9 + i}:00`,
  time_end: `${9 + i}:30`, technology: 'Java', interview_round: round, interview_attendee: 'Bhavana',
  interview_attendance_status: feedback ? 'attended' : '', interview_feedback: feedback,
  interview_attendance_remark: feedback ? 'went well' : '',
}))

function setPhone(phone) {
  window.matchMedia = query => ({
    matches: phone && query === '(max-width: 767px)', media: query,
    addEventListener() {}, removeEventListener() {},
  })
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    const rounds = url.searchParams.getAll('round')
    const rows = ROWS.filter(r => !rounds.length || rounds.includes(r.interview_round))
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({ status: 'ok', interviews: { by_candidate: [], by_technology: [] },
        booking_overview: { total: rows.length, by_candidate: [], by_level: [], by_technology: [] } })
    }
    if (/\/interviews\/(daily|monitor)$/.test(url.pathname)) {
      return jsonResponse({ status: 'ok', interviews: rows, count: rows.length, awaiting_interviews: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  delete window.matchMedia
})

const shown = () => [...document.querySelectorAll('.ops-interview-table tbody tr')]
  .map(tr => ROWS.find(r => tr.textContent.includes(r.name))?.name).filter(Boolean)

async function renderPanel() {
  render(<DailyOpsPanel />)
  await waitFor(() => expect(shown()).toContain('Asha Varma'))
  await act(async () => { await Promise.resolve() })
}

describe('on a phone', () => {
  beforeEach(() => setPhone(true))

  it('has no Status button: Status is a section of Profile + Round', async () => {
    await renderPanel()
    expect(screen.queryByRole('button', { name: /^Status filter/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Profile, round and status/ }))
    const status = screen.getByRole('group', { name: 'Interview status' })
    expect(within(status).getAllByRole('checkbox').map(b => b.getAttribute('aria-label')).slice(0, 5))
      .toEqual(['Good (2)', 'Excellent (1)', 'Average (1)', 'Needs Improvement (0)', 'Negative (0)'])
    // Rounds are still there, in the same menu.
    expect(screen.getByRole('group', { name: 'Interview rounds' })).toBeTruthy()
  })

  it('applies a status at once, counts it on the button, and Clear All clears it with the rest', async () => {
    await renderPanel()
    fireEvent.click(screen.getByRole('button', { name: /^Profile, round and status/ }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Interview status' })).getByRole('checkbox', { name: /^Good/ }))
    expect(shown()).toEqual(['Asha Varma', 'Bala Kumar'])
    expect(screen.getByRole('button', { name: 'Profile, round and status (1 selected)' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Clear all profile, round and status filters' }))
    expect(shown()).toHaveLength(ROWS.length)
    expect(screen.getByRole('button', { name: 'Profile, round and status' })).toBeTruthy()
  })

  it('uses a search placeholder that fits beside Reset and Refresh', async () => {
    await renderPanel()
    expect(screen.getByLabelText('Candidate search').getAttribute('placeholder')).toBe('Search name or phone')
  })
})

describe('on wider screens', () => {
  beforeEach(() => setPhone(false))

  it('keeps the separate Status button and a Profile + Round menu without it', async () => {
    await renderPanel()
    expect(screen.getByRole('button', { name: /^Status filter/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /^Profile and round/ }))
    expect(screen.queryByRole('group', { name: 'Interview status' })).toBeNull()
    expect(screen.getByLabelText('Candidate search').getAttribute('placeholder')).toBe('Search candidate or phone...')
  })
})

describe('the phone layout rules', () => {
  // The 16 Nov block, up to the 24 Nov mockup block that follows it.
  const block = CSS.slice(CSS.indexOf('/* ── iPhone controls (owner, 16 Nov)'), CSS.indexOf('/* ── Daily Ops on an iPhone: the owner'))

  it('sit last in the stylesheet, inside the phone breakpoint only', () => {
    expect(block.length).toBeGreaterThan(0)
    expect(block).toContain('@media (max-width: 767px) {')
    expect(block.match(/@media[^{]*\{/g)).toEqual(['@media (max-width: 767px) {', '@media (max-width: 360px) {'])
  })

  it('give every control the same 40px height and 8px gaps, in three rows', () => {
    expect(block).toMatch(/\.ops-roster-search \{[^}]*height: 40px/)
    expect(block).toMatch(/gap: 8px; padding: 10px/)
    expect(block).toMatch(/\.ops-filter-trigger \{[^}]*height: 40px/)
    expect(block).toMatch(/order: 2; flex: 1 1 calc\(50% - 4px\)/)
    expect(block).toMatch(/order: 3; flex: 1 1 calc\(50% - 4px\)/)
    expect(block).toMatch(/font-size: 16px/)
  })
})
