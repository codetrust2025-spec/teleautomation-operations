/**
 * Data Room -> Accounts by type (19 Oct; one table with the dashboard logins since 20 Oct).
 *
 * Every account is Naukri, Gmail, Telegram or a Dashboard / Tool Login (or "Not set" until someone says which). An Account type filter
 * beside the search narrows the list to one type, with the count of each in the dropdown -- and every account is counted
 * once, so All Accounts is always the sum of the options below it. Search and filter work together, and the choice
 * holds for the browser session. A new account is Naukri unless another is chosen. The list
 * carries no passwords: one is read from the server when it is shown, copied or edited.
 */
import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/ConfirmContext.jsx', () => ({ useConfirm: () => ({ confirm: vi.fn() }) }))
vi.mock('../utils/copyToClipboard.js', () => ({ copyToClipboard: vi.fn(() => Promise.resolve(true)) }))

import { copyToClipboard } from '../utils/copyToClipboard.js'
import { DataRoomAccountsTab, LOGIN_TYPE, SERVICE_FILTER_KEY, UNSET_SERVICE } from './DataRoomAccountsTab.jsx'

// Invented names, addresses and secrets.
const ACCOUNTS = [
  { id: 'asha_naukri', label: 'Asha Rao Naukri', service: 'Naukri', username: 'asha.rao@example.com', has_password: true },
  { id: 'ravi_naukri', label: 'Ravi Kumar Naukri', service: 'Naukri', username: 'ravi@example.com', has_password: true },
  { id: 'ops_gmail', label: 'Ops mailbox', service: 'Gmail', username: 'ops@example.com', has_password: true },
  { id: 'tg_login', label: 'Telegram login number', service: 'Telegram', username: '+91 90000 00000', has_password: true },
  { id: 'unknown_one', label: '', service: '', username: 'someone@example.com', has_password: false },
]
const PASSWORDS = { asha_naukri: 'Asha-Pass-1 ', ravi_naukri: 'Ravi=2', ops_gmail: 'ops-5', tg_login: 'tg-3' }

let calls

beforeEach(() => {
  calls = []
  window.sessionStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
    const call = { url: String(url), method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null }
    calls.push(call)
    const read = call.url.match(/service-accounts\/([^/]+)\/password$/)
    const json = read
      ? (PASSWORDS[read[1]] !== undefined ? { status: 'ok', password: PASSWORDS[read[1]] } : { status: 'error', detail: 'Not found' })
      : { status: 'ok' }
    return { ok: !read || PASSWORDS[read[1]] !== undefined, status: 200, json: async () => json }
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
  window.sessionStorage.clear()
  document.body.style.overflow = ''
})

const renderTab = (props = {}) => render(<DataRoomAccountsTab accounts={ACCOUNTS} onReload={() => {}} {...props} />)
const filter = () => screen.getByRole('combobox', { name: 'Account type' })
const choose = value => fireEvent.change(filter(), { target: { value } })
const search = text => fireEvent.change(screen.getByRole('searchbox', { name: 'Search accounts' }), { target: { value: text } })
const shownNames = () => [...document.querySelectorAll('tbody .dr-acct-name')].map(n => n.textContent)
const optionCounts = () => [...filter().options].map(o => [o.value, Number(/\((\d+)\)$/.exec(o.textContent)[1])])
const passwordReads = () => calls.filter(c => c.url.endsWith('/password'))

describe('the Account type filter', () => {
  it('sits beside the search with every service and its count', () => {
    renderTab()
    expect(filter().previousElementSibling).toBe(screen.getByRole('searchbox').closest('.dr-search-wrap'))
    expect([...filter().options].map(o => [o.value, o.textContent])).toEqual([
      ['all', 'All Accounts (5)'], [LOGIN_TYPE, 'Dashboard / Tool Logins (0)'], ['Naukri', 'Naukri (2)'], ['Gmail', 'Gmail (1)'],
      ['Telegram', 'Telegram (1)'], [UNSET_SERVICE, 'Not set (1)'],
    ])
    expect(filter().value).toBe('all')
    expect(shownNames()).toHaveLength(5)
  })

  it('counts every account once, so All Accounts is the sum of the rest', () => {
    // Production on 19 Oct: 26 Naukri logins, one Telegram account and one with no service -- 28 in all. The
    // dropdown said 26 + 0 + 1 = 27 against a total of 28; the 28th is now counted under Not set.
    const production = [
      ...Array.from({ length: 26 }, (_, i) => ({ id: `n${i}`, label: `Candidate ${i} Naukri`, service: 'Naukri', username: `c${i}@example.com` })),
      { id: 'tg', label: 'Telegram login number', service: 'Telegram', username: '+91 90000 00001' },
      { id: 'blank', label: '', service: '', username: 'blank@example.com' },
    ]
    renderTab({ accounts: production })
    expect(optionCounts()).toEqual([['all', 28], [LOGIN_TYPE, 0], ['Naukri', 26], ['Gmail', 0], ['Telegram', 1], [UNSET_SERVICE, 1]])
    const [[, all], ...rest] = optionCounts()
    expect(rest.reduce((sum, [, n]) => sum + n, 0)).toBe(all)
    expect(document.querySelector('.dr-tab-stat-value').textContent).toBe(String(all))   // the Total accounts card agrees
    choose('all')
    expect(shownNames()).toHaveLength(28)
  })

  it('counts a service it does not know under Not set rather than dropping it', () => {
    renderTab({ accounts: [...ACCOUNTS, { id: 'odd', label: 'Odd one', service: 'LinkedIn', username: 'odd@example.com' }] })
    const [[, all], ...rest] = optionCounts()
    expect(all).toBe(6)
    expect(rest.reduce((sum, [, n]) => sum + n, 0)).toBe(6)
    choose(UNSET_SERVICE)
    expect(shownNames()).toEqual(['unknown_one', 'Odd one'])
  })

  it('offers Not set only while an account has no service, and forgets it once none is left', () => {
    window.sessionStorage.setItem(SERVICE_FILTER_KEY, UNSET_SERVICE)
    renderTab({ accounts: ACCOUNTS.filter(a => a.service) })
    expect([...filter().options].map(o => o.value)).toEqual(['all', LOGIN_TYPE, 'Naukri', 'Gmail', 'Telegram'])
    expect(filter().value).toBe('all')
    expect(shownNames()).toHaveLength(4)
    expect(document.querySelector('.dr-svc-notice')).toBeNull()
  })

  it('says how many accounts still need a service, and shows them', () => {
    renderTab()
    const notice = screen.getByRole('status')
    expect(notice.textContent).toContain('1 account has no service type yet.')
    fireEvent.click(within(notice).getByRole('button', { name: 'Show it' }))
    expect(filter().value).toBe(UNSET_SERVICE)
    expect(shownNames()).toEqual(['unknown_one'])
    expect(document.querySelector('.dr-svc-notice')).toBeNull()               // no need to say it while showing them
  })

  it.each([
    ['Naukri', ['Asha Rao Naukri', 'Ravi Kumar Naukri']],
    ['Gmail', ['Ops mailbox']],
    ['Telegram', ['Telegram login number']],
  ])('shows only %s accounts when it is chosen', (service, names) => {
    renderTab()
    choose(service)
    expect(shownNames()).toEqual(names)
    for (const badge of document.querySelectorAll('tbody .dr-acct-svc')) expect(badge.textContent).toBe(service)
  })

  it('lists an account with no service under Not set and All Accounts, never under a service', () => {
    renderTab()
    for (const service of ['Naukri', 'Gmail', 'Telegram', LOGIN_TYPE]) {
      choose(service)
      expect(screen.queryByText('someone@example.com')).toBeNull()
    }
    choose(UNSET_SERVICE)
    expect(screen.getByText('someone@example.com')).toBeInTheDocument()
    choose('all')
    expect(screen.getByText('someone@example.com')).toBeInTheDocument()
  })

  it('explains an empty Gmail list: a Naukri login on a Gmail address is Naukri', () => {
    renderTab({ accounts: ACCOUNTS.filter(a => a.service !== 'Gmail') })
    choose('Gmail')
    expect(screen.getByText('No Gmail accounts. Naukri logins that use a Gmail address are under Naukri.')).toBeInTheDocument()
  })

  it('narrows the same list as the search', () => {
    renderTab()
    choose('Naukri')
    search('ravi')
    expect(shownNames()).toEqual(['Ravi Kumar Naukri'])
    search('ops')                                                         // a Gmail account: not under Naukri
    expect(shownNames()).toEqual([])
    expect(screen.getByText('No Naukri accounts match your search.')).toBeInTheDocument()
    choose('all')
    expect(shownNames()).toEqual(['Ops mailbox'])
  })

  it('says so when a service has no accounts', () => {
    renderTab({ accounts: ACCOUNTS.filter(a => a.service !== 'Telegram') })
    choose('Telegram')
    expect(screen.getByText('No Telegram accounts.')).toBeInTheDocument()
    expect(filter().querySelector('option[value="Telegram"]').textContent).toBe('Telegram (0)')
  })

  it('is remembered while moving around the Data Room, and only for this session', () => {
    const first = renderTab()
    choose('Telegram')
    expect(window.sessionStorage.getItem(SERVICE_FILTER_KEY)).toBe('Telegram')
    first.unmount()                                                       // another tab, then back to Accounts
    renderTab()
    expect(filter().value).toBe('Telegram')
    expect(shownNames()).toEqual(['Telegram login number'])
  })

  it('ignores a remembered value that is not a service', () => {
    window.sessionStorage.setItem(SERVICE_FILTER_KEY, 'LinkedIn')
    renderTab()
    expect(filter().value).toBe('all')
  })
})

describe('every row', () => {
  it('shows its service, and Not set when none is recorded', () => {
    renderTab()
    const badges = [...document.querySelectorAll('tbody tr')].map(tr => tr.querySelector('.dr-acct-svc'))
    expect(badges.map(b => b.textContent)).toEqual(['Naukri', 'Naukri', 'Gmail', 'Telegram', 'Not set'])
    expect(badges[4].className).toContain('dr-acct-svc--none')
    // labelled for the card layout on a phone
    expect(badges[0].closest('td').getAttribute('data-label')).toBe('Type')
  })

  it('reads a password from the server only when asked, and hides it again', async () => {
    renderTab()
    expect(document.body.textContent).not.toContain('Asha-Pass-1')
    expect(passwordReads()).toHaveLength(0)
    const row = screen.getByText('Asha Rao Naukri').closest('tr')
    fireEvent.click(within(row).getByRole('button', { name: 'Show password for Asha Rao Naukri' }))
    await waitFor(() => expect(row.querySelector('.dr-creds-pass').textContent).toBe('Asha-Pass-1 '))
    expect(passwordReads().map(c => c.url)).toEqual([expect.stringMatching(/\/data-room\/credentials\/service-accounts\/asha_naukri\/password$/)])
    fireEvent.click(within(row).getByRole('button', { name: 'Hide password for Asha Rao Naukri' }))
    expect(row.querySelector('.dr-creds-pass').textContent).toBe('••••••••')
  })

  it('copies a password exactly, read on demand', async () => {
    renderTab()
    const row = screen.getByText('Ravi Kumar Naukri').closest('tr')
    fireEvent.click(within(row).getByRole('button', { name: 'Copy password' }))
    await waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith('Ravi=2'))
    expect(passwordReads()).toHaveLength(1)
  })

  it('has no password controls when there is no password', () => {
    renderTab()
    const row = screen.getByText('someone@example.com').closest('tr')
    expect(within(row).queryByRole('button', { name: /Show password/ })).toBeNull()
    expect(within(row).queryByRole('button', { name: 'Copy password' })).toBeNull()
  })
})

describe('adding and editing', () => {
  const dialog = () => screen.getByRole('dialog')
  const field = label => [...dialog().querySelectorAll('.dr-form-grid label')]
    .find(l => l.childNodes[0].textContent.trim() === label).querySelector('input, select, textarea')
  const save = () => fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  const writes = () => calls.filter(c => c.method !== 'GET')

  it('adds a Naukri account unless another service is chosen', async () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    fireEvent.change(field('Account name'), { target: { value: 'Kiran Naukri' } })
    fireEvent.change(field('Username / email'), { target: { value: 'kiran@example.com' } })
    fireEvent.change(field('Password'), { target: { value: 'kiran-pw' } })
    save()
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0]).toMatchObject({ method: 'POST', body: { id: 'kiran_naukri', service: 'Naukri', password: 'kiran-pw' } })
  })

  it('says under the Account type that a Naukri login on a Gmail address is Naukri', () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    const select = field('Account type')
    expect(document.getElementById(select.getAttribute('aria-describedby')).textContent)
      .toBe('A Naukri login is Naukri even when its username is a Gmail address.')
  })

  it('adds a Telegram account when Telegram is chosen', async () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    fireEvent.change(field('Account name'), { target: { value: 'Second Telegram' } })
    fireEvent.change(field('Account type'), { target: { value: 'Telegram' } })
    save()
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0].body.service).toBe('Telegram')
  })

  it('asks for a service before saving an account that has none, rather than guessing', async () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Edit unknown_one' }))   // no name: shown by its id
    expect(field('Account type').value).toBe('')
    save()
    expect(await screen.findByText('Choose a service type.')).toBeInTheDocument()
    expect(writes()).toHaveLength(0)
    fireEvent.change(field('Account type'), { target: { value: 'Gmail' } })
    save()
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0]).toMatchObject({ method: 'PATCH', body: { service: 'Gmail' } })
    expect(writes()[0].url).toMatch(/\/data-room\/credentials\/vault\/service_accounts\/unknown_one$/)
  })

  it('changes the service without resending the password', async () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Edit Ops mailbox' }))
    await waitFor(() => expect(field('Password').value).toBe('ops-5'))   // read so it can be seen and changed
    fireEvent.change(field('Account type'), { target: { value: 'Naukri' } })
    save()
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0].body.service).toBe('Naukri')
    expect('password' in writes()[0].body).toBe(false)
  })

  it('sends a password only when it is changed', async () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Edit Asha Rao Naukri' }))
    await waitFor(() => expect(field('Password').value).toBe('Asha-Pass-1 '))
    fireEvent.change(field('Password'), { target: { value: 'Brand-New-9' } })
    save()
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0].body).toMatchObject({ service: 'Naukri', password: 'Brand-New-9' })
  })
})
