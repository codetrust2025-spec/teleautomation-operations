import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(fileURLToPath(import.meta.url))
describe('AI Controls stylesheet cascade', () => {
  it('loads scoped overrides after the shared components they reuse', () => {
    const entry = readFileSync(join(root, 'main.jsx'), 'utf8')
    const panel = readFileSync(join(root, 'components/AiControlsDashboard.jsx'), 'utf8')
    expect(entry.indexOf("import './components/AiControlsDashboard.css'")).toBeGreaterThan(entry.indexOf("import './recruitmentMail.css'"))
    // An earlier component import wins Vite's deduplication and defeats the
    // entry-point order even when the same CSS is imported again at the end.
    expect(panel).not.toMatch(/import\s+['"].*AiControlsDashboard\.css/)
  })
})

describe('AI Controls on the shared kit', () => {
  const css = readFileSync(join(root, 'components/AiControlsDashboard.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
  it('takes every colour and font size from tokens', () => {
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b/i)
    expect(css).not.toMatch(/rgba?\(|hsla?\(/i)
    expect(css).not.toMatch(/font-size:\s*[\d.]+(px|rem|em)/)
  })
  it('no longer pulls in the Mail Alerts page scope', () => {
    const panel = readFileSync(join(root, 'components/AiControlsDashboard.jsx'), 'utf8')
    expect(panel).not.toMatch(/sot-mailboxes-page|sot-page/)
    expect(panel).toContain('ui-page')
  })
})

