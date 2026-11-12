// Interview Related: compact rows (owner, 12 Nov 2026) -- as many emails on
// screen as possible, with candidate, company, subject, status, date and
// Unread / Opened all still on the card. Selection Related is unchanged.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, 'MailCategoryPage.css'), 'utf-8').replace(/\r\n/g, '\n')
const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
// The body of the first top-level-or-nested rule whose selector is exactly this.
const rule = (selector, css = CSS) => {
  const match = css.match(new RegExp(`(?:^|\\n)\\s*${escapeRe(selector)}\\s*\\{([^}]*)\\}`))
  return match ? match[1] : ''
}
const container = (query) => {
  const start = CSS.indexOf(`@container mcp-emails (${query}) {`)
  expect(start, query).toBeGreaterThan(-1)
  let depth = 0
  for (let i = CSS.indexOf('{', start); i < CSS.length; i += 1) {
    if (CSS[i] === '{') depth += 1
    if (CSS[i] === '}' && --depth === 0) return CSS.slice(start, i + 1)
  }
  return ''
}
const px = (body, prop) => Number((body.match(new RegExp(`(?:^|[;\\s])${prop}:\\s*([\\d.]+)px`)) || [])[1])

describe('Interview Related: compact email rows', () => {
  it('packs the rows tightly: small gap, slim padding, small radius', () => {
    expect(px(rule('.mcp--interview .mcp-list'), 'gap')).toBeLessThanOrEqual(4)
    const row = rule('.mcp-email__row')
    expect(row).toMatch(/padding:\s*6px\s/)
    expect(px(row, 'gap')).toBeLessThanOrEqual(2)
    expect(px(rule('.mcp-email'), 'border-radius')).toBeLessThanOrEqual(8)
  })

  it('puts every field on one line on a wide desktop', () => {
    expect(container('min-width: 1100px')).toMatch(/grid-template-areas:\s*"name company subject status when state"/)
  })

  it("lays out by the list's own width, so a sidebar cannot squeeze it", () => {
    expect(rule('.mcp--interview .mcp-list')).toMatch(/container:\s*mcp-emails \/ inline-size/)
    expect(CSS).not.toMatch(/@media[^{]*\{[^@]*grid-template-areas:\s*"name company subject/)
  })

  it('uses two lines between phone and desktop width', () => {
    const mid = container('min-width: 560px')
    expect(mid).toMatch(/display:\s*grid/)
    expect(mid).toMatch(/"name\s+company\s+company\s+state"/)
    expect(mid).toMatch(/"subject\s+subject\s+status\s+when"/)
    expect(mid).toMatch(/\.mcp-email__top, \.mcp-email__foot \{ display: contents; \}/)
  })

  it('keeps the phone stack, with the subject readable over two lines', () => {
    expect(rule('.mcp-email__row')).toMatch(/flex-direction:\s*column/)
    expect(rule('.mcp-email__subject')).toMatch(/-webkit-line-clamp:\s*2/)
  })

  it('keeps every field readable: no text under 11.5px', () => {
    for (const selector of ['.mcp-email__name', '.mcp-email__company', '.mcp-email__subject', '.mcp-email__when', '.mcp-email .mcp-state', '.mcp-email .mcp-pill']) {
      expect(px(rule(selector), 'font-size'), selector).toBeGreaterThanOrEqual(11.5)
    }
  })

  it('leaves Selection Related alone: its list gap is not compacted', () => {
    expect(px(rule('.mcp-list'), 'gap')).toBe(10)
    for (const query of ['min-width: 560px', 'min-width: 1100px']) {
      expect(container(query)).not.toMatch(/\.mcp-group|\.mcp-mail\b|\.mcp-mail__|\.mcp--selection/)
    }
  })

  it('scopes every compact rule to the Interview card or page', () => {
    const section = CSS.slice(CSS.indexOf('/* Interview Related: one compact row per email'), CSS.indexOf('/* The history: a timeline'))
    const selectors = [...section.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{/g)]
      .map((m) => m[1].trim()).filter((s) => !s.startsWith('@'))
    expect(selectors.length).toBeGreaterThan(10)
    for (const selector of selectors) {
      for (const part of selector.split(',')) expect(part.trim(), selector).toMatch(/\.mcp-email|\.mcp--interview/)
    }
  })
})
