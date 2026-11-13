// Interview Related cards (owner, 13 Nov 2026): a balanced two-line card.
// The 12 Nov one-line rows were too compressed to read. Line one carries the
// candidate, company, status, date and Unread / Opened; line two the full
// subject, up to two lines. Selection Related is unchanged.
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

describe('Interview Related: two-line email cards', () => {
  it('gives the cards moderate room: gap, padding and radius', () => {
    expect(px(rule('.mcp--interview .mcp-list'), 'gap')).toBe(8)
    const row = rule('.mcp-email__row')
    expect(row).toMatch(/padding:\s*11px 14px 11px 16px/)
    expect(px(row, 'gap')).toBeGreaterThanOrEqual(4)
    expect(px(rule('.mcp-email'), 'border-radius')).toBe(10)
  })

  it('uses two lines on a wide list: every detail, then the whole subject', () => {
    const wide = container('min-width: 1040px')
    expect(wide).toMatch(/"name\s+company\s+status\s+when\s+state"\s*\n\s*"subject\s+subject\s+subject\s+subject\s+subject"/)
  })

  it('keeps two lines on a medium list without squeezing the company out', () => {
    const mid = container('min-width: 720px')
    expect(mid).toMatch(/display:\s*grid/)
    expect(mid).toMatch(/"name\s+company\s+company\s+when"\s*\n\s*"subject\s+subject\s+status\s+state"/)
    expect(mid).toMatch(/minmax\(8rem, 1fr\)/)
    expect(mid).toMatch(/\.mcp-email__top, \.mcp-email__foot \{ display: contents; \}/)
    expect(mid).toMatch(/padding:\s*12px 16px 12px 18px/)
  })

  it('no longer squeezes a card onto one line', () => {
    expect(CSS).not.toMatch(/"name company subject status when state"/)
    expect(CSS).not.toMatch(/@container mcp-emails \(min-width: 1100px\)/)
    for (const query of ['min-width: 720px', 'min-width: 1040px']) {
      expect(container(query)).not.toMatch(/mcp-email__subject[^}]*nowrap/)
    }
  })

  it('shows the full subject over up to two lines at every width', () => {
    const subject = rule('.mcp-email__subject')
    expect(subject).toMatch(/-webkit-line-clamp:\s*2/)
    expect(subject).toMatch(/white-space:\s*normal/)
  })

  it("lays out by the list's own width, so a sidebar cannot squeeze it", () => {
    expect(rule('.mcp--interview .mcp-list')).toMatch(/container:\s*mcp-emails \/ inline-size/)
    expect(CSS).not.toMatch(/@media[^{]*\{[^@]*grid-template-areas:\s*"name company/)
  })

  it('keeps the phone stack', () => {
    expect(rule('.mcp-email__row')).toMatch(/flex-direction:\s*column/)
  })

  it('keeps every field readable: no text under 12px', () => {
    for (const selector of ['.mcp-email__name', '.mcp-email__company', '.mcp-email__subject', '.mcp-email__when', '.mcp-email .mcp-state', '.mcp-email .mcp-pill']) {
      expect(px(rule(selector), 'font-size'), selector).toBeGreaterThanOrEqual(12)
    }
  })

  it('leaves Selection Related alone', () => {
    expect(px(rule('.mcp-list'), 'gap')).toBe(10)
    for (const query of ['min-width: 720px', 'min-width: 1040px']) {
      expect(container(query)).not.toMatch(/\.mcp-group|\.mcp-mail\b|\.mcp-mail__|\.mcp--selection/)
    }
  })

  it('scopes every card rule to the Interview card or page', () => {
    const section = CSS.slice(CSS.indexOf('/* Interview Related: one card per email'), CSS.indexOf('/* The history: a timeline'))
    const selectors = [...section.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{/g)]
      .map((m) => m[1].trim()).filter((s) => !s.startsWith('@'))
    expect(selectors.length).toBeGreaterThan(10)
    for (const selector of selectors) {
      for (const part of selector.split(',')) expect(part.trim(), selector).toMatch(/\.mcp-email|\.mcp--interview/)
    }
  })
})
