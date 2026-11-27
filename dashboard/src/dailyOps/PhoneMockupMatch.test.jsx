/**
 * The current phone design replaces the 24 Nov fixed thumbnail column and
 * scrolling KPI strip. Verify the winning mobile overrides, not stale rules.
 * Actual viewport geometry still needs the browser layout harness.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ICON_NAMES } from '../components/ui/Icon.jsx'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, '..', 'dailyOps.css'), 'utf-8')
const ROSTER = readFileSync(join(here, 'InterviewRoster.jsx'), 'utf-8')
const BLOCK = CSS.slice(CSS.indexOf('/* Mobile roster refinement:'))

describe('Daily Ops current mobile mockup', () => {
  it('scopes all layout overrides to phone widths', () => {
    expect(BLOCK.length).toBeGreaterThan(100)
    expect(BLOCK.match(/@media[^{]+/g).map(q => q.trim())).toEqual(['@media (max-width: 767px)', '@media (max-width: 360px)'])
    expect(CSS.indexOf('.ops-card-icon { display: none; }')).toBeLessThan(CSS.indexOf('/* Mobile roster refinement:'))
  })

  it('uses different technology and round icons and an aligned two-column layout', () => {
    for (const [label, icon] of [['Date', 'calendar'], ['Company', 'building'], ['Technology', 'layers'], ['Round', 'list'], ['Attendee', 'user']]) {
      expect(ICON_NAMES).toContain(icon)
      const cell = ROSTER.indexOf(`data-label="${label}"`)
      expect(cell).toBeGreaterThan(0)
      expect(ROSTER.slice(cell, cell + 200)).toContain(`<CardIcon name="${icon}" />`)
    }
    expect(BLOCK).toContain('grid-template-columns: 16px minmax(0,1fr)')
    expect(BLOCK).toContain('content: attr(data-label)')
    expect(BLOCK).toContain('grid-column: 2; grid-row: 2')
    expect(BLOCK).toContain('"comp tech" "attd round"')
    expect(BLOCK).toContain('overflow-wrap: anywhere')
  })

  it('keeps count-first status cards and wraps the full list', () => {
    expect(CSS).toMatch(/\.ops-dash-kpi__value \{ order: -1;/)
    expect(BLOCK).toContain('repeat(4, minmax(56px, 1fr))')
    expect(BLOCK).toContain('repeat(3, minmax(56px, 1fr))')
  })

  it('retains all date and filter controls in a non-overlapping grid', () => {
    expect(BLOCK).toContain('.ops-roster-control--date { grid-column: 1; grid-row: 2; }')
    expect(BLOCK).toContain('.ops-roster-today { grid-column: 2; grid-row: 2; }')
    expect(BLOCK).toContain('.ops-profile-round-filter { grid-column: 1; grid-row: 3; }')
    expect(BLOCK).toContain('.ops-filter-dropdown-wrap { grid-column: 2; grid-row: 3; }')
    expect(BLOCK).toContain('min-height: 44px')
  })

  it('keeps the WhatsApp and attendance controls thumb-sized and booking chips in flow', () => {
    expect(BLOCK).toMatch(/\.ops-whatsapp \{ width: 44px; height: 44px; \}/)
    expect(BLOCK).toContain('position: static; height: auto; min-height: 22px')
    expect(BLOCK).toMatch(/\.ops-mobile-attendance \{[\s\S]*?min-height: 44px/)
    expect(ROSTER).toContain('isDashboard && phone && canManage')
  })

  it('makes the preview bigger without replacing the existing viewer', () => {
    expect(BLOCK).toContain('.ops-slot-shot-thumb { width: 110px; height: 64px; }')
    expect(ROSTER).toContain('onOpen={() => setScreenshotRow(row)}')
  })
})
