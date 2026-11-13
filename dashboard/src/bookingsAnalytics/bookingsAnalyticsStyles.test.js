import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const css = readFileSync(join(here, 'bookingsAnalytics.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
const panel = readFileSync(join(here, 'BookingsAnalyticsPanel.jsx'), 'utf8')

describe('Bookings Analytics on the shared kit', () => {
  it('takes every colour and font size from tokens', () => {
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b/i)
    expect(css).not.toMatch(/rgba?\(|hsla?\(/i)
    expect(css).not.toMatch(/font-size:\s*[\d.]+(px|rem|em)/)
  })

  it('uses the kit for its buttons, alerts and card surfaces', () => {
    expect(panel).toMatch(/from '\.\.\/components\/ui\/Button\.jsx'/)
    expect(panel).not.toMatch(/className="ba-button[ "]|ba-button--(primary|ghost)|className="ba-link"|ba-count/)
    expect(panel.match(/className="ui-card ba-card/g)).toHaveLength(4)
  })

  it('keeps phone layouts on the shared breakpoints', () => {
    const widths = new Set([...css.matchAll(/@media[^{]*\((?:max|min)-width:\s*(\d+)px\)/g)].map(m => m[1]))
    expect([...widths].filter(w => !['599', '899', '1280', '1499'].includes(w))).toEqual([])
  })
})
