/**
 * The extracted-payment-details panel must be readable, not cropped.
 *
 * `.cand-proofs-ai-result` had no CSS rules at all. Inside the desktop
 * `.cand-proofs` grid — which becomes
 * `grid-template-columns: minmax(0, 1fr) 120px` once a proof exists — it
 * auto-flowed into the 120px column meant for thumbnails. The parent sets
 * `overflow: hidden`, so the panel was silently cropped rather than scrolled:
 * "₹20,00", "Not detect", and a UTR cut off at the panel edge.
 *
 * Two separate things had to be true, and asserting only one leaves the bug
 * reachable:
 *
 *   1. the panel occupies a full-width row of its own
 *   2. its values may wrap — a 22-character transaction ID is a single
 *      unbreakable token that otherwise forces the panel wider than its column
 *
 * Assertions are on the stylesheet: jsdom performs no layout, so a rendered
 * width check would pass no matter what these rules said.
 */

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const CSS = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'index.css'),
  'utf-8',
)

/** Body of the rule whose selector contains `needle`, comments stripped.
 *
 * Anchored on a line start, because `.cand-proofs-ai-result {` is also a
 * substring of `.cand-panel .cand-proofs .cand-proofs-ai-result {` - matching
 * loosely reads the placement override instead of the base rule and quietly
 * asserts the wrong thing.
 */
function rule(needle) {
  const at = CSS.indexOf('\n' + needle)
  expect(at, `no rule starting a line with ${needle}`).toBeGreaterThan(-1)
  const open = CSS.indexOf('{', at)
  return CSS.slice(open, CSS.indexOf('}', open)).replace(/\/\*[\s\S]*?\*\//g, '')
}

describe('payment proof extracted-details panel', () => {
  it('is given a full-width row in the desktop proofs grid', () => {
    // Without an explicit placement it lands in the 120px thumbnail column.
    const placed = rule('  .cand-panel .cand-proofs:has(.cand-proofs-grid) .cand-proofs-ai-result')
    expect(placed).toMatch(/grid-column:\s*1\s*\/\s*-1/)
  })

  it('lets a long identifier break instead of overflowing', () => {
    const idValue = rule('.cand-proofs-ai-value--id')
    expect(idValue).toMatch(/word-break:\s*break-all|overflow-wrap:\s*anywhere/)
  })

  it('lets every value wrap, not just the identifier', () => {
    // Receiver names and status text overflow the same way.
    expect(rule('.cand-proofs-ai-value')).toMatch(/overflow-wrap:\s*anywhere/)
  })

  it('keeps the label column from collapsing under a long value', () => {
    // `auto 1fr` lets a long unbreakable value squeeze labels to one character
    // per line, which is how the original panel became unreadable.
    const grid = rule('.cand-proofs-ai-grid {')
    expect(grid).toMatch(/grid-template-columns:.*minmax\(\s*90px/)
  })

  it('does not rely on the parent clipping to contain it', () => {
    // The parent has overflow: hidden. The panel must fit by construction.
    const panel = rule('.cand-proofs-ai-result {')
    expect(panel).toMatch(/min-width:\s*0/)
    expect(panel).toMatch(/max-width:\s*100%/)
  })

  it('stacks label above value on narrow screens', () => {
    const at = CSS.indexOf('@media (max-width: 560px)')
    expect(at, 'no narrow-screen rules for the panel').toBeGreaterThan(-1)
    const block = CSS.slice(at, at + 400)
    expect(block).toContain('.cand-proofs-ai-grid')
    expect(block).toMatch(/grid-column:\s*1\s*\/\s*-1/)
  })
})

describe('payment proof preview sizing and compact thumbnail layout', () => {
  it('constrains upload preview image to max 140px height and 180px width with object-fit: contain', () => {
    const preview = rule('.cand-proof-upload-preview {')
    expect(preview).toMatch(/max-height:\s*140px/)
    expect(preview).toMatch(/max-width:\s*180px/)
    expect(preview).toMatch(/object-fit:\s*contain/)
  })

  it('constrains upload thumbnail container to max 140px height and 180px width', () => {
    const thumb = rule('.cand-proof-upload-thumb {')
    expect(thumb).toMatch(/max-height:\s*140px/)
    expect(thumb).toMatch(/max-width:\s*180px/)
  })

  it('keeps upload jobs in a full-width row in the desktop proofs grid', () => {
    const placed = rule('  .cand-panel .cand-proofs .cand-proof-upload-jobs')
    expect(placed).toMatch(/grid-column:\s*1\s*\/\s*-1/)
  })

  it('constrains saved proof thumbnails to max 140px height and 180px width with object-fit contain', () => {
    const thumb = rule('.cand-proof-thumb {')
    expect(thumb).toMatch(/max-height:\s*140px/)
    expect(thumb).toMatch(/max-width:\s*180px/)

    const img = rule('.cand-proof-thumb img {')
    expect(img).toMatch(/max-height:\s*140px/)
    expect(img).toMatch(/max-width:\s*180px/)
    expect(img).toMatch(/object-fit:\s*contain/)
  })

  it('wraps upload job cards responsively on narrow screens', () => {
    const at = CSS.indexOf('@media (max-width: 560px)')
    expect(at).toBeGreaterThan(-1)
    const block = CSS.slice(at, at + 700)
    expect(block).toContain('.cand-proof-upload-job')
    expect(block).toMatch(/flex-wrap:\s*wrap/)
    expect(block).toContain('.cand-proof-upload-actions')
  })
})
