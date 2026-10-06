/**
 * Data Room after the cleanup: no Interview Data section, tabs that say which
 * one is open, copy controls that say what they copy, Accounts actions in one
 * row (Copy all, Edit, Delete), and tables that scroll inside their box
 * rather than being clipped.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../context/ConfirmContext.jsx', () => ({ useConfirm: () => ({ confirm: vi.fn() }) }))
vi.mock('../context/AuthContext.jsx', () => ({ useAuth: () => ({ role: 'admin' }) }))
vi.mock('../utils/copyToClipboard.js', () => ({ copyToClipboard: vi.fn(() => Promise.resolve(true)) }))

import { DataRoomPanel } from './DataRoomPanel.jsx'
import { DataRoomAccountsTab } from './DataRoomAccountsTab.jsx'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(HERE, '..', 'index.css'), 'utf8').replace(/\r\n/g, '\n')

const ACCOUNTS = [
  { id: 'a1', label: 'Ops Gmail (current)', service: 'Gmail', username: 'ops@example.com', password: 'pw-example' },
]

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url) => {
    const body = String(url).includes('/data-room/credentials')
      // A file still holding the removed section: the page must not show it.
      ? { status: 'ok', credentials: { admin: null, handlers: [], service_accounts: ACCOUNTS, prompts: [], resources: [], offer_letters: [], interview_data: [{ id: 'x' }] } }
      : { status: 'ok', opportunities: [], stats: { total: 0, by_status: {} } }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the Data Room tabs', () => {
  it('no longer include Interview Data', async () => {
    render(<DataRoomPanel />)
    await screen.findByText('Ops Gmail (current)')
    const labels = [...document.querySelectorAll('.dr-tab .dr-tab-label')].map(l => l.textContent)
    expect(labels).toEqual(['Logins', 'Opportunities', 'Accounts', 'Prompts', 'Key Links', 'Offers'])
    expect(screen.queryByText(/Interview Data/i)).toBeNull()
  })

  it('mark the open tab for assistive tech as well as visually', async () => {
    render(<DataRoomPanel />)
    await screen.findByText('Ops Gmail (current)')
    const open = document.querySelector('.dr-tab--active')
    expect(open).toHaveAttribute('aria-current', 'page')
    expect(open.querySelector('.dr-tab-label').textContent).toBe('Accounts')
    expect(open.querySelector('.dr-tab-count').textContent).toBe('1')
    expect(document.querySelectorAll('.dr-tab[aria-current]')).toHaveLength(1)
  })
})

describe('an Accounts row', () => {
  it('names what each copy button copies and confirms the copy', async () => {
    render(<DataRoomAccountsTab accounts={ACCOUNTS} onReload={() => {}} />)
    const row = screen.getByText('Ops Gmail (current)').closest('tr')
    const user = within(row).getByRole('button', { name: 'Copy username' })
    within(row).getByRole('button', { name: 'Copy password' })
    expect(row.querySelector('code').getAttribute('title')).toBe('ops@example.com')
    fireEvent.click(user)
    await waitFor(() => expect(within(row).getByRole('button', { name: 'Copied username' })).toBeInTheDocument())
  })

  it('keeps its actions in one row, the destructive one last', () => {
    render(<DataRoomAccountsTab accounts={ACCOUNTS} onReload={() => {}} />)
    const row = screen.getByText('Ops Gmail (current)').closest('tr')
    const actions = [...row.querySelector('.dr-acct-actions').querySelectorAll('button')].map(b => b.textContent)
    expect(actions).toEqual(['Copy all', 'Edit', 'Delete'])
    expect(document.querySelector('thead th:last-child').className).toContain('dr-acct-th--actions')
  })

  it('has a labelled search beside an Add account button', () => {
    render(<DataRoomAccountsTab accounts={ACCOUNTS} onReload={() => {}} />)
    const actions = document.querySelector('.dr-tab-header-actions')
    expect(within(actions).getByRole('searchbox', { name: 'Search accounts' })).toBeInTheDocument()
    expect(within(actions).getByRole('button', { name: 'Add account' })).toBeInTheDocument()
  })
})

describe('Data Room CSS', () => {
  const desktop = () => {
    const at = CSS.indexOf('/* ── Data Room: compact, aligned tabs, cards and tables')
    expect(at).toBeGreaterThan(-1)
    return CSS.slice(at)
  }

  it('scrolls a wide table inside its box at every width instead of clipping it', () => {
    expect(desktop()).toMatch(/\.dr-page \.dr-tab-table-wrap \{ overflow-x: auto; \}/)
  })

  it('sizes tabs to their label and keeps KPI cards to two short lines on desktop', () => {
    const rules = desktop()
    expect(rules).toMatch(/\.dr-page \.dr-tab \{\s*flex: 0 0 auto;/)
    expect(rules).toMatch(/grid-template-areas: "head head" "value sub";/)
    expect(rules).toMatch(/\.dr-page \.dr-tab-stat \{[^}]*min-height: 0;/)
  })

  it('gives the Accounts columns set widths', () => {
    const rules = desktop()
    expect(rules).toMatch(/\.dr-page \.dr-acct-table \{ table-layout: fixed; \}/)
    expect(rules).toMatch(/\.dr-acct-th--actions \{ width: \d+px; text-align: right; \}/)
  })

  it('has no Interview Data styles left', () => {
    expect(CSS).not.toContain('dr-interview')
  })
})
