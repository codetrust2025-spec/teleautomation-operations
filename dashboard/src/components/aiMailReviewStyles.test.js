/**
 * Guards the AI Mail Review page against shipping class names no rule matches.
 *
 * The AI nodes pool did exactly that. The 2026-08 split copied AiNodeManager
 * into this repo but not its stylesheet, so `.sot-ai-node*` existed only in the
 * markup and the section rendered in production as raw stacked text: the data
 * was all there, unstyled. Nothing failed, because an unmatched class is not an
 * error anywhere in the toolchain — not in the build, not in a render test.
 *
 * These tests therefore assert the pairing directly: every structural class the
 * page renders must have a rule somewhere in the stylesheets the entry point
 * imports. They are deliberately narrow. The app as a whole still ships many
 * unstyled classes inherited from the split, and fixing that is separate work;
 * what must not happen again is this page losing its layout silently.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (rel) => readFileSync(join(SRC, rel), 'utf8')

// The stylesheets main.jsx actually imports. A rule living in an unimported
// file would not ship, so only these count as "styled".
const STYLESHEETS = ['index.css', 'businessShell.css', 'dailyOps.css', 'recruitmentMail.css']
const css = STYLESHEETS.map(read).join('\n')

const panel = read('components/RecruitmentMailPanelRedesign.jsx')
const toggle = read('components/OcrToggle.jsx')

/** Every class in a className="..." or className={`...`} in the given source. */
function classesIn(source, filter) {
  const found = new Set()
  for (const m of source.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
    const raw = (m[1] || m[2] || '').replace(/\$\{[^}]*\}/g, ' ')
    for (const cls of raw.split(/\s+/)) {
      if (cls && /^[a-zA-Z][\w-]*$/.test(cls) && filter(cls)) found.add(cls)
    }
  }
  return [...found].sort()
}

const hasRule = (cls) => new RegExp(`\\.${cls}(?![\\w-])`).test(css)

describe('AI nodes pool is styled', () => {
  const classes = classesIn(panel, (c) => c.startsWith('sot-ai-node'))

  it('renders at least the section, grid and card classes', () => {
    expect(classes).toEqual(expect.arrayContaining([
      'sot-ai-nodes', 'sot-ai-node-grid', 'sot-ai-node', 'sot-ai-node-name',
    ]))
  })

  for (const cls of ['sot-ai-nodes', 'sot-ai-node-grid', 'sot-ai-node', 'sot-ai-node-name',
                     'sot-ai-node-primary', 'sot-ai-node-state', 'sot-ai-node-latency',
                     'sot-ai-node-actions', 'sot-ai-node-error']) {
    it(`.${cls} has a rule`, () => {
      expect(hasRule(cls)).toBe(true)
    })
  }

  // The card is built from `sot-ai-node is-${node.status}`, so the state
  // modifiers carry the offline/degraded presentation the operator reads first.
  for (const state of ['is-online', 'is-degraded', 'is-offline']) {
    it(`node state .${state} is styled`, () => {
      expect(new RegExp(`\\.sot-ai-node\\.${state}(?![\\w-])`).test(css)).toBe(true)
    })
  }

  it('styles the dl rows the health fields render into', () => {
    expect(/\.sot-ai-node dl/.test(css)).toBe(true)
    expect(/\.sot-ai-node dt/.test(css)).toBe(true)
    expect(/\.sot-ai-node dd/.test(css)).toBe(true)
  })

  it('styles the header that carries the Refresh button', () => {
    expect(/\.sot-ai-nodes > header/.test(css)).toBe(true)
  })
})

describe('OCR control is styled', () => {
  for (const cls of classesIn(toggle, (c) => c.startsWith('sot-ocr-toggle'))) {
    it(`.${cls} has a rule`, () => {
      expect(hasRule(cls)).toBe(true)
    })
  }
})

// sot-title is not listed: the title block it styled was removed, because the
// shell header already names the page. The header row now holds only actions.
describe('the page header keeps its layout', () => {
  for (const cls of ['sot-page', 'sot-header', 'sot-header-actions']) {
    it(`.${cls} has a rule`, () => {
      expect(hasRule(cls)).toBe(true)
    })
  }
})

describe('no decommissioned styling came back', () => {
  it('carries no Handler Kit rules', () => {
    expect(/\.handler-kit/.test(css)).toBe(false)
  })
})
