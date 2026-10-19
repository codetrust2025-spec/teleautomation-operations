/**
 * Data Room -> Key Links, compact (20 Oct): the WhatsApp import's provenance is
 * hidden from the table but kept in the record, every link still renders, and
 * add, edit, delete, open and copy still work -- now from icon buttons that say
 * what they do.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../context/AuthContext.jsx', () => ({ useAuth: () => ({ role: 'admin' }) }))
vi.mock('../utils/copyToClipboard.js', () => ({ copyToClipboard: vi.fn(() => Promise.resolve(true)) }))

import { ConfirmProvider } from '../context/ConfirmContext.jsx'
import { captureLayout } from '../test/captureLayout.js'
import { copyToClipboard } from '../utils/copyToClipboard.js'
import { DataRoomLinksTab, keyLinkDescription, keyLinkDisplayUrl } from './DataRoomLinksTab.jsx'
import { DataRoomPanel } from './DataRoomPanel.jsx'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(HERE, '..', 'index.css'), 'utf8').replace(/\r\n/g, '\n')

// Invented links, shaped like production's nine: each one's notes end with the
// provenance the WhatsApp import of 5 Oct appended, which is also kept in fields.
const SOURCE_FILE = 'Example Support WhatsApp archive'
const SOURCE_REF = 'Example Support data as supplied by you on 5 Oct 2026 (cleaned dataset; the archive could not be opened, so message times are not available)'
const PROVENANCE = { source_file: SOURCE_FILE, source_ref: SOURCE_REF, source_verified: 'supplied dataset', import_batch: 'whatsapp-import-2026-10-05', imported_at: '2026-10-05T08:54:14.861604+00:00' }
const imported = (id, title, url, lead, extra = '') => ({
  id, title, url, notes: `${lead} Source: ${SOURCE_FILE}. ${SOURCE_REF}.${extra}`, ...PROVENANCE,
})
const LINKS = [
  imported('sheet_unclear', 'Unclassified sheet (purpose unclear - review)', 'https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefg/edit?usp=sharing', 'FLAGGED FOR REVIEW: purpose unclear.'),
  imported('tools_drive', 'Candidate pre-interview tools (Drive)', 'https://drive.google.com/drive/folders/1ExampleFolderId0123456789', 'Candidates install the required tools from here before interviews.'),
  imported('freelance_sheet', 'Freelancing group update sheet', 'https://docs.google.com/spreadsheets/d/1ExampleSheet2/edit?usp=sharing', 'Freelancing group updates.'),
  imported('helper_join', 'Interview helper - join', 'https://app.example.com/app/helper/join', 'Helper join link.'),
  imported('office_map', 'Office / location (map)', 'https://maps.google.com/?q=17.5,78.3', 'Location link recorded in the chat.'),
  imported('person_linkedin', 'Asha Example - LinkedIn', 'https://www.linkedin.com/in/asha-example-000000', 'LinkedIn profile.'),
  imported('submit_slot', 'Submit interview slot', 'https://example.online/submit-slot', 'Candidate slot booking page.',
    " Also in WhatsApp group '🖥️Interview slots update': posted and pinned 14 Jun 2026 1:57 PM (_chat.txt L368-387), re-shared to 30 Sep 2026."),
  imported('main_site', 'Example main site', 'https://example.online', 'Main public site.'),
  imported('ops_sheet', 'Operations sheet', 'https://docs.google.com/spreadsheets/d/1ExampleSheet3/edit?usp=sharing', 'Purpose not stated in the chat; confirm before relying on it.'),
]
const MEANINGFUL = {
  sheet_unclear: 'FLAGGED FOR REVIEW: purpose unclear.',
  tools_drive: 'Candidates install the required tools from here before interviews.',
  freelance_sheet: 'Freelancing group updates.',
  helper_join: 'Helper join link.',
  office_map: 'Location link recorded in the chat.',
  person_linkedin: 'LinkedIn profile.',
  submit_slot: 'Candidate slot booking page.',
  main_site: 'Main public site.',
  ops_sheet: 'Purpose not stated in the chat; confirm before relying on it.',
}

let calls
beforeEach(() => {
  calls = []
  copyToClipboard.mockClear()
  vi.stubGlobal('fetch', vi.fn((url, opts = {}) => {
    calls.push({ url: String(url), method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : undefined })
    const body = String(url).includes('/data-room/credentials') && !opts.method
      ? { status: 'ok', credentials: { admin: null, handlers: [], service_accounts: [], prompts: [], resources: LINKS, offer_letters: [] } }
      : String(url).includes('/data-room/credentials')
        ? { status: 'ok' }
        : { status: 'ok', opportunities: [], stats: { total: 0, by_status: {} } }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.body.style.overflow = ''
})

function renderTab(resources = LINKS, onReload = vi.fn()) {
  render(<ConfirmProvider><DataRoomLinksTab resources={resources} onReload={onReload} /></ConfirmProvider>)
  return onReload
}
const rowOf = (title) => screen.getByText(title, { selector: '.dr-kl-title' }).closest('tr')

describe('a Key Links description', () => {
  it('is what the notes say about the link, without the WhatsApp import provenance', () => {
    for (const row of LINKS) expect(keyLinkDescription(row)).toBe(MEANINGFUL[row.id])
  })

  it('drops the provenance even on a row that does not carry it as fields', () => {
    const { id, title, url, notes } = LINKS[1]
    expect(keyLinkDescription({ id, title, url, notes })).toBe(MEANINGFUL.tools_drive)
  })

  it('leaves notes with no provenance as they are, and is empty when the notes were only provenance', () => {
    expect(keyLinkDescription({ notes: 'Shared drive for onboarding. Ask Ops for access.' })).toBe('Shared drive for onboarding. Ask Ops for access.')
    expect(keyLinkDescription({ notes: `Source: ${SOURCE_FILE}. ${SOURCE_REF}.`, ...PROVENANCE })).toBe('')
    expect(keyLinkDescription({})).toBe('')
  })

  it('never changes the stored row', () => {
    const row = structuredClone(LINKS[6])
    keyLinkDescription(row)
    expect(row).toEqual(LINKS[6])
  })
})

describe('the Key Links table', () => {
  it('renders every link, each title once, with its whole URL behind the shortened one', () => {
    renderTab()
    const rows = document.querySelectorAll('tbody tr.dr-kl-row')
    expect(rows).toHaveLength(9)
    for (const link of LINKS) {
      const row = rowOf(link.title)
      expect(within(row).getAllByText(link.title)).toHaveLength(1)
      const url = row.querySelector('a.dr-kl-url')
      expect(url).toHaveAttribute('href', link.url)
      expect(url).toHaveAttribute('title', link.url)
      expect(url.textContent).toBe(keyLinkDisplayUrl(link.url))
    }
    expect(keyLinkDisplayUrl('https://www.linkedin.com/in/asha-example-000000')).toBe('linkedin.com/in/asha-example-000000')
    expect(keyLinkDisplayUrl('https://example.online/')).toBe('example.online')
  })

  it('shows the meaningful description and none of the repeated source / archive text', () => {
    renderTab()
    const table = document.querySelector('.dr-kl-table')
    for (const link of LINKS) {
      expect(within(rowOf(link.title)).getByText(MEANINGFUL[link.id], { selector: '.dr-kl-desc' })).toBeInTheDocument()
    }
    expect(table.textContent).not.toMatch(/WhatsApp|Source:|supplied by you|archive|_chat\.txt/i)
  })

  it('opens each link in a new tab from a labelled icon button', () => {
    renderTab()
    for (const link of LINKS) {
      const open = within(rowOf(link.title)).getByRole('link', { name: `Open ${link.title} in a new tab` })
      expect(open).toHaveAttribute('href', link.url)
      expect(open).toHaveAttribute('target', '_blank')
      expect(open).toHaveAttribute('rel', 'noopener noreferrer')
      expect(open).toHaveAttribute('title', 'Open in a new tab')
      expect(open.textContent).toBe('')
      expect(open.querySelector('svg.ta-icon--external-link')).not.toBeNull()
    }
  })

  it('copies the whole URL and says so', async () => {
    renderTab()
    const link = LINKS[0]
    const copy = within(rowOf(link.title)).getByRole('button', { name: `Copy the URL of ${link.title}` })
    expect(copy).toHaveAttribute('title', 'Copy URL')
    fireEvent.click(copy)
    await waitFor(() => expect(copy).toHaveAccessibleName(`Copied the URL of ${link.title}`))
    expect(copyToClipboard).toHaveBeenCalledWith(link.url)
    expect(copy).toHaveClass('dr-kl-copy--copied')
    expect(screen.getByRole('status')).toHaveTextContent(`Copied the URL of ${link.title}`)
  })

  it('edits a link from the stored notes, provenance and all, and sends them back unchanged', async () => {
    const onReload = renderTab()
    const link = LINKS[6]
    fireEvent.click(within(rowOf(link.title)).getByRole('button', { name: `Edit ${link.title}` }))
    const dialog = screen.getByRole('dialog', { name: 'Edit link' })
    const notes = dialog.querySelector('textarea')
    expect(notes.value).toBe(link.notes)
    const title = within(dialog).getByLabelText('Title / label')
    fireEvent.change(title, { target: { value: 'Submit an interview slot' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onReload).toHaveBeenCalled())
    const patch = calls.find(c => c.method === 'PATCH')
    expect(patch.url).toMatch(/\/data-room\/credentials\/vault\/resources\/submit_slot$/)
    expect(patch.body).toEqual({ id: 'submit_slot', title: 'Submit an interview slot', url: link.url, notes: link.notes })
  })

  it('deletes a link only once the deletion is confirmed', async () => {
    const onReload = renderTab()
    const link = LINKS[2]
    const del = within(rowOf(link.title)).getByRole('button', { name: `Delete ${link.title}` })
    expect(del).toHaveAttribute('title', 'Delete link')

    // The confirm dialog's backdrop is aria-hidden, so its buttons are found with `hidden`.
    fireEvent.click(del)
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel', hidden: true }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Cancel', hidden: true })).toBeNull())
    expect(calls.filter(c => c.method === 'DELETE')).toHaveLength(0)

    fireEvent.click(del)
    fireEvent.click(await screen.findByRole('button', { name: 'Delete', hidden: true }))
    await waitFor(() => expect(onReload).toHaveBeenCalled())
    const deleted = calls.filter(c => c.method === 'DELETE')
    expect(deleted).toHaveLength(1)
    expect(deleted[0].url).toMatch(/\/data-room\/credentials\/vault\/resources\/freelance_sheet$/)
  })

  it('adds a link with an id made from its title', async () => {
    const onReload = renderTab()
    fireEvent.click(screen.getByRole('button', { name: '+ Add link' }))
    const dialog = screen.getByRole('dialog', { name: 'Add link' })
    fireEvent.change(within(dialog).getByLabelText('Title / label'), { target: { value: 'Team handbook' } })
    fireEvent.change(within(dialog).getByLabelText('URL'), { target: { value: 'https://example.com/handbook' } })
    fireEvent.change(within(dialog).getByLabelText('Notes'), { target: { value: 'Policies and how-tos.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onReload).toHaveBeenCalled())
    const post = calls.find(c => c.method === 'POST')
    expect(post.url).toMatch(/\/data-room\/credentials\/vault\/resources$/)
    expect(post.body).toEqual({ id: 'team_handbook', title: 'Team handbook', url: 'https://example.com/handbook', notes: 'Policies and how-tos.' })
  })

  it('gives every action an accessible name and a tooltip, reachable from the keyboard', () => {
    renderTab()
    const actions = document.querySelectorAll('.dr-kl-icon-btn')
    // Copy, Open, Edit, Delete on each of the nine.
    expect(actions).toHaveLength(36)
    for (const el of actions) {
      expect(el.getAttribute('aria-label')).toBeTruthy()
      expect(el.getAttribute('title')).toBeTruthy()
      expect(['A', 'BUTTON']).toContain(el.tagName)
      if (el.tagName === 'A') expect(el).toHaveAttribute('href')
      expect(el).not.toHaveAttribute('tabindex', '-1')
      expect(el).not.toBeDisabled()
    }
    expect(screen.queryByRole('button', { name: /^(Edit|Delete)$/ })).toBeNull()
    expect(screen.queryByText(/Open ↗/)).toBeNull()
  })

  it('keeps a link without a URL in the table, its Open control disabled', () => {
    renderTab([{ id: 'no_url', title: 'Placeholder', notes: 'To be added.' }])
    const row = rowOf('Placeholder')
    expect(within(row).getByRole('button', { name: 'Open Placeholder: no URL' })).toBeDisabled()
    expect(within(row).getByRole('button', { name: 'Edit Placeholder' })).toBeEnabled()
    expect(within(row).queryByRole('button', { name: /Copy/ })).toBeNull()
  })
})

describe('the Key Links tab in the Data Room', () => {
  it('lists all nine links once the tab is opened', async () => {
    render(<ConfirmProvider><DataRoomPanel /></ConfirmProvider>)
    const tab = await screen.findByRole('button', { name: /Key Links/ })
    await waitFor(() => expect(tab.querySelector('.dr-tab-count').textContent).toBe('9'))
    fireEvent.click(tab)
    await screen.findByText(LINKS[0].title, { selector: '.dr-kl-title' })
    expect(document.querySelectorAll('.dr-page .dr-kl-table tbody tr.dr-kl-row')).toHaveLength(9)
    expect(document.querySelector('.dr-kl-table').textContent).not.toMatch(/WhatsApp/)
    captureLayout('data-room-key-links', document.querySelector('.dr-page'))
  })
})

describe('the Key Links styles', () => {
  const block = CSS.slice(CSS.indexOf('/* ── Data Room: Key Links, compact'), CSS.indexOf('/* Confirmed slots: one interview booked twice'))
  const phone = block.slice(block.indexOf('@media (max-width: 640px)'))

  it('cut the URL to one line and the title and description to two', () => {
    expect(block).toMatch(/\.dr-kl-url \{[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap;/)
    expect(block).toMatch(/\.dr-kl-title,\n\.dr-page \.dr-kl-desc \{[^}]*-webkit-line-clamp: 2;/)
  })

  it('fix the column widths at every width, so no viewport falls between the table and the cards', () => {
    const base = block.slice(0, block.indexOf('@media'))
    expect(base).toMatch(/\.dr-page \.dr-kl-table \{ table-layout: fixed; \}/)
    expect(base).toMatch(/\.dr-kl-th--actions \{ width: 122px;/)
    expect(phone).toMatch(/\.dr-kl-table thead \{ display: none; \}/)
  })

  it('make every action at least 40px square on a phone and under a finger', () => {
    expect(phone).toMatch(/\.dr-kl-icon-btn,\n\s*\.dr-page \.dr-kl-copy \{ width: 40px; height: 40px;/)
    expect(block).toMatch(/\(pointer: coarse\) \{[^@]*\.dr-kl-copy \{ width: 40px; height: 40px; \}/)
  })

  it('keep Edit Candidate compact as the last block of the stylesheet', () => {
    expect(CSS.indexOf('/* ── Data Room: Key Links, compact')).toBeLessThan(CSS.indexOf('/* ── Edit Candidate, compact'))
  })
})
