/**
 * The Daily Ops roster refreshes every five seconds while someone can see it.
 *
 * A tab left open in the background used to keep asking too, all day, for a
 * table nobody was looking at. Hidden, it waits; shown again, it catches up at
 * once, so what an operator sees is never older than it was before.
 * Names are invented.
 */
import React from 'react'
import { act, cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))
vi.mock('./PendingWorksStrip.jsx', () => ({ PendingWorksStrip: () => null }))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'

let rosterReads
let hidden

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-10-12T06:00:00Z'))
  rosterReads = 0
  hidden = false
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/interviews/monitor') || url.pathname.endsWith('/interviews/daily')) {
      rosterReads += 1
      return jsonResponse({ status: 'ok', interviews: [{ id: 'r1', name: 'Asha Fakename', date: '2026-10-12', time: '15:00' }], count: 1, pending_count: 1 })
    }
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({ status: 'ok', interviews: { count: 1, pending_count: 1 }, available_months: [],
        booking_overview: { total: 1, by_candidate: [], by_level: [], by_technology: [] } })
    }
    return jsonResponse({ status: 'ok', interviews: [], count: 0 })
  }))
})

afterEach(() => {
  cleanup()
  delete document.hidden
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('roster refresh', () => {
  it('keeps refreshing while the tab is visible', async () => {
    render(<DailyOpsPanel />)
    await waitFor(() => expect(rosterReads).toBeGreaterThan(0))
    const start = rosterReads
    await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
    expect(rosterReads - start).toBeGreaterThanOrEqual(3)
  })

  it('stops while the tab is hidden and catches up when it is shown', async () => {
    render(<DailyOpsPanel />)
    await waitFor(() => expect(rosterReads).toBeGreaterThan(0))
    await act(async () => { await vi.advanceTimersByTimeAsync(100) })
    hidden = true
    const whileHidden = rosterReads
    await act(async () => { await vi.advanceTimersByTimeAsync(60000) })
    expect(rosterReads).toBe(whileHidden)

    hidden = false
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
    await waitFor(() => expect(rosterReads).toBeGreaterThan(whileHidden))
  })
})
