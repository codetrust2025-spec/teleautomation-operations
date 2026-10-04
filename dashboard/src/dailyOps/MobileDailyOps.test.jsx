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
            interview_round: 'Final', interview_attendance_remark: 'Panel asked to move it to the afternoon slot.',
            interview_booking_source: 'candidate_booked', interview_feedback: 'positive',
            slot_screenshot_proof: { url: '/media/slot-shots/example.png', original_name: 'invite.png' } }),
    ])
    await renderPanel()
    fireEvent.click(screen.getByRole('tab', { name: 'All unresolved' }))
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
    // now one row of five columns that shrink to fit; long labels take two
    // lines inside their own tab. Equal specificity, so the last phone rule
    // for the strip is the one a phone gets.
    const strip = ALL.filter(r => r.selector.includes('.ops-roster-controls .ops-date-range__presets') && phoneOnly(r.media))
    const winner = strip.at(-1)
    expect(winner, 'nothing lays the strip out on a phone').toBeTruthy()
    expect(winner.body).toMatch(/display\s*:\s*grid/)
    const columns = /grid-template-columns\s*:\s*([^;]+)/.exec(winner.body)
    expect(columns, 'the grid needs its columns').toBeTruthy()
    expect(columns[1].match(/minmax\(0,/g) || [], 'five columns, each free to shrink').toHaveLength(5)
    expect(winner.body).toMatch(/overflow\s*:\s*visible/)
    // A tab may wrap its label but must not be split mid-word.
    const tab = ALL.filter(r => r.selector.includes('.ops-roster-controls .ops-date-range__preset') &&
      !r.selector.includes('presets') && !r.selector.includes('--') && phoneOnly(r.media)).at(-1)
    expect(tab.body).toMatch(/min-width\s*:\s*0/)
    expect(tab.body).toMatch(/word-break\s*:\s*normal/)
    expect(tab.body).toMatch(/min-height\s*:\s*40px/)
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

  it('brings the selected tab into view when it is off the edge', async () => {
    // jsdom has no scrollIntoView, so the panel calls it optionally and this
    // supplies one. `nearest` is what leaves a desktop, where nothing
    // overflows, completely still.
    const scrolled = []
    const original = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = function (options) { scrolled.push([this, options]) }
    try {
      await renderPanel()
      scrolled.length = 0
      fireEvent.click(screen.getByRole('tab', { name: 'All unresolved' }))
      await act(async () => { await Promise.resolve() })

      const [element, options] = scrolled.at(-1) || []
      expect(element, 'the newly selected tab was never scrolled to').toBe(
        screen.getByRole('tab', { name: 'All unresolved' }))
      expect(options).toEqual({ inline: 'nearest', block: 'nearest' })
    } finally {
      Element.prototype.scrollIntoView = original
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
    expect(padded.at(-1).body).toMatch(/padding\s*:\s*4px 10px/)
  })

  it('keeps the screenshot openable and the menu thumb-sized', async () => {
    mockFetch([row({ slot_screenshot_proof: { url: '/media/x.png', original_name: 'invite.png' } })])
    await renderPanel()
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())

    // Smaller, not gone: the thumbnail is still the button that opens the
    // full screenshot, and the row menu keeps its 44px target.
    expect(screen.getByTitle('View booking screenshot for Asha Rao')).toBeInTheDocument()
    // The last phone-scoped rule wins: an earlier 700px block sizes it 86x50.
    const thumb = ALL.filter(r => phoneOnly(r.media) && r.selector.includes('.ops-slot-shot-thumb')).at(-1)
    expect(thumb.body).toMatch(/width\s*:\s*64px/)
    expect(thumb.body).toMatch(/height\s*:\s*40px/)
    const menu = ALL.filter(r => phoneOnly(r.media) && r.selector.includes('.ops-row-menu__trigger'))
    expect(menu.some(r => /min-height\s*:\s*44px/.test(r.body))).toBe(true)
  })

  it('leaves the note readable rather than shortening it', async () => {
    const remark = 'Panel asked to move it to the afternoon slot.'
    mockFetch([row({ interview_attendance_remark: remark })])
    await renderPanel()

    expect(await screen.findByText(remark)).toBeInTheDocument()
    const notes = ALL.filter(r => phoneOnly(r.media) && r.selector.includes('.ops-interview-notes-text'))
    expect(notes.some(r => /max-width\s*:\s*none/.test(r.body)), 'the note must not be capped').toBe(true)
  })
})

describe('the candidate line', () => {
  it('groups the name with the number', () => {
    // One box to lay out, so a phone can keep them together and wrap the
    // booking source underneath instead of squeezing three things onto a line.
    // One table renders it now that Awaiting outcome is gone.
    expect((ROSTER.match(/ops-interview-identity/g) || []).length).toBe(1)
    const identity = ROSTER.indexOf('ops-interview-identity')
    expect(ROSTER.slice(identity, identity + 260)).toMatch(/<strong>\{row\.name\}<\/strong>/)
    expect(ROSTER.slice(identity, identity + 260)).toMatch(/ops-interview-phone/)
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
  const span = label => /grid-column\s*:\s*span\s*(\d)/.exec(cellRule(label)?.body || '')?.[1]

  it('lays the card out as a grid, not ten label rows', () => {
    const grid = ALL.filter(r => phoneOnly(r.media) && r.selector.trim() === CARD).at(-1)
    expect(grid.body).toMatch(/display\s*:\s*grid/)
    expect(grid.body).toMatch(/grid-template-columns\s*:\s*repeat\(6,\s*minmax\(0,\s*1fr\)\)/)
  })

  it('pairs date with time, and technology, round and attendee on one row', () => {
    expect([span('Date'), span('Time')]).toEqual(['3', '3'])
    expect([span('Technology'), span('Round'), span('Attendee')]).toEqual(['2', '2', '2'])
  })

  it('gives the notes the full width the desktop cap would take away', () => {
    // A desktop rule caps the notes cell at 160px; on a card that left a long
    // remark in a narrow column beside empty space.
    const notes = cellRule('Notes')
    expect(notes.body).toMatch(/max-width\s*:\s*none/)
    expect(notes.body).not.toMatch(/grid-column\s*:\s*span/)
  })

  it('puts the screenshot and the actions menu side by side on the last row', () => {
    const shot = cellRule('Screenshot')
    const actions = cellRule('Actions')
    expect([span('Screenshot'), span('Actions')]).toEqual(['3', '3'])
    const order = rule => /(^|[;\s])order\s*:\s*(\d)/.exec(rule.body)?.[2]
    expect(order(shot)).toBe(order(actions))
    expect(Number(order(shot))).toBeGreaterThan(Number(order(cellRule('Notes'))))
    // The menu is its own label; "Actions" above a ⋮ only costs a line.
    const label = ALL.filter(r => phoneOnly(r.media) && r.selector.includes('td[data-label="Actions"]::before')).at(-1)
    expect(label.body).toMatch(/display\s*:\s*none/)
  })

  it('shows the booking source as a coloured chip, on a phone only', () => {
    expect(ROSTER).toMatch(/ops-booking-source ops-booking-source--\$\{bookingSource\.tone\}/)
    for (const tone of ['candidate', 'auto']) {
      const rule = ALL.filter(r => phoneOnly(r.media) && r.selector.includes(`.ops-booking-source--${tone}`)).at(-1)
      expect(rule, `no chip colour for ${tone}`).toBeTruthy()
      expect(rule.body).toMatch(/background\s*:/)
    }
    for (const rule of ALL.filter(r => /\.ops-booking-(source|type)\b/.test(r.selector))) {
      expect(phoneOnly(rule.media), `${rule.media} restyles the desktop source text`).toBe(true)
    }
  })

  it('puts Refresh beside the search box as an icon button that keeps its name', async () => {
    const order = selector => /(^|[;\s])order\s*:\s*(\d)/.exec(
      ALL.filter(r => phoneOnly(r.media) && r.selector.trim() === selector && /order\s*:/.test(r.body)).at(-1)?.body || '',
    )?.[2]
    expect(order('.ops-roster-controls__filters .ops-roster-search')).toBe('0')
    expect(order('.ops-roster-controls__filters .ops-roster-controls__actions')).toBe('1')
    expect(order('.ops-roster-controls__filters .ops-roster-control')).toBe('2')
    const refresh = ALL.filter(r => phoneOnly(r.media) && r.selector.trim() === '.ops-roster-controls__actions .ops-roster-refresh').at(-1)
    expect(refresh.body).toMatch(/width\s*:\s*44px/)
    // With a filter set, Clear joins them; the search makes room rather than
    // the two buttons dropping to a row of their own.
    const withClear = ALL.filter(r => phoneOnly(r.media) && r.selector.includes(':has(.ops-roster-clear) .ops-roster-search'))
    expect(withClear.at(-1)?.body).toMatch(/flex-basis\s*:\s*calc\(100% - \d+px\)/)
    // font-size:0 hides the word visually only; it still names the button.
    await renderPanel()
    expect(screen.getByRole('button', { name: /Refresh/ })).toBeInTheDocument()
  })

  it('drops the captions above the filters only because each select names itself', async () => {
    await renderPanel()
    // An admin sees all four (the attendee filter is hidden for a handler).
    for (const name of ['Candidate filter', 'Attendee filter', 'Candidate interview level filter', 'Technology filter']) {
      expect(screen.getByRole('combobox', { name })).toBeInTheDocument()
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
