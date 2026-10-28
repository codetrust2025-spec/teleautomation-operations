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
const nodePanel = readFileSync(resolve(HERE, 'AiNodeManager.jsx'), 'utf8')
const css = readFileSync(resolve(HERE, '..', 'recruitmentMail.css'), 'utf8')

describe('AI nodes collapse', () => {
  it('is a disclosure, not an always-open section', () => {
    expect(nodePanel).toContain('<details className="sot-ai-nodes"')
    expect(nodePanel).not.toContain('<section className="sot-ai-nodes"')
  })

  it('is open on every load, and still collapsible', () => {
    // It was collapsed to keep the mailbox list above the fold. Node health
    // turned out to be read often enough that a click to see it cost more
    // than the scroll does, so the default changed; it is still a <details>,
    // so one click puts it away for the rest of the visit.
    const tag = nodePanel.match(/<details className="sot-ai-nodes"[^>]*>/)[0]
    expect(tag).toMatch(/\bopen\b/)
  })

  it('still says whether the nodes are healthy while collapsed', () => {
    expect(nodePanel).toContain('sot-ai-nodes-glance')
    expect(nodePanel).toMatch(/\$\{online\}\/\$\{nodes\.length\} online/)
  })

  it('colours the glance when a node is down', () => {
    expect(nodePanel).toContain('online < nodes.length ? " is-degraded" : ""')
    expect(css).toMatch(/\.sot-ai-nodes-glance\.is-degraded \{[^}]*--sot-amber/)
  })

  it('carries no Refresh of its own', () => {
    // There were two on the page: this one refreshed node health, the header
    // one refreshed candidates and mailboxes, and which to press depended on
    // what you happened to be looking at. The header button does both now.
    const nodes = nodePanel.slice(nodePanel.indexOf('<details className="sot-ai-nodes"'))
    const summary = nodes.slice(nodes.indexOf('<summary>'), nodes.indexOf('</summary>'))
    expect(summary).not.toContain('Refresh')
    expect(summary).not.toContain('onRefresh')
  })

  it('keeps mail Refresh limited to email data', () => {
    expect(panel.match(/↻ Refresh/g) || []).toHaveLength(1)
    expect(panel).toContain('load({ showLoader: true })')
    expect(panel).not.toContain('refreshOllama')
    expect(panel).not.toContain('/ollama/')
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

  it('come after the tabs rather than above them', () => {
    expect(panel.indexOf('sot-mailbox-view-tabs'))
      .toBeLessThan(panel.indexOf('sot-list-toolbar-actions'))
  })

  it('no longer sit in the section heading', () => {
    const head = panel.slice(
      panel.indexOf('<div className="sot-overview-head">'),
      panel.indexOf('<div className="sot-mailbox-toolbar">'),
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

  it('no longer exist: every count they carried is on a tab', () => {
    // Total Mailboxes was Linked, Monitoring Active was the note inside it,
    // Pending Gmail was itself, and Reconnect Required was the Reconnect
    // tab. Four chips repeating four numbers that were already on screen.
    const row = panel.slice(
      panel.indexOf('<div className="sot-mailbox-toolbar">'),
      panel.indexOf('{showAddMailbox && ('),
    )
    expect(row).not.toContain('sot-mailbox-metrics')
    for (const label of ['Total Mailboxes', 'Monitoring Active', 'Reconnect Required']) {
      expect(row).not.toContain(label)
    }
  })

  it('says each number once, and only the filters are clickable', () => {
    const row = panel.slice(
      panel.indexOf('<div className="sot-mailbox-toolbar">'),
      panel.indexOf('{showAddMailbox && ('),
    )
    expect(row.match(/Pending Gmail/g) || []).toHaveLength(1)
    expect(row.match(/\{rows\.length\}/g) || []).toHaveLength(1)
    expect(row.match(/activeMailboxCount/g) || []).toHaveLength(1)
    // Active is a reading: there is no Active list, so it is not a tab.
    const status = row.slice(row.indexOf('sot-mailbox-status'))
    expect(status.slice(0, 200)).not.toContain('role="tab"')
    expect(status.slice(0, 200)).not.toContain('onClick')
  })

  it('stays a single row on desktop', () => {
    expect(css).toMatch(/\.sot-mailbox-metrics \{[^}]*repeat\(4, minmax\(0, 1fr\)\)/)
  })
})

describe('nothing was taken away', () => {
  it('keeps every toolbar control', () => {
    for (const control of ['Global candidate filter', 'Manage AI Controls', '↻ Refresh', '<SearchInput',
                           '+ Add Gmail']) {
      expect(panel).toContain(control)
    }
  })

  it('keeps both list tabs and the node actions', () => {
    expect(panel).toContain('Pending Gmail <span>')
    expect(nodePanel).toContain('onMakePrimary(node)')
    expect(nodePanel).toContain('Unload')
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
  it('wraps the tabs, the reading and the controls in one toolbar', () => {
    const row = panel.slice(
      panel.indexOf('<div className="sot-mailbox-toolbar">'),
      panel.indexOf('{showAddMailbox && ('),
    )
    expect(row).toContain('className="sot-mailbox-view-tabs"')
    expect(row).toContain('className="sot-mailbox-status"')
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
  it('offers Reconnect Gmail once per row, in its Actions cell rather than in the menu', () => {
    // Shown only when uiStatus is RECONNECT_REQUIRED and calling
    // onAction('reconnect', row). Since 12 Oct it sits beside the menu in
    // the row's own Actions cell; the red warning row beneath, which said
    // again what the Reconnect Required badge says, is gone.
    expect(panel.match(/Reconnect Gmail/g) || []).toHaveLength(1)
    const menu = panel.slice(panel.indexOf('export function ActionMenu'))
    expect(menu.slice(0, menu.indexOf('</details>'))).not.toContain('Reconnect Gmail')
    const row = panel.slice(panel.indexOf('export function MailboxRow'), panel.indexOf('export function MailboxTable'))
    const actions = row.slice(row.indexOf('<td data-label="Actions">'))
    expect(actions.indexOf('Reconnect Gmail')).toBeGreaterThan(-1)
    expect(actions.indexOf('Reconnect Gmail')).toBeLessThan(actions.indexOf('</td>'))
    expect(panel).not.toContain('sot-reconnect-row')
    expect(panel).not.toContain('Gmail connection expired. Reconnect to continue monitoring.')
  })

  it('keeps the row actions that are not offered anywhere else', () => {
    const menu = panel.slice(panel.indexOf('export function ActionMenu'))
    const body = menu.slice(0, menu.indexOf('</details>'))
    for (const action of ['Pause Monitoring', 'Resume Monitoring']) {
      expect(body).toContain(action)
    }
  })
})