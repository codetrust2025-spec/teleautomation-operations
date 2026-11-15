/**
 * Daily Ops Status filter (owner's mockup, 15 Nov).
 *
 * - A control of its own between Profile + Round and Filters.
 * - Good, Excellent, Average, Needs Improvement and Negative, several at once
 *   (any of them), each with its count; Clear All; no Apply button.
 * - Every click applies at once to the table and the counters, without a new
 *   request, and other filters keep working alongside it.
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
import { FeedbackStatusFilter } from './FeedbackStatusFilter.jsx'
import { countFeedback, filterByFeedback } from './feedbackScale.js'

const CSS = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'dailyOps.css'), 'utf-8').replace(/\r\n/g, '\n')
const TODAY = '2026-10-06'
let calls

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

// Three Good, one Excellent, one Average, one Negative, one legacy Positive,
// and two still pending (no feedback).
const ROWS = [
  ['Asha Varma', 'good', 'L1'], ['Bala Kumar', 'good', 'L2'], ['Chitra Rao', 'good', 'L1'],
  ['Dev Prasad', 'excellent', 'L2'], ['Esha Pillai', 'average', 'L1'], ['Farah Khan', 'negative', 'HR'],
  ['Gopi Nair', 'positive', 'L1'], ['Hema Das', '', 'L1'], ['Ishan Roy', '', 'L2'],
].map(([name, feedback, round], i) => ({
  id: `r${i}`, name, phone: `90000005${String(i).padStart(2, '0')}`, date: TODAY, time: `${9 + i}:00`,
  time_end: `${9 + i}:30`, technology: 'Java', interview_round: round, interview_attendee: 'Bhavana',
  interview_attendance_status: feedback ? 'attended' : '', interview_feedback: feedback,
  interview_attendance_remark: feedback ? 'went well' : '',
}))

function mockFetch() {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    const rounds = url.searchParams.getAll('round')
    const rows = ROWS.filter(r => !rounds.length || rounds.includes(r.interview_round))
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok',
        interviews: { by_candidate: ROWS.map(r => ({ name: r.name, scheduled: 1 })), by_technology: [] },
        booking_overview: { total: rows.length, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (/\/interviews\/(daily|monitor)$/.test(url.pathname)) {
      return jsonResponse({ status: 'ok', interviews: rows, count: rows.length, awaiting_interviews: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
}

const rosterCalls = () => calls.filter(u => /\/interviews\/(daily|monitor)$/.test(u.pathname))
const shown = () => [...document.querySelectorAll('.ops-interview-table tbody tr')]
  .map(tr => ROWS.find(r => tr.textContent.includes(r.name))?.name).filter(Boolean)
const openMenu = () => fireEvent.click(screen.getByRole('button', { name: /^Status filter/ }))
const group = () => screen.getByRole('group', { name: 'Interview status' })
const box = label => within(group()).getByRole('checkbox', { name: new RegExp(`^${label} \\(`) })
const tick = label => fireEvent.click(box(label))
const options = () => within(group()).getAllByRole('checkbox').map(b => b.getAttribute('aria-label'))
const scheduled = () => screen.getByRole('button', { name: /^Scheduled/ }).textContent.replace(/\D+/g, '')
const attended = () => screen.getByRole('button', { name: /^Attended/ }).textContent.replace(/\D+/g, '')
const pending = () => screen.getByRole('button', { name: /^Pending/ }).textContent.replace(/\D+/g, '')

async function renderPanel() {
  render(<DailyOpsPanel />)
  await waitFor(() => expect(shown()).toContain('Asha Varma'))
  await act(async () => { await Promise.resolve() })
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  mockFetch()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('feedback helpers', () => {
  it('counts each stored value and leaves rows with none out', () => {
    expect(countFeedback(ROWS)).toEqual({ good: 3, excellent: 1, average: 1, negative: 1, positive: 1 })
  })

  it('keeps any of the chosen values, and everything when none is chosen', () => {
    expect(filterByFeedback(ROWS, ['good', 'excellent']).map(r => r.name))
      .toEqual(['Asha Varma', 'Bala Kumar', 'Chitra Rao', 'Dev Prasad'])
    expect(filterByFeedback(ROWS, [])).toHaveLength(ROWS.length)
  })
})

describe('the Status control', () => {
  it('matches the mockup: five statuses with counts, Clear All, and no Apply button', () => {
    render(<FeedbackStatusFilter counts={{ good: 3, excellent: 1 }} />)
    openMenu()
    expect(options()).toEqual(['Good (3)', 'Excellent (1)', 'Average (0)', 'Needs Improvement (0)', 'Negative (0)'])
    const pills = [...document.querySelectorAll('.ops-feedback-filter-pill')]
    expect(pills.map(p => p.className.match(/ops-feedback-pill--(\w+)/)[1]))
      .toEqual(['positive', 'excellent', 'caution', 'warning', 'negative'])
    expect(screen.getByRole('button', { name: 'Clear all status filters' }).textContent).toBe('Clear All')
    expect(screen.queryByRole('button', { name: /^Apply/ })).toBeNull()
  })

  it('centres each badge in its row (the shared pill aligns to the top)', () => {
    // Live 15 Nov: each badge sat 0px from the top of its row, 24px from the bottom.
    expect(CSS).toMatch(/\.ops-feedback-pill\{[^}]*align-self:flex-start/)
    const rule = /\.ops-feedback-filter-menu \.ops-feedback-filter-pill \{([^}]*)\}/.exec(CSS)?.[1]
    expect(rule).toMatch(/align-self:\s*center/)
  })

  it('emits on every click, in the menu order', () => {
    const onChange = vi.fn()
    const { rerender } = render(<FeedbackStatusFilter selected={[]} onChange={onChange} />)
    openMenu()
    tick('Negative')
    expect(onChange).toHaveBeenLastCalledWith(['negative'])
    rerender(<FeedbackStatusFilter selected={['negative']} onChange={onChange} />)
    tick('Good')
    expect(onChange).toHaveBeenLastCalledWith(['good', 'negative'])
    rerender(<FeedbackStatusFilter selected={['good', 'negative']} onChange={onChange} />)
    tick('Good')
    expect(onChange).toHaveBeenLastCalledWith(['negative'])
    fireEvent.click(screen.getByRole('button', { name: 'Clear all status filters' }))
    expect(onChange).toHaveBeenLastCalledWith([])
  })

  it('shows how many are ticked on the button', () => {
    render(<FeedbackStatusFilter selected={['good', 'excellent']} />)
    expect(screen.getByRole('button', { name: 'Status filter (2 selected)' }).textContent).toContain('(2)')
  })

  it('lists a stored value off the scale (legacy Positive) while rows carry it', () => {
    render(<FeedbackStatusFilter counts={{ good: 1, positive: 2 }} />)
    openMenu()
    expect(options().at(-1)).toBe('Positive (2)')
  })
})

describe('on the Daily Ops page', () => {
  it('sits between Profile + Round and Filters', async () => {
    await renderPanel()
    const triggers = [...document.querySelectorAll('.ops-roster-controls button')].map(b => b.getAttribute('aria-label') || b.textContent.trim())
    const profile = triggers.findIndex(t => /^Profile and round/.test(t))
    const status = triggers.findIndex(t => /^Status filter/.test(t))
    const filters = triggers.findIndex(t => /^Filters/.test(t))
    expect(profile).toBeGreaterThan(-1)
    expect(status).toBe(profile + 1)
    expect(filters).toBeGreaterThan(status)
  })

  it('counts what the period holds', async () => {
    await renderPanel()
    openMenu()
    expect(options()).toEqual(['Good (3)', 'Excellent (1)', 'Average (1)', 'Needs Improvement (0)', 'Negative (1)', 'Positive (1)'])
  })

  it('applies every click to the table and the counters at once, with no new request', async () => {
    await renderPanel()
    expect(scheduled()).toBe('9')
    const before = rosterCalls().length
    openMenu()

    tick('Good')
    expect(shown()).toEqual(['Asha Varma', 'Bala Kumar', 'Chitra Rao'])
    await waitFor(() => expect(scheduled()).toBe('3'))
    expect(attended()).toBe('3')
    expect(pending()).toBe('0')

    tick('Excellent')
    expect(shown()).toEqual(['Asha Varma', 'Bala Kumar', 'Chitra Rao', 'Dev Prasad'])
    await waitFor(() => expect(scheduled()).toBe('4'))
    // The menu's counts are the period's, not the selection's.
    expect(options().slice(0, 2)).toEqual(['Good (3)', 'Excellent (1)'])

    tick('Good')
    expect(shown()).toEqual(['Dev Prasad'])
    await waitFor(() => expect(scheduled()).toBe('1'))

    fireEvent.click(screen.getByRole('button', { name: 'Clear all status filters' }))
    expect(shown()).toHaveLength(ROWS.length)
    await waitFor(() => expect(scheduled()).toBe('9'))
    expect(rosterCalls().length).toBe(before)
  })

  it('says so when nothing matches', async () => {
    await renderPanel()
    openMenu()
    tick('Needs Improvement')
    expect(shown()).toEqual([])
    expect(screen.getByText(/Nothing matches status Needs Improvement/)).toBeInTheDocument()
  })

  it('works alongside Profile + Round: rounds go to the server, status narrows what comes back', async () => {
    await renderPanel()
    openMenu()
    tick('Good')
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(screen.getByRole('button', { name: /^Profile and round/ }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Interview rounds' })).getByRole('checkbox', { name: 'L1' }))
    await waitFor(() => expect(rosterCalls().at(-1).searchParams.getAll('round')).toEqual(['L1']))
    await waitFor(() => expect(shown()).toEqual(['Asha Varma', 'Chitra Rao']))
    await waitFor(() => expect(scheduled()).toBe('2'))
    // The Status menu now counts the L1 rows.
    fireEvent.keyDown(document, { key: 'Escape' })
    openMenu()
    expect(options().slice(0, 3)).toEqual(['Good (2)', 'Excellent (0)', 'Average (1)'])
  })

  it('does not tell the sidebar there is nothing pending when a status is ticked', async () => {
    await renderPanel()
    const seen = []
    const listener = e => seen.push(e.detail?.pendingCount)
    window.addEventListener('teleautomation:pending-work-changed', listener)
    openMenu()
    tick('Good')
    await act(async () => { await Promise.resolve() })
    window.removeEventListener('teleautomation:pending-work-changed', listener)
    expect(seen).not.toContain(0)
  })

  it('is cleared by the page Reset, which counts it', async () => {
    await renderPanel()
    openMenu()
    tick('Good')
    tick('Negative')
    fireEvent.keyDown(document, { key: 'Escape' })
    const reset = screen.getByRole('button', { name: /reset/i })
    expect(reset.textContent).toContain('1')
    fireEvent.click(reset)
    await waitFor(() => expect(shown()).toHaveLength(ROWS.length))
    expect(screen.getByRole('button', { name: 'Status filter' })).toBeInTheDocument()
  })
})
