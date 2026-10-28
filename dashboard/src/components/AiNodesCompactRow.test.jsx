/**
 * The AI nodes panel is glanced at, so it shows what an operator acts on.
 *
 * Each node was a definition list of nine rows -- host URL, Ollama version, the
 * GPU/CPU split, vision-model state, last success, last failure -- laid out two
 * per line, which made three nodes taller than the panel around them. Kept:
 * name, PRIMARY, Online/Offline, Ready/Busy, latency, Set primary, Unload.
 *
 * Nothing was deleted from the health endpoint; those fields are still there
 * for anyone debugging a node. They are just no longer on the dashboard.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = dirname(fileURLToPath(import.meta.url))
const panel = readFileSync(join(SRC, 'AiNodeManager.jsx'), 'utf8')
const css = readFileSync(join(SRC, '..', 'recruitmentMail.css'), 'utf8')

/** The node card, isolated from the rest of the panel. */
function nodeCard() {
  const start = panel.indexOf('className={`sot-ai-node is-${node.status')
  const end = panel.indexOf('</article>', start)
  expect(start).toBeGreaterThan(-1)
  return panel.slice(start, end)
}

describe('the AI node row keeps only what is acted on', () => {
  it('shows the name, the primary badge, both states, latency and both actions', () => {
    const card = nodeCard()
    expect(card).toContain('node.label')
    expect(card).toContain('PRIMARY')
    expect(card).toContain('Online')
    expect(card).toContain('Offline')
    expect(card).toContain('Ready')
    expect(card).toContain('Busy')
    expect(card).toContain('response_time_ms')
    expect(card).toContain('Set primary')
    expect(card).toContain('Unload')
  })

  it('no longer shows the host URL, the version or the other diagnostics', () => {
    const card = nodeCard()
    for (const gone of ['node.endpoint ', 'ollama_version', 'Acceleration', 'gpu_fraction',
                        'Vision model', 'Last success', 'Last failure', 'Endpoint']) {
      expect(card).not.toContain(gone)
    }
  })

  it('drops the definition list the nine rows lived in', () => {
    const card = nodeCard()
    expect(card).not.toContain('<dl>')
    expect(card).not.toContain('<dt>')
  })

  it('still reaches the endpoint for Online, not a separate guess', () => {
    // One source for reachability: the same field the Unload button disables on.
    const card = nodeCard()
    expect(card).toContain('node.endpoint_reachable ? "Online" : "Offline"')
    expect(card).toContain('disabled={busy || !node.endpoint_reachable}')
  })

  it('distinguishes Busy from Ready rather than conflating them', () => {
    // model_loaded and ready are different facts: a model held in memory versus
    // every required model installed. One label meaning either would be a lie
    // in whichever case it did not describe.
    const card = nodeCard()
    expect(card).toContain('node.model_loaded')
    expect(card).toContain('node.ready')
    expect(card).toContain('"Not ready"')
  })

  it('offers Set primary only on a node that is not already primary', () => {
    expect(nodeCard()).toContain('{!node.primary && (')
  })

  it('says the override is time-limited on the control that starts it', () => {
    expect(nodeCard()).toContain('for one hour')
  })
})

describe('the three nodes fit one desktop row', () => {
  it('lays the grid out in three columns', () => {
    expect(css).toMatch(/\.sot-ai-node-grid \{[^}]*grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)/)
  })

  it('still collapses to one column on a narrow screen', () => {
    const mobile = css.slice(css.indexOf('@media'))
    expect(mobile).toMatch(/\.sot-ai-node-grid \{\s*grid-template-columns: 1fr/)
  })

  it('gives the row its own compact styling', () => {
    for (const rule of ['.sot-ai-node-name', '.sot-ai-node-primary',
                        '.sot-ai-node-state', '.sot-ai-node-latency',
                        '.sot-ai-node-actions']) {
      expect(css).toContain(rule)
    }
  })
})
