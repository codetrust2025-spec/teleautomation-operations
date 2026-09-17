/**
 * The Gmail operations page, with the list high enough to see.
 *
 * The header, the AI node grid and four tall metric cards took roughly half
 * the viewport before the mailbox table began, so the list an operator opened
 * the page for started below the fold. Nothing was removed to fix that: the
 * node grid collapses, the cards became chips, and search and Add Gmail moved
 * down to sit beside the list they act on.
 *
 * These are source and stylesheet assertions rather than a rendered layout,
 * because jsdom does no layout — it reports every height as zero, so a test
 * that "measured" the page here would be measuring nothing. What is pinned
 * instead is the structure and the specific spacing values, which is what a
 * regression would change.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const panel = readFileSync(resolve(HERE, 'RecruitmentMailPanelRedesign.jsx'), 'utf8')
const css = readFileSync(resolve(HERE, '..', 'recruitmentMail.css'), 'utf8')

describe('AI nodes collapse', () => {
  it('is a disclosure, not an always-open section', () => {
    expect(panel).toContain('<details className="sot-ai-nodes"')
    expect(panel).not.toContain('<section className="sot-ai-nodes"')
  })

  it('is open on every load, and still collapsible', () => {
    // It was collapsed to keep the mailbox list above the fold. Node health
    // turned out to be read often enough that a click to see it cost more
    // than the scroll does, so the default changed; it is still a <details>,
    // so one click puts it away for the rest of the visit.
    const tag = panel.match(/<details className="sot-ai-nodes"[^>]*>/)[0]
    expect(tag).toMatch(/\bopen\b/)
  })

  it('still says whether the nodes are healthy while collapsed', () => {
    expect(panel).toContain('sot-ai-nodes-glance')
    expect(panel).toMatch(/\$\{online\}\/\$\{nodes\.length\} online/)
  })

  it('colours the glance when a node is down', () => {
    expect(panel).toContain('online < nodes.length ? " is-degraded" : ""')
    expect(css).toMatch(/\.sot-ai-nodes-glance\.is-degraded \{[^}]*--sot-amber/)
  })

  it('carries no Refresh of its own', () => {
    // There were two on the page: this one refreshed node health, the header
    // one refreshed candidates and mailboxes, and which to press depended on
    // what you happened to be looking at. The header button does both now.
    const nodes = panel.slice(panel.indexOf('<details className="sot-ai-nodes"'))
    const summary = nodes.slice(nodes.indexOf('<summary>'), nodes.indexOf('</summary>'))
    expect(summary).not.toContain('Refresh')
    expect(summary).not.toContain('onRefresh')
  })

  it('leaves exactly one Refresh button on the page', () => {
    // The visible label, not the word: the file also says "Refreshing" as a
    // loading state and "Refresh record" in the evidence panel, which is a
    // different screen.
    expect(panel.match(/↻ Refresh/g) || []).toHaveLength(1)
  })

  it('makes that one Refresh reload node health as well', () => {
    const header = panel.slice(panel.indexOf('The only Refresh on the page'))
    const button = header.slice(0, header.indexOf('</button>'))
    expect(button).toContain('load({ showLoader: true })')
    expect(button).toContain('refreshOllama(true)')
  })

  it('replaces the native marker with one that still reads as expandable', () => {
    expect(css).toMatch(/\.sot-ai-nodes > summary::-webkit-details-marker \{\s*display: none;/)
    expect(css).toMatch(/\.sot-ai-nodes > summary::before \{[^}]*content: "▸"/)
    expect(css).toMatch(/\.sot-ai-nodes\[open\] > summary::before \{[^}]*rotate\(90deg\)/)
  })
})

describe('search and Add Gmail sit above the list', () => {
  it('are inside the toolbar row', () => {
    const toolbar = panel.slice(
      panel.indexOf('sot-list-toolbar-actions'),
      panel.indexOf('sot-list-toolbar-actions') + 700,
    )
    expect(toolbar).toContain('<SearchInput')
    expect(toolbar).toContain('+ Add Gmail')
  })

  it('come after the metric chips rather than above them', () => {
    expect(panel.indexOf('sot-mailbox-metrics'))
      .toBeLessThan(panel.indexOf('sot-list-toolbar'))
  })

  it('no longer sit in the section heading', () => {
    const head = panel.slice(
      panel.indexOf('<div className="sot-overview-head">'),
      panel.indexOf('sot-mailbox-metrics'),
    )
    expect(head).not.toContain('<SearchInput')
    expect(head).not.toContain('+ Add Gmail')
  })

  it('opens its form next to the button rather than further up the page', () => {
    expect(panel.indexOf('sot-list-toolbar'))
      .toBeLessThan(panel.indexOf('className="sot-add-mailbox-form"'))
  })

  it('keeps the tablist free of things that are not tabs', () => {
    const tablist = panel.slice(
      panel.indexOf('className="sot-mailbox-view-tabs"'),
      panel.indexOf('sot-list-toolbar-actions'),
    )
    expect(tablist).not.toContain('<SearchInput')
    // Three tabs now: Linked, Pending Gmail, Reconnect. What matters is
    // that only tabs live in here.
    expect(tablist.match(/role="tab"/g).length).toBeGreaterThanOrEqual(2)
    expect(tablist).not.toContain('sot-add-mailbox-button')
  })
})

describe('the metric cards became chips', () => {
  const scoped = css.slice(css.indexOf('Compact Gmail operations layout'))

  it('drops the fixed card height', () => {
    expect(scoped).toMatch(/\.sot-mailboxes-page \.sot-mailbox-metric \{[^}]*min-height: 0;/)
  })

  it('tightens their padding and spacing', () => {
    expect(scoped).toMatch(/\.sot-mailboxes-page \.sot-mailbox-metric \{[^}]*padding: 7px 10px;/)
    expect(scoped).toMatch(/\.sot-mailboxes-page \.sot-mailbox-metrics \{[^}]*margin: 0 0 10px;/)
  })

  it('still shows all four, reconnect count included', () => {
    const metrics = panel.slice(panel.indexOf('sot-mailbox-metrics'))
    for (const label of ['Total Mailboxes', 'Monitoring Active', 'Pending Gmail',
                         'Reconnect Required']) {
      expect(metrics).toContain(`label="${label}"`)
    }
  })

  it('keeps the reconnect chip hidden when nothing is broken', () => {
    expect(panel).toContain('{reconnectRequiredCount > 0 && (')
  })

  it('stays a single row on desktop', () => {
    expect(css).toMatch(/\.sot-mailbox-metrics \{[^}]*repeat\(4, minmax\(0, 1fr\)\)/)
  })
})

describe('nothing was taken away', () => {
  it('keeps every toolbar control', () => {
    for (const control of ['Global candidate filter', '<PureOllamaToggle />',
                           '<OcrToggle />', '↻ Refresh', '<SearchInput',
                           '+ Add Gmail']) {
      expect(panel).toContain(control)
    }
  })

  it('keeps both list tabs and the node actions', () => {
    expect(panel).toContain('Pending Gmail <span>')
    expect(panel).toContain('onMakePrimary(node)')
    expect(panel).toContain('Unload')
  })
})

describe('the compaction is scoped and ordered', () => {
  it('applies only to this page, so other pages keep their spacing', () => {
    const scoped = css.slice(css.indexOf('Compact Gmail operations layout'))
    const overrides = scoped.match(/^\.sot-(header|title|content-card|mailbox-metric|overview-head)[^,{]*\{/gm)
    expect(overrides).toBeNull()
  })

  it('comes after the rules it overrides, or it would be dead', () => {
    // Anchored to the start of a line so the scoped ".sot-mailboxes-page
    // .sot-mailbox-metric {" rules in the new block do not match themselves.
    const base = [...css.matchAll(/^\.sot-(?:mailbox-metric|header) \{/gm)]
    expect(base.length).toBeGreaterThan(0)
    const lastBase = Math.max(...base.map(match => match.index))
    expect(css.indexOf('Compact Gmail operations layout')).toBeGreaterThan(lastBase)
  })
})

describe('responsive', () => {
  const scoped = css.slice(css.indexOf('Compact Gmail operations layout'))

  it('gives search full width once the toolbar stacks', () => {
    const tablet = scoped.slice(scoped.indexOf('@media (max-width: 900px)'))
    expect(tablet).toMatch(/\.sot-list-toolbar-actions \{[^}]*width: 100%;/)
  })

  it('halves the chip row on tablet', () => {
    const tablet = scoped.slice(scoped.indexOf('@media (max-width: 900px)'))
    expect(tablet).toMatch(/repeat\(2, minmax\(0, 1fr\)\)/)
  })

  it('stacks the header on a phone', () => {
    const phone = scoped.slice(scoped.indexOf('@media (max-width: 560px)'))
    expect(phone).toMatch(/\.sot-mailboxes-page \.sot-header \{[^}]*flex-direction: column;/)
  })

  it('lets the toolbar wrap rather than overflow', () => {
    expect(scoped).toMatch(/\.sot-list-toolbar \{[^}]*flex-wrap: wrap;/)
    expect(scoped).toMatch(/\.sot-mailboxes-page \.sot-header-actions \{[^}]*flex-wrap: wrap;/)
  })
})

describe('the counts, tabs and controls share one row', () => {
  // The stylesheet is checked out with CRLF on Windows, so every assertion
  // below reads a copy with one kind of line ending.
  const sheet = css.split(String.fromCharCode(13)).join('')

  // Measured in a browser against this stylesheet, 1440px wide: the two rows
  // were 110px from the top of the chips to the bottom of the toolbar, and the
  // single row is 58px. jsdom reports both as zero, so what is pinned here is
  // the structure and the rules that produce it.
  it('wraps all three in one toolbar', () => {
    const row = panel.slice(
      panel.indexOf('<div className="sot-mailbox-toolbar">'),
      panel.indexOf('{showAddMailbox && ('),
    )
    expect(row).toContain('className="sot-mailbox-metrics"')
    expect(row).toContain('className="sot-mailbox-view-tabs"')
    expect(row).toContain('sot-list-toolbar-actions')
  })

  it('lays that toolbar out as a row', () => {
    const rule = sheet.slice(sheet.indexOf('.sot-mailbox-toolbar {'))
    expect(rule.slice(0, 160)).toContain('display: flex')
    expect(rule.slice(0, 160)).toContain('flex-wrap: wrap')
  })

  it('keeps search and Add Gmail hard right', () => {
    const actions = sheet.slice(
      sheet.indexOf('.sot-mailbox-toolbar .sot-list-toolbar-actions {'))
    expect(actions.slice(0, 200)).toContain('margin-left: auto')
  })

  it('never lets the inner toolbar wrap on desktop, which is what made two rows', () => {
    const inner = sheet.slice(sheet.indexOf('.sot-mailbox-toolbar .sot-list-toolbar {'))
    expect(inner.slice(0, 420)).toContain('flex-wrap: nowrap')
  })

  it('gives the search a floor rather than letting it collapse', () => {
    // At 1440 with a fourth chip it shrank to 92px before this, which is not a
    // search box.
    const search = sheet.slice(
      sheet.indexOf('.sot-mailbox-toolbar .sot-list-toolbar-actions .sot-search {'))
    expect(search.slice(0, 300)).toContain('min-width: 150px')
  })

  it('drops that floor below the tablet breakpoint, where it is wider than the screen', () => {
    const mobile = sheet.slice(sheet.indexOf('/* Tablet and narrower'))
    expect(mobile.slice(0, 520)).toContain('.sot-mailbox-toolbar .sot-list-toolbar')
    expect(mobile.slice(0, 520)).toContain('min-width: 0')
  })
})


describe('one control per action', () => {
  it('offers Reconnect Gmail once per row, on the warning rather than in the menu', () => {
    // Both appeared only when uiStatus is RECONNECT_REQUIRED and both called
    // onAction('reconnect', row): the same action on the same row, two
    // clicks apart. The banner sits next to the sentence explaining why, so
    // it is the one that stayed.
    expect(panel.match(/Reconnect Gmail/g) || []).toHaveLength(1)
    const menu = panel.slice(panel.indexOf('export function ActionMenu'))
    expect(menu.slice(0, menu.indexOf('</details>'))).not.toContain('Reconnect Gmail')
  })

  it('keeps the row actions that are not offered anywhere else', () => {
    const menu = panel.slice(panel.indexOf('export function ActionMenu'))
    const body = menu.slice(0, menu.indexOf('</details>'))
    for (const action of ['Pause Monitoring', 'Resume Monitoring']) {
      expect(body).toContain(action)
    }
  })
})