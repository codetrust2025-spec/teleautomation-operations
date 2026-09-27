/**
 * Daily Ops on a phone (measured at 440px — iPhone 16 Pro Max).
 *
 * Six things were wrong on the card layout and none of them on a desktop, so
 * every fix has to sit inside the phone breakpoint and nothing above it may
 * move. Two kinds of check, because they prove different things:
 *
 *  - the markup ones drive the real panel: a card's labels and the text in it
 *    are rendered, not styled, so jsdom can see them;
 *  - the stylesheet ones read the sheet, because jsdom performs no layout and
 *    a rendered height check here would pass whatever the rules said. What
 *    they assert is not "it looks right" but "this rule exists and cannot
 *    reach a desktop", which is the part a test can hold on to.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { captureLayout } from '../test/captureLayout.js'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))
vi.mock('./PendingWorksStrip.jsx', () => ({ PendingWorksStrip: () => null }))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, '..', 'dailyOps.css'), 'utf-8')
const ROSTER = readFileSync(join(here, 'InterviewRoster.jsx'), 'utf-8')

/** The phone breakpoint this work was done in. 440px is well inside it. */
const PHONE = 767

/**
 * Every rule in the sheet tagged with the `@media` prelude it sits in, and a
 * declaration lookup over them. The sheet nests one level deep.
 */
function rules() {
  const css = CSS.replace(/\/\*[\s\S]*?\*\//g, '')
  const found = []
  let media = null
  let prelude = ''
  for (let i = 0; i < css.length; i += 1) {
    const ch = css[i]
    if (ch === '{') {
      const head = prelude.trim()
      prelude = ''
      if (head.startsWith('@')) {
        media = head
      } else {
        const end = css.indexOf('}', i)
        found.push({ media, selector: head, body: css.slice(i + 1, end).trim() })
        i = end
      }
    } else if (ch === '}') {
      media = null
      prelude = ''
    } else {
      prelude += ch
    }
  }
  return found
}

const ALL = rules()

/** Does this prelude stop at the phone breakpoint or below? */
const phoneOnly = media => {
  const max = /max-width:\s*(\d+)px/.exec(media || '')
  return Boolean(max) && Number(max[1]) <= PHONE
}

/** Rules for `selector` that declare `property`, with where each one applies. */
const declaring = (selector, property) => ALL.filter(
  rule => rule.selector.includes(selector) && new RegExp(`(^|[;\\s])${property}\\s*:`).test(rule.body),
)

const TODAY = '2026-09-28'
let calls

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function row(overrides = {}) {
  return {
    id: 'r1', name: 'Asha Rao', phone: '9000000001', date: TODAY, time: '10:00', time_end: '11:00',
    technology: 'Python', interview_attendee: 'Bhavana', interview_attendance_status: '',
    interview_booking_source: 'ai_auto_booked', ...overrides,
  }
}

function mockFetch(rows) {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok', interviews: {}, available_months: [],
        booking_overview: { total: 0, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (/\/interviews\/(daily|monitor)$/.test(url.pathname)) {
      return jsonResponse({ status: 'ok', interviews: rows, count: rows.length, awaiting_interviews: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
}

async function renderPanel() {
  const view = render(<DailyOpsPanel />)
  await waitFor(() => expect(calls.length).toBeGreaterThan(0))
  await act(async () => { await Promise.resolve() })
  return view
}

const cell = label => document.querySelector(`.ops-interview-row td[data-label="${label}"]`)

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  mockFetch([row()])
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the status reads once', () => {
  it('leaves the dropdown as the only place the status is written', async () => {
    await renderPanel()
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())

    // Both are in the markup; the phone hides the pill, which is the one that
    // cannot be acted on. Stacked under the dropdown it read "Pending" twice.
    expect(screen.getByLabelText('Attendance for Asha Rao').value).toBe('')
    const hidden = declaring('.ops-interview-attendance-form .ops-status-pill', 'display')
    expect(hidden.length, 'no rule hides the duplicate pill').toBeGreaterThan(0)
    for (const rule of hidden) {
      expect(rule.body).toMatch(/display\s*:\s*none/)
      expect(phoneOnly(rule.media), `${rule.media} reaches a desktop`).toBe(true)
    }
  })
})

describe('the card labels say what the control does', () => {
  it('calls the status column Status on a card and Attendance in the table head', async () => {
    await renderPanel()
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())

    // The card label is the cell's data-label; the desktop table head is
    // untouched, so a laptop still reads exactly what it read before.
    expect(cell('Status')).not.toBeNull()
    expect(cell('Attendance')).toBeNull()
    expect(screen.getByRole('columnheader', { name: 'Attendance' })).toBeInTheDocument()
  })

  it('names a missing round the way the booking page does', async () => {
    mockFetch([row({ interview_round: '' })])
    await renderPanel()
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())

    expect(cell('Round').textContent).toBe('Round not specified')
    expect(ROSTER).not.toContain('Not specified in email')
  })
})

describe('the date control goes when it cannot do anything', () => {
  it('marks it not applicable under All unresolved, and only then', async () => {
    await renderPanel()
    const dateControl = () => document.querySelector('.ops-roster-control--date')
    expect(dateControl().className).not.toContain('not-applicable')

    fireEvent.click(screen.getByRole('tab', { name: 'All unresolved' }))
    await act(async () => { await Promise.resolve() })
    expect(dateControl().className).toContain('ops-roster-control--not-applicable')

    fireEvent.click(screen.getByRole('tab', { name: 'Today' }))
    await act(async () => { await Promise.resolve() })
    expect(dateControl().className).not.toContain('not-applicable')
  })

  it('hides it on a phone only', () => {
    const hidden = declaring('.ops-roster-control--not-applicable', 'display')
    expect(hidden.length, 'the marker class must hide the control somewhere').toBeGreaterThan(0)
    for (const rule of hidden) {
      expect(rule.body).toMatch(/display\s*:\s*none/)
      expect(phoneOnly(rule.media), `${rule.media} reaches a desktop`).toBe(true)
    }
  })
})

describe('the counters scroll instead of colliding', () => {
  it('gives the strip a sideways scroll on a phone', () => {
    const scrolls = declaring('.ops-topbar__kpis', 'overflow-x')
    expect(scrolls.length, 'eight counters cannot share 440px without one').toBeGreaterThan(0)
    for (const rule of scrolls) {
      expect(rule.body).toMatch(/overflow-x\s*:\s*auto/)
      expect(phoneOnly(rule.media), `${rule.media} reaches a desktop`).toBe(true)
    }
  })

  it('stops the cards being squeezed, without touching the 56px floor', () => {
    const chip = ALL.filter(r => r.selector.includes('.ops-topbar__kpis .ops-dash-kpi') && phoneOnly(r.media))
    const held = chip.find(r => /flex\s*:\s*0\s+0\s+auto/.test(r.body))
    expect(held, 'the cards have to keep their width for a scroll to mean anything').toBeTruthy()
    // That floor belongs to the 900px block; a later min-width here would
    // outrank it at every width below 900 and squash the strip again.
    for (const rule of chip) expect(rule.body).not.toMatch(/min-width\s*:/)
  })

  it('keeps a thumb-sized target', () => {
    const tall = ALL.filter(
      r => phoneOnly(r.media) && /min-height\s*:\s*44px/.test(r.body),
    ).map(r => r.selector)
    expect(tall).toEqual(expect.arrayContaining([
      expect.stringContaining('.ops-topbar__kpis .ops-dash-kpi'),
      expect.stringContaining('.ops-attendance-select'),
    ]))
  })
})

describe('what it all looks like laid out', () => {
  it('captures the unresolved view for the layout harness', async () => {
    // A no-op unless LAYOUT_CAPTURE_DIR is set. With it:
    //   LAYOUT_CAPTURE_DIR=../.layout-harness/captures npx vitest run src/dailyOps/MobileDailyOps.test.jsx
    //   python scripts/layout_harness.py .layout-harness/captures/daily-ops-unresolved-phone.html
    // then `await window.layoutReport()` measures overflow and clipped text at
    // every width at once — 440px (this phone) and 1440px (the laptop) included.
    // Invented names, sized like the real ones: a short, a medium and one
    // long enough to have been clipped by the desktop table's ellipsis.
    mockFetch([
      row({ id: 'a', name: 'Asha Rao', date: '2026-05-27', time: '14:45', interview_round: 'L1' }),
      row({ id: 'b', name: 'Vikram Devi', date: '2026-06-19', time: '13:00', interview_round: '',
            technology: 'Oracle Fusion (Tech Con)' }),
      row({ id: 'c', name: 'Nandini Balasubramanian', date: '2026-09-24', time: '16:00',
            interview_round: 'Final', interview_attendance_remark: 'Panel asked to move it to the afternoon slot.' }),
    ])
    await renderPanel()
    fireEvent.click(screen.getByRole('tab', { name: 'All unresolved' }))
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())

    expect(document.querySelectorAll('.ops-interview-row')).toHaveLength(3)
    captureLayout('daily-ops-unresolved-phone')
  })
})

describe('the desktop layout is left alone', () => {
  // The phone work is appended at the end of the sheet, which is also what
  // lets it win over the 900px and 600px blocks at equal specificity. The
  // first rule of that block is the pill it hides.
  const blockStart = ALL.findIndex(
    rule => rule.selector.trim() === '.ops-interview-attendance-form .ops-status-pill',
  )

  it('sits in one block at the end of the sheet', () => {
    expect(blockStart, 'the phone block must be findable to be checked').toBeGreaterThan(-1)
    expect(ALL.length - blockStart, 'the whole block is these rules').toBeGreaterThan(5)
  })

  it('scopes every rule in that block to a phone', () => {
    // Written flat, any one of them would outrank the desktop design at every
    // width — which is how a laptop gets changed by a phone fix.
    for (const rule of ALL.slice(blockStart)) {
      expect(
        `${rule.media || 'no media'} { ${rule.selector.trim()} }`,
        'this would reach a desktop',
      ).toSatisfy(() => phoneOnly(rule.media))
    }
  })

  it('never names the two new selectors outside a phone width', () => {
    // These exist only for the phone, so no desktop rule may mention them.
    for (const selector of ['.ops-interview-attendance-form .ops-status-pill',
                            '.ops-roster-control--not-applicable']) {
      for (const rule of ALL.filter(r => r.selector.includes(selector))) {
        expect(phoneOnly(rule.media), `${selector} styled at ${rule.media}`).toBe(true)
      }
    }
  })
})
