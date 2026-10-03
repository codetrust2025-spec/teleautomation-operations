/**
 * Edit Candidate modal: proof cards, footer and scrolling.
 *
 * Three faults, all in the stylesheet (jsdom does no layout, so these assert the
 * rules; the real widths were measured in a browser at 1280, 1000x480, 768, 390
 * and 360 wide):
 *
 *   1. `.cand-panel .cand-proof-card` and the proofs grid were pinned to 120px
 *      with `overflow: hidden` by rules written for the old thumbnail layout, and
 *      they outranked the later card rules. Transaction ID / UTR, the upload date
 *      and the note were cut off (the reference value measured 10px wide).
 *   2. The footer was `position: sticky` inside a flex column, which does nothing,
 *      and the body's height was a guess (`calc(90vh - 100px)`).
 *   3. So the scroll area did not reliably match the room it had.
 */

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const RAW = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'index.css'), 'utf-8')
const CSS = RAW.replace(/\r\n/g, '\n')
const START = CSS.indexOf('Edit Candidate modal: proof cards, footer and scroll')
const FIX = CSS.slice(START).replace(/\/\*[\s\S]*?\*\//g, '')

/** Declarations of the rule whose selector list contains `selector` exactly, in the fix block. */
function rule(selector, within = FIX) {
  const re = new RegExp('(^|\\n)\\s*' + selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*(,[^{]*)?\\{([^}]*)\\}')
  const m = within.match(re)
  expect(m, `no rule for ${selector} in the modal fix block`).not.toBeNull()
  return m[3]
}

describe('the modal fix block exists and comes last', () => {
  it('is present', () => expect(START).toBeGreaterThan(-1))
})

describe('1. proof cards are as wide as their column', () => {
  const DESKTOP = FIX.slice(FIX.indexOf('@media (min-width: 900px)'))

  it('the desktop proofs block is one full-width column, not dropzone | 120px', () => {
    expect(DESKTOP).toMatch(/\.cand-panel \.cand-proofs:has\(\.cand-proofs-grid\)\s*\{[^}]*display:\s*flex[^}]*flex-direction:\s*column/)
  })

  it('beats the old 120px rules, including the :has() variant they used', () => {
    const grid = rule('.cand-panel .cand-proofs:has(.cand-proofs-grid) .cand-proofs-grid', DESKTOP)
    expect(grid).toMatch(/max-width:\s*none/)
    expect(grid).toMatch(/width:\s*100%/)
    expect(grid).toMatch(/overflow:\s*visible/)
  })

  it('a card is not capped or clipped', () => {
    const card = rule('.cand-panel .cand-proof-card', DESKTOP)
    expect(card).toMatch(/width:\s*auto/)
    expect(card).toMatch(/max-width:\s*none/)
    expect(card).toMatch(/overflow:\s*visible/)
  })

  it('the card text wraps whole values and never ends in an ellipsis', () => {
    const meta = rule('.cand-panel .cand-proof-meta', DESKTOP)
    expect(meta).toMatch(/white-space:\s*normal/)
    expect(meta).toMatch(/text-overflow:\s*clip/)
    expect(meta).toMatch(/max-width:\s*none/)
    expect(rule('.cand-proof-meta')).toMatch(/white-space:\s*normal/)
  })

  it('a long Transaction ID or UTR breaks inside its own box instead of being cut', () => {
    expect(rule('.cand-proof-facts dd')).toMatch(/overflow-wrap:\s*anywhere/)
    expect(rule('.cand-proof-facts dd')).toMatch(/word-break:\s*break-all/)
  })

  it('the label column fits "Transaction ID"', () => {
    expect(rule('.cand-proof-facts dt')).toMatch(/flex:\s*0 0 96px/)
  })

  it('the caption, lost-file message and re-upload actions wrap too', () => {
    expect(rule('.cand-proof-note')).toMatch(/white-space:\s*normal/)
    expect(rule('.cand-proof-broken-actions')).toMatch(/flex-wrap:\s*wrap/)
    expect(rule('.cand-proof-sub')).toMatch(/flex-wrap:\s*wrap/)
  })

  it('the thumbnail shows the whole receipt, not a 78px crop', () => {
    expect(rule('.cand-panel .cand-proof-card img', DESKTOP)).toMatch(/object-fit:\s*contain/)
  })
})

describe('2. the footer never covers proof content', () => {
  it('is in flow below the body, not sticky', () => {
    const footer = rule('.cand-modal .cand-modal-footer')
    expect(footer).toMatch(/position:\s*relative/)
    expect(footer).toMatch(/bottom:\s*auto/)
    expect(footer).not.toMatch(/sticky/)
  })

  it('everything except the body keeps its own height', () => {
    expect(rule('.cand-modal > :not(.cand-modal-body)')).toMatch(/flex:\s*0 0 auto/)
  })
})

describe('3. one scroller, and all of it reachable', () => {
  it('the body shrinks and scrolls; nothing guesses the header and footer height', () => {
    const body = rule('.cand-modal .cand-modal-body')
    expect(body).toMatch(/flex:\s*1 1 auto/)
    expect(body).toMatch(/min-height:\s*0/)
    expect(body).toMatch(/max-height:\s*none/)
    expect(body).toMatch(/overflow-y:\s*auto/)
    expect(body).toMatch(/overflow-x:\s*hidden/)
  })

  it('the modal is a bounded flex column, in dynamic viewport units too', () => {
    const modal = rule('.cand-modal')
    expect(modal).toMatch(/flex-direction:\s*column/)
    expect(modal).toMatch(/max-height:\s*90dvh/)
  })

  it('phones still give the form the whole viewport, after the generic bound above', () => {
    const phone = FIX.slice(FIX.lastIndexOf('@media (max-width: 599px)'))
    expect(phone).toMatch(/\.cand-modal\s*\{\s*max-height:\s*100dvh/)
  })

  it('the last panel leaves room under the final card', () => {
    expect(FIX).toMatch(/\.cand-modal-body > \.cand-panel:last-child\s*\{\s*padding-bottom:\s*20px/)
    expect(rule('.cand-modal .cand-modal-body')).toMatch(/scroll-padding-bottom/)
  })

  it('stacked panels below 900px are not squeezed by the flex column', () => {
    expect(FIX).toMatch(/@media \(max-width: 899px\)\s*\{\s*\.cand-modal-body > \.cand-panel\s*\{\s*flex:\s*0 0 auto/)
  })
})
