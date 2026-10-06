/**
 * Data Room → Logins: every row on the same grid.
 *
 * The bugs this locks out:
 *   - the admin password rendered in clear while handler rows showed nothing,
 *     so the Password column never lined up. Now every row shows a masked
 *     placeholder and no real password is ever in the DOM text.
 *   - the username ran straight into its copy control ("charanUser"); the value
 *     and the button now sit in a spaced flex cell.
 *   - the Pass copy button appeared even on rows with no password to copy; it
 *     now renders only when there is a real value (the admin).
 *   - the admin's Actions was a bare "Account menu" string beside the handlers'
 *     buttons; it is now a real secondary (ghost) button, enabled and clearly
 *     a secondary action rather than a greyed-out disabled field.
 *   - copy controls said "User" / "Pass" / "Copy all"; their accessible names
 *     now say what gets copied.
 *
 * Final polish locked in here too:
 *   - handler rows (no password to bundle) show "Copy username" in the Copy
 *     column instead of "Copy all"; the admin keeps "Copy all".
 *   - the Site moved out of the header actions into a quiet secondary line in
 *     the header text block; Add handler sits alone on the right.
 *   - the labelled copy button fills its cell, and rows are a touch shorter.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../context/ConfirmContext.jsx', () => ({ useConfirm: () => ({ confirm: vi.fn() }) }))
vi.mock('../context/AuthContext.jsx', () => ({ useAuth: () => ({ role: 'admin' }) }))

import { DataRoomPanel } from './DataRoomPanel.jsx'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(HERE, '..', 'index.css'), 'utf8').replace(/\r\n/g, '\n')

const CREDS = {
  admin: { username: 'operations_admin', password: 'super-secret-pw' },
  handlers: [
    { username: 'charan', reference: 'Charan' },
    { username: 'ravinder', reference: 'Ravinder' },
  ],
  site_url: 'https://operations.example.online',
  service_accounts: [], prompts: [], resources: [], offer_letters: [],
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn((url) => {
    const body = String(url).includes('/data-room/credentials')
      ? { status: 'ok', credentials: CREDS }
      : { status: 'ok', opportunities: [], stats: { total: 0, by_status: {} } }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function openLogins() {
  render(<DataRoomPanel />)
  // Wait for the tabs to render, then switch to the Logins tab by clicking its
  // tab chip (the label text also appears in the page subtitle, so scope to it).
  const tab = await waitFor(() => {
    const label = [...document.querySelectorAll('.dr-tab .dr-tab-label')].find(l => l.textContent === 'Logins')
    expect(label).toBeTruthy()
    return label.closest('.dr-tab')
  })
  fireEvent.click(tab)
  await screen.findByText('Dashboard logins')
}

function adminRow() {
  return screen.getByText('operations_admin').closest('tr')
}
function handlerRow(username) {
  return screen.getByText(username).closest('tr')
}

describe('the admin password', () => {
  it('is never shown in clear anywhere in the row', async () => {
    await openLogins()
    const row = adminRow()
    expect(row.textContent).not.toContain('super-secret-pw')
    // The masked placeholder occupies the Password cell.
    expect(row.querySelector('.dr-creds-pass').textContent).toMatch(/•+/)
  })

  it('can still be copied, so the real value is reachable without being displayed', async () => {
    await openLogins()
    const row = adminRow()
    expect(within(row).getByRole('button', { name: 'Copy password' })).toBeInTheDocument()
  })
})

describe('a handler row', () => {
  it('shows a masked placeholder in the same Password slot, not an empty cell', async () => {
    await openLogins()
    const row = handlerRow('charan')
    const pass = row.querySelector('.dr-creds-pass')
    expect(pass).toBeTruthy()
    expect(pass.textContent.trim().length).toBeGreaterThan(0)
  })

  it('offers no password copy button, because there is no password to copy', async () => {
    await openLogins()
    const row = handlerRow('charan')
    expect(within(row).queryByRole('button', { name: 'Copy password' })).toBeNull()
  })

  it('keeps the username separate from its copy control', async () => {
    await openLogins()
    const row = handlerRow('charan')
    // The value lives in its own <code>; the copy button is a sibling in the
    // spaced cell, so the text is "charan", never "charanUser".
    const code = row.querySelector('.dr-creds-cell code')
    expect(code.textContent).toBe('charan')
    // Both the username-cell icon and the Copy column copy the username.
    expect(within(row).getAllByRole('button', { name: 'Copy username' }).length).toBeGreaterThanOrEqual(1)
    expect(row.querySelector('.dr-creds-cell')).toBeTruthy()
  })

  it('offers "Copy username" in the Copy column, not "Copy all", since there is nothing to bundle', async () => {
    await openLogins()
    const row = handlerRow('charan')
    const copyCell = row.querySelector('.dr-creds-copy-all')
    expect(copyCell).toBeTruthy()
    const labelled = copyCell.querySelector('.dr-copy-btn--all')
    expect(labelled).toBeTruthy()
    expect(labelled.textContent).toContain('Copy username')
    expect(copyCell.textContent).not.toContain('Copy all')
  })
})

describe('actions are consistent across rows', () => {
  it('gives the admin a real secondary button, enabled and ghost-styled, not a disabled field', async () => {
    await openLogins()
    const row = adminRow()
    const menu = within(row).getByRole('button', { name: 'Account menu' })
    expect(menu.tagName).toBe('BUTTON')
    // A clear secondary action: enabled, ghost-styled, with an explanatory title.
    expect(menu).not.toBeDisabled()
    expect(menu.className).toContain('cand-btn--ghost')
    expect(menu.getAttribute('title')).toMatch(/account menu/i)
  })

  it('keeps "Copy all" on the admin row, which has a password to bundle', async () => {
    await openLogins()
    const row = adminRow()
    const copyCell = row.querySelector('.dr-creds-copy-all')
    expect(copyCell.querySelector('.dr-copy-btn--all').textContent).toContain('Copy all')
  })

  it('gives each handler Edit and Delete buttons', async () => {
    await openLogins()
    const row = handlerRow('charan')
    expect(within(row).getByRole('button', { name: 'Edit' })).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Delete' })).toBeInTheDocument()
  })
})

describe('copy controls name what they copy', () => {
  it('labels username, password and copy-all by what they copy', async () => {
    await openLogins()
    const row = adminRow()
    expect(within(row).getByRole('button', { name: 'Copy username' })).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Copy password' })).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Copy all' })).toBeInTheDocument()
  })
})

describe('the header', () => {
  it('keeps Add handler alone on the right of the header', async () => {
    await openLogins()
    const head = document.querySelector('.dr-creds-head-actions')
    expect(head).toBeTruthy()
    expect(within(head).getByRole('button', { name: /Add handler/ })).toBeInTheDocument()
    // The site chip no longer crowds the action area — it moved to the text block.
    expect(head.querySelector('.dr-creds-site')).toBeNull()
  })

  it('shows the site as a quiet secondary line in the header text block', async () => {
    await openLogins()
    const text = document.querySelector('.dr-creds-head-text')
    expect(text).toBeTruthy()
    expect(text.querySelector('.dr-creds-site a').textContent).toBe('operations.example.online')
    // The helper text names the actual copy actions available on each row.
    const desc = text.querySelector('.dr-section-desc')
    expect(desc.textContent).toMatch(/Copy username/)
    expect(desc.textContent).toMatch(/Copy all/)
  })
})

describe('Logins CSS', () => {
  it('fixes the credentials table to balanced column widths', () => {
    expect(CSS).toMatch(/\.dr-page \.dr-creds-table \{ table-layout: fixed; \}/)
  })
  it('spaces the value from its copy button in a flex cell', () => {
    expect(CSS).toMatch(/\.dr-creds-cell \{[^}]*gap: 8px;/)
  })
  it('tones the site chip down rather than making it prominent', () => {
    expect(CSS).toMatch(/\.dr-creds-site \{[^}]*font-size: 0\.72rem;/)
  })
  it('gives the username column a real width share instead of greedy auto, killing the gap before Password', () => {
    // The Username column (3rd) must not be `auto` any more, or it eats the
    // slack and reopens the empty gap before Password.
    expect(CSS).not.toMatch(/\.dr-page \.dr-creds-table td:nth-child\(3\) \{ width: auto; \}/)
    expect(CSS).toMatch(/\.dr-page \.dr-creds-table td:nth-child\(3\) \{ width: 25%; \}/)
  })
  it('fills the labelled copy button across its cell so Copy all / Copy username read as one action', () => {
    expect(CSS).toMatch(/\.dr-creds-copy-all \.dr-copy-btn--all \{ width: 100%;/)
  })
  it('tightens the row height', () => {
    expect(CSS).toMatch(/\.dr-page \.dr-creds-table th,\n\s*\.dr-page \.dr-creds-table td \{ vertical-align: middle; padding: 6px 12px;/)
  })
})
