/**
 * Data Room -> Accounts by service (19 Oct).
 *
 * Every account is Naukri, Gmail or Telegram (or "Not set" until someone says which). A Service type filter beside
 * the search narrows the list to one service, with the count of each in the dropdown; search and filter work
 * together, and the choice holds for the browser session. A new account is Naukri unless another is chosen. The list
 * carries no passwords: one is read from the server when it is shown, copied or edited.
 */
import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/ConfirmContext.jsx', () => ({ useConfirm: () => ({ confirm: vi.fn() }) }))
vi.mock('../utils/copyToClipboard.js', () => ({ copyToClipboard: vi.fn(() => Promise.resolve(true)) }))

import { copyToClipboard } from '../utils/copyToClipboard.js'
import { DataRoomAccountsTab, SERVICE_FILTER_KEY } from './DataRoomAccountsTab.jsx'

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
const filter = () => screen.getByRole('combobox', { name: 'Service type' })
const choose = value => fireEvent.change(filter(), { target: { value } })
const search = text => fireEvent.change(screen.getByRole('searchbox', { name: 'Search accounts' }), { target: { value: text } })
const shownNames = () => [...document.querySelectorAll('tbody .dr-acct-name')].map(n => n.textContent)
const passwordReads = () => calls.filter(c => c.url.endsWith('/password'))

describe('the Service type filter', () => {
  it('sits beside the search with every service and its count', () => {
    renderTab()
    expect(filter().previousElementSibling).toBe(screen.getByRole('searchbox').closest('.dr-search-wrap'))
    expect([...filter().options].map(o => [o.value, o.textContent])).toEqual([
      ['all', 'All Services (5)'], ['Naukri', 'Naukri (2)'], ['Gmail', 'Gmail (1)'], ['Telegram', 'Telegram (1)'],
    ])
    expect(filter().value).toBe('all')
    expect(shownNames()).toHaveLength(5)
  })

  it.each([
    ['Naukri', ['Asha Rao Naukri', 'Ravi Kumar Naukri']],
    ['Gmail', ['Ops mailbox']],
    ['Telegram', ['Telegram login number']],
  ])('shows only %s accounts when it is chosen', (service, names) => {
    renderTab()
    choose(service)
    expect(shownNames()).toEqual(names)
    for (const badge of document.querySelectorAll('tbody .dr-svc-badge')) expect(badge.textContent).toBe(service)
  })

  it('lists an account with no service under All Services only', () => {
    renderTab()
    for (const service of ['Naukri', 'Gmail', 'Telegram']) {
      choose(service)
      expect(screen.queryByText('someone@example.com')).toBeNull()
    }
    choose('all')
    expect(screen.getByText('someone@example.com')).toBeInTheDocument()
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
    const badges = [...document.querySelectorAll('tbody tr')].map(tr => tr.querySelector('.dr-svc-badge'))
    expect(badges.map(b => b.textContent)).toEqual(['Naukri', 'Naukri', 'Gmail', 'Telegram', 'Not set'])
    expect(badges[4].className).toContain('dr-svc-badge--none')
    // labelled for the card layout on a phone
    expect(badges[0].closest('td').getAttribute('data-label')).toBe('Service')
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

  it('adds a Telegram account when Telegram is chosen', async () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    fireEvent.change(field('Account name'), { target: { value: 'Second Telegram' } })
    fireEvent.change(field('Service type'), { target: { value: 'Telegram' } })
    save()
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0].body.service).toBe('Telegram')
  })

  it('asks for a service before saving an account that has none, rather than guessing', async () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Edit unknown_one' }))   // no name: shown by its id
    expect(field('Service type').value).toBe('')
    save()
    expect(await screen.findByText('Choose a service type.')).toBeInTheDocument()
    expect(writes()).toHaveLength(0)
    fireEvent.change(field('Service type'), { target: { value: 'Gmail' } })
    save()
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0]).toMatchObject({ method: 'PATCH', body: { service: 'Gmail' } })
    expect(writes()[0].url).toMatch(/\/data-room\/credentials\/vault\/service_accounts\/unknown_one$/)
  })

  it('changes the service without resending the password', async () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Edit Ops mailbox' }))
    await waitFor(() => expect(field('Password').value).toBe('ops-5'))   // read so it can be seen and changed
    fireEvent.change(field('Service type'), { target: { value: 'Naukri' } })
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
