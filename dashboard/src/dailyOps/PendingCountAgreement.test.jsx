/**
 * One Pending number, wherever it is shown.
 *
 * Six pending rows on screen were reported as 8 by the tab above them and 5 by
 * the sidebar badge, because all three measured different things:
 *
 *   - the table showed rows for the selected day and filters
 *   - the tab read globalStats, a summary over the whole date range that never
 *     sees the client-side status filter
 *   - the sidebar polled /candidates/interviews/upcoming and took
 *     `scheduled_count || pending_count` — and scheduled_count is a slot
 *     phase, every row whose slot has not ended, which is not Pending at all
 *
 * The rows are now the source: the roster tallies what it loaded and publishes
 * that, and both readouts take it.
 */
import fs from 'node:fs'
import path from 'node:path'
import { act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { countStatusRows, emptyStatusCounts } from './interviewStatuses.js'
import { publishPendingWorkChanged } from './PendingWorksProvider.jsx'

const panel = fs.readFileSync(path.join(__dirname, 'DailyOpsPanel.jsx'), 'utf8')
const roster = fs.readFileSync(path.join(__dirname, 'InterviewRoster.jsx'), 'utf8')
const provider = fs.readFileSync(path.join(__dirname, 'PendingWorksProvider.jsx'), 'utf8')

const statusOf = row => (row.interview_attendance_status || '').trim().toLowerCase()

/** Six pending, plus a spread of resolved ones. */
function sixPending() {
  return [
    ...Array.from({ length: 6 }, (_, i) => ({ id: `p${i}`, interview_attendance_status: '' })),
    { id: 'a1', interview_attendance_status: 'attended' },
    { id: 'c1', interview_attendance_status: 'cancelled' },
    { id: 'r1', interview_attendance_status: 're_service' },
  ]
}

describe('the rows are the count', () => {
  it('counts exactly the pending rows', () => {
    expect(countStatusRows(sixPending(), statusOf).pending_count).toBe(6)
  })

  it('counts every row once, and totals to the row count', () => {
    const counts = countStatusRows(sixPending(), statusOf)
    expect(counts.count).toBe(9)
    const byStatus = counts.pending_count + counts.attended_count
      + counts.cancelled_count + counts.re_service_count
      + counts.not_attended_count + counts.rescheduled_count
    expect(byStatus).toBe(9)
  })

  it('does not treat a resolved row as pending', () => {
    const counts = countStatusRows(sixPending(), statusOf)
    expect(counts.attended_count).toBe(1)
    expect(counts.cancelled_count).toBe(1)
    expect(counts.re_service_count).toBe(1)
  })

  it('is zero for an empty roster', () => {
    expect(countStatusRows([], statusOf).pending_count).toBe(0)
  })

  it('survives a payload that is not a list', () => {
    expect(countStatusRows(undefined, statusOf)).toEqual(emptyStatusCounts())
  })

  it('follows the rows when a status changes', () => {
    const rows = sixPending()
    expect(countStatusRows(rows, statusOf).pending_count).toBe(6)
    rows[0].interview_attendance_status = 'attended'
    expect(countStatusRows(rows, statusOf).pending_count).toBe(5)
  })
})

describe('the roster counts what it loaded', () => {
  it('tallies its rows instead of reading the payload totals', () => {
    expect(roster).toContain('countStatusRows(data.interviews || [], resolvedStatus)')
    // The payload reader is no longer how the roster gets its numbers.
    expect(roster).not.toContain('readStatusCounts(data)')
  })

  it('publishes that same number', () => {
    expect(roster).toContain('publishPendingWorkChanged(totalPending)')
  })
})

describe('the top counter reads the roster', () => {
  it('prefers the roster tally over the range summary', () => {
    expect(panel).toContain('const interviews = rosterCounts || globalStats?.interviews || {}')
  })

  it('leaves the range-wide dropdown options on the range summary', () => {
    // Those lists are the whole range's vocabulary, not the day's tally.
    expect(panel).toContain('const rangeInterviews = globalStats?.interviews || {}')
    expect(panel).toContain('(rangeInterviews.by_technology || [])')
    expect(panel).toContain('rangeInterviews.by_candidate')
  })
})

describe('the sidebar reads the same number', () => {
  it('takes the published count directly', () => {
    expect(provider).toMatch(/const published = Number\(event\?\.detail\?\.pendingCount\)/)
    expect(provider).toMatch(/setPendingCount\(Math\.max\(0, published\)\)/)
  })

  it('no longer reports a slot phase as Pending', () => {
    // scheduled_count is every row whose slot has not ended yet.
    expect(provider).not.toContain('data.scheduled_count || data.pending_count')
    expect(provider).toContain('setPendingCount(data.pending_count || 0)')
  })

  it('still refreshes itself when no count comes with the signal', () => {
    expect(provider).toMatch(/reload\(\{ silent: true \}\)/)
  })
})

describe('publishing carries the number', () => {
  it('delivers the count to a listener', async () => {
    const seen = vi.fn()
    const handler = event => seen(event.detail?.pendingCount)
    window.addEventListener('teleautomation:pending-work-changed', handler)
    act(() => publishPendingWorkChanged(6))
    expect(seen).toHaveBeenCalledWith(6)
    window.removeEventListener('teleautomation:pending-work-changed', handler)
  })

  it('sends no count when there is none to send', () => {
    const seen = vi.fn()
    const handler = event => seen(event.detail?.pendingCount)
    window.addEventListener('teleautomation:pending-work-changed', handler)
    act(() => publishPendingWorkChanged())
    expect(seen).toHaveBeenCalledWith(undefined)
    window.removeEventListener('teleautomation:pending-work-changed', handler)
  })

  it('never publishes a negative count', () => {
    const seen = vi.fn()
    const handler = event => seen(event.detail?.pendingCount)
    window.addEventListener('teleautomation:pending-work-changed', handler)
    act(() => publishPendingWorkChanged(-3))
    expect(seen).toHaveBeenCalledWith(0)
    window.removeEventListener('teleautomation:pending-work-changed', handler)
  })

  it('publishes zero so the badge can disappear', () => {
    const seen = vi.fn()
    const handler = event => seen(event.detail?.pendingCount)
    window.addEventListener('teleautomation:pending-work-changed', handler)
    act(() => publishPendingWorkChanged(0))
    expect(seen).toHaveBeenCalledWith(0)
    window.removeEventListener('teleautomation:pending-work-changed', handler)
  })
})
