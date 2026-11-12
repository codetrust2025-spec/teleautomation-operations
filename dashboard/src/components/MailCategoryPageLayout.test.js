// Selection Related and Interview Related: one full-width card per row on
// every screen size (owner, 12 Nov 2026). No two-column desktop grid.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, 'MailCategoryPage.css'), 'utf-8').replace(/\r\n/g, '\n')
const listRules = [...CSS.matchAll(/\.mcp-list\s*\{([^}]*)\}/g)].map((m) => m[1])

describe('Mail Alerts category pages: card layout', () => {
  it('stacks the cards in one column', () => {
    expect(listRules[0]).toMatch(/display:\s*flex/)
    expect(listRules[0]).toMatch(/flex-direction:\s*column/)
  })

  it('never turns the card list into a grid at any width', () => {
    for (const body of listRules) {
      expect(body).not.toMatch(/display:\s*grid/)
      expect(body).not.toMatch(/grid-template-columns/)
    }
    expect(CSS).not.toMatch(/\.mcp-list[^{]*\{[^}]*grid-template-columns/)
  })

  it('lets each card take the full row without pushing the page sideways', () => {
    expect(CSS).toMatch(/\.mcp-list > \*\s*\{[^}]*width:\s*100%[^}]*max-width:\s*100%/)
    expect(listRules[0]).toMatch(/min-width:\s*0/)
  })
})
