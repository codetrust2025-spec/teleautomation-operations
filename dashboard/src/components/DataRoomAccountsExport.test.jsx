/**
 * Download the credentials shown (Data Room -> Credentials, 19 Oct; every credential since 20 Oct; what the table
 * shows since later on 20 Oct).
 *
 * The admin downloads, as Excel, CSV or text, exactly the credentials the table shows: under the chosen type
 * (All Credentials, Dashboard / Tool Logins, Naukri, Gmail, Telegram) and the search. The button says how many --
 * Download (26) under Naukri -- and with none shown the dialog says there is nothing to download rather than making an
 * empty file. The request names the shown credentials in its body, never in the URL. There is no password prompt and
 * no encryption step; the one thing between the button and the file is a confirmation that says what it holds.
 */
import React from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../context/ConfirmContext.jsx'
import { DataRoomAccountsTab, LOGIN_TYPE, downloadName } from './DataRoomAccountsTab.jsx'

// Invented, shaped like production on 20 Oct: one admin and five handlers, 26 Naukri accounts (two of them
// ServiceNow), no Gmail, one Telegram -- 33. As the list arrives: whether there is a password, never the password.
const LOGINS = {
  admin: { username: 'operations_admin', reference: 'Full dashboard', role: 'admin' },
  handlers: ['asha', 'kiran', 'meera', 'ravi', 'sunil'].map(u => ({ username: u, reference: u[0].toUpperCase() + u.slice(1), role: 'handler' })),
}
const NAUKRI = [
  ...Array.from({ length: 24 }, (_, i) => ({ id: `n${i + 1}`, label: `Candidate ${i + 1} Naukri`, service: 'Naukri', username: `c${i + 1}@example.com`, has_password: true })),
  { id: 'sn1', label: 'Divya Servicenow Naukri', service: 'Naukri', username: 'divya@example.com', has_password: true },
  { id: 'sn2', label: 'Kiran Servicenow Naukri', service: 'Naukri', username: 'kiran.k@example.com', has_password: true },
]
const TELEGRAM = { id: 'tg', label: 'Telegram login number', service: 'Telegram', username: '+91 90000 00000', has_password: false }
const ACCOUNTS = [...NAUKRI, TELEGRAM]

let calls
let reply
let saved

beforeEach(() => {
  calls = []
  saved = []
  window.sessionStorage.clear()
  reply = { ok: true, status: 200, disposition: 'attachment; filename="teleautomation-credentials-naukri-20261020-0930.xlsx"', count: null, json: {} }
  vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null, credentials: init.credentials })
    const body = calls[calls.length - 1].body
    return {
      ok: reply.ok,
      status: reply.status,
      headers: {
        get: name => ({
          'content-disposition': reply.disposition,
          'x-credential-count': String(reply.count ?? body?.credentials?.length ?? ''),
        })[name.toLowerCase()] ?? null,
      },
      blob: async () => new Blob(['data']),
      json: async () => reply.json,
    }
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:export'), revokeObjectURL: vi.fn() })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() { saved.push(this.download) })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.sessionStorage.clear()
  document.body.style.overflow = ''
})

function renderTab(props = {}) {
  return render(
    <ConfirmProvider>
      <DataRoomAccountsTab accounts={ACCOUNTS} logins={LOGINS} onReload={() => {}} canExport {...props} />
    </ConfirmProvider>,
  )
}

const downloadButton = () => screen.getByRole('button', { name: /^Download \(\d+\)$/ })
const choose = value => fireEvent.change(screen.getByRole('combobox', { name: 'Credential type' }), { target: { value } })
const search = text => fireEvent.change(screen.getByRole('searchbox', { name: 'Search credentials' }), { target: { value: text } })
const openExport = () => {
  fireEvent.click(downloadButton())
  return screen.getByRole('dialog')
}
const startDownload = () => fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^Download \(\d+\)$/ }))
const confirmDownload = () => fireEvent.click(screen.getByRole('button', { name: 'Yes, download' }))

describe('the Download button', () => {
  it('sits next to Add credential, for the admin, and is not there for anyone else', () => {
    renderTab()
    expect(downloadButton().nextElementSibling).toBe(screen.getByRole('button', { name: 'Add credential' }))
    cleanup()
    renderTab({ canExport: false })
    expect(screen.queryByRole('button', { name: /^Download/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'Add credential' })).toBeTruthy()
  })

  it.each([
    ['all', 'Download (33)'], [LOGIN_TYPE, 'Download (6)'], ['Naukri', 'Download (26)'], ['Gmail', 'Download (0)'], ['Telegram', 'Download (1)'],
  ])('says how many it will download under %s: %s', (type, label) => {
    renderTab()
    choose(type)
    expect(downloadButton().textContent).toBe(label)
  })

  it('counts only what a search leaves on screen', () => {
    renderTab()
    choose('Naukri')
    search('servicenow')
    expect(downloadButton().textContent).toBe('Download (2)')
    expect(downloadButton()).toHaveAttribute('title', 'Download the 2 credentials shown')
  })
})

describe('the dialog says what the file will hold', () => {
  it('everything, under All Credentials', () => {
    renderTab()
    const dialog = openExport()
    expect(dialog).toHaveAccessibleName('Download all credentials')
    expect(dialog.textContent).toContain('All 33 credentials: credential name, type, username and password.')
    expect(dialog.textContent).toContain('Not available (hashed)')
    expect(dialog.textContent).toContain('The file holds every password in plain text.')
    expect([...dialog.querySelectorAll('.dr-export-format')].map(l => l.textContent)).toEqual(['Excel (.xlsx)', 'CSV (.csv)', 'Text (.txt)'])
    expect(dialog.querySelector('input[type="password"], input[type="checkbox"]')).toBeNull()
  })

  it.each([
    ['Naukri', 'Download 26 credentials', 'The 26 Naukri credentials: credential name, type, username and password.'],
    [LOGIN_TYPE, 'Download 6 credentials', 'The 6 dashboard / tool login credentials: credential name, type, username and password.'],
    ['Telegram', 'Download 1 credential', 'The 1 Telegram credential: credential name, type, username and password.'],
  ])('only the %s credentials, under that type', (type, title, lead) => {
    renderTab()
    choose(type)
    const dialog = openExport()
    expect(dialog).toHaveAccessibleName(title)
    expect(dialog.textContent).toContain(lead)
  })

  it('only the credentials a search shows', () => {
    renderTab()
    choose('Naukri')
    search('servicenow')
    const dialog = openExport()
    expect(dialog).toHaveAccessibleName('Download 2 credentials')
    expect(dialog.textContent).toContain('The 2 credentials shown for “servicenow” under Naukri: credential name, type, username and password.')
  })

  it('and asks once, counting only the passwords it will hold, before anything is downloaded', () => {
    renderTab()
    choose('Naukri')
    openExport()
    startDownload()
    expect(screen.getByRole('alertdialog', { name: 'Download every password?' }).textContent)
      .toContain('This Excel (.xlsx) file will contain 26 passwords in plain text.')
    expect(calls).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    cleanup()
    renderTab()
    choose(LOGIN_TYPE)
    openExport()
    startDownload()
    expect(screen.getByRole('alertdialog', { name: 'Download these credentials?' }).textContent)
      .toContain('This Excel (.xlsx) file holds no passwords in plain text')
    expect(calls).toHaveLength(0)
  })
})

describe('with nothing shown', () => {
  it('Gmail says there are no credentials to download, and sends nothing', () => {
    renderTab()
    choose('Gmail')
    const dialog = openExport()
    expect(dialog).toHaveAccessibleName('No credentials to download')
    expect(within(dialog).getByRole('status').textContent).toBe('No credentials to download. There are no Gmail credentials. Choose another type.')
    expect(within(dialog).queryByRole('button', { name: /^Download/ })).toBeNull()
    expect(dialog.querySelector('.dr-export-format')).toBeNull()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(calls).toHaveLength(0)
  })

  it('a search that matches nothing says so', () => {
    renderTab()
    search('no such credential')
    expect(downloadButton().textContent).toBe('Download (0)')
    expect(within(openExport()).getByRole('status').textContent)
      .toBe('No credentials to download. Nothing shown matches “no such credential”. Clear the search or choose another type.')
    expect(calls).toHaveLength(0)
  })
})

describe('the download', () => {
  it.each([['Excel (.xlsx)', 'xlsx'], ['CSV (.csv)', 'csv'], ['Text (.txt)', 'txt']])('of %s names exactly the credentials shown', async (label, fmt) => {
    renderTab()
    choose('Naukri')
    openExport()
    fireEvent.click(screen.getByLabelText(label))
    startDownload()
    expect(screen.getByRole('alertdialog').textContent).toContain(`This ${label} file`)
    confirmDownload()
    await screen.findByText('Downloaded 26 credentials. Keep the file safe and delete it when you are done.')
    expect(calls).toHaveLength(1)
    const [call] = calls
    expect(call.url).toMatch(/\/data-room\/credentials\/service-accounts\/export$/)            // nothing in the URL
    expect(call.method).toBe('POST')
    expect(call.credentials).toBe('include')
    expect(call.body).toEqual({
      format: fmt, type: 'Naukri', searched: false,
      credentials: [...NAUKRI].map(a => ({ kind: 'account', id: a.id }))
        .sort((x, y) => (nameOf(x.id) < nameOf(y.id) ? -1 : 1)),                             // in the table's order
    })
    expect(saved).toEqual(['teleautomation-credentials-naukri-20261020-0930.xlsx'])          // the server's file name
  })

  it('of the logins names them by username, and of a search only what it shows', async () => {
    renderTab()
    choose(LOGIN_TYPE)
    openExport()
    startDownload()
    confirmDownload()
    await screen.findByText(/^Downloaded 6 credentials/)
    expect(calls[0].body.credentials).toEqual(['operations_admin', 'asha', 'kiran', 'meera', 'ravi', 'sunil'].map(id => ({ kind: 'login', id })))
    expect(calls[0].body.type).toBe(LOGIN_TYPE)
    cleanup()
    calls = []
    renderTab()
    choose('Naukri')
    search('servicenow')
    openExport()
    startDownload()
    confirmDownload()
    await screen.findByText(/^Downloaded 2 credentials/)
    expect(calls[0].body).toEqual({ format: 'xlsx', type: 'Naukri', searched: true,
      credentials: [{ kind: 'account', id: 'sn1' }, { kind: 'account', id: 'sn2' }] })
  })

  it('says how many the server wrote', async () => {
    reply.count = 25
    renderTab()
    choose('Naukri')
    openExport()
    startDownload()
    confirmDownload()
    expect(await screen.findByRole('status')).toHaveTextContent('Downloaded 25 credentials.')
  })

  it("shows the server's reason when it refuses", async () => {
    reply = { ok: false, status: 403, json: { detail: 'Only the admin can download all accounts' } }
    renderTab()
    openExport()
    startDownload()
    confirmDownload()
    expect(await screen.findByRole('alert')).toHaveTextContent('Only the admin can download all accounts')
    expect(saved).toEqual([])
  })
})

describe('the file name', () => {
  it("is the server's, or the fallback", () => {
    expect(downloadName('attachment; filename="a-b.xlsx"', 'x.xlsx')).toBe('a-b.xlsx')
    expect(downloadName(null, 'x.csv')).toBe('x.csv')
  })
})

// The table's name for an account id, as utils/credentialNames.js gives it: "Candidate 3 Naukri" reads "Candidate 3".
function nameOf(id) {
  const row = ACCOUNTS.find(a => a.id === id)
  return row.label.replace(/ Naukri$/, '').replace('Servicenow', 'ServiceNow').toLowerCase()
}
