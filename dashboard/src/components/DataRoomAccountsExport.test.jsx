/**
 * Download all credentials (Data Room -> Credentials, 19 Oct; every credential since 20 Oct).
 *
 * The admin downloads every credential -- the dashboard logins and the service accounts -- as Excel, CSV or text:
 * name, type, username and password. There
 * is no password prompt and no encryption step; the one thing between the button and the file is a confirmation
 * that says the file holds every password in plain text.
 */
import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../context/ConfirmContext.jsx'
import { DataRoomAccountsTab, downloadName } from './DataRoomAccountsTab.jsx'

const ACCOUNTS = [
  // As the list arrives: whether there is a password, never the password itself.
  { id: 'a', label: 'Karthik Gmail (current)', username: 'karthik@example.com', has_password: true },
  { id: 'b', label: 'Naukri legacy', service: 'Naukri', username: 'ops@example.com', has_password: true },
]

let calls
let reply
let saved

beforeEach(() => {
  calls = []
  saved = []
  reply = { ok: true, status: 200, disposition: 'attachment; filename="teleautomation-credentials-20261019-0930.xlsx"', json: {} }
  vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method || 'GET', body: init.body, credentials: init.credentials })
    return {
      ok: reply.ok,
      status: reply.status,
      headers: { get: name => (name.toLowerCase() === 'content-disposition' ? reply.disposition : null) },
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
  document.body.style.overflow = ''
})

function renderTab(props = {}) {
  return render(
    <ConfirmProvider>
      <DataRoomAccountsTab accounts={ACCOUNTS} onReload={() => {}} canExport {...props} />
    </ConfirmProvider>,
  )
}

const openExport = () => {
  fireEvent.click(screen.getByRole('button', { name: 'Download all credentials' }))
  return screen.getByRole('dialog', { name: 'Download all credentials' })
}
const confirmDownload = () => fireEvent.click(screen.getByRole('button', { name: 'Yes, download' }))

describe('the Download all accounts button', () => {
  it('sits next to Add account, for the admin', () => {
    renderTab()
    const download = screen.getByRole('button', { name: 'Download all credentials' })
    expect(download.nextElementSibling).toBe(screen.getByRole('button', { name: 'Add credential' }))
  })

  it('is not there for anyone else', () => {
    renderTab({ canExport: false })
    expect(screen.queryByRole('button', { name: 'Download all credentials' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Add credential' })).toBeTruthy()
  })
})

describe('the dialog', () => {
  it('says what is in the file, offers Excel, CSV and text, and asks for no password', () => {
    renderTab()
    const dialog = openExport()
    expect(dialog.textContent).toContain('All 2 credentials: credential name, type, username and password.')
    for (const gone of ['status', 'last used', 'notes']) expect(dialog.textContent).not.toContain(gone)
    expect(dialog.textContent).toContain('The file holds every password in plain text.')
    expect([...dialog.querySelectorAll('.dr-export-format')].map(l => l.textContent)).toEqual(['Excel (.xlsx)', 'CSV (.csv)', 'Text (.txt)'])
    expect(dialog.querySelector('input[type="password"], input[type="checkbox"]')).toBeNull()
    expect(screen.queryByText(/Include passwords/)).toBeNull()
  })

  it('asks once, plainly, before anything is downloaded, and Back sends nothing', () => {
    renderTab()
    openExport()
    fireEvent.click(screen.getByRole('button', { name: 'Download all' }))
    const confirm = screen.getByRole('alertdialog', { name: 'Download every password?' })
    expect(confirm.textContent).toContain('This Excel (.xlsx) file will contain 2 passwords in plain text.')
    expect(calls).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(calls).toHaveLength(0)
  })

  it.each([['Excel (.xlsx)', 'xlsx'], ['CSV (.csv)', 'csv'], ['Text (.txt)', 'txt']])('downloads %s once confirmed', async (label, fmt) => {
    renderTab()
    openExport()
    fireEvent.click(screen.getByLabelText(label))
    fireEvent.click(screen.getByRole('button', { name: 'Download all' }))
    expect(screen.getByRole('alertdialog').textContent).toContain(`This ${label} file`)
    confirmDownload()
    await screen.findByText('Downloaded 2 credentials. Keep the file safe and delete it when you are done.')
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toMatch(new RegExp(`/data-room/credentials/service-accounts/export\\?format=${fmt}$`))
    expect(calls[0].method).toBe('GET')
    expect(calls[0].credentials).toBe('include')
    expect(saved).toEqual(['teleautomation-credentials-20261019-0930.xlsx'])   // the server's file name
  })

  it("shows the server's reason when it refuses", async () => {
    reply = { ok: false, status: 403, json: { detail: 'Only the admin can download all accounts' } }
    renderTab()
    openExport()
    fireEvent.click(screen.getByRole('button', { name: 'Download all' }))
    confirmDownload()
    expect(await screen.findByRole('alert')).toHaveTextContent('Only the admin can download all accounts')
    expect(saved).toEqual([])
  })

  it('cannot download an empty list', () => {
    renderTab({ accounts: [] })
    openExport()
    expect(screen.getByRole('button', { name: 'Download all' }).disabled).toBe(true)
  })
})

describe('every credential', () => {
  it('counts the dashboard logins in the file, and only stored passwords as passwords', () => {
    const logins = { admin: { username: 'operations_admin', reference: 'Full dashboard', role: 'admin' },
      handlers: [{ username: 'asha', reference: 'Asha', role: 'handler' }, { username: 'ravi', reference: 'Ravi', role: 'handler' }] }
    renderTab({ logins, accounts: [...ACCOUNTS, { id: 'c', label: 'No password', service: 'Telegram', username: '+91 90000 00000', has_password: false }] })
    const dialog = openExport()
    expect(dialog.textContent).toContain('All 6 credentials: credential name, type, username and password.')
    expect(dialog.textContent).toContain('Not available (hashed)')
    expect(dialog.textContent).toContain('Not stored')
    fireEvent.click(screen.getByRole('button', { name: 'Download all' }))
    expect(screen.getByRole('alertdialog').textContent).toContain('will contain 2 passwords in plain text')
  })
})

describe('the file name', () => {
  it("is the server's, or the fallback", () => {
    expect(downloadName('attachment; filename="a-b.xlsx"', 'x.xlsx')).toBe('a-b.xlsx')
    expect(downloadName(null, 'x.csv')).toBe('x.csv')
  })
})
