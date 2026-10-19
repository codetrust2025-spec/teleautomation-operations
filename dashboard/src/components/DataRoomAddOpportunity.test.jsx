/**
 * Opportunities tab: a partner lead can be added from the tab itself. The
 * only Add opportunity button used to sit in the page header, hidden by
 * index.css on every .cand-page, and went with that header (2637301) -- so
 * there was no way to create one in the UI.
 */
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../context/ConfirmContext.jsx', () => ({ useConfirm: () => ({ confirm: vi.fn() }) }))
vi.mock('../context/AuthContext.jsx', () => ({ useAuth: () => ({ role: 'admin' }) }))

import { DataRoomPanel } from './DataRoomPanel.jsx'
import { captureLayout } from '../test/captureLayout.js'

const CREDENTIALS = { admin: null, handlers: [], service_accounts: [], prompts: [], resources: [], offer_letters: [] }
const LEAD = {
  id: 'opp-0', opportunity_type: 'vendor', status: 'reviewing', name: 'Northwind Telecom',
  whatsapp: '9876500000', tech_stack: 'SIP trunks', summary: 'Bulk minutes for the outbound team',
  contact_complete: true,
}

let fetchMock
beforeEach(() => {
  fetchMock = vi.fn((url, init = {}) => {
    const u = String(url)
    const body = u.includes('/data-room/credentials')
      ? { status: 'ok', credentials: CREDENTIALS }
      : init.method === 'POST'
        ? { status: 'ok', opportunity: { id: 'opp-1' } }
        : { status: 'ok', opportunities: [LEAD], stats: { total: 1, by_status: { reviewing: 1 } } }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function openOpportunities() {
  render(<DataRoomPanel />)
  const tab = await screen.findByRole('button', { name: /Opportunities/ })
  fireEvent.click(tab)
  return screen.getByRole('region', { name: 'Operations opportunities' })
}

describe('the Opportunities tab', () => {
  it('has an Add opportunity button beside its title, like the other tabs', async () => {
    const section = await openOpportunities()
    const header = section.querySelector('.dr-tab-header')
    expect(header).not.toBeNull()
    expect(within(header).getByRole('heading', { name: 'Operations opportunities' })).toBeInTheDocument()
    const add = within(header).getByRole('button', { name: '+ Add opportunity' })
    expect(add.className).toContain('cand-btn--primary')
    await screen.findByText('Northwind Telecom')
    captureLayout('data-room-opportunities-add')
  })

  it('opens an empty Add opportunity form and saves it as a new lead', async () => {
    const section = await openOpportunities()
    fireEvent.click(within(section).getByRole('button', { name: '+ Add opportunity' }))

    const dialog = screen.getByRole('dialog', { name: 'Add opportunity' })
    const name = within(dialog).getByLabelText('Name')
    expect(name).toHaveValue('')
    expect(within(dialog).getByLabelText('Type')).toHaveValue('partnership')
    expect(within(dialog).getByLabelText('Status')).toHaveValue('new')

    fireEvent.change(name, { target: { value: 'Acme Voice' } })
    fireEvent.change(within(dialog).getByLabelText('WhatsApp (10-digit or +91)'), { target: { value: '9876543210' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(post).toBeDefined()
    expect(String(post[0])).toMatch(/\/data-room$/)
    expect(JSON.parse(post[1].body)).toMatchObject({
      opportunity_type: 'partnership',
      status: 'new',
      name: 'Acme Voice',
      whatsapp: '9876543210',
    })
  })

  it('is not offered on the other tabs', async () => {
    render(<DataRoomPanel />)
    await screen.findByRole('button', { name: /Opportunities/ })
    expect(screen.queryByRole('button', { name: '+ Add opportunity' })).toBeNull()
  })
})
