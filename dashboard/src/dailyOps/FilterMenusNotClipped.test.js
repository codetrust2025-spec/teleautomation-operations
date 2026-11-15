/**
 * The Filters panel and the Levels menu open below their buttons, inside the
 * roster controls box. That box clips its content (overflow: hidden) by
 * default and only let it out above 1100px, so between 768 and 1100px the
 * panel was cut off at the box's bottom edge: 110px of a 457px panel showed
 * at 915px on 15 Nov. jsdom performs no layout, so this reads the stylesheet:
 * at every width the box's last word on overflow must be "visible".
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, '..', 'dailyOps.css'), 'utf-8').replace(/\r\n/g, '\n')

/** Every `.ops-roster-controls` rule that sets overflow, tagged with its
 *  `@media` prelude, in source order (the sheet nests one level deep; the same
 *  reading MobileDailyOps.test.jsx uses). */
function overflowRules() {
  const css = CSS.replace(/\/\*[\s\S]*?\*\//g, '')
  const found = []
  let media = null
  let prelude = ''
  for (let i = 0; i < css.length; i += 1) {
    const ch = css[i]
    if (ch === '{') {
      const head = prelude.trim()
      prelude = ''
      if (head.startsWith('@')) {
        media = head
      } else {
        const end = css.indexOf('}', i)
        const body = css.slice(i + 1, end)
        const own = head.split(',').map(part => part.trim())
        const value = /overflow\s*:\s*([a-z]+)/.exec(body)?.[1]
        if (own.includes('.ops-roster-controls') && value) found.push({ media, value })
        i = end
      }
    } else if (ch === '}') {
      media = null
      prelude = ''
    } else {
      prelude += ch
    }
  }
  return found
}

const applies = (query, width) => {
  if (!query) return true
  const max = /max-width:\s*(\d+)px/.exec(query)
  const min = /min-width:\s*(\d+)px/.exec(query)
  return (!max || width <= Number(max[1])) && (!min || width >= Number(min[1]))
}

describe('the roster controls box lets its menus out', () => {
  it('ends on overflow: visible at every width from phone to desktop', () => {
    const rules = overflowRules()
    expect(rules.length).toBeGreaterThan(0)
    for (const width of [360, 390, 440, 600, 768, 900, 915, 1024, 1100, 1101, 1280, 1440]) {
      const last = rules.filter(r => applies(r.media, width)).at(-1)
      expect(last?.value, `${width}px`).toBe('visible')
    }
  })
})
