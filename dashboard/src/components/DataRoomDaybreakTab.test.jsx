import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DataRoomDaybreakTab } from './DataRoomDaybreakTab.jsx'

const confirm = vi.hoisted(() => vi.fn(() => Promise.resolve(true)))
vi.mock('../context/ConfirmContext.jsx', () => ({ useConfirm: () => ({ confirm }) }))

// Synthetic people only.
const VIEW = {
  status: 'ok',
  executors: [{ id: 'exe_1', name: 'Praveen', kind: 'praveen', fingerprint: 'AAAA-BBBB-CCCC-DDDD-EEEE', status: 'pending', active: false }],
  active_executor: null,
  roster_reported_at: null,
  accounts: [
    { account_id: 'asha', label: 'Asha Rao', email: 'asha@example.com', status: 'connected', synced: true, delivered: true, connected: true,
      attention: [], attention_codes: [], notes: [], can_add_to_daybreak: false, profile: { id: 'p1', name: 'Asha Rao' },
      delivery: { id: 'd1', state: 'unchanged', receipt_at: '2026-10-28T10:00:00Z', verify_result: 'queued' }, pending: null,
      connection: { signed_in_at: '2026-10-28T10:05:00Z', password_used: false } },
    { account_id: 'ravi', label: 'Ravi Kumar', email: 'ravi@example.com', status: 'synced', synced: true, delivered: false, connected: false,
      attention: [], attention_codes: [], notes: ['name_differs'], can_add_to_daybreak: false, profile: { id: 'p2', name: 'Ravi K' },
      delivery: null, pending: null, connection: null },
    { account_id: 'new', label: 'New Person', email: 'new@example.com', status: 'needs_attention', synced: false, delivered: false, connected: false,
      attention: ['Not in Daybreak yet'], attention_codes: ['missing_in_daybreak'], notes: [], can_add_to_daybreak: true, profile: null,
      delivery: null, pending: null, connection: null },
    { account_id: 'diff', label: 'Diff Person', email: 'diff@example.com', status: 'needs_attention', synced: true, delivered: false, connected: false,
      attention: ['Praveen holds a different password for this candidate; choose Replace to use the Data Room\'s'], attention_codes: ['differs'],
      notes: [], can_add_to_daybreak: false, profile: { id: 'p4', name: 'Diff Person' }, delivery: { id: 'd4', state: 'differs' }, pending: null, connection: null },
  ],
  daybreak_only: [{ id: 'p9', name: 'Only Here', email: 'only@example.com' }],
  counts: { synced: 1, delivered: 0, connected: 1, needs_attention: 2 },
  reconciliation: { data_room_accounts: 4, matched: 3, missing_in_daybreak: 1, duplicates: 0, invalid_usernames: 0, names_differ: 1, daybreak_only: 1 },
}

let calls
function mockFetch(view) {
  calls = []
  global.fetch = vi.fn(async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null })
    if ((options.method || 'GET') === 'GET') return { ok: true, json: async () => view }
    return { ok: true, json: async () => ({ status: 'ok', queued: ['ravi'], refused: [], executor: { name: 'Praveen' } }) }
  })
}

const show = () => render(<DataRoomDaybreakTab />)
const confirmedAs = label => expect(confirm.mock.calls.at(-1)[0].confirmLabel).toBe(label)

beforeEach(() => { mockFetch(VIEW); confirm.mockClear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('DataRoomDaybreakTab', () => {
  it('shows every account with its status, reasons and matching summary, and no password anywhere', async () => {
    show()
    expect(await screen.findByText('Asha Rao')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Connected · 1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Needs Attention · 2' })).toBeInTheDocument()
    expect(screen.getByText('Not in Daybreak yet')).toBeInTheDocument()
    expect(screen.getByText(/4 Naukri accounts · 3 match a Daybreak candidate by address · 1 not in Daybreak/)).toBeInTheDocument()
    expect(screen.getByText('Daybreak: Ravi K')).toBeInTheDocument()
    expect(screen.getByText(/Signed in to Naukri .* \(saved session\)/)).toBeInTheDocument()
    expect(document.querySelector('input[type="password"]')).toBeNull()
    expect(document.body.textContent).not.toMatch(/password:/i)
  })

  it('approves a computer only with the fingerprint typed in', async () => {
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Approve…' }))
    const approve = screen.getByRole('button', { name: 'Approve' })
    expect(approve).toBeDisabled()
    fireEvent.change(screen.getByPlaceholderText('XXXX-XXXX-XXXX-XXXX-XXXX'), { target: { value: 'aaaa-bbbb-cccc-dddd-eeee' } })
    fireEvent.click(approve)
    await waitFor(() => expect(calls.some(c => c.method === 'POST')).toBe(true))
    const post = calls.find(c => c.method === 'POST')
    expect(post.url).toMatch(/\/data-room\/daybreak\/executors\/exe_1\/approve$/)
    expect(post.body).toEqual({ fingerprint: 'aaaa-bbbb-cccc-dddd-eeee', cutover_confirmed: false })
  })

  it('delivers with a proof run, offers Replace and Add to Daybreak only where they apply', async () => {
    mockFetch({ ...VIEW, active_executor: { id: 'exe_1', name: 'Praveen' }, executors: [{ ...VIEW.executors[0], status: 'approved', active: true }] })
    show()
    const ravi = (await screen.findByText('Ravi Kumar')).closest('tr')
    expect(within(screen.getByText('New Person').closest('tr')).queryByRole('button', { name: 'Deliver' })).toBeNull()
    expect(within(screen.getByText('Diff Person').closest('tr')).getByRole('button', { name: 'Replace' })).toBeInTheDocument()
    fireEvent.click(within(ravi).getByRole('button', { name: 'Deliver' }))
    await waitFor(() => expect(confirm).toHaveBeenCalled())
    confirmedAs('Deliver')
    await waitFor(() => expect(calls.some(c => c.method === 'POST')).toBe(true))
    expect(calls.find(c => c.method === 'POST').body).toEqual({ account_ids: ['ravi'], verify_ids: ['ravi'] })

    calls.length = 0
    fireEvent.click(within(screen.getByText('New Person').closest('tr')).getByRole('button', { name: 'Add to Daybreak' }))
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(2))
    confirmedAs('Candidate agreed · Add')
    expect(confirm.mock.calls.at(-1)[0].message).toMatch(/automation off/)
    await waitFor(() => expect(calls.some(c => c.method === 'POST')).toBe(true))
    expect(calls.find(c => c.method === 'POST').body).toEqual({ create_ids: ['new'], consent_confirmed: true })
  })

  it('filters by status from the counts', async () => {
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Needs Attention · 2' }))
    expect(screen.queryByText('Asha Rao')).toBeNull()
    expect(screen.getByText('New Person')).toBeInTheDocument()
  })
})
