/**
 * Download All Accounts (Data Room -> Accounts, 19 Oct).
 *
 * The admin downloads every account as Excel, CSV or text with no passwords; passwords come only as an encrypted
 * Excel workbook, after the admin's sign-in password is entered again and a file password is set. Both passwords
 * are emptied after every attempt that sends them.
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
  reply = { ok: true, status: 200, disposition: 'attachment; filename="teleautomation-accounts-20261019-0930.csv"', json: {} }
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
const passwordBox = label => screen.getByLabelText(label)

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

describe('the ordinary export', () => {
  it('offers Excel, CSV and text, says what is in it, and has no password field', () => {
    renderTab()
    const dialog = openExport()
    expect([...dialog.querySelectorAll('.dr-export-format')].map(l => l.textContent)).toEqual(['Excel (.xlsx)', 'CSV (.csv)', 'Text (.txt)'])
    expect(dialog.textContent).toContain('Account name, type, username and status of all 2 accounts. No passwords.')
    expect(dialog.querySelector('input[type="password"]')).toBeNull()
  })

  it.each([['Excel (.xlsx)', 'xlsx'], ['CSV (.csv)', 'csv'], ['Text (.txt)', 'txt']])('downloads %s', async (label, fmt) => {
    renderTab()
    openExport()
    fireEvent.click(screen.getByLabelText(label))
    fireEvent.click(screen.getByRole('button', { name: 'Download' }))
    await screen.findByText('Downloaded.')
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toMatch(new RegExp(`/data-room/credentials/service-accounts/export\\?format=${fmt}$`))
    expect(calls[0].method).toBe('GET')
    expect(calls[0].credentials).toBe('include')
    expect(saved).toEqual(['teleautomation-accounts-20261019-0930.csv'])   // the server's file name
  })

  it("shows the server's reason when it refuses", async () => {
    reply = { ok: false, status: 403, json: { detail: 'Only the admin can download all accounts' } }
    renderTab()
    openExport()
    fireEvent.click(screen.getByRole('button', { name: 'Download' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Only the admin can download all accounts')
    expect(saved).toEqual([])
  })

  it('cannot download an empty list', () => {
    renderTab({ accounts: [] })
    openExport()
    expect(screen.getByRole('button', { name: 'Download' }).disabled).toBe(true)
  })
})

describe('the export with passwords', () => {
  function choosePasswords() {
    const dialog = openExport()
    fireEvent.click(screen.getByRole('checkbox', { name: /Include passwords/ }))
    return dialog
  }

  it('is a separate choice: encrypted Excel only, confirmed with the sign-in password and a file password', () => {
    renderTab()
    const dialog = choosePasswords()
    expect(dialog.querySelector('.dr-export-formats')).toBeNull()
    expect(dialog.textContent).toContain('with their passwords, as an encrypted Excel workbook')
    expect(passwordBox('Your sign-in password').getAttribute('autocomplete')).toBe('current-password')
    expect(passwordBox('File password').type).toBe('password')
    expect(screen.getByRole('button', { name: 'Download encrypted workbook' })).toBeTruthy()
  })

  it.each([
    ['', 'a-long-file-password', 'a-long-file-password', 'Enter your sign-in password.'],
    ['admin-pass', 'short', 'short', 'Choose a file password of at least 10 characters.'],
    ['admin-pass', 'a-long-file-password', 'a-different-password', 'The two file passwords are not the same.'],
  ])('asks for what is missing before sending anything (%#)', async (admin, file, again, message) => {
    renderTab()
    choosePasswords()
    fireEvent.change(passwordBox('Your sign-in password'), { target: { value: admin } })
    fireEvent.change(passwordBox('File password'), { target: { value: file } })
    fireEvent.change(passwordBox('Repeat file password'), { target: { value: again } })
    fireEvent.click(screen.getByRole('button', { name: 'Download encrypted workbook' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(calls).toHaveLength(0)
  })

  it('sends both passwords once, saves the workbook and empties every password box', async () => {
    reply.disposition = 'attachment; filename="teleautomation-accounts-with-passwords-20261019-0930.xlsx"'
    renderTab()
    choosePasswords()
    fireEvent.change(passwordBox('Your sign-in password'), { target: { value: 'admin-pass' } })
    fireEvent.change(passwordBox('File password'), { target: { value: 'a-long-file-password' } })
    fireEvent.change(passwordBox('Repeat file password'), { target: { value: 'a-long-file-password' } })
    fireEvent.click(screen.getByRole('button', { name: 'Download encrypted workbook' }))
    await screen.findByText('Encrypted workbook downloaded. Excel asks for the file password to open it.')
    expect(calls).toHaveLength(1)
    expect(calls[0].method).toBe('POST')
    expect(calls[0].url).toMatch(/\/data-room\/credentials\/service-accounts\/export-with-passwords$/)
    expect(JSON.parse(calls[0].body)).toEqual({ admin_password: 'admin-pass', file_password: 'a-long-file-password' })
    expect(saved).toEqual(['teleautomation-accounts-with-passwords-20261019-0930.xlsx'])
    for (const label of ['Your sign-in password', 'File password', 'Repeat file password']) expect(passwordBox(label).value).toBe('')
  })

  it('a wrong sign-in password is said plainly, and that box is emptied', async () => {
    reply = { ok: false, status: 403, json: { detail: 'Your sign-in password is not correct' } }
    renderTab()
    choosePasswords()
    fireEvent.change(passwordBox('Your sign-in password'), { target: { value: 'guess' } })
    fireEvent.change(passwordBox('File password'), { target: { value: 'a-long-file-password' } })
    fireEvent.change(passwordBox('Repeat file password'), { target: { value: 'a-long-file-password' } })
    fireEvent.click(screen.getByRole('button', { name: 'Download encrypted workbook' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Your sign-in password is not correct')
    expect(passwordBox('Your sign-in password').value).toBe('')
    expect(saved).toEqual([])
  })
})

describe('the file name', () => {
  it("is the server's, or the fallback", () => {
    expect(downloadName('attachment; filename="a-b.xlsx"', 'x.xlsx')).toBe('a-b.xlsx')
    expect(downloadName(null, 'x.csv')).toBe('x.csv')
  })
})
