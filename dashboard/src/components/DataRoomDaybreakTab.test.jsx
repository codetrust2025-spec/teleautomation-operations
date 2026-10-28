import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DataRoomDaybreakTab } from './DataRoomDaybreakTab.jsx'

const confirm = vi.hoisted(() => vi.fn(() => Promise.resolve(true)))
vi.mock('../context/ConfirmContext.jsx', () => ({ useConfirm: () => ({ confirm }) }))

// Synthetic people only.
const WORKERS = [
  { id: 'w1', name: 'RTX 4060', key_id: 'k1', fingerprint: 'a'.repeat(64), enabled: true, approved: true, approved_by: 'admin', approved_at: '2026-10-28T10:00:00Z', last_seen: '2026-10-28T10:00:00Z' },
  { id: 'w2', name: 'Jagadeesh', key_id: 'k2', fingerprint: 'b'.repeat(64), enabled: true, approved: false },
]
const ROW = { notes: [], attention: [], attention_codes: [], can_add_to_daybreak: false, delivery: null, connection: null }
const VIEW = {
  status: 'ok',
  workers: WORKERS,
  remote_control: { enabled: false, writes: false },
  fetched_at: '2026-10-28T10:00:00Z',
  accounts: [
    { ...ROW, account_id: 'asha', label: 'Asha Rao', email: 'asha@example.com', status: 'connected', synced: true, delivered: true, connected: true,
      profile: { id: 'p1', name: 'Asha Rao' },
      delivery: { at: '2026-10-28T10:00:00Z', receipts: [{ worker: 'RTX 4060', at: '2026-10-28T10:01:00Z' }, { worker: 'Jagadeesh', at: '2026-10-28T10:02:00Z' }], waiting_for: [] },
      connection: { signed_in_at: '2026-10-28T10:05:00Z', worker: 'RTX 4060' } },
    { ...ROW, account_id: 'ravi', label: 'Ravi Kumar', email: 'ravi@example.com', status: 'synced', synced: true, delivered: false, connected: false,
      notes: ['name_differs'], profile: { id: 'p2', name: 'Ravi K' } },
    { ...ROW, account_id: 'new', label: 'New Person', email: 'new@example.com', status: 'needs_attention', synced: false, delivered: false, connected: false,
      attention: ['Not in Daybreak yet'], attention_codes: ['missing_in_daybreak'], can_add_to_daybreak: true, profile: null },
  ],
  daybreak_only: [{ id: 'p9', name: 'Only Here', email: 'only@example.com' }],
  counts: { synced: 1, delivered: 0, connected: 1, needs_attention: 1 },
  reconciliation: { data_room_accounts: 3, matched: 2, missing_in_daybreak: 1, duplicates: 0, invalid_usernames: 0, names_differ: 1, daybreak_only: 1 },
}
const APPROVED = { ...VIEW, workers: WORKERS.map(w => ({ ...w, approved: true })) }

let calls
function mockFetch(view) {
  calls = []
  global.fetch = vi.fn(async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null })
    if ((options.method || 'GET') === 'GET') return { ok: true, json: async () => view }
    return { ok: true, json: async () => ({ status: 'ok', delivered: ['ravi'], refused: [], worker: {} }) }
  })
}
const posts = () => calls.filter(c => c.method === 'POST')

beforeEach(() => { mockFetch(VIEW); confirm.mockClear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('DataRoomDaybreakTab', () => {
  it('shows each account, its per-worker receipts and sign-in, the workers and the matching, and no password anywhere', async () => {
    render(<DataRoomDaybreakTab />)
    expect(await screen.findByText('Asha Rao')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Connected · 1' })).toBeInTheDocument()
    expect(screen.getByText(/RTX 4060 acknowledged .* Jagadeesh acknowledged/)).toBeInTheDocument()
    expect(screen.getByText(/Signed in to Naukri on RTX 4060/)).toBeInTheDocument()
    expect(screen.getByText(/3 Naukri accounts · 2 match a Daybreak candidate by address · 1 not in Daybreak/)).toBeInTheDocument()
    expect(screen.getByText('Daybreak: Ravi K')).toBeInTheDocument()
    expect(screen.getByText(/has not switched to its dedicated workers yet/)).toBeInTheDocument()
    expect(document.querySelector('input[type="password"]')).toBeNull()
  })

  it('delivers nothing until every enrolled worker key is approved', async () => {
    render(<DataRoomDaybreakTab />)
    const ravi = (await screen.findByText('Ravi Kumar')).closest('tr')
    expect(within(ravi).queryByRole('button', { name: 'Deliver' })).toBeNull()
    expect(screen.getByText(/Approve every worker's key before delivering/)).toBeInTheDocument()
  })

  it('approves a worker key only with at least 16 characters of its machine-check fingerprint', async () => {
    render(<DataRoomDaybreakTab />)
    fireEvent.click(await screen.findByRole('button', { name: 'Approve key…' }))
    const approve = screen.getByRole('button', { name: 'Approve' })
    fireEvent.change(screen.getByPlaceholderText('first 16+ characters'), { target: { value: 'bbbbbbbb' } })
    expect(approve).toBeDisabled()
    fireEvent.change(screen.getByPlaceholderText('first 16+ characters'), { target: { value: 'bbbbbbbbbbbbbbbb' } })
    fireEvent.click(approve)
    await waitFor(() => expect(posts()).toHaveLength(1))
    expect(posts()[0].url).toMatch(/\/data-room\/daybreak\/workers\/k2\/approve$/)
    expect(posts()[0].body).toEqual({ fingerprint: 'bbbbbbbbbbbbbbbb' })
  })

  it('needs only the workers Daybreak delivers to: RTX alone while Jagadeesh is left out', async () => {
    mockFetch({ ...VIEW, workers: [WORKERS[0], { ...WORKERS[1], recipient: false }] })
    render(<DataRoomDaybreakTab />)
    expect(await screen.findByText('not receiving new logins')).toBeInTheDocument()
    expect(screen.queryByText(/Approve every worker's key before delivering/)).toBeNull()
    fireEvent.click(within(screen.getByText('Ravi Kumar').closest('tr')).getByRole('button', { name: 'Deliver' }))
    await waitFor(() => expect(confirm).toHaveBeenCalled())
    expect(confirm.mock.calls[0][0].title).toBe('Deliver 1 login to RTX 4060?')
  })

  it('names the machine-check value to type, and offers no approval for a key of the wrong kind', async () => {
    mockFetch({ ...VIEW, workers: [WORKERS[0], { ...WORKERS[1], key: 'RSA-2048 RSA-OAEP-256', key_problem: 'the key is RSA-2048, not RSA-3072' }] })
    render(<DataRoomDaybreakTab />)
    expect(await screen.findByText(/Key cannot be approved: the key is RSA-2048, not RSA-3072/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve key…' })).toBeNull()
    cleanup()
    mockFetch({ ...VIEW, workers: [WORKERS[0], { ...WORKERS[1], key: 'RSA-3072 RSA-OAEP-256' }] })
    render(<DataRoomDaybreakTab />)
    fireEvent.click(await screen.findByRole('button', { name: 'Approve key…' }))
    expect(screen.getByText(/Type the publicKeyCanonicalSha256 that machine-check printed on Jagadeesh/)).toBeInTheDocument()
    expect(screen.getByText(/RSA-3072 RSA-OAEP-256/)).toBeInTheDocument()
  })

  it('delivers after confirming, and adds a missing account only with consent', async () => {
    mockFetch(APPROVED)
    render(<DataRoomDaybreakTab />)
    fireEvent.click(within((await screen.findByText('Ravi Kumar')).closest('tr')).getByRole('button', { name: 'Deliver' }))
    await waitFor(() => expect(posts()).toHaveLength(1))
    expect(confirm.mock.calls[0][0].title).toBe('Deliver 1 login to RTX 4060 and Jagadeesh?')
    expect(posts()[0].body).toEqual({ account_ids: ['ravi'] })
    fireEvent.click(within(screen.getByText('New Person').closest('tr')).getByRole('button', { name: 'Add to Daybreak' }))
    await waitFor(() => expect(posts()).toHaveLength(2))
    expect(confirm.mock.calls[1][0].confirmLabel).toBe('Candidate agreed · Add')
    expect(posts()[1].body).toEqual({ create_ids: ['new'], consent_confirmed: true })
  })

  it('filters by status from the counts', async () => {
    render(<DataRoomDaybreakTab />)
    fireEvent.click(await screen.findByRole('button', { name: 'Needs Attention · 1' }))
    expect(screen.queryByText('Asha Rao')).toBeNull()
    expect(screen.getByText('New Person')).toBeInTheDocument()
  })
})
