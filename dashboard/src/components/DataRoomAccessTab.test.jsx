/**
 * Data Room -> Credentials (20 Oct; "Accounts & Logins" until later that day): one table for every credential.
 *
 * Each credential is shown by its standard name (utils/credentialNames.js): "Candidate 1 Naukri" under Naukri reads
 * "Candidate 1", and the service accounts are listed by name, as the export lists them.
 *
 * The dashboard's logins (the admin and the handlers) and the service accounts
 * are listed together -- 33 in production -- with one Account type filter (All
 * Accounts, Dashboard / Tool Logins, Naukri, Gmail, Telegram) whose counts come
 * from the records. A tool login and a dashboard login are one type since 20 Oct:
 * one option, one badge, counted once each -- but each is still its own record,
 * saved where it always was. Each row keeps the columns and actions of its kind: a login
 * is stored hashed, so it has nothing to reveal and is saved to the handlers
 * endpoint; a service account keeps Show password and its own endpoint. A
 * handler is still sent neither.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

const auth = vi.hoisted(() => ({ role: 'admin' }))
const confirmMock = vi.hoisted(() => ({ answer: true, asked: [] }))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: (opts) => { confirmMock.asked.push(opts); return Promise.resolve(confirmMock.answer) } }),
}))
vi.mock('../context/AuthContext.jsx', () => ({ useAuth: () => ({ role: auth.role }) }))
vi.mock('../utils/copyToClipboard.js', () => ({ copyToClipboard: vi.fn(() => Promise.resolve(true)) }))

import { captureLayout } from '../test/captureLayout.js'
import { copyToClipboard } from '../utils/copyToClipboard.js'
import { DataRoomAccountsTab, LOGIN_TYPE, SERVICE_FILTER_KEY } from './DataRoomAccountsTab.jsx'
import { DataRoomPanel } from './DataRoomPanel.jsx'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(HERE, '..', 'index.css'), 'utf8').replace(/\r\n/g, '\n')

// Invented, shaped like production on 20 Oct: one admin and five handlers (six
// logins), 26 Naukri accounts and one Telegram (27) -- 33 records, none a tool.
// As the server sends them: a login carries no password, an account says only
// whether it has one. The admin's password is planted here to prove it is never
// drawn even if a response ever carried it.
const ADMIN = { username: 'operations_admin', reference: 'Admin', role: 'admin', password: 'super-secret-pw' }
const HANDLERS = ['asha', 'kiran', 'meera', 'ravi', 'sunil'].map((u) => ({ username: u, reference: u[0].toUpperCase() + u.slice(1), role: 'handler' }))
const NAUKRI = Array.from({ length: 26 }, (_, i) => ({
  id: `naukri_${i + 1}`, label: `Candidate ${i + 1} Naukri`, service: 'Naukri', username: `candidate${i + 1}@example.com`, has_password: true,
}))
const TELEGRAM = { id: 'tg_ops', label: 'Ops Telegram', service: 'Telegram', username: '+91 90000 00000', has_password: true }
// The names the table shows: the type word dropped, the service accounts in name order.
const SHOWN = [...NAUKRI.map((_, i) => `Candidate ${i + 1}`), 'Ops'].sort((a, b) => (a.toLowerCase() < b.toLowerCase() ? -1 : 1))
const ACCOUNTS = [...NAUKRI, TELEGRAM]
const SITE = 'https://operations.example.online'
const CREDS = {
  site_url: SITE, vps_host: 'vps.example.net',
  admin: ADMIN, handlers: HANDLERS, service_accounts: ACCOUNTS,
  prompts: [], resources: [], offer_letters: [],
}
const SECRET = 'example-revealed-password'

let calls
beforeEach(() => {
  auth.role = 'admin'
  confirmMock.answer = true
  confirmMock.asked = []
  calls = []
  window.sessionStorage.clear()
  copyToClipboard.mockClear()
  vi.stubGlobal('fetch', vi.fn((url, init = {}) => {
    const u = String(url)
    calls.push({ url: u, method: init.method || 'GET', body: init.body && typeof init.body === 'string' ? JSON.parse(init.body) : null })
    const body = u.includes('/password')
      ? { status: 'ok', password: SECRET }
      : u.includes('/data-room/credentials') && !init.method
        ? { status: 'ok', credentials: CREDS }
        : u.includes('/data-room/credentials')
          ? { status: 'ok' }
          : { status: 'ok', opportunities: [], stats: { total: 0, by_status: {} } }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.sessionStorage.clear()
  document.body.style.overflow = ''
})

const renderTable = (props = {}) => render(
  <div className="cand-page dr-page">
    <DataRoomAccountsTab accounts={ACCOUNTS} logins={{ admin: ADMIN, handlers: HANDLERS }} site={SITE} vpsHost="vps.example.net" onReload={vi.fn()} canExport {...props} />
  </div>,
)
const filter = () => screen.getByRole('combobox', { name: 'Credential type' })
const choose = (value) => fireEvent.change(filter(), { target: { value } })
const rows = () => [...document.querySelectorAll('.dr-acct-table tbody tr:not(.dr-acct-empty-row)')]
const names = () => rows().map((r) => r.querySelector('.dr-acct-name').textContent)
const rowOf = (name) => rows().find((r) => r.querySelector('.dr-acct-name').textContent === name)
const dialog = () => screen.getByRole('dialog')
const fieldLabels = () => [...dialog().querySelectorAll('.dr-form-grid label')].map((l) => l.childNodes[0].textContent.trim())
const field = (label) => [...dialog().querySelectorAll('.dr-form-grid label')]
  .find((l) => l.childNodes[0].textContent.trim() === label).querySelector('input, select, textarea')
const writes = () => calls.filter((c) => c.method !== 'GET')

describe('the Data Room tabs', () => {
  it('have one Credentials tab, open first, counting all 33, and no sub-tabs inside it', async () => {
    render(<DataRoomPanel />)
    await screen.findByText('Candidate 1')
    const labels = [...document.querySelectorAll('.dr-tab .dr-tab-label')].map((l) => l.textContent)
    expect(labels).toEqual(['Credentials', 'Opportunities', 'Prompts', 'Key Links', 'Offers'])
    const open = document.querySelector('.dr-tab[aria-current="page"]')
    expect(open.querySelector('.dr-tab-label').textContent).toBe('Credentials')
    expect(open.querySelector('.dr-tab-count').textContent).toBe('33')
    expect(screen.queryByRole('tablist')).toBeNull()
    expect(screen.queryByRole('tab')).toBeNull()
    expect(document.querySelectorAll('.dr-page table')).toHaveLength(1)
    expect(rows()).toHaveLength(33)
    captureLayout('data-room-accounts-one-table', document.querySelector('.dr-page'))
  })
})

describe('the one table', () => {
  it('lists every record once: the admin, the handlers, then the service accounts', () => {
    renderTable()
    expect(rows()).toHaveLength(33)
    expect(names().slice(0, 6)).toEqual(['Admin', 'Asha', 'Kiran', 'Meera', 'Ravi', 'Sunil'])
    expect(names().slice(6)).toEqual(SHOWN)
    expect(rows().slice(0, 6).every((r) => r.classList.contains('dr-acct-login-row'))).toBe(true)
    expect(document.querySelector('.dr-tab-stat-value').textContent).toBe('33')
    expect(document.querySelector('.dr-tab-stat-sub').textContent).toBe('6 dashboard / tool logins · 27 others')
  })

  it('has one Account type filter whose counts come from the records and add up to All Accounts', () => {
    renderTable()
    expect([...filter().options].map((o) => [o.value, o.textContent])).toEqual([
      ['all', 'All Credentials (33)'], [LOGIN_TYPE, 'Dashboard / Tool Logins (6)'], ['Naukri', 'Naukri (26)'],
      ['Gmail', 'Gmail (0)'], ['Telegram', 'Telegram (1)'],
    ])
    const [[, all], ...rest] = [...filter().options].map((o) => [o.value, Number(/\((\d+)\)$/.exec(o.textContent)[1])])
    expect(rest.reduce((sum, [, n]) => sum + n, 0)).toBe(all)
  })

  it.each([
    [LOGIN_TYPE, 6, 'Dashboard / Tool'], ['Naukri', 26, 'Naukri'], ['Telegram', 1, 'Telegram'],
  ])('shows only %s records when that type is chosen', (type, count, badge) => {
    renderTable()
    choose(type)
    expect(rows()).toHaveLength(count)
    for (const r of rows()) expect(r.querySelector('.dr-acct-svc').textContent).toBe(badge)
  })

  it('counts tool logins under Dashboard / Tool Logins, once each, with no Tool option of its own', () => {
    const tools = ['Interview helper', 'Screen recorder', 'Notes app', 'Code pad'].map((label, i) => ({ id: `tool_${i}`, label, service: 'Tool', username: `t${i}@example.com`, has_password: true }))
    renderTable({ accounts: [...ACCOUNTS, ...tools] })
    expect(filter().querySelector('option[value="Tool"]')).toBeNull()
    expect(filter().querySelector(`option[value="${LOGIN_TYPE}"]`).textContent).toBe('Dashboard / Tool Logins (10)')
    expect(filter().querySelector('option[value="all"]').textContent).toBe('All Credentials (37)')
    expect(rows()).toHaveLength(37)                                                     // nothing listed twice
    choose(LOGIN_TYPE)
    // Tool names keep every word ("Tool" is not one of their words), capitalised; listed by name.
    expect(names()).toEqual(['Admin', 'Asha', 'Kiran', 'Meera', 'Ravi', 'Sunil', 'Code Pad', 'Interview Helper', 'Notes App', 'Screen Recorder'])
    for (const r of rows()) expect(r.querySelector('.dr-acct-svc').textContent).toBe('Dashboard / Tool')
    // Still a service account underneath: its own role line and its password to show.
    const tool = rowOf('Code Pad')
    expect(tool.querySelector('.dr-acct-role').textContent).toBe('Tool login')
    expect(within(tool).getByRole('button', { name: 'Show password for Code Pad' })).toBeInTheDocument()
    expect(rowOf('Kiran').querySelector('.dr-acct-role').textContent).toBe('Handler')
  })

  it('says there are none rather than inventing any, and opens it for a Tool chosen before the change', () => {
    window.sessionStorage.setItem(SERVICE_FILTER_KEY, 'Tool')
    renderTable({ logins: { admin: null, handlers: [] } })
    expect(filter().value).toBe(LOGIN_TYPE)
    expect(rows()).toHaveLength(0)
    expect(screen.getByText('No dashboard or tool logins.')).toBeInTheDocument()
  })

  it('searches logins and accounts together', () => {
    renderTable()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search credentials' }), { target: { value: 'handler' } })
    expect(names()).toEqual(['Asha', 'Kiran', 'Meera', 'Ravi', 'Sunil'])
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search credentials' }), { target: { value: 'telegram' } })
    expect(names()).toEqual(['Ops'])
  })

  it('keeps the chosen type for the session', () => {
    const first = renderTable()
    choose(LOGIN_TYPE)
    first.unmount()
    renderTable()
    expect(filter().value).toBe(LOGIN_TYPE)
    expect(rows()).toHaveLength(6)
  })
})

describe('a dashboard login row', () => {
  it('is stored hashed: nothing to reveal or copy, and no password text anywhere', () => {
    renderTable()
    for (const name of ['Admin', 'Kiran']) {
      const row = rowOf(name)
      expect(row.querySelector('.dr-acct-svc').textContent).toBe('Dashboard / Tool')
      expect(row.querySelector('.dr-creds-pass').textContent.trim()).toBe('Not available (hashed)')
      expect(within(row).queryByRole('button', { name: /Show password|Copy password/ })).toBeNull()
    }
    expect(document.body.textContent).not.toContain('super-secret-pw')
  })

  it('keeps the username in its own cell beside its copy control', () => {
    renderTable()
    const row = rowOf('Kiran')
    expect(row.querySelector('.dr-acct-cred-cell code').textContent).toBe('kiran')
    expect(within(row).getByRole('button', { name: 'Copy username' })).toBeInTheDocument()
  })

  it('copies the site, username and role with Copy all', async () => {
    renderTable()
    fireEvent.click(within(rowOf('Kiran')).getByRole('button', { name: 'Copy all' }))
    await waitFor(() => expect(copyToClipboard).toHaveBeenCalled())
    expect(copyToClipboard.mock.calls[0][0]).toBe(`Site: ${SITE}\nUsername: kiran\nPassword: Not available (hashed)\nRole: handler\nReference: Kiran`)
  })

  it('gives a handler Edit and Delete, and the admin the account-menu pointer instead', () => {
    renderTable()
    const handler = rowOf('Kiran')
    expect(within(handler).getByRole('button', { name: 'Edit Kiran' })).toBeInTheDocument()
    expect(within(handler).getByRole('button', { name: 'Delete Kiran' })).toBeInTheDocument()
    const admin = rowOf('Admin')
    // Named by its role, not by its stored reference, which in production is "Full dashboard".
    expect(admin.querySelector('.dr-acct-role').textContent).toBe('Full dashboard')
    cleanup()
    renderTable({ logins: { admin: { ...ADMIN, reference: 'Full dashboard' }, handlers: HANDLERS } })
    expect(names()[0]).toBe('Admin')
    expect(rows()[0].querySelector('.dr-acct-role').textContent).toBe('Full dashboard')
    const menuButton = within(rows()[0]).getByRole('button', { name: 'Account menu' })
    expect(menuButton.classList.contains('dr-acct-nowrap')).toBe(true)
    const menu = within(admin).getByRole('button', { name: 'Account menu' })
    expect(menu).not.toBeDisabled()
    expect(menu.className).toContain('cand-btn--ghost')
    expect(menu.getAttribute('title')).toMatch(/account menu/i)
    expect(within(admin).queryByRole('button', { name: /^(Edit|Delete) / })).toBeNull()
  })

  it('is edited at the handlers endpoint, its password sent only when typed', async () => {
    const onReload = vi.fn()
    renderTable({ onReload })
    fireEvent.click(screen.getByRole('button', { name: 'Edit Kiran' }))
    expect(screen.getByRole('dialog', { name: 'Edit dashboard login' })).toBeInTheDocument()
    expect(fieldLabels()).toEqual(['Reference (display name)', 'Username', 'Password (leave blank to keep)', 'Notes (optional)'])
    expect(field('Username')).toHaveAttribute('readonly')
    expect(field('Password (leave blank to keep)').value).toBe('')
    fireEvent.change(field('Reference (display name)'), { target: { value: 'Kiran K' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onReload).toHaveBeenCalled())
    expect(writes()).toEqual([{ url: expect.stringMatching(/\/data-room\/credentials\/handlers\/kiran$/), method: 'PATCH', body: { username: 'kiran', reference: 'Kiran K', notes: '' } }])
  })

  it('is removed at the handlers endpoint once confirmed', async () => {
    const onReload = vi.fn()
    renderTable({ onReload })
    confirmMock.answer = false
    fireEvent.click(screen.getByRole('button', { name: 'Delete Kiran' }))
    await waitFor(() => expect(confirmMock.asked).toHaveLength(1))
    expect(writes()).toHaveLength(0)
    confirmMock.answer = true
    fireEvent.click(screen.getByRole('button', { name: 'Delete Kiran' }))
    await waitFor(() => expect(onReload).toHaveBeenCalled())
    expect(confirmMock.asked[1]).toMatchObject({ title: 'Remove handler?' })
    expect(writes()).toEqual([{ url: expect.stringMatching(/\/data-room\/credentials\/handlers\/kiran$/), method: 'DELETE', body: null }])
  })
})

describe('a service account row', () => {
  it('keeps Show password, Copy all, Edit and Delete, and reads a password only when asked', async () => {
    renderTable()
    const row = rowOf('Ops')
    for (const name of ['Show password for Ops', 'Copy password', 'Copy all', 'Edit Ops', 'Delete Ops']) {
      expect(within(row).getByRole('button', { name })).toBeInTheDocument()
    }
    expect(calls.some((c) => c.url.includes('/password'))).toBe(false)
    fireEvent.click(within(row).getByRole('button', { name: 'Show password for Ops' }))
    expect(await within(row).findByText(SECRET)).toBeInTheDocument()
    expect(calls.filter((c) => c.url.includes('/password')).map((c) => c.url))
      .toEqual([expect.stringMatching(/\/service-accounts\/tg_ops\/password$/)])
  })
})

describe('Add', () => {
  it('adds a dashboard login from the same dialog, saved at the handlers endpoint', async () => {
    const onReload = vi.fn()
    renderTable({ onReload })
    fireEvent.click(screen.getByRole('button', { name: 'Add credential' }))
    expect([...field('Credential type').options].map((o) => [o.value, o.textContent])).toEqual([
      ['Naukri', 'Naukri'], ['Gmail', 'Gmail'], ['Telegram', 'Telegram'],
      ['Tool', 'Tool login (another tool or site)'], [LOGIN_TYPE, 'Dashboard login (signs in here)'],
    ])
    // Both under the one type, which says which record each makes.
    const group = field('Credential type').querySelector('optgroup')
    expect(group.label).toBe('Dashboard / Tool Login')
    expect([...group.querySelectorAll('option')].map((o) => o.value)).toEqual(['Tool', LOGIN_TYPE])
    fireEvent.change(field('Credential name'), { target: { value: 'Divya' } })
    fireEvent.change(field('Credential type'), { target: { value: LOGIN_TYPE } })
    expect(screen.getByRole('dialog', { name: 'Add dashboard login' })).toBeInTheDocument()
    expect(fieldLabels()).toEqual(['Reference (display name)', 'Credential type', 'Username', 'Password', 'Notes (optional)'])
    expect(field('Reference (display name)').value).toBe('Divya')                     // the name typed so far is kept
    expect(dialog().querySelector('input[type="file"]')).toBeNull()                    // no screenshot for a login
    fireEvent.change(field('Username'), { target: { value: 'divya' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Enter a password.')).toBeInTheDocument()
    expect(writes()).toHaveLength(0)
    fireEvent.change(field('Password'), { target: { value: 'New-Handler-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onReload).toHaveBeenCalled())
    expect(writes()).toEqual([{
      url: expect.stringMatching(/\/data-room\/credentials\/handlers$/), method: 'POST',
      body: { username: 'divya', reference: 'Divya', notes: '', password: 'New-Handler-1' },
    }])
  })

  it('starts from the type the list is narrowed to: under Dashboard / Tool Logins a tool login, a dashboard login only by choice', async () => {
    renderTable()
    choose('Telegram')
    fireEvent.click(screen.getByRole('button', { name: 'Add credential' }))
    expect(field('Credential type').value).toBe('Telegram')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    choose(LOGIN_TYPE)
    fireEvent.click(screen.getByRole('button', { name: 'Add credential' }))
    expect(screen.getByRole('dialog', { name: 'Add credential' })).toBeInTheDocument()
    expect(field('Credential type').value).toBe('Tool')
    fireEvent.change(field('Credential type'), { target: { value: LOGIN_TYPE } })
    expect(screen.getByRole('dialog', { name: 'Add dashboard login' })).toBeInTheDocument()
    fireEvent.change(field('Credential type'), { target: { value: 'Tool' } })
    expect(screen.getByRole('dialog', { name: 'Add credential' })).toBeInTheDocument()
    fireEvent.change(field('Credential name'), { target: { value: 'Interview helper' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0]).toMatchObject({ method: 'POST', body: { id: 'interview_helper', label: 'Interview helper', service: 'Tool' } })
    expect(writes()[0].url).toMatch(/\/data-room\/credentials\/vault\/service_accounts$/)
    expect('reference' in writes()[0].body).toBe(false)
  })

  it('never offers to turn a service account into a login when editing it', () => {
    renderTable()
    fireEvent.click(screen.getByRole('button', { name: 'Edit Ops' }))
    expect([...field('Credential type').options].map((o) => o.value)).toEqual(['Naukri', 'Gmail', 'Telegram', 'Tool'])
  })
})

describe('the header', () => {
  it('keeps the site, the VPS SSH host with its copy, search, the filter, Download all and Add', async () => {
    renderTable()
    expect(document.querySelector('.dr-acct-meta .dr-creds-site a').textContent).toBe('operations.example.online')
    fireEvent.click(screen.getByRole('button', { name: 'Copy SSH host' }))
    await waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith('root@vps.example.net'))
    expect(screen.getByRole('searchbox', { name: 'Search credentials' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Download all credentials/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Add credential/ })).toBeInTheDocument()
  })
})

describe('a handler', () => {
  it('is sent no credential of either kind and sees no table', async () => {
    auth.role = 'handler'
    render(<DataRoomPanel />)
    await screen.findByText('Operations opportunities')
    expect(calls.some((c) => c.url.includes('/data-room/credentials'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: /^Credentials/ }))
    await waitFor(() => expect(document.querySelector('.dr-tab[aria-current="page"] .dr-tab-label').textContent).toBe('Opportunities'))
    expect(document.querySelector('.dr-acct-table')).toBeNull()
  })
})

describe('the styles', () => {
  const block = CSS.slice(CSS.indexOf('/* Accounts & Logins (20 Oct)'), CSS.indexOf('.dr-page .dr-section {'))
  it('no longer carry the sub-tabs', () => {
    expect(CSS).not.toMatch(/\.dr-subtabs?\b|\.dr-subpanel\b/)
  })
  it('keep the account name readable between a phone and a wide desktop, Actions in reach', () => {
    const mid = block.slice(block.indexOf('@media (min-width: 641px) and (max-width: 1180px)'))
    // 996px since the Password column was widened to 184px for its dots, Show and Copy (20 Oct).
    expect(mid).toMatch(/\.dr-accounts-tab \.dr-acct-table \{ table-layout: fixed; min-width: 996px; \}/)
    expect(mid).toMatch(/\.dr-accounts-tab \.dr-acct-th--pass \{ width: 184px; \}/)
    expect(mid).toMatch(/\.dr-accounts-tab \.dr-acct-table th:last-child,\n[^{]*td:last-child \{\n    position: sticky;\n    right: 0;/)
  })
  it('make every control a 40px target on a phone', () => {
    const phone = block.slice(block.indexOf('@media (max-width: 640px)'))
    expect(phone).toMatch(/\.dr-accounts-tab \.dr-acct-table button \{ min-height: 40px; \}/)
    expect(phone).toMatch(/\.dr-accounts-tab \.dr-copy-btn--icon \{ flex: 0 0 40px; width: 40px; height: 40px; \}/)
    expect(phone).toMatch(/\.dr-accounts-tab \.dr-add-btn \{ min-height: 40px; \}/)
  })
  it('start the records near the top of a phone: stats two by two, type beside the name, no "—" lines', () => {
    const phone = block.slice(block.indexOf('@media (max-width: 640px)'))
    expect(phone).toMatch(/\.dr-accounts-tab \.dr-tab-stats \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/)
    expect(phone).toMatch(/td\[data-label="Type"\] \{ grid-column: 2; grid-row: 1;/)
    expect(phone).toMatch(/td\.dr-acct-cell--empty \{ display: none; \}/)
    renderTable()
    const login = rowOf('Kiran')
    expect([...login.querySelectorAll('td.dr-acct-cell--empty')].map((td) => td.dataset.label)).toEqual(['Status', 'Last used'])
    expect(rowOf('Ops').querySelector('td[data-label="Status"]').classList.contains('dr-acct-cell--empty')).toBe(false)
  })
  it('give Dashboard / Tool one badge colour, the same on a phone and a desktop', () => {
    expect(CSS).toMatch(/\.dr-acct-svc--dashboard \{/)
    expect(CSS).not.toMatch(/\.dr-acct-svc--tool\b/)
  })
})
