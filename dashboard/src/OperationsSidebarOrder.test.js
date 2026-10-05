/**
 * The Operations sidebar order, and what the shell opens on.
 *
 * Requested order runs the day forwards: what needs doing now (Daily Ops),
 * then what has come in (Mail Alerts, AI Mail Review), then the records behind
 * it (Candidates, Data Room), and last Attendance and Slot Booking.
 *
 * The landing view matters as much as the order. It used to be `candidates`,
 * so the first item in the sidebar was not the page anyone actually arrived on
 * - the nav said one thing and the shell did another.
 *
 * These read the source rather than rendering, which is the convention
 * decommission.test.js already uses for App.jsx: `OperationsShell` is not
 * exported and `App` pulls in providers and network calls, so a render test
 * here would assert mostly mocks. The trade is real - this cannot prove what a
 * browser paints - so the assertions are kept tight to things that cannot be
 * satisfied by a coincidence: the exact ordered label list, the default id, and
 * that the default is genuinely the first entry rather than a value that merely
 * happens to match today.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ICON_NAMES } from './components/ui/Icon.jsx'

const SRC = dirname(fileURLToPath(import.meta.url))
const app = readFileSync(join(SRC, 'App.jsx'), 'utf8')

const EXPECTED = [
  'Daily Ops',
  'Mail Alerts',
  'AI Mail Review',
  'Candidates',
  'Data Room',
  'Attendance',
  'Slot Booking',
]

const labels = [...app.matchAll(/\blabel:\s*'([^']+)'/g)].map((m) => m[1])
const ids = [...app.matchAll(/\bid:\s*'([^']+)',\s*label:/g)].map((m) => m[1])

describe('operations sidebar order', () => {
  it('lists the shipped features in the requested order', () => {
    expect(labels).toEqual(EXPECTED)
  })

  it('opens on Daily Ops', () => {
    expect(app).toMatch(/const DEFAULT_VIEW = 'daily-ops'/)
    expect(app).toMatch(/useState\(DEFAULT_VIEW\)/)
  })

  it('does not hardcode a landing view that bypasses DEFAULT_VIEW', () => {
    // useState('candidates') was the old default. Any literal id passed
    // straight to the view state would silently win over DEFAULT_VIEW again.
    for (const id of ids) {
      expect(app).not.toContain(`useState('${id}')`)
    }
  })

  it('lands on the first entry in the sidebar, not merely on a fixed id', () => {
    // Guards the pairing rather than the value: reorder the nav without
    // revisiting the landing view and this fails, which is exactly the mistake
    // that left the shell opening on Candidates while Daily Ops sat first.
    const defaultView = app.match(/const DEFAULT_VIEW = '([^']+)'/)?.[1]
    expect(defaultView).toBe(ids[0])
  })

  it('keeps the landing view in the shipped nav', () => {
    const defaultView = app.match(/const DEFAULT_VIEW = '([^']+)'/)?.[1]
    expect(ids).toContain(defaultView)
  })

  it('has no persisted or routed view that could override the default', () => {
    // There is no router and no storage behind the sidebar, so DEFAULT_VIEW is
    // what login, a refresh and the Operations root all land on. If any of
    // these appear later, this file's claim about refresh stops being true and
    // the new mechanism needs its own test.
    for (const escape of ['localStorage', 'sessionStorage', 'useSearchParams', 'window.location.hash']) {
      expect(app).not.toContain(escape)
    }
  })

  it('never paints an icon as text beside its label', () => {
    // `icon: 'AI'` beside 'AI Mail Review' once painted "AI AI Mail Review".
    // Icons are now names in the shared line-icon set, drawn as SVG through
    // <Icon>, so an icon can no longer render as a word at all.
    const pairs = [...app.matchAll(/\blabel:\s*'([^']+)',\s*icon:\s*'([^']+)'/g)]
    expect(pairs).toHaveLength(EXPECTED.length)
    for (const [, , icon] of pairs) expect(ICON_NAMES).toContain(icon)
    expect(app).toContain('<Icon name={item.icon}')
    expect(app).not.toMatch(/>\s*\{item\.icon\}\s*</)
  })

  it('has no icon that collides with the status glyph below it', () => {
    // The footer marks "Operations service online" with a check. Attendance
    // shipped with the same check as its nav icon, so one glyph meant both
    // "this section" and "the service is up", a few rows apart in one sidebar.
    const status = app.match(/desktop-sidebar__status-check" aria-hidden>([^<]+)</)?.[1]
    expect(status).toBeTruthy()
    const icons = [...app.matchAll(/\bicon:\s*'([^']+)'/g)].map((m) => m[1])
    expect(icons).not.toContain(status)
  })

  it('gives every sidebar entry a distinct icon', () => {
    const icons = [...app.matchAll(/\bicon:\s*'([^']+)'/g)].map((m) => m[1])
    expect(icons).toHaveLength(EXPECTED.length)
    expect(new Set(icons).size).toBe(icons.length)
  })

  it('tells assistive technology which section is current', () => {
    // The active item was styled by a class and nothing else, so which
    // section you were in existed only for people who could see the
    // highlight.
    expect(app).toMatch(/aria-current=\{view === item\.id \? 'page' : undefined\}/)
  })

  it('says what the sidebar badge is counting', () => {
    // countLabel renders a bare number, which reads as "Candidates 3" with
    // nothing saying what 3 is.
    //
    // Matched across the class attribute rather than immediately after it:
    // the Mail Alerts badge takes a --unread modifier, so the class is a
    // template literal now. What has to hold is the label, not its position.
    expect(app).toMatch(/desktop-sidebar__badge[^>]*aria-label=/)
  })
})
