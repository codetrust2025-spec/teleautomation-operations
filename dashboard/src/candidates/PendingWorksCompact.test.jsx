/**
 * Pending Works, compact: a long explanation waits behind "Why?" (and on
 * hover) instead of repeating on every row, a short one stays; Refresh sits
 * beside the summary; High is the loudest priority and Low the quietest; a
 * group with a High task is marked down its candidate cell. Task logic and
 * order are the server's and unchanged.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { PendingWorksTab } from './PendingWorksTab.jsx'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(HERE, 'PendingWorksTab.css'), 'utf8').replace(/\r\n/g, '\n')

const LONG = '1 payment proof record has no stored image, and no other stored proof backs the recorded amount.'
const WORKS = [
  { id: 'w1', kind: 'missing_payment_proof', priority: 15, candidate_id: 'raj', candidate_name: 'Rajashekar', technology: 'AWS DevOps', label: 'Upload payment proof', detail: 'Payment of ₹20,000 recorded with no proof.' },
  { id: 'w2', kind: 'payment_proof_file_lost', priority: 45, candidate_id: 'lav', candidate_name: 'Lavanya', technology: 'Salesforce', label: 'Restore / Upload payment proof', detail: LONG },
]

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function renderTab() {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
    ok: true, json: () => Promise.resolve({ status: 'ok', works: WORKS, count: 2, candidate_count: 2 }),
  })))
  render(<PendingWorksTab />)
  await screen.findByText('Rajashekar')
}
const row = (name) => screen.getByText(name).closest('tr')

describe('explanations', () => {
  it('keep a short one on the row', async () => {
    await renderTab()
    const detail = row('Rajashekar').querySelector('.cand-pending__detail')
    expect(detail.textContent).toBe('Payment of ₹20,000 recorded with no proof.')
    expect(detail.className).not.toContain('collapsed')
    expect(within(row('Rajashekar')).queryByRole('button', { name: 'Why?' })).toBeNull()
  })

  it('put a long one behind Why?, with the whole text on hover', async () => {
    await renderTab()
    const r = row('Lavanya')
    const why = within(r).getByRole('button', { name: 'Why?' })
    expect(why).toHaveAttribute('aria-expanded', 'false')
    expect(why).toHaveAttribute('title', LONG)
    expect(r.querySelector('.cand-pending__detail').className).toContain('cand-pending__detail--collapsed')
    fireEvent.click(why)
    expect(within(r).getByRole('button', { name: 'Hide' })).toHaveAttribute('aria-expanded', 'true')
    expect(r.querySelector('.cand-pending__detail').className).not.toContain('collapsed')
    expect(CSS).toMatch(/\.cand-pending__detail--collapsed \{ display: none; \}/)
  })
})

describe('the row', () => {
  it('names the act on a compact button', async () => {
    await renderTab()
    expect(within(row('Lavanya')).getByRole('button', { name: 'Restore Proof' }).className).toContain('cand-pending-action--row')
  })

  it('marks a High group down its candidate cell, and Low stays quiet', async () => {
    await renderTab()
    expect(row('Rajashekar').querySelector('td[rowspan]').className).toContain('cand-pending__cand--high')
    expect(row('Lavanya').querySelector('td[rowspan]').className).toContain('cand-pending__cand--low')
    expect(CSS).toMatch(/\.cand-pending__cand--high \{ box-shadow: inset 3px 0 0/)
    expect(CSS).toMatch(/\.cand-pending__priority\.is-high \{[^}]*border-color:[^}]*font-weight: 800;/)
    expect(CSS).toMatch(/\.cand-pending__priority\.is-low \{[^}]*background: transparent;/)
  })
})

describe('the header', () => {
  it('puts Refresh right after the summary', async () => {
    await renderTab()
    const head = document.querySelector('.cand-pending__head')
    const [summary, refresh] = head.children
    expect(summary.className).toBe('cand-pending__summary')
    expect(refresh).toHaveTextContent('Refresh')
    expect(CSS).toMatch(/\.cand-pending__head \{ justify-content: flex-start;/)
  })

  it('gives the columns set widths on desktop', () => {
    expect(CSS).toMatch(/\.cand-pending-table \{ table-layout: fixed; \}/)
    expect(CSS).toMatch(/\.cand-pending-th--action \{ width: \d+px; \}/)
  })
})

describe('on a phone', () => {
  it('labels each card cell with its own name, not the Candidates table column', () => {
    const at = CSS.lastIndexOf('@media (max-width: 720px) {')
    expect(CSS.slice(at)).toMatch(/\.cand-page \.cand-pending \.cand-pending-table tbody td\[data-label\]::before \{ content: attr\(data-label\); \}/)
  })
})
