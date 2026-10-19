/**
 * Data Room -> Accounts & Logins (20 Oct): the Logins and Accounts tabs are one
 * tab with two sub-tabs, Dashboard Logins and Service Accounts. Each still lists
 * all of its own records and nothing of the other's, keeps its own actions, and
 * a handler is still sent neither.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

const auth = vi.hoisted(() => ({ role: 'admin' }))
vi.mock('../context/ConfirmContext.jsx', () => ({ useConfirm: () => ({ confirm: vi.fn(() => Promise.resolve(false)) }) }))
vi.mock('../context/AuthContext.jsx', () => ({ useAuth: () => ({ role: auth.role }) }))
vi.mock('../utils/copyToClipboard.js', () => ({ copyToClipboard: vi.fn(() => Promise.resolve(true)) }))

import { captureLayout } from '../test/captureLayout.js'
import { DataRoomPanel } from './DataRoomPanel.jsx'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(HERE, '..', 'index.css'), 'utf8').replace(/\r\n/g, '\n')

// Invented, shaped like production on 20 Oct: one admin and five handlers (six
// logins), 26 Naukri accounts and one Telegram (27). As the server sends them:
// no login carries a password, and an account says only whether it has one.
const HANDLERS = ['asha', 'kiran', 'meera', 'ravi', 'sunil'].map((u) => ({ username: u, reference: u[0].toUpperCase() + u.slice(1), role: 'handler', account_id: `acct_${u}` }))
const ACCOUNTS = [
  ...Array.from({ length: 26 }, (_, i) => ({
    id: `naukri_${i + 1}`, label: `Naukri recruiter ${i + 1}`, service: 'Naukri', username: `recruiter${i + 1}@example.com`, has_password: true,
  })),
  { id: 'tg_ops', label: 'Ops Telegram', service: 'Telegram', username: '@example_ops', has_password: true },
]
const CREDS = {
  site_url: 'https://operations.example.online',
  admin: { username: 'operations_admin', reference: 'Admin', role: 'admin' },
  handlers: HANDLERS,
  service_accounts: ACCOUNTS,
  prompts: [], resources: [], offer_letters: [],
}
const SECRET = 'example-revealed-password'

let calls
beforeEach(() => {
  auth.role = 'admin'
  calls = []
  vi.stubGlobal('fetch', vi.fn((url) => {
    calls.push(String(url))
    const u = String(url)
    const body = u.includes('/password')
      ? { status: 'ok', password: SECRET }
      : u.includes('/data-room/credentials')
        ? { status: 'ok', credentials: CREDS }
        : { status: 'ok', opportunities: [], stats: { total: 0, by_status: {} } }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function openPanel() {
  render(<DataRoomPanel />)
  await screen.findByText('Naukri recruiter 1')
}
const panel = () => screen.getByRole('tabpanel')
const accountRows = () => [...document.querySelectorAll('.dr-acct-table tbody tr:not(.dr-acct-empty-row)')]
const loginRows = () => [...document.querySelectorAll('.dr-creds-table tbody tr')]

describe('the Data Room tabs', () => {
  it('have one Accounts & Logins tab in place of Logins and Accounts, open first, counting all 33', async () => {
    await openPanel()
    const labels = [...document.querySelectorAll('.dr-tab .dr-tab-label')].map((l) => l.textContent)
    expect(labels).toEqual(['Accounts & Logins', 'Opportunities', 'Prompts', 'Key Links', 'Offers'])
    const open = document.querySelector('.dr-tab[aria-current="page"]')
    expect(open.querySelector('.dr-tab-label').textContent).toBe('Accounts & Logins')
    expect(open.querySelector('.dr-tab-count').textContent).toBe('33')
  })
})

describe('the Accounts & Logins sub-tabs', () => {
  it('are a labelled tab list of Dashboard Logins (6) and Service Accounts (27), Service Accounts open', async () => {
    await openPanel()
    const list = screen.getByRole('tablist', { name: 'Accounts & Logins' })
    const tabs = within(list).getAllByRole('tab')
    expect(tabs.map((t) => t.getAttribute('aria-label'))).toEqual(['Dashboard Logins (6)', 'Service Accounts (27)'])
    expect(tabs.map((t) => t.getAttribute('aria-selected'))).toEqual(['false', 'true'])
    expect(tabs.map((t) => t.tabIndex)).toEqual([-1, 0])
    expect(panel()).toHaveAttribute('aria-labelledby', tabs[1].id)
    expect(tabs[1]).toHaveAttribute('aria-controls', panel().id)
    expect(tabs[0]).toHaveAttribute('title', 'Admin and handler credentials')
  })

  it('show every service account, with its search, filter, Download all and Add, and no dashboard login', async () => {
    await openPanel()
    expect(accountRows()).toHaveLength(27)
    for (const a of ACCOUNTS) expect(within(panel()).getByText(a.label)).toBeInTheDocument()
    const inside = within(panel())
    expect(inside.getByRole('searchbox', { name: 'Search accounts' })).toBeInTheDocument()
    expect(inside.getByRole('combobox', { name: 'Service type' })).toHaveDisplayValue('All Services (27)')
    expect(inside.getByRole('button', { name: /Download all accounts/ })).toBeInTheDocument()
    expect(inside.getByRole('button', { name: /Add account/ })).toBeInTheDocument()
    expect(inside.getAllByRole('button', { name: /^Edit Naukri recruiter/ })).toHaveLength(26)
    expect(inside.getAllByRole('button', { name: /^Delete / })).toHaveLength(27)
    expect(inside.getAllByRole('button', { name: /^Show password for/ })).toHaveLength(27)
    // Nothing of the other kind.
    expect(document.querySelector('.dr-creds-table')).toBeNull()
    expect(panel().textContent).not.toMatch(/operations_admin|Add handler/)
    for (const h of HANDLERS) expect(within(panel()).queryByText(h.username)).toBeNull()
  })

  it('show every dashboard login, with Add handler and each handler’s Edit and Delete, and no service account', async () => {
    await openPanel()
    fireEvent.click(screen.getByRole('tab', { name: 'Dashboard Logins (6)' }))
    await screen.findByText('operations_admin')
    expect(loginRows()).toHaveLength(6)
    for (const h of HANDLERS) expect(within(panel()).getByText(h.username)).toBeInTheDocument()
    const inside = within(panel())
    expect(inside.getByRole('button', { name: /Add handler/ })).toBeInTheDocument()
    expect(inside.getAllByRole('button', { name: 'Edit' })).toHaveLength(5)
    expect(inside.getAllByRole('button', { name: 'Delete' })).toHaveLength(5)
    // Each of the six has its username chip and, with no password to bundle (the
    // admin's is withheld too), the labelled Copy username in the Copy column.
    expect(inside.getAllByRole('button', { name: 'Copy username' })).toHaveLength(12)
    // A login's password is a hash on the server: nothing to show, nothing sent.
    expect(inside.queryByRole('button', { name: /Show password/ })).toBeNull()
    expect(document.querySelector('.dr-acct-table')).toBeNull()
    expect(panel().textContent).not.toMatch(/Naukri|Telegram|recruiter|Download all/)
    expect(screen.getByRole('tab', { name: 'Dashboard Logins (6)' })).toHaveAttribute('aria-selected', 'true')
    captureLayout('data-room-dashboard-logins', document.querySelector('.dr-page'))
  })

  it('move with the arrow keys, Home and End, as a tab list does', async () => {
    await openPanel()
    const [logins, accounts] = within(screen.getByRole('tablist')).getAllByRole('tab')
    accounts.focus()
    fireEvent.keyDown(accounts, { key: 'ArrowRight' })
    expect(logins).toHaveAttribute('aria-selected', 'true')
    expect(document.activeElement).toBe(logins)
    expect(await screen.findByText('operations_admin')).toBeInTheDocument()
    fireEvent.keyDown(logins, { key: 'End' })
    expect(accounts).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(accounts, { key: 'Home' })
    expect(logins).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(logins, { key: 'ArrowLeft' })
    expect(accounts).toHaveAttribute('aria-selected', 'true')
    expect(document.activeElement).toBe(accounts)
  })

  it('keep the open sub-tab while another Data Room tab is visited', async () => {
    await openPanel()
    fireEvent.click(screen.getByRole('tab', { name: 'Dashboard Logins (6)' }))
    fireEvent.click(screen.getByRole('button', { name: /^Prompts/ }))
    expect(screen.queryByRole('tablist')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Accounts & Logins/ }))
    expect(screen.getByRole('tab', { name: 'Dashboard Logins (6)' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('operations_admin')).toBeInTheDocument()
  })
})

describe('a service account password', () => {
  it('is read from the server for that one account when asked for, and gone once the sub-tab is left', async () => {
    await openPanel()
    expect(calls.some((u) => u.includes('/password'))).toBe(false)
    expect(document.body.textContent).not.toContain(SECRET)
    fireEvent.click(screen.getByRole('button', { name: 'Show password for Ops Telegram' }))
    expect(await screen.findByText(SECRET)).toBeInTheDocument()
    expect(calls.filter((u) => u.includes('/password'))).toEqual([
      expect.stringMatching(/\/data-room\/credentials\/service-accounts\/tg_ops\/password$/),
    ])
    fireEvent.click(screen.getByRole('tab', { name: 'Dashboard Logins (6)' }))
    await screen.findByText('operations_admin')
    expect(document.body.textContent).not.toContain(SECRET)
    fireEvent.click(screen.getByRole('tab', { name: 'Service Accounts (27)' }))
    await screen.findByText('Ops Telegram')
    expect(document.body.textContent).not.toContain(SECRET)
    captureLayout('data-room-accounts-logins', document.querySelector('.dr-page'))
  })
})

describe('a handler', () => {
  it('is sent neither kind of credential and sees no Accounts & Logins content', async () => {
    auth.role = 'handler'
    render(<DataRoomPanel />)
    await screen.findByText('Operations opportunities')
    expect(calls.some((u) => u.includes('/data-room/credentials'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: /^Accounts & Logins/ }))
    await waitFor(() => expect(document.querySelector('.dr-tab[aria-current="page"] .dr-tab-label').textContent).toBe('Opportunities'))
    expect(screen.queryByRole('tablist')).toBeNull()
    expect(document.querySelector('.dr-creds-table, .dr-acct-table')).toBeNull()
  })
})

describe('the sub-tab styles', () => {
  const block = CSS.slice(CSS.indexOf('/* Accounts & Logins (20 Oct)'), CSS.indexOf('.dr-page .dr-section {'))
  it('share the width on a phone, each at least 40px tall', () => {
    expect(block).toMatch(/@media \(max-width: 640px\) \{[^@]*\.dr-subtab \{ flex: 1 1 0; min-width: 0; min-height: 40px;/)
  })
  it('take the Logins panel out of its own box inside the tab', () => {
    expect(block).toMatch(/\.dr-page \.dr-subpanel > \.dr-credentials-section \{\n  padding: 0;\n  border: 0;/)
  })
  it('keep the account name readable between a phone and a wide desktop, Actions in reach', () => {
    // At 1024px the fixed columns left Account 0px wide: one letter per line.
    const mid = block.slice(block.indexOf('@media (min-width: 641px) and (max-width: 1180px)'))
    expect(mid).toMatch(/\.dr-subpanel \.dr-acct-table \{ table-layout: fixed; min-width: 940px; \}/)
    expect(mid).toMatch(/\.dr-subpanel \.dr-acct-table th:last-child,\n[^{]*td:last-child \{\n    position: sticky;\n    right: 0;/)
    expect(block).toMatch(/@media \(min-width: 600px\) and \(max-width: 1180px\) \{\n  \.dr-page \.dr-subpanel \.dr-creds-table th:last-child,\n  \.dr-page \.dr-subpanel \.dr-creds-table td:last-child \{\n    position: sticky;/)
  })
  it('make every control in either sub-tab a 40px target on a phone', () => {
    const phone = block.slice(block.indexOf('@media (max-width: 640px)'))
    expect(phone).toMatch(/\.dr-subpanel \.dr-creds-table button,\n  \.dr-page \.dr-subpanel \.dr-acct-table button \{ min-height: 40px; \}/)
    expect(phone).toMatch(/\.dr-subpanel \.dr-copy-btn--icon \{ flex: 0 0 40px; width: 40px; height: 40px; \}/)
    // The header buttons too: Add handler, Add account, Download all accounts.
    expect(phone).toMatch(/\.dr-page \.dr-subpanel \.dr-add-btn \{ min-height: 40px; \}/)
  })
})
