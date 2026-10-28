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
