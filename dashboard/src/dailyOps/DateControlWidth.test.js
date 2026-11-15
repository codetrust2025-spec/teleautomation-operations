/**
 * The date control must not widen the Daily Ops controls row.
 *
 * That row is already wider than its container at 1440px before this control
 * exists — measured at 1155px of content in a 1142px box — and it lays out
 * with `overflow: visible` above 1101px. So every pixel the date control takes
 * beyond the text it shows comes off the far end of the row, where the Refresh
 * button sits: an early version fixed the control at 176px, 78px more than the
 * month dropdown it replaced, and Refresh was cut off by the viewport edge.
 *
 * Two things keep it honest, and asserting only one leaves the bug reachable:
 *
 *   1. the control is sized to its own label, not to a fixed width
 *   2. the label itself is never given a fixed width either, or `max-content`
 *      on the trigger would be overruled by `.ops-roster-control .cand-input`
 *
 * Assertions are on the stylesheet: jsdom performs no layout, so a rendered
 * width check would pass whatever these rules said. The rendered behaviour is
 * covered against a real browser instead.
 */

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const FULL_CSS = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'dailyOps.css'),
  'utf-8',
)
// The iPhone block (owner, 16 Nov) sits last and only applies at 767px and
// below, where the date control deliberately fills half of its own row
// (PhoneControls.test.jsx covers it). The row this file protects is the
// desktop one, so it reads the rules before that block.
const IPHONE_BLOCK = FULL_CSS.indexOf('/* ── iPhone controls (owner, 16 Nov)')
const CSS = IPHONE_BLOCK >= 0 ? FULL_CSS.slice(0, IPHONE_BLOCK) : FULL_CSS

/** Every rule body whose selector contains `needle`, comments stripped. */
function bodies(needle) {
  const withoutComments = CSS.replace(/\/\*[\s\S]*?\*\//g, '')
  const found = []
  const pattern = new RegExp(`([^{}]*${needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[^{}]*)\\{([^}]*)\\}`, 'g')
  let match
  while ((match = pattern.exec(withoutComments)) !== null) {
    found.push({ selector: match[1].trim(), body: match[2].trim() })
  }
  return found
}

describe('the Daily Ops date control is sized to its label', () => {
  it('never pins the control to a fixed or minimum width', () => {
    const rules = bodies('.ops-roster-control--date')
    expect(rules.length).toBeGreaterThan(0)
    for (const { selector, body } of rules) {
      // `min-width:0` is the point — anything else reserves space the row
      // does not have.
      const minWidth = /min-width\s*:\s*([^;]+)/.exec(body)
      if (minWidth) expect(`${selector} -> ${minWidth[1].trim()}`).toMatch(/-> 0$/)
      expect(body).not.toMatch(/(^|[;\s])width\s*:\s*\d/)
    }
  })

  it('lets the trigger take exactly the width of its text', () => {
    const rule = bodies('.ops-datepicker__trigger').find(r => /width\s*:\s*max-content/.test(r.body))
    expect(rule, 'a rule must set the trigger to width:max-content').toBeTruthy()
    // `.ops-roster-control .cand-input` sets `width:100%` at (0,2,0), so the
    // override has to carry two classes of its own or it silently loses.
    expect((rule.selector.match(/\./g) || []).length).toBeGreaterThanOrEqual(2)
  })

  it('carries no fixed-width pill beside the date', () => {
    // Yesterday/Today/Tomorrow moved to the trigger's tooltip; the pill cost
    // ~58px and repeated what the calendar's own quick picks say.
    expect(CSS).not.toMatch(/\.ops-datepicker__relative/)
  })
})
