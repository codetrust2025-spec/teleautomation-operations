/**
 * Daily Ops on an iPhone, 24 Nov mockup: count-first status cards, the controls
 * in two rows with no box, and cards whose values are named by icons instead of
 * captions. Phone only -- every new rule sits inside max-width 767px and the
 * icons are hidden from 768px up, so the desktop table is unchanged.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ICON_NAMES } from '../components/ui/Icon.jsx'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, '..', 'dailyOps.css'), 'utf-8')
const ROSTER = readFileSync(join(here, 'InterviewRoster.jsx'), 'utf-8')
const BLOCK = CSS.slice(CSS.indexOf('/* ── Daily Ops on an iPhone: the owner'))

describe('Daily Ops phone layout matches the 24 Nov mockup', () => {
  it('lives at the end of the sheet inside the phone query, with the icons hidden everywhere else', () => {
    expect(BLOCK.length).toBeGreaterThan(100)
    expect(BLOCK.match(/@media[^{]+/g).map(q => q.trim())).toEqual(['@media (max-width: 767px)'])
    // Unscoped and before the phone block, so only a phone shows the icons.
    const hide = CSS.indexOf('.ops-card-icon { display: none; }')
    expect(hide).toBeGreaterThan(0)
    expect(hide).toBeLessThan(CSS.indexOf('/* ── Daily Ops on an iPhone: the owner'))
    expect(BLOCK).toMatch(/\.ops-card-icon \{\s*display: inline-flex;/)
  })

  it('names each card value with an icon instead of a caption', () => {
    for (const [label, icon] of [['Date', 'calendar'], ['Company', 'building'], ['Technology', 'layers'], ['Round', 'layers'], ['Attendee', 'user']]) {
      expect(ICON_NAMES).toContain(icon)
      const cell = ROSTER.indexOf(`data-label="${label}"`)
      expect(cell).toBeGreaterThan(0)
      expect(ROSTER.slice(cell, cell + 200)).toContain(`<CardIcon name="${icon}" />`)
    }
    expect(BLOCK).toContain('.ops-interview-row > td::before { display: none; }')
    expect(BLOCK).toContain('"date time time"')
    expect(BLOCK).toContain('"comp tech shot"')
    expect(BLOCK).toContain('"attd round shot"')
  })

  it('shows the count before the label and four status cards across the width', () => {
    expect(BLOCK).toMatch(/\.ops-dash-kpi__value \{ order: -1;/)
    expect(BLOCK).toContain('width: calc((100% - 18px) / 4)')
  })

  it('drops the box around the controls and puts the four filters on one row', () => {
    expect(BLOCK).toContain('.daily-ops-page .ops-roster-controls { background: transparent; border: 0;')
    expect(BLOCK).toContain('flex: 1 1 auto; width: auto; min-width: 0;')
  })
})

describe('Daily Ops phone layout, second 24 Nov mockup', () => {
  it('puts Upcoming, Today and Profile + Round on one row and Filters on its own, outlined in blue', () => {
    expect(BLOCK).toContain('.ops-roster-control--date { flex: 1.4 1 28%; }')
    expect(BLOCK).toContain('.ops-roster-today { flex: 1 1 20%; }')
    expect(BLOCK).toContain('.ops-profile-round-filter { flex: 1.7 1 36%; }')
    expect(BLOCK).toContain('.ops-filter-dropdown-wrap { order: 4; flex: 1 1 100%; }')
    expect(BLOCK).toMatch(/\.ops-filter-trigger:not\(\.ops-filter-trigger--active\):not\(\.ops-filter-trigger--open\) \{\s*border: 1px solid #3b82f6;/)
  })

  it('centres the green outlined WhatsApp circle beside the number', () => {
    expect(BLOCK).toContain('.ops-interview-identity { align-items: center; column-gap: 10px; }')
    expect(BLOCK).toMatch(/\.ops-whatsapp \{\s*width: 30px; height: 30px; margin-left: 0; align-self: center;\s*background: rgba\(37, 211, 102, \.08\); border: 1\.5px solid rgba\(37, 211, 102, \.6\);/)
  })
})
