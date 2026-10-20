/**
 * Data Room -> Credentials (20 Oct): every credential named and shown one way.
 *
 * The tab is Credentials; each credential is shown by its standard name (utils/credentialNames.js) with its type
 * beside it; search finds it by that name, its type or its username; a dashboard login's password reads
 * "Not available (hashed)" and a service account with none stored reads "Not stored"; and Copy all copies a credential
 * the way the export writes it. On a phone the Data Room tabs are one row that scrolls, and no figure's note is cut off.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../context/ConfirmContext.jsx', () => ({ useConfirm: () => ({ confirm: vi.fn(() => Promise.resolve(true)) }) }))
vi.mock('../utils/copyToClipboard.js', () => ({ copyToClipboard: vi.fn(() => Promise.resolve(true)) }))

import { captureLayout } from '../test/captureLayout.js'
import { copyToClipboard } from '../utils/copyToClipboard.js'
import { DataRoomAccountsTab } from './DataRoomAccountsTab.jsx'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(HERE, '..', 'index.css'), 'utf8').replace(/\r\n/g, '\n')

// Invented, with names as untidy as production's were.
const ADMIN = { username: 'operations_admin', reference: 'Full dashboard', role: 'admin' }
const HANDLERS = [{ username: 'asha', reference: 'asha rao', role: 'handler' }]
const ACCOUNTS = [
  { id: 'kiran_sn', label: 'kiran kumar Servicenow  Naukri', service: 'Naukri', username: 'kiran@example.com', has_password: true, notes: 'weekly refresh' },
  { id: 'meera_sp', label: 'Meera Sailpoint Naukri', service: 'Naukri', username: 'meera@example.com', has_password: true },
  { id: 'divya', label: 'Divya RajaKumar Naukri', service: 'Naukri', username: 'divya@example.com', has_password: true },
  { id: 'tg_line', label: 'Telegram login number', service: 'Telegram', username: '+91 90000 00000', has_password: false },
]
const SECRET = 'example-password-1'

beforeEach(() => {
  window.sessionStorage.clear()
  copyToClipboard.mockClear()
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'ok', password: SECRET }) })))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const renderTab = () => render(
  <div className="cand-page dr-page">
    <DataRoomAccountsTab accounts={ACCOUNTS} logins={{ admin: ADMIN, handlers: HANDLERS }} site="https://ops.example.online" onReload={vi.fn()} canExport />
  </div>,
)
const rows = () => [...document.querySelectorAll('.dr-acct-table tbody tr:not(.dr-acct-empty-row)')]
const names = () => rows().map((r) => r.querySelector('.dr-acct-name').textContent)
const rowOf = (name) => rows().find((r) => r.querySelector('.dr-acct-name').textContent === name)
const search = (text) => fireEvent.change(screen.getByRole('searchbox', { name: 'Search credentials' }), { target: { value: text } })

describe('the Credentials table', () => {
  it('names every credential the standard way, logins first, then by name', () => {
    renderTab()
    expect(names()).toEqual(['Admin', 'Asha Rao', 'Divya RajaKumar', 'Kiran Kumar ServiceNow', 'Meera SailPoint', 'Telegram Login Number'])
    expect(rowOf('Kiran Kumar ServiceNow').querySelector('.dr-acct-svc').textContent).toBe('Naukri')   // the type, beside it
    expect(document.querySelector('thead th').textContent).toBe('Credential')
    captureLayout('data-room-credentials', document.querySelector('.dr-page'))
  })

  it('finds a credential by its name, its type or its username', () => {
    renderTab()
    search('servicenow')
    expect(names()).toEqual(['Kiran Kumar ServiceNow'])
    search('naukri')
    expect(names()).toEqual(['Divya RajaKumar', 'Kiran Kumar ServiceNow', 'Meera SailPoint'])
    search('meera@example')
    expect(names()).toEqual(['Meera SailPoint'])
    search('dashboard / tool')
    expect(names()).toEqual(['Admin', 'Asha Rao'])
  })

  it('says what a password cell cannot show: hashed for a login, not stored for an account with none', () => {
    renderTab()
    expect(rowOf('Admin').querySelector('.dr-creds-pass').textContent.trim()).toBe('Not available (hashed)')
    expect(rowOf('Asha Rao').querySelector('.dr-creds-pass').textContent.trim()).toBe('Not available (hashed)')
    expect(rowOf('Telegram Login Number').querySelector('.dr-creds-pass').textContent.trim()).toBe('Not stored')
    expect(within(rowOf('Telegram Login Number')).queryByRole('button', { name: /Show password/ })).toBeNull()
  })

  it('copies a credential the way the export writes it', async () => {
    renderTab()
    fireEvent.click(within(rowOf('Kiran Kumar ServiceNow')).getByRole('button', { name: 'Copy all' }))
    await waitFor(() => expect(copyToClipboard).toHaveBeenCalled())
    expect(copyToClipboard.mock.calls.at(-1)[0]).toBe(
      `Credential Name: Kiran Kumar ServiceNow\nType: Naukri\nUsername: kiran@example.com\nPassword: ${SECRET}\nweekly refresh`)
    copyToClipboard.mockClear()
    fireEvent.click(within(rowOf('Telegram Login Number')).getByRole('button', { name: 'Copy all' }))
    await waitFor(() => expect(copyToClipboard).toHaveBeenCalled())
    expect(copyToClipboard.mock.calls.at(-1)[0]).toBe(
      'Credential Name: Telegram Login Number\nType: Telegram\nUsername: +91 90000 00000\nPassword: Not stored')
  })

  it('is headed and searched as Credentials, with Download all credentials and Add credential', () => {
    renderTab()
    expect(screen.getByRole('heading', { name: 'Credentials' })).toBeInTheDocument()
    expect(screen.getByRole('searchbox', { name: 'Search credentials' })).toHaveAttribute('placeholder', 'Search credentials...')
    expect(screen.getByRole('combobox', { name: 'Credential type' }).options[0].textContent).toBe('All Credentials (6)')
    expect(screen.getByRole('button', { name: /Download all credentials/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Add credential/ })).toBeInTheDocument()
    expect(document.querySelector('.dr-tab-stat-title').textContent).toBe('Total credentials')
  })
})

describe('on a phone', () => {
  const phone = (() => {
    const at = CSS.indexOf('/* Phones (20 Oct): the five tabs wrapped')
    return CSS.slice(at, CSS.indexOf('\n}\n', at) + 3)
  })()

  it('keeps the Data Room tabs on one row that scrolls, each label beside its count', () => {
    expect(phone).toContain('@media (max-width: 767px)')
    expect(phone).toMatch(/\.dr-tabs \{[^}]*flex-wrap: nowrap;[^}]*overflow-x: auto;/)
    expect(phone).toMatch(/\.dr-tab \{[^}]*flex: 0 0 auto;[^}]*min-height: 40px;[^}]*white-space: nowrap;/)
  })

  it('lets a figure\'s note wrap instead of cutting it off', () => {
    expect(CSS).toMatch(/\.dr-accounts-tab \.dr-tab-stat-sub \{ margin-top: 0; overflow: visible; text-overflow: clip; white-space: normal;/)
  })

  it('lays the credential out by its Credential cell', () => {
    expect(CSS).toMatch(/td\[data-label="Credential"\] \{ grid-column: 1; grid-row: 1; \}/)
    expect(CSS).not.toMatch(/dr-acct-table tbody td\[data-label="Account"\]/)
  })
})
