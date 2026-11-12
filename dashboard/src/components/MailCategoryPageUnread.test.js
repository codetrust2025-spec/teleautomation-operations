// Unread vs Opened on both Mail Alerts category pages: the difference reads at
// a glance (owner, 12 Nov 2026). Unread: blue left edge, tinted background,
// bold text, solid blue badge. Opened: neutral border, darker background,
// normal text, subtle grey badge.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, 'MailCategoryPage.css'), 'utf-8').replace(/\r\n/g, '\n')
const section = CSS.slice(CSS.indexOf('/* Unread vs Opened'))
const rule = (selector) => {
  const at = section.indexOf(`${selector} {`) >= 0 ? section.indexOf(`${selector} {`) : section.indexOf(`${selector},`)
  expect(at, `${selector} is styled`).toBeGreaterThanOrEqual(0)
  return section.slice(section.indexOf('{', at), section.indexOf('}', at))
}

describe('Mail Alerts: Unread vs Opened', () => {
  it('is the last section, so phone and base rules cannot undo it', () => {
    expect(section.length).toBeGreaterThan(0)
    expect(section).not.toMatch(/@media/)
  })

  it('gives unread cards on both pages a blue left edge and a tinted background', () => {
    const unread = rule('.mcp-group--unread')
    expect(section).toMatch(/\.mcp-email--unread,\s*\.mcp-group--unread\s*\{/)
    expect(unread).toMatch(/background:\s*var\(--mcp-unread-bg\)/)
    expect(unread).toMatch(/box-shadow:\s*inset 4px 0 0 var\(--mcp-unread-edge\)/)
  })

  it('gives opened cards a neutral border and a darker background, no edge', () => {
    expect(section).toMatch(/\.mcp-email--read,\s*\.mcp-group:not\(\.mcp-group--unread\)\s*\{[^}]*border-color:\s*var\(--mcp-read-border\)[^}]*background:\s*var\(--mcp-read-bg\)[^}]*box-shadow:\s*none/)
  })

  it('makes unread text bold and opened text normal', () => {
    expect(section).toMatch(/\.mcp-group--unread \.mcp-group__name\s*\{[^}]*font-weight:\s*700/)
    expect(section).toMatch(/\.mcp-group:not\(\.mcp-group--unread\) \.mcp-group__name\s*\{[^}]*font-weight:\s*500/)
    expect(section).toMatch(/\.mcp-email--read \.mcp-email__subject\s*\{[^}]*font-weight:\s*400/)
  })

  it('draws a solid blue Unread badge and a quiet grey Opened badge', () => {
    expect(rule('.mcp-state--unread')).toMatch(/background:\s*#2563eb/)
    expect(rule('.mcp-state--read')).toMatch(/background:\s*transparent/)
  })

  it('defines its colours on the page root', () => {
    expect(section).toMatch(/\.mcp\s*\{[^}]*--mcp-unread-bg[^}]*--mcp-read-bg/)
  })
})
