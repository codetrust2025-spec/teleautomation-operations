/**
 * Mail Alerts on an iPhone (390-430px), and nothing else moved.
 *
 * Measured in the real app at 390px before this: a three-line title, three
 * stacked counter cards, four stacked filters, and a nine-column table whose
 * columns ran past the screen edge. After: a 45px header, one row of counters,
 * cards of 134-157px with candidate, received time, company, status, subject,
 * automation outcome and Open / Read / Dismiss, and no horizontal scroll.
 *
 * Stylesheet checks, because jsdom performs no layout: every phone rule lives
 * in one `max-width: 640px` block, and the table cells carry the classes that
 * block keys on.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, '..', 'recruitmentMail.css'), 'utf-8').replace(/\r\n/g, '\n')
const JSX = readFileSync(join(here, 'MailMonitoringNotifications.jsx'), 'utf-8').replace(/\r\n/g, '\n')

/** The body of the phone block: the 640px media query holding the card rules. */
function phoneBlock() {
  let from = 0
  for (;;) {
    const at = CSS.indexOf('@media (max-width: 640px) {', from)
    if (at < 0) return ''
    let depth = 0
    for (let i = CSS.indexOf('{', at); i < CSS.length; i += 1) {
      if (CSS[i] === '{') depth += 1
      if (CSS[i] === '}') depth -= 1
      if (depth === 0) {
        const body = CSS.slice(at, i + 1)
        if (body.includes('tr.mail-notification-row')) return body
        from = i
        break
      }
    }
  }
}
const PHONE = phoneBlock()
const rule = (selector) => {
  const at = PHONE.indexOf(`${selector} {`)
  return at < 0 ? '' : PHONE.slice(at, PHONE.indexOf('}', at))
}
const CELLS = ['candidate', 'company', 'status', 'subject', 'confidence', 'received', 'detected', 'automation', 'actions']

describe('Mail Alerts on a phone', () => {
  it('has one phone block for the page', () => {
    expect(PHONE, 'no 640px block holds the card layout').not.toBe('')
  })

  it('puts the three counters in one row', () => {
    expect(rule('.mail-summary--compact')).toMatch(/grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/)
  })

  it('keeps search on its own row and the selects two by two', () => {
    expect(rule('.mail-filters--compact')).toMatch(/grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/)
    expect(PHONE).toMatch(/\.mail-filters--compact > input:first-child,\s*\.mail-filters--compact > :last-child \{ grid-column: 1 \/ -1; \}/)
  })

  it('never shrinks a filter below 16px, which makes iOS Safari zoom on tap', () => {
    const controls = rule('.mail-filters--compact input,\n  .mail-filters--compact select')
    expect(controls, 'the filter control rule is missing').not.toBe('')
    expect(controls).not.toMatch(/font-size/)
  })

  it('turns each alert into a card instead of a sideways-scrolling table', () => {
    expect(rule('.mail-table-wrap')).toMatch(/overflow:\s*visible/)
    expect(rule('.mail-table thead')).toMatch(/display:\s*none/)
    const card = rule('.mail-table tr.mail-notification-row')
    expect(card).toMatch(/display:\s*grid/)
    for (const area of ['candidate received', 'company company', 'status status', 'subject subject', 'automation actions']) {
      expect(card).toContain(`"${area}"`)
    }
  })

  it('shows the actions, and leaves confidence and detection time to the alert itself', () => {
    const scope = '.mail-table tr.mail-notification-row > '
    expect(rule(`${scope}.mail-cell--actions`)).toMatch(/display:\s*flex/)
    expect(PHONE).toContain(`${scope}.mail-cell--confidence,\n  ${scope}.mail-cell--detected { display: none; }`)
  })

  it('keeps the category accent on the whole card', () => {
    expect(rule('.mail-table tr.mail-notification-row--interview')).toContain('inset 3px 0 0')
    expect(rule('.mail-table tr.mail-notification-row--selection')).toContain('inset 3px 0 0')
  })
})

describe('the desktop table is left alone', () => {
  it('styles the cell classes only inside the phone block', () => {
    const outside = CSS.replace(PHONE, '')
    expect(outside).not.toMatch(/\.mail-cell--/)
  })

  it('gives every column of the row its class, in table order', () => {
    const order = CELLS.map(name => JSX.indexOf(`mail-cell--${name}`))
    for (const at of order) expect(at).toBeGreaterThan(-1)
    expect([...order].sort((a, b) => a - b)).toEqual(order)
  })
})
