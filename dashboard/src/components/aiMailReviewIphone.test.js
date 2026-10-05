/**
 * AI Mail Review (Candidate Mailboxes) on an iPhone, 375-430px.
 *
 * Measured in the real app at 390px before this: header 172px, AI nodes
 * 321px, the list tabs running to x=509, the mailbox table 537px wide in a
 * 307px card (a long Gmail address set the width, so the list scrolled
 * sideways), each mailbox about 285px tall with its Reconnect Gmail button at
 * x=550, off the screen. After, at 375, 390 and 430px: nothing past the edge,
 * no clipped text, no sideways scroller; header 134px, nodes 245px, a linked
 * mailbox 146px (an expired one 146px including its Reconnect button, which
 * had been a further 73px warning row), Pending 98-107px, Reconnect 122-139px.
 *
 * Stylesheet checks, because jsdom performs no layout: the phone rules live in
 * one 640px block, every one scoped to this page.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, '..', 'recruitmentMail.css'), 'utf-8').replace(/\r\n/g, '\n')

/** The 640px media block that lays out the mailbox cards. */
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
        if (body.includes('tr.sot-mailbox-row')) return body
        from = i
        break
      }
    }
  }
}
const PHONE = phoneBlock()
const P = '.sot-mailboxes-page'
const rule = (selector) => {
  const at = PHONE.indexOf(`${selector} {`)
  return at < 0 ? '' : PHONE.slice(at, PHONE.indexOf('}', at))
}

describe('AI Mail Review on a phone', () => {
  it('has one phone block, and every rule in it is scoped to this page', () => {
    expect(PHONE, 'no 640px block holds the mailbox cards').not.toBe('')
    const body = PHONE.slice(PHONE.indexOf('{') + 1, PHONE.lastIndexOf('}')).replace(/\/\*[\s\S]*?\*\//g, '')
    const selectors = [...body.matchAll(/([^{}]+)\{[^{}]*\}/g)].flatMap(m => m[1].split(','))
      .map(s => s.trim()).filter(Boolean)
    expect(selectors.length).toBeGreaterThan(20)
    for (const selector of selectors) {
      expect(selector.startsWith(P), `${selector} could reach another page`).toBe(true)
    }
  })

  it('puts the candidate filter beside Refresh and the switches under them', () => {
    const head = rule(`${P} .sot-header-actions`)
    expect(head).toMatch(/display:\s*grid/)
    expect(head).toMatch(/grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto/)
    expect(rule(`${P} .sot-header-actions > button`)).toMatch(/grid-column:\s*2;\s*grid-row:\s*1/)
  })

  it('lays each AI node out on two short lines', () => {
    const node = rule(`${P} .sot-ai-node`)
    expect(node).toMatch(/display:\s*grid/)
    expect(node).toContain('"name name latency"')
    expect(node).toContain('"up ready actions"')
  })

  it('keeps the three list tabs on one row, and search full width', () => {
    expect(rule(`${P} .sot-mailbox-toolbar .sot-mailbox-view-tabs`)).toMatch(/grid-template-columns:\s*repeat\(3,\s*auto\)/)
    expect(rule(`${P} .sot-mailbox-toolbar .sot-list-toolbar-actions`)).toMatch(/display:\s*contents/)
    const search = rule(`${P} .sot-mailbox-toolbar .sot-list-toolbar-actions .sot-search`)
    expect(search).toMatch(/flex:\s*1 1 100%/)
    expect(search).toMatch(/order:\s*2/)
  })

  it('never lets a long value widen the card', () => {
    expect(rule(`${P} .sot-table-wrap`)).toMatch(/overflow:\s*visible/)
    const cell = rule(`${P} .sot-mailbox-table td`)
    expect(cell).toMatch(/grid-template-columns:\s*84px minmax\(0,\s*1fr\)/)
    expect(cell).toMatch(/overflow-wrap:\s*anywhere/)
  })

  it('gives a linked mailbox a card: menu in its own column, last sync on its own line', () => {
    // Sharing the status row squeezed "Waiting to start…" and the sync time
    // beside a "Reconnect Required" badge; at 375-390px the two cannot fit.
    const card = rule(`${P} .sot-mailbox-table tr.sot-mailbox-row`)
    expect(card).toMatch(/grid-template-columns:\s*minmax\(0,\s*1fr\)\s+44px/)
    for (const area of ['"candidate actions"', '"gmail gmail"', '"sync sync"', '"status status"']) {
      expect(card).toContain(area)
    }
    expect(rule(`${P} .sot-mailbox-table tr.sot-mailbox-row > td[data-label="Last Sync"] .sot-sync-progress`))
      .toMatch(/white-space:\s*normal/)
  })

  it('folds an expired Gmail into one bottom row: badge and its one Reconnect button', () => {
    // The badge already says Reconnect Required, so the warning sentence and
    // its red band go on a phone, and the row's single Reconnect Gmail button
    // is laid over the badge's row instead of adding a block beneath.
    const T = `${P} .sot-mailbox-table`
    expect(rule(`${T} tr.sot-mailbox-row:has(+ tr.sot-reconnect-row)`)).toMatch(/margin-bottom:\s*0/)
    const row = rule(`${T} tr.sot-reconnect-row`)
    expect(row).toMatch(/margin:\s*-36px 0 8px/)
    expect(row).toMatch(/background:\s*transparent/)
    expect(row).toMatch(/pointer-events:\s*none/)
    expect(rule(`${T} tr.sot-reconnect-row td`)).toMatch(/height:\s*36px/)
    expect(rule(`${T} tr.sot-reconnect-row td > span`)).toMatch(/display:\s*none/)
    expect(rule(`${T} tr.sot-reconnect-row td > button`)).toMatch(/pointer-events:\s*auto/)
    // The badge's row is the 36px the overlay covers, centred on it.
    const status = rule(`${T} tr.sot-mailbox-row > td[data-label="Status"]`)
    expect(status).toMatch(/min-height:\s*36px/)
    expect(status).toMatch(/align-items:\s*center/)
  })

  it('keeps one Reconnect Gmail control per row, as the rest of the page does', () => {
    const panel = readFileSync(join(here, 'RecruitmentMailPanelRedesign.jsx'), 'utf-8')
    expect(panel.match(/Reconnect Gmail/g) || []).toHaveLength(1)
  })

  it('gives Pending Gmail and Reconnect the same compact card', () => {
    // Each list's own areas rule; the shared display rule names both.
    const areas = (table) => {
      const at = PHONE.indexOf(`${P} .${table} tbody tr:has(td[data-label]) { grid-template-areas:`)
      return at < 0 ? '' : PHONE.slice(at, PHONE.indexOf('}', at))
    }
    expect(areas('sot-pending-mailbox-table')).toContain('"candidate action"')
    const reconnect = areas('sot-reconnect-table')
    expect(reconnect).toContain('"candidate candidate"')
    expect(reconnect).toContain('"reminders action"')
  })

  it('never shrinks a field below 16px, which makes iOS Safari zoom on tap', () => {
    for (const selector of [`${P} .sot-search input`, `${P} .sot-header-actions > .sot-global-candidate-filter select`]) {
      const body = rule(selector)
      expect(body, `${selector} rule missing`).not.toBe('')
      expect(body).not.toMatch(/font-size/)
    }
  })
})
