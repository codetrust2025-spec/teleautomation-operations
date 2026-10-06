import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../context/ConfirmContext.jsx'
import { DataRoomAccountsTab } from './DataRoomAccountsTab.jsx'

afterEach(() => {
  cleanup()
  document.body.style.overflow = ''
})

describe('DataRoomAccountsTab service account modal', () => {
  it('opens as an isolated modal and restores page scrolling when closed', () => {
    render(
      <ConfirmProvider>
        <DataRoomAccountsTab accounts={[]} onReload={() => {}} />
      </ConfirmProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))

    const dialog = screen.getByRole('dialog', { name: 'Add service account' })
    expect(dialog).toHaveClass('dr-modal')
    expect(dialog.parentElement).toHaveClass('dr-modal-backdrop')
    expect(dialog.parentElement?.parentElement).toBe(document.body)
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(document.body.style.overflow).toBe('hidden')

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByRole('dialog', { name: 'Add service account' })).not.toBeInTheDocument()
    expect(document.body.style.overflow).toBe('')
  })

  it('asks only for what an operator has to type', () => {
    // ID and Service were on this form and neither needed to be: the id is a
    // slug derived from the title when left blank, and Service was free text
    // the table already defaulted to Gmail.
    render(
      <ConfirmProvider>
        <DataRoomAccountsTab accounts={[]} onReload={() => {}} />
      </ConfirmProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))

    // Scoped to the dialog: "Password" is also a column header in the table
    // behind it, so an unscoped query matches both.
    const dialog = screen.getByRole('dialog')
    // Scoped to the form grid: the screenshot panel also uses a <label> to
    // wrap its hidden file input.
    const labels = [...dialog.querySelectorAll('.dr-form-grid label')]
      .map(l => l.childNodes[0].textContent.trim())
    expect(labels).toEqual([
      'Account name',
      'Username / email',
      'Password',
      'Notes (optional)',
    ])
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(labels.some(l => /ID|slug|Service/i.test(l))).toBe(false)
  })

  it('still sends a generated id, because the backend requires one', async () => {
    // create_vault_item rejects a row without an id, so removing the field
    // must not remove the value.
    const calls = []
    const originalFetch = global.fetch
    global.fetch = async (url, init) => {
      calls.push({ url: String(url), body: JSON.parse(init.body) })
      return { ok: true, json: async () => ({ status: 'ok' }) }
    }
    try {
      render(
        <ConfirmProvider>
          <DataRoomAccountsTab accounts={[]} onReload={() => {}} />
        </ConfirmProvider>,
      )
      fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
      const inputs = screen.getByRole('dialog').querySelectorAll('.dr-form-grid input, .dr-form-grid textarea')
      fireEvent.change(inputs[0], { target: { value: 'Karthik Gmail 2026' } })
      fireEvent.click(screen.getByRole('button', { name: 'Save' }))
      await new Promise(resolve => setTimeout(resolve, 0))

      expect(calls).toHaveLength(1)
      expect(calls[0].body.id).toBe('karthik_gmail_2026')
      expect(calls[0].body.label).toBe('Karthik Gmail 2026')
    } finally {
      global.fetch = originalFetch
    }
  })

  it('previews a chosen screenshot before anything is saved', () => {
    // jsdom has no object URLs; the panel only needs one to render an <img>.
    const created = []
    vi.stubGlobal('URL', Object.assign(Object.create(URL), {
      createObjectURL: (file) => { created.push(file); return 'blob:preview-1' },
      revokeObjectURL: () => {},
    }))
    render(
      <ConfirmProvider>
        <DataRoomAccountsTab accounts={[]} onReload={() => {}} />
      </ConfirmProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    expect(screen.getByText('No image')).toBeInTheDocument()

    const file = new File([new Uint8Array([1, 2, 3])], 'shot.png', { type: 'image/png' })
    const picker = screen.getByRole('dialog').querySelector('input[type="file"]')
    fireEvent.change(picker, { target: { files: [file] } })

    expect(created).toHaveLength(1)
    expect(screen.getByAltText('Selected screenshot preview')).toHaveAttribute('src', 'blob:preview-1')
    expect(screen.getByRole('button', { name: 'Remove' })).toBeInTheDocument()
  })

  it('refuses a file type the backend would reject anyway', () => {
    render(
      <ConfirmProvider>
        <DataRoomAccountsTab accounts={[]} onReload={() => {}} />
      </ConfirmProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    const file = new File([new Uint8Array([1])], 'notes.pdf', { type: 'application/pdf' })
    fireEvent.change(screen.getByRole('dialog').querySelector('input[type="file"]'), {
      target: { files: [file] },
    })
    expect(screen.getByText('Choose a PNG, JPG, JPEG or WebP image.')).toBeInTheDocument()
    expect(screen.queryByAltText('Selected screenshot preview')).not.toBeInTheDocument()
  })

  it('offers View image for an account that already has one', () => {
    render(
      <ConfirmProvider>
        <DataRoomAccountsTab
          accounts={[{ id: 'gmail_a', label: 'Gmail A', username: 'a@x.com', has_image: true }]}
          onReload={() => {}}
        />
      </ConfirmProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: /^Edit/ }))
    const view = screen.getByRole('link', { name: 'View image' })
    expect(view).toHaveAttribute('href', expect.stringContaining('/data-room/service-accounts/gmail_a/image'))
    expect(screen.getByAltText('Screenshot for Gmail A')).toBeInTheDocument()
  })

  it('uploads the screenshot after the row exists, so a new account has an id', async () => {
    const calls = []
    vi.stubGlobal('URL', Object.assign(Object.create(URL), {
      createObjectURL: () => 'blob:preview-2',
      revokeObjectURL: () => {},
    }))
    vi.stubGlobal('fetch', async (url, init) => {
      calls.push({ url: String(url), method: init?.method, isForm: init?.body instanceof FormData })
      return { ok: true, json: async () => ({ status: 'ok' }) }
    })

    render(
      <ConfirmProvider>
        <DataRoomAccountsTab accounts={[]} onReload={() => {}} />
      </ConfirmProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }))
    const dialog = screen.getByRole('dialog')
    fireEvent.change(dialog.querySelector('.dr-form-grid input'), { target: { value: 'Gmail B' } })
    fireEvent.change(dialog.querySelector('input[type="file"]'), {
      target: { files: [new File([new Uint8Array([1])], 's.png', { type: 'image/png' })] },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(calls).toHaveLength(2))
    // The row first, then the image against the id the row was given.
    expect(calls[0].method).toBe('POST')
    expect(calls[0].url).toContain('/data-room/credentials/vault/service_accounts')
    expect(calls[1].method).toBe('POST')
    expect(calls[1].url).toContain('/data-room/service-accounts/gmail_b/image')
    expect(calls[1].isForm).toBe(true)
  })
})
