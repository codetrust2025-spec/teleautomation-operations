/**
 * Download All Accounts (Data Room -> Accounts, 19 Oct).
 *
 * The admin downloads every account's name, username and password, as Excel, CSV or text. There
 * is no password prompt and no encryption step; the one thing between the button and the file is a confirmation
 * that says the file holds every password in plain text.
 */
import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../context/ConfirmContext.jsx'
import { DataRoomAccountsTab, downloadName } from './DataRoomAccountsTab.jsx'

const ACCOUNTS = [
  { id: 'a', label: 'Karthik Gmail (current)', username: 'karthik@example.com', password: 'x-secret' },
  { id: 'b', label: 'Naukri legacy', service: 'Naukri', username: 'ops@example.com', password: 'y-secret' },
]

let calls
let reply
let saved

beforeEach(() => {
  calls = []
  saved = []
  reply = { ok: true, status: 200, disposition: 'attachment; filename="teleautomation-accounts-20261019-0930.xlsx"', json: {} }
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
  fireEvent.click(screen.getByRole('button', { name: 'Download all accounts' }))
  return screen.getByRole('dialog', { name: 'Download all accounts' })
}
const confirmDownload = () => fireEvent.click(screen.getByRole('button', { name: 'Yes, download' }))

describe('the Download all accounts button', () => {
  it('sits next to Add account, for the admin', () => {
    renderTab()
    const download = screen.getByRole('button', { name: 'Download all accounts' })
    expect(download.nextElementSibling).toBe(screen.getByRole('button', { name: 'Add account' }))
  })

  it('is not there for anyone else', () => {
    renderTab({ canExport: false })
    expect(screen.queryByRole('button', { name: 'Download all accounts' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Add account' })).toBeTruthy()
  })
})

describe('the dialog', () => {
  it('says what is in the file, offers Excel, CSV and text, and asks for no password', () => {
    renderTab()
    const dialog = openExport()
    expect(dialog.textContent).toContain('All 2 accounts: account name, username and password.')
    for (const gone of ['type', 'status', 'last used', 'notes']) expect(dialog.textContent).not.toContain(gone)
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
    expect(confirm.textContent).toContain('This Excel (.xlsx) file will contain all 2 passwords in plain text.')
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
    await screen.findByText('Downloaded 2 accounts. Keep the file safe and delete it when you are done.')
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toMatch(new RegExp(`/data-room/credentials/service-accounts/export\\?format=${fmt}$`))
    expect(calls[0].method).toBe('GET')
    expect(calls[0].credentials).toBe('include')
    expect(saved).toEqual(['teleautomation-accounts-20261019-0930.xlsx'])   // the server's file name
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

describe('the file name', () => {
  it("is the server's, or the fallback", () => {
    expect(downloadName('attachment; filename="a-b.xlsx"', 'x.xlsx')).toBe('a-b.xlsx')
    expect(downloadName(null, 'x.csv')).toBe('x.csv')
  })
})
