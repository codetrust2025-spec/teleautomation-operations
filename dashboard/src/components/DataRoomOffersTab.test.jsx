/**
 * The offer-letter dialog asks for what the PDF cannot say for itself.
 *
 * Five of the eight boxes asked for values the upload already produces:
 * `create_offer_letter_from_pdf` slugs the id, reads filename, size and date
 * from the bytes, stamps `uploaded_at`, and writes the file to
 * DATA_DIR/data_room/offer_letters_cache. Typing them again offered no
 * information and one way to get each wrong.
 *
 * The half that matters here is compatibility. `update_vault_item` merges the
 * keys it is given and deletes any sent as null, so what the PATCH omits is
 * preserved and what it sends as an empty string is destroyed. These tests pin
 * that the request carries only the three editable fields, which is what makes
 * removing the boxes safe for records that already exist.
 */
import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../context/ConfirmContext.jsx'
import { DataRoomOffersTab } from './DataRoomOffersTab.jsx'

const SAVED_ROW = {
  id: 'luxoft_2024_01',
  filename: 'Luxoft-offer.pdf',
  candidate: 'Naveen',
  company_name: 'Luxoft',
  date_modified: '2026-08-14',
  size_kb: 412,
  drive_file_id: 'drive-abc-123',
  notes: 'Signed copy',
  has_pdf: true,
  uploaded_at: '2026-08-14T09:00:00+00:00',
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderTab(offers = [SAVED_ROW]) {
  return render(
    <ConfirmProvider>
      <DataRoomOffersTab offers={offers} onReload={() => {}} />
    </ConfirmProvider>,
  )
}

describe('offer letter dialog', () => {
  it('asks only for the candidate, the company and notes', () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))

    // The label's own text node, not its control's value: a textarea with
    // content would otherwise read as "Notes (optional)Signed copy".
    const labels = [...screen.getByRole('dialog').querySelectorAll('label')]
      .map(l => l.childNodes[0].textContent.trim())
    expect(labels).toEqual(['Candidate name', 'Company name', 'Notes (optional)'])
  })

  it('no longer exposes the values the upload derives', () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))

    const text = screen.getByRole('dialog').textContent
    for (const gone of ['ID (stable slug)', 'Date modified', 'Size (KB)', 'Google Drive file ID']) {
      expect(text).not.toContain(gone)
    }
    // "Filename" as a labelled input is gone; the filename is still shown, as
    // the read-only confirmation that the record points at its PDF.
    const labels = [...screen.getByRole('dialog').querySelectorAll('label')].map(l => l.textContent)
    expect(labels.some(l => /Filename/i.test(l))).toBe(false)
    expect(text).toContain('Luxoft-offer.pdf')
  })

  it('sends only the three editable keys, so the merge preserves the rest', async () => {
    // update_vault_item merges: an omitted key keeps its stored value, and an
    // empty string overwrites it. Sending the whole form is what used to blank
    // size_kb on any record that had none.
    const calls = []
    vi.stubGlobal('fetch', async (url, init) => {
      calls.push({ url: String(url), method: init.method, body: JSON.parse(init.body) })
      return {
        ok: true,
        status: 200,
        headers: { get: () => 'application/json' },
        json: async () => ({ status: 'ok' }),
      }
    })

    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].method).toBe('PATCH')
    expect(calls[0].url).toContain('/data-room/credentials/vault/offer_letters/luxoft_2024_01')
    expect(Object.keys(calls[0].body).sort()).toEqual(['candidate', 'company_name', 'notes'])
    for (const derived of ['id', 'filename', 'date_modified', 'size_kb', 'drive_file_id']) {
      expect(calls[0].body).not.toHaveProperty(derived)
    }
  })

  it('keeps the PDF required when adding a new offer letter', async () => {
    // A new offer letter IS the PDF: upload-analyze writes the file, derives
    // the row and switches to edit. Saving without one would catalogue nothing.
    const calls = []
    vi.stubGlobal('fetch', async (...args) => {
      calls.push(args)
      return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => ({ status: 'ok' }) }
    })

    renderTab([])
    fireEvent.click(screen.getByRole('button', { name: '+ Add offer' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('Choose the offer letter PDF first.')).toBeInTheDocument()
    expect(calls).toHaveLength(0)
  })

  it('offers the PDF chooser when adding, and not when editing', () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: '+ Add offer' }))
    expect(screen.getByRole('dialog').textContent).toContain('Choose PDF')

    fireEvent.keyDown(document, { key: 'Escape' })
    cleanup()

    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.getByRole('dialog').textContent).not.toContain('Choose PDF')
  })
})
