/**
 * The Daily Ops top bar is one row on a desktop, and only on a desktop.
 *
 * Measured at 1440px before the change: the title took 160px, the seven KPI
 * cards 685px, the booking card 470px and the pending pill 87px — 1410px of
 * content in a 1388px bar, so the booking card and the pill wrapped to a
 * second line and the bar stood 161px tall. The 470px came from a single
 * `min-width` on `.ops-booking-pie`, held open by a per-candidate legend and a
 * row of level chips that the analytics modal shows in full anyway.
 *
 * The compaction has to stay inside a `min-width` media query. Written flat it
 * sits after the 900px and 600px blocks in the same file, at the same
 * specificity, and wins over them: the phone strip's 56px card floor became 0
 * and seven labels squashed into each other. Above 900px there is room to take
 * the pixels out of padding and gaps; below it the responsive design that was
 * already there is the right one.
 *
 * Assertions are on the stylesheet. jsdom performs no layout, so a rendered
 * height check here would pass whatever these rules said; the widths above
 * were measured against a real browser instead.
 */

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const CSS = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'dailyOps.css'),
  'utf-8',
)

/**
 * Every rule in the sheet, each tagged with the `@media` prelude it sits in.
 * The sheet nests one level deep and uses no other at-rule.
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
const matching = needle => ALL.filter(r => r.selector.includes(needle))

/** A media prelude that stops at `limit` or below. */
const appliesOnlyBelow = (media, limit) => {
  const max = /max-width:\s*(\d+)px/.exec(media || '')
  return Boolean(max) && Number(max[1]) <= limit
}

/** A media prelude that only ever applies to wide viewports. */
const isDesktopOnly = media =>
  typeof media === 'string' && /min-width:\s*(9[0-9]{2}|[1-9][0-9]{3})px/.test(media)

describe('the top bar fits on one line above 900px', () => {
  it('lets the booking card out of its 470px minimum inside the bar', () => {
    const inBar = matching('.ops-topbar .ops-booking-pie').filter(
      r => !r.selector.includes('__'),
    )
    expect(inBar.length, 'a rule must scope the booking card to the top bar').toBeGreaterThan(0)
    const floor = inBar.find(r => /min-width\s*:\s*0\b/.test(r.body))
    expect(floor, 'the 470px minimum is what wrapped the row').toBeTruthy()
    // `.ops-booking-pie{min-width:470px}` is (0,1,0), so the override needs
    // two classes; `.ops-topbar .ops-booking-pie` has them.
    expect((floor.selector.match(/\./g) || []).length).toBeGreaterThanOrEqual(2)
  })

  it('keeps the way into the analytics modal', () => {
    // The legend and the level chips are hidden in the bar because the modal
    // behind this button shows both. Hide the button too and the detail is
    // gone rather than moved.
    for (const rule of matching('.ops-booking-pie__open')) {
      expect(rule.body).not.toMatch(/display\s*:\s*none/)
    }
  })
})

describe('and leaves tablet and phone alone', () => {
  it('scopes the sizes it changed to a desktop width', () => {
    // These four selectors are the ones the single row re-sizes, and each of
    // them is also sized by the 900px block. Same specificity, so a flat rule
    // written after that block wins over it at every width: that is how the
    // 56px card floor became 0 and the phone labels ran together.
    const RESIZED = [
      /^\.ops-topbar$/,
      /^\.ops-topbar__kpis$/,
      /^\.ops-topbar__kpis \.ops-dash-kpi$/,
      /^\.ops-topbar \.ops-booking-pie$/,
    ]
    const SIZING = /(^|[;\s])(min-width|max-width|padding|gap)\s*:/

    const block900 = ALL.findIndex(
      r => /max-width:\s*900px/.test(r.media || '') && r.selector.startsWith('.ops-topbar'),
    )
    expect(block900, 'the 900px top-bar block must exist to be protected').toBeGreaterThan(-1)

    for (const rule of ALL.slice(block900 + 1)) {
      const selector = rule.selector.trim()
      if (!RESIZED.some(re => re.test(selector))) continue
      if (!SIZING.test(rule.body)) continue
      // A narrower block after it (600px) is the cascade working; a wider
      // one, or none at all, reaches the phone.
      expect(
        `${rule.media || 'no media'} { ${selector} }`,
        'this outranks the 900px block at every width, phones included',
      ).toSatisfy(() => isDesktopOnly(rule.media) || appliesOnlyBelow(rule.media, 900))
    }
  })

  it('keeps the 56px card floor the phone strip lays out on', () => {
    const phone = ALL.find(
      r =>
        r.selector.includes('.ops-topbar__kpis .ops-dash-kpi') &&
        /max-width:\s*900px/.test(r.media || ''),
    )
    expect(phone, 'the 900px block sizes the cards for a narrow strip').toBeTruthy()
    expect(phone.body).toMatch(/min-width\s*:\s*56px/)

    // Anything later and unscoped would win at equal specificity.
    const later = ALL.slice(ALL.indexOf(phone) + 1).filter(
      r =>
        r.selector.includes('.ops-topbar__kpis .ops-dash-kpi') &&
        !isDesktopOnly(r.media) &&
        /min-width/.test(r.body),
    )
    expect(later).toHaveLength(0)
  })

  it('leaves the card hidden on a phone rather than crushed to a sliver', () => {
    // At 390px the card measures 0px wide. That is `display:none` from the
    // 600px block, not a squeeze: the donut has no room beside the title and
    // the pill, and the analytics modal still reaches all of it.
    const hidden = ALL.find(
      r =>
        r.selector.trim() === '.ops-booking-pie' &&
        /max-width:\s*600px/.test(r.media || '') &&
        /display\s*:\s*none/.test(r.body),
    )
    expect(hidden).toBeTruthy()
  })
})
