/**
 * Daily Ops desktop polish, second pass.
 *
 * - The status counters and the pill beside them both said "pending": "5
 *   Pending" counts interviews in the current view with no status, "12
 *   pending" counted open candidate tasks across the whole roster. The pill now
 *   says "Candidate tasks" and explains itself; each counter names its scope.
 * - Every Screenshot cell is the same centred box: a thumbnail, a file that
 *   will not load, or a dash when there is none.
 * - The Actions cell is a table cell again (it was display:flex, which took it
 *   out of the row), right after a narrower Notes column.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))
const pending = { works: [], count: 12, candidateCount: 9, loading: false, error: '' }
vi.mock('./PendingWorksProvider.jsx', async (importOriginal) => ({
  ...(await importOriginal()),
  usePendingWorksContext: () => pending,
}))

import { DailyOpsPanel, kpiScope } from './DailyOpsPanel.jsx'
import { PendingWorksStrip } from './PendingWorksStrip.jsx'
import { STATUS_TABS } from './interviewStatuses.js'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, '..', 'dailyOps.css'), 'utf-8').replace(/\r\n/g, '\n')
const TODAY = '2026-10-06'

/** The body of the laptop/desktop block this pass added. */
const DESKTOP = (() => {
  const at = CSS.indexOf('@media (min-width: 768px) {\n  .ops-dash-table--v3 td.ops-dash-attend-cell')
  if (at < 0) return ''
  let depth = 0
  for (let i = CSS.indexOf('{', at); i < CSS.length; i += 1) {
    if (CSS[i] === '{') depth += 1
    if (CSS[i] === '}') { depth -= 1; if (depth === 0) return CSS.slice(at, i + 1) }
  }
  return ''
})()
const rule = selector => {
  const at = DESKTOP.indexOf(`${selector} {`)
  return at < 0 ? '' : DESKTOP.slice(at, DESKTOP.indexOf('}', at))
}

const ROWS = [
  { id: 'a', name: 'Asha Rao', phone: '9000000001', date: TODAY, time: '10:00', time_end: '10:30', technology: 'Python',
    interview_round: 'L1', interview_attendee: 'Bhavana', interview_attendance_status: '', interview_booking_source: 'candidate_booked',
    slot_screenshot_proof: { url: '/files/a.png' } },
  { id: 'b', name: 'Vikram Devi', phone: '9000000002', date: TODAY, time: '12:00', time_end: '12:30', technology: 'Java',
    interview_round: 'L2', interview_attendee: 'Bhavana', interview_attendance_status: 'attended', interview_booking_source: 'ai_auto_booked' },
]

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({ status: 'ok', interviews: { count: 2, pending_count: 1, attended_count: 1 }, available_months: [], booking_overview: { total: 2, by_candidate: [], by_level: [], by_technology: [] } })
    }
    if (/\/interviews\/(daily|monitor)$/.test(url.pathname)) {
      return jsonResponse({ status: 'ok', interviews: ROWS, count: ROWS.length, awaiting_interviews: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function renderPanel() {
  render(<DailyOpsPanel />)
  await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())
  await act(async () => { await Promise.resolve() })
}

describe('the two "pending" counts say what they count', () => {
  it('calls the pill Candidate tasks and keeps "pending" out of it', () => {
    render(<PendingWorksStrip compact onOpenCandidates={() => {}} />)
    const pill = screen.getByRole('button', { name: /^Candidate tasks:/ })
    expect(pill.textContent).toBe('Candidate tasks12')
    expect(pill.textContent.toLowerCase()).not.toContain('pending')
    expect(pill.getAttribute('aria-label')).toBe('Candidate tasks: 12 open across 9 candidates. Opens Candidates.')
    expect(pill.getAttribute('title')).toMatch(/across 9 candidates, not limited to this view/)
  })

  it('gives every status counter its scope', async () => {
    await renderPanel()
    const pendingTab = STATUS_TABS.find(tab => tab.filterValue === 'pending')
    expect(kpiScope(pendingTab)).toBe('Interviews in the current view with no status recorded yet')
    expect(screen.getByRole('button', { name: /^Pending/ })).toHaveAttribute('title', kpiScope(pendingTab))
    expect(screen.getByRole('button', { name: /^Scheduled/ })).toHaveAttribute('title', 'Every interview in the current view')
    expect(screen.getByRole('button', { name: /^Attended/ })).toHaveAttribute('title', 'Interviews in the current view marked Attended')
  })
})

describe('the Screenshot column', () => {
  it('shows a dash, still named, when there is no screenshot', async () => {
    await renderPanel()
    const empty = screen.getByText('Vikram Devi').closest('tr').querySelector('td[data-label="Screenshot"] .ops-slot-shot-empty')
    expect(empty.textContent).toBe('—')
    expect(empty).toHaveAttribute('aria-label', 'No booking screenshot')
  })

  it('draws every screenshot cell as one centred box of one size', () => {
    expect(DESKTOP, 'the desktop block is missing').not.toBe('')
    expect(rule('.ops-dash-table--v3 td.ops-slot-shot-cell')).toMatch(/text-align:\s*center/)
    const thumb = rule('.ops-dash-table--v3 .ops-slot-shot-thumb')
    expect(thumb).toMatch(/width:\s*60px/)
    expect(thumb).toMatch(/height:\s*34px/)
    expect(rule('.ops-dash-table--v3 .ops-slot-shot-empty')).toMatch(/width:\s*60px/)
  })
})

describe('Notes and Actions', () => {
  it('keeps the Actions cell a table cell, so it stays part of its row', () => {
    const cell = rule('.ops-dash-table--v3 td.ops-dash-attend-cell')
    expect(cell).toMatch(/display:\s*table-cell/)
    expect(cell).toMatch(/vertical-align:\s*middle/)
    expect(cell).toMatch(/text-align:\s*left/)
  })

  it('narrows Notes so the menu sits close to the row it belongs to', () => {
    const width = col => new RegExp(`col\\.ops-col--${col}\\{width:([^}]+)\\}`).exec(CSS)?.[1]
    expect(width('notes')).toBe('10%')
    expect(Number.parseFloat(width('notes'))).toBeLessThan(Number.parseFloat(width('candidate')))
  })

  it('applies none of this to phones, which lay rows out as cards', () => {
    expect(CSS).toContain('@media (min-width: 768px) {\n  .ops-dash-table--v3 td.ops-dash-attend-cell')
  })
})
