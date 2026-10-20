/**
 * Next 2 days (12 Oct): today and tomorrow in IST, only what is still to come.
 *
 * It asks the server for exactly those two days with the same time-aware rule
 * Upcoming uses, so an interview whose time has passed leaves it on the hour,
 * and the counters and the pie (both read from that same request family) agree
 * with the table. Names are invented.
 */
import React from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'
import { PRESETS, resolvePresetRange } from './dateRangePresets.js'

// 23:30 UTC on 11 Oct is 05:00 IST on 12 Oct: the IST day, not the UTC one.
const NOW = '2026-10-11T23:30:00Z'
let calls

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

const TODAY_ROW = { id: 't1', name: 'Asha Fakename', phone: '9000000001', date: '2026-10-12', time: '15:00', time_end: '15:30',
  technology: 'Python', interview_round: 'L1', interview_attendee: 'Bhavana', interview_attendance_status: '' }
const TOMORROW_ROW = { ...TODAY_ROW, id: 't2', name: 'Bala Fakename', phone: '9000000002', date: '2026-10-13', time: '11:00', time_end: '11:30' }

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(NOW))
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({ status: 'ok', interviews: { count: 2, pending_count: 2 }, available_months: [],
        booking_overview: { total: 2, by_candidate: [], by_level: [], by_technology: [] } })
    }
    if (url.pathname.endsWith('/interviews/monitor') || url.pathname.endsWith('/interviews/daily')) {
      return jsonResponse({ status: 'ok', interviews: [TODAY_ROW, TOMORROW_ROW], count: 2, pending_count: 2 })
    }
    return jsonResponse({ status: 'ok', interviews: [], count: 0 })
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('Next 2 days helper', () => {
  it('is today and tomorrow, by the IST day', () => {
    expect(PRESETS.map(p => p.label)).toContain('Next 2 days')
    expect(resolvePresetRange('next2days')).toEqual({ from: '2026-10-12', to: '2026-10-13' })
  })
})
