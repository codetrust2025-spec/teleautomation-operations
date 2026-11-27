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
  it('leaves the read-only label as the only place the status is written', async () => {
    await renderPanel()
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())

    // The label alone says the status (12 Oct: it is changed in Edit, not on
    // the row). The pill that once sat under the old dropdown is not rendered;
    // the phone rule that hid it stays as a guard.
    const cell = screen.getByText('Asha Rao').closest('tr').querySelector('td[data-label="Attendance"]')
    expect(cell.textContent.trim()).toBe('Pending')
    expect(cell.querySelector('select')).toBeNull()
    expect(document.querySelector('.ops-interview-attendance-form .ops-status-pill')).toBeNull()
    const hidden = declaring('.ops-interview-attendance-form .ops-status-pill', 'display')
    expect(hidden.length, 'no rule hides the duplicate pill').toBeGreaterThan(0)
    for (const rule of hidden) {
      expect(rule.body).toMatch(/display\s*:\s*none/)
      expect(phoneOnly(rule.media), `${rule.media} reaches a desktop`).toBe(true)
    }
  })
})

describe('the card labels say what the control does', () => {
  it('labels each card cell as its column head does: Attendance, and Status for the feedback', async () => {
    await renderPanel()
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())

    // 15 Nov: Notes became Status, so the attendance cell (whose card caption
    // is hidden anyway) is labelled Attendance, as its column head says.
    expect(cell('Attendance').querySelector('.ops-attendance-status')).not.toBeNull()
    expect(cell('Status').classList.contains('ops-interview-status-cell')).toBe(true)
    expect(cell('Notes')).toBeNull()
    expect(screen.getByRole('columnheader', { name: 'Attendance' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Status' })).toBeInTheDocument()
  })

  it('names a missing round the way the booking page does', async () => {
    mockFetch([row({ interview_round: '' })])
    await renderPanel()
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())

    expect(cell('Round').textContent).toBe('Needs Review')
    expect(ROSTER).not.toContain('Round not specified')
    expect(ROSTER).not.toContain('Not specified in email')
  })
})


describe('all status counters wrap without horizontal scrolling on a phone', () => {
  it('uses a responsive grid with a 56px track floor and readable wrapping labels', () => {
    const grid = ALL.filter(r => r.selector === '.daily-ops-page--dashboard .ops-topbar__kpis' && phoneOnly(r.media))
    expect(grid).toHaveLength(2)
    expect(grid[0].body).toMatch(/display\s*:\s*grid/)
    expect(grid[0].body).toContain('repeat(4, minmax(56px, 1fr))')
    expect(grid[0].body).toMatch(/overflow\s*:\s*visible/)
    expect(grid[1].body).toContain('repeat(3, minmax(56px, 1fr))')
    expect(grid[1].media).toContain('360px')
    const chip = ALL.find(r => r.selector === '.daily-ops-page--dashboard .ops-topbar__kpis .ops-dash-kpi')
    expect(phoneOnly(chip.media)).toBe(true)
    expect(chip.body).toMatch(/min-width\s*:\s*0/)
    expect(chip.body).toMatch(/min-height\s*:\s*48px/)
    const label = ALL.find(r => r.selector === '.daily-ops-page--dashboard .ops-topbar__kpis .ops-dash-kpi__label')
    expect(label.body).toMatch(/white-space\s*:\s*normal/)
  })

  it('keeps all nine status filters available with accessible labels', async () => {
    await renderPanel()
    for (const name of ['Scheduled', 'Pending', 'Attended', 'Not attended', 'Technical issue', 'Cancelled', 'Rescheduled', 'Awaiting new slot', 'Re-Service']) {
      const button = screen.getByRole('button', { name: new RegExp('^' + name + '\\s', 'i') })
      expect(button).toBeInTheDocument()
      expect(button).toHaveAttribute('aria-pressed')
    }
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
            interview_round: 'Final', interview_attendance_remark: 'Panel asked to move it to the afternoon slot.',
            interview_booking_source: 'candidate_booked', interview_feedback: 'positive',
            slot_screenshot_proof: { url: '/media/slot-shots/example.png', original_name: 'invite.png' } }),
    ])
    await renderPanel()
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())

    expect(document.querySelectorAll('.ops-interview-row')).toHaveLength(3)
    captureLayout('daily-ops-unresolved-phone')
  })
})

describe('the period tabs scroll rather than being cut off', () => {
  it('lets the strip shrink, which is what turns the overflow into a scroll', () => {
    // Measured at 390px before the fix: the period group sat at the width of
    // its five tabs (404px) inside a 354px box that clips, so All unresolved
    // was cut off by 43px. A flex item does not shrink below its content
    // unless it is told it may.
    const shrink = declaring('.ops-roster-control-group--period', 'min-width')
    expect(shrink.length, 'nothing lets the period group shrink').toBeGreaterThan(0)
    for (const rule of shrink) {
      expect(rule.body).toMatch(/min-width\s*:\s*0/)
      expect(phoneOnly(rule.media), `${rule.media} reaches a desktop`).toBe(true)
    }
  })

  it('shows all five tabs in one row on an iPhone, with nothing past the edge', () => {
    // The scrolling strip still hid "All unresolved" past the edge at 390px,
    // with no visible scrollbar to say it was there. On a phone the strip is
    // one row of five content-sized columns: a label stays on one line
    // wherever it fits (all five at 390-430px, measured) and wraps only
    // where it cannot. Equal specificity, so the last phone rule for the
    // strip is the one a phone gets.
    const strip = ALL.filter(r => r.selector.includes('.ops-roster-controls .ops-date-range__presets') && phoneOnly(r.media))
    const winner = strip.at(-1)
    expect(winner, 'nothing lays the strip out on a phone').toBeTruthy()
    expect(winner.body).toMatch(/display\s*:\s*grid/)
    const columns = /grid-template-columns\s*:\s*([^;]+)/.exec(winner.body)
    expect(columns, 'the grid needs its columns').toBeTruthy()
    expect(columns[1].trim(), 'five columns, each the width of its label while it fits').toBe('repeat(5, auto)')
    expect(winner.body).toMatch(/overflow\s*:\s*visible/)
    // A tab may wrap its label but must not be split mid-word.
    const tab = ALL.filter(r => r.selector.includes('.ops-roster-controls .ops-date-range__preset') &&
      !r.selector.includes('presets') && !r.selector.includes('--') && phoneOnly(r.media)).at(-1)
    expect(tab.body).toMatch(/min-width\s*:\s*0/)
    expect(tab.body).toMatch(/word-break\s*:\s*normal/)
    // One line of label, not two: 32px, down from 40.
    expect(tab.body).toMatch(/min-height\s*:\s*32px/)
    expect(tab.body).toMatch(/white-space\s*:\s*normal/)
    // The desktop's margin before "All unresolved" would make it the one narrow column.
    const unresolved = ALL.filter(r => r.selector.includes('.ops-date-range__preset--unresolved') && phoneOnly(r.media)).at(-1)
    expect(unresolved.body).toMatch(/margin-left\s*:\s*0/)
  })

  it('leaves the desktop strip as it was', () => {
    // No rule outside a phone width may turn the strip into a grid.
    for (const rule of declaring('.ops-date-range__presets', 'grid-template-columns')) {
      expect(phoneOnly(rule.media), `${rule.media} reaches a desktop`).toBe(true)
    }
  })
})

describe('the card is not a page in itself', () => {
  it('trims the padding every row of the card carries', () => {
    // 440px measurements: the card was 516px tall, of which ten rows of
    // table-cell padding. 424px after, with nothing removed from it; the
    // iPhone grid (below) then puts several cells on one row.
    const padded = ALL.filter(
      r => phoneOnly(r.media) && r.selector.includes('ta-table-responsive--cards') &&
        /\btd\s*$/.test(r.selector) && /padding\s*:/.test(r.body),
    )
    expect(padded.length).toBeGreaterThan(0)
    expect(padded.some(r => /padding\s*:\s*4px 10px/.test(r.body))).toBe(true)
    // 24 Nov mockup: the card's own 12px padding replaces every cell's.
    const cells = ALL.filter(r => phoneOnly(r.media) && r.selector.trim() === '.ops-dash-table-wrap.ta-table-responsive--cards .ops-interview-row > td').at(-1)
    expect(cells.body).toMatch(/padding\s*:\s*0/)
  })

  it('keeps the screenshot openable and the menu thumb-sized', async () => {
    mockFetch([row({ slot_screenshot_proof: { url: '/media/x.png', original_name: 'invite.png' } })])
    await renderPanel()
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())

    // Smaller, not gone: the thumbnail is still the button that opens the
    // full screenshot, and the row menu keeps its 44px target.
    expect(screen.getByTitle('View booking screenshot for Asha Rao')).toBeInTheDocument()
    // The last phone-scoped rule wins: an earlier 700px block sizes it 86x50.
    const thumb = ALL.filter(r => phoneOnly(r.media) && r.selector.trim().endsWith('.ops-slot-shot-cell .ops-slot-shot-thumb')).at(-1)
    expect(thumb.body).toMatch(/width\s*:\s*110px/)
    expect(thumb.body).toMatch(/height\s*:\s*64px/)
    fireEvent.click(screen.getByTitle('View booking screenshot for Asha Rao'))
    expect(screen.getByRole('dialog', { name: 'Booking screenshot for Asha Rao' })).toBeInTheDocument()
    const action = ALL.find(r => phoneOnly(r.media) && r.selector === '.daily-ops-page--dashboard .ops-mobile-attendance')
    expect(action.body).toMatch(/min-height\s*:\s*44px/)
  })

  it('shows the whole note on a tap of the badge, never shortened', async () => {
    const remark = 'Panel asked to move it to the afternoon slot.'
    mockFetch([row({ interview_attendance_remark: remark })])
    await renderPanel()

    const badge = await screen.findByRole('button', { name: 'Status: Note. Show notes' })
    expect(screen.queryByText(remark)).toBeNull()
    fireEvent.click(badge)
    expect(screen.getByRole('tooltip').textContent).toBe(remark)
    // The card wraps the text; it is never ellipsised.
    const card = ALL.filter(r => r.selector === '.ops-status-note').at(-1)
    expect(card.body).toMatch(/white-space:normal/)
    expect(card.body).not.toMatch(/text-overflow/)
  })
})

describe('the candidate line', () => {
  it('groups the name and number into separately wrapping lines', () => {
    expect((ROSTER.match(/className="ops-interview-identity"/g) || []).length).toBe(1)
    expect(ROSTER).toContain('className="ops-interview-name"')
    expect(ROSTER).toContain('className="ops-interview-contact"')
  })

  it('opens the existing edit dialog from the phone-only attendance action', async () => {
    vi.stubGlobal('matchMedia', vi.fn(query => ({
      matches: query.includes('max-width: 767px'), media: query,
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
    })))
    await renderPanel()
    const action = await screen.findByRole('button', { name: 'Edit attendance for Asha Rao' })
    expect(cell('Attendance').querySelector('.ops-attendance-status').textContent).toBe('Pending')
    expect(cell('Attendance').querySelector('select')).toBeNull()
    expect(document.querySelector('.ops-mobile-avatar').textContent).toBe('AR')
    fireEvent.click(action)
    expect(screen.getByText('Edit interview slot')).toBeInTheDocument()
  })

  it('renders that group around the name and phone', async () => {
    await renderPanel()
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())

    const identity = document.querySelector('.ops-interview-row .ops-interview-identity')
    expect(identity.querySelector('strong').textContent).toBe('Asha Rao')
    expect(identity.querySelector('.ops-interview-phone').textContent).toBe('9000000001')
    // The source chip is a sibling of the group, free to wrap below it.
    expect(identity.parentElement.querySelector('.ops-booking-source')).not.toBeNull()
    expect(identity.querySelector('.ops-booking-source')).toBeNull()
  })

  it('is invisible to the desktop layout', () => {
    // `display:contents` means the wrapper is not laid out at all, so a laptop
    // renders exactly the two boxes it rendered before this element existed.
    const base = ALL.filter(r => r.selector.trim() === '.ops-interview-identity')
    const unscoped = base.filter(r => r.media === null)
    expect(unscoped.length, 'the wrapper needs a base rule that erases it').toBe(1)
    expect(unscoped[0].body).toMatch(/display\s*:\s*contents/)
    for (const rule of base.filter(r => r.media !== null)) {
      expect(phoneOnly(rule.media), `${rule.media} reaches a desktop`).toBe(true)
    }
  })
})

describe('on an iPhone (390-430px)', () => {
  const CARD = '.ops-dash-table-wrap.ta-table-responsive--cards .ops-interview-row'
  /** The last phone rule for one card cell: the one a phone gets. */
  const cellRule = label => ALL.filter(
    r => phoneOnly(r.media) && r.selector.split(',').some(s => s.trim() === `${CARD} > td[data-label="${label}"]`),
  ).at(-1)

  it('lays the card out in two shrinkable detail columns', () => {
    const grid = ALL.find(r => phoneOnly(r.media) && r.selector.trim() === '.daily-ops-page--dashboard ' + CARD)
    expect(grid.body).toContain('repeat(2, minmax(0, 1fr))')
    expect(grid.body).toContain('grid-template-areas: "date time" "cand cand" "comp tech" "attd round" "shot shot" "attn attn"')
    const note = ALL.find(r => r.selector === '.daily-ops-page--dashboard ' + CARD + ':has(.ops-status-badge)')
    expect(note.body).toContain('"feed feed"')
  })

  it('puts every cell in its mockup place', () => {
    const area = label => /grid-area\s*:\s*([a-z]+)/.exec(cellRule(label)?.body || '')?.[1]
    expect(['Date', 'Time', 'Candidate', 'Company', 'Technology', 'Attendee', 'Round', 'Screenshot', 'Attendance', 'Status'].map(area))
      .toEqual(['date', 'time', 'cand', 'comp', 'tech', 'attd', 'round', 'shot', 'attn', 'feed'])
  })

  it('gives an empty status no space at all', () => {
    // A row with nothing recorded holds only a dash; it took 39px of every
    // such card. A badge (feedback, or Note) still shows.
    const empty = ALL.filter(r => phoneOnly(r.media) &&
      r.selector.includes('td[data-label="Status"]:not(:has(.ops-status-badge))')).at(-1)
    expect(empty, 'nothing hides an empty note').toBeTruthy()
    expect(empty.body).toMatch(/display\s*:\s*none/)
  })

  it('names detail values with aligned icons and captions', () => {
    const hidesAll = ALL.filter(r => phoneOnly(r.media) && r.selector.trim() === `${CARD} > td::before`).at(-1)
    expect(hidesAll.body).toMatch(/display\s*:\s*none/)
    const captions = ALL.find(r => phoneOnly(r.media) && r.selector.startsWith('.daily-ops-page--dashboard ' + CARD) && r.selector.includes('[data-label="Company"]') && r.selector.endsWith('::before'))
    expect(captions.body).toMatch(/content\s*:\s*attr\(data-label\)/)
    for (const label of ['Date', 'Company', 'Technology', 'Round', 'Attendee']) {
      const at = ROSTER.indexOf(`data-label="${label}"`)
      expect(ROSTER.slice(at, at + 200), `${label} has no icon`).toMatch(/<CardIcon name="[a-z-]+" \/>/)
    }
  })

  it('shows the booking source as a coloured chip', () => {
    expect(ROSTER).toMatch(/ops-booking-source ops-booking-source--\$\{bookingSource\.tone\}/)
    for (const tone of ['candidate', 'auto']) {
      const rule = ALL.filter(r => phoneOnly(r.media) && r.selector.includes(`.ops-booking-source--${tone}`)).at(-1)
      expect(rule, `no chip colour for ${tone}`).toBeTruthy()
      expect(rule.body).toMatch(/background\s*:/)
    }
    // The desktop draws the same source as a compact chip now (it used to be a
    // third line of text in every row); the Assessment chip stays phone-only.
    for (const rule of ALL.filter(r => /\.ops-booking-type\b/.test(r.selector))) {
      expect(phoneOnly(rule.media), `${rule.media} restyles the desktop assessment text`).toBe(true)
    }
    const desktop = ALL.filter(r => r.media === null && r.selector.trim() === '.ops-booking-source').at(-1)
    expect(desktop?.body).toMatch(/height\s*:\s*16px/)
  })

  it('puts search and both actions on one row and retains named controls', async () => {
    const prefix = '.daily-ops-page--dashboard .ops-roster-controls .ops-roster-controls__filters'
    const get = suffix => ALL.find(r => phoneOnly(r.media) && r.selector === prefix + suffix)?.body
    expect(get('')).toContain('repeat(2, minmax(0, 1fr))')
    expect(get(' .ops-roster-search')).toContain('grid-row: 1')
    expect(get(' .ops-roster-controls__actions')).toContain('grid-row: 1')
    expect(get(' .ops-roster-control--date')).toContain('grid-row: 2')
    expect(get(' .ops-roster-today')).toContain('grid-row: 2')
    expect(get(' .ops-profile-round-filter')).toContain('grid-row: 3')
    expect(get(' .ops-filter-dropdown-wrap')).toContain('grid-row: 3')
    await renderPanel()
    expect(screen.getByRole('button', { name: /Refresh/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Reset all filters/ })).toBeInTheDocument()
  })

  it('drops the captions above the filters only because each field names itself', async () => {
    await renderPanel()
    fireEvent.click(screen.getByRole('button', { name: /^Filters/i }))
    // An admin sees all four (the attendee filter is hidden for a handler).
    for (const name of ['Candidate filter', 'Attendee filter']) {
      expect(screen.getByRole('group', { name })).toBeInTheDocument()
    }
    const hidden = ALL.filter(r => r.selector.includes('.ops-roster-controls__filters .ops-roster-control > span'))
    expect(hidden.length).toBeGreaterThan(0)
    for (const rule of hidden) expect(phoneOnly(rule.media), `${rule.media} hides desktop captions`).toBe(true)
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
