/**
 * Daily Ops on a desktop: one screenshot frame for every row.
 *
 * An image, a file that will not load, and no screenshot at all share one
 * 60x34 frame, centred the same way, and the image is pinned to the frame
 * (cover, anchored at the top) so its own proportions or a slow load never
 * change the box. Phones keep their own thumbnail size. Company shows only a
 * stored value, "—" otherwise -- nothing is guessed for an old booking.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))
vi.mock('./PendingWorksStrip.jsx', () => ({ PendingWorksStrip: () => null }))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, '..', 'dailyOps.css'), 'utf-8').replace(/\r\n/g, '\n')

const TODAY = '2026-10-06'
const base = {
  date: TODAY, time_end: '10:30', technology: 'Java', interview_round: 'L1',
  interview_attendee: 'Bhavana', interview_attendance_status: '', interview_booking_source: 'candidate_booked',
}
const ROWS = [
  { ...base, id: 'p', name: 'Portrait Person', phone: '9000000031', time: '10:00', slot_screenshot_proof: { url: '/files/tall.png' } },
  { ...base, id: 'w', name: 'Wide Person', phone: '9000000032', time: '11:00', slot_screenshot_proof: { url: '/files/wide.png' } },
  { ...base, id: 'n', name: 'None Person', phone: '9000000033', time: '12:00',
    // Fields that mention a company somewhere -- none of them may be shown as one.
    email: 'none.person@infosys.example', interview_feedback: 'Wipro panel was late', interview_role: 'Java Developer' },
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
      return jsonResponse({
        status: 'ok',
        interviews: { count: 3, pending_count: 3, by_candidate: ROWS.map(r => ({ name: r.name, scheduled: 1 })), by_technology: [{ name: 'Java' }] },
        available_months: [], booking_overview: { total: 3, by_candidate: [], by_level: [], by_technology: [] },
      })
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
  await waitFor(() => expect(screen.getByText('Portrait Person')).toBeInTheDocument())
  await act(async () => { await Promise.resolve() })
}
const shotCell = name => screen.getByText(name).closest('tr').querySelector('td.ops-slot-shot-cell')

function desktopBlock() {
  const at = CSS.indexOf('/* One 60x34 frame for all three states')
  expect(at).toBeGreaterThan(-1)
  const media = CSS.lastIndexOf('@media', at)
  expect(CSS.slice(media, CSS.indexOf('{', media))).toBe('@media (min-width: 768px) ')
  return CSS.slice(at, CSS.indexOf('\n}\n', at))
}

describe('the screenshot cell', () => {
  it('renders the same frame for every image, whatever its file', async () => {
    await renderPanel()
    for (const name of ['Portrait Person', 'Wide Person']) {
      const frame = shotCell(name).firstElementChild
      expect(frame.className).toBe('ops-slot-shot-thumb')
      expect(frame.querySelector('img')).not.toBeNull()
    }
  })

  it('keeps a file that will not load in the same frame, as an icon', async () => {
    await renderPanel()
    fireEvent.error(shotCell('Wide Person').querySelector('img'))
    const frame = shotCell('Wide Person').firstElementChild
    expect(frame.className).toContain('ops-slot-shot-thumb--missing')
    expect(frame.querySelector('img')).toBeNull()
    expect(frame.querySelector('svg.ta-icon--image-off')).not.toBeNull()
  })

  it('shows "—" in the frame when there is no screenshot', async () => {
    await renderPanel()
    const empty = shotCell('None Person').firstElementChild
    expect(empty.className).toBe('ops-slot-shot-empty')
    expect(empty.textContent).toBe('—')
  })
})

describe('desktop CSS', () => {
  it('gives the image, the missing file and the empty cell one 60x34 frame', () => {
    const block = desktopBlock()
    expect(block).toMatch(/\.ops-slot-shot-thumb,\n\s*\.ops-dash-table--v3 \.ops-slot-shot-empty \{[^}]*width: 60px; height: 34px;/)
    expect(block).toMatch(/box-sizing: border-box; flex: none;/)
  })

  it('pins the image to the frame and crops every one the same way', () => {
    const img = desktopBlock().match(/\.ops-slot-shot-thumb img \{([^}]*)\}/)[1]
    expect(img).toMatch(/position: absolute; inset: 0;/)
    expect(img).toMatch(/width: 100%; height: 100%; max-width: none; max-height: none;/)
    expect(img).toMatch(/object-fit: cover; object-position: center top;/)
  })

  it('leaves the phone thumbnail as it was', () => {
    expect(CSS).toMatch(/\.ops-slot-shot-cell \.ops-slot-shot-thumb \{\s*width: 64px;\s*height: 40px;/)
  })
})

describe('Company', () => {
  it('is "—" when none was stored, never taken from an email, a note or the role', async () => {
    await renderPanel()
    const cell = screen.getByText('None Person').closest('tr').querySelector('td[data-label="Company"]')
    expect(cell.querySelector('.ops-interview-company__none').textContent).toBe('—')
    expect(cell.querySelector('.ops-interview-company__name')).toBeNull()
    expect(cell.textContent).not.toMatch(/infosys|wipro/i)
  })
})
