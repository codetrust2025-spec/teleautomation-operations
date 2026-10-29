import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { AiControlsDashboard, aiControlsRequest } from './AiControlsDashboard.jsx'
import { captureLayout } from '../test/captureLayout.js'

const mocks = vi.hoisted(() => ({ role: 'admin', confirm: vi.fn(), activity: null, status: null }))
vi.mock('../context/AuthContext.jsx', () => ({ useAuth: () => ({ role: mocks.role }) }))
vi.mock('../context/ConfirmContext.jsx', () => ({ useConfirm: () => ({ confirm: mocks.confirm }) }))
vi.mock('../notifications/mailEventStream.js', () => ({
  subscribeNodeActivity: fn => { mocks.activity = fn; return vi.fn() },
  subscribeMailStatus: fn => { mocks.status = fn; return vi.fn() },
}))
const inventory = [{ id: 'rtx4060', label: 'RTX 4060', available: true, models: [{ name: 'qwen2.5:7b', size_bytes: 4.7e9 }, { name: 'other:8b', size_bytes: 6.14e9 }] },
  { id: 'jagadeesh', label: 'Jagadeesh', available: true, models: [{ name: 'qwen2.5:7b', size_bytes: 4.7e9 }] },
  { id: 'our_machine', label: 'Praveen', available: true, models: [] }]
const nodes = inventory.map((n, i) => ({ ...n, endpoint_reachable: true, ready: true, status: 'online', primary: i === 0, response_time_ms: 123,
  loaded_models: i === 0 ? ['qwen2.5:7b'] : [], required_models: { 'qwen2.5:7b': true }, ollama_version: 'fixture-version',
  breaker: { consecutive_failures: 0, in_cooldown: false, cooldown_remaining_s: 0 },
  gpu: i === 0 ? { size_bytes: 4.76e9, size_vram_bytes: 4.76e9 } : undefined }))
function fixture(path) {
  if (path.includes('/mail-model')) return { selection: { model: 'qwen2.5:7b', revision: 0, last_used: { model: 'qwen2.5:7b', node_label: 'Jagadeesh' } }, nodes: inventory }
  if (path.endsWith('/ollama/nodes')) return { primary_node: 'rtx4060', nodes, activity: { boot: 'one', version: 1, nodes: {} } }
  if (path.endsWith('/ollama/status')) return { models: { primary: 'qwen2.5:7b', validator: 'validator:9b', vision: 'qwen3-vl:8b-instruct', fallback: 'qwen2.5:7b' }, ollama: { status: 'healthy', configured_model: 'qwen2.5:7b', successful_requests: 42, failed_requests: 2, last_successful_request_at: '2026-10-29T05:27:51Z' } }
  if (path.endsWith('/metrics')) return { metrics: { queue_depth: 3, ai_retry_pending_total: 4, ai_processing_failure_total: 5800, mailbox_sync_success_total: 80199, mailbox_sync_failure_total: 273, websocket_connection_count: 2 } }
  if (path.endsWith('/health')) return { mailboxes: [{ connection_status: 'ERROR' }, { connection_status: 'CONNECTED' }] }
  if (path.includes('/review?')) return { events: [{ id: 'synthetic', created_at: '2026-10-28T12:00:00Z', ai_model: 'qwen2.5:7b@jagadeesh', validation_status: 'AUTO_VALIDATED', processing_duration_ms: 1234 }] }
  if (path.includes('/audit?')) return { entries: [{ at: '2026-10-28T12:00:00Z', actor: 'synthetic-admin', previous: false, new: true }] }
  if (path.endsWith('/config')) return { enabled: true, mailbox_sync_enabled: true, interview_auto_booking_enabled: true, offer_verification_enabled: false, oauth_configured: true, gmail_pubsub_configured: true }
  if (path.endsWith('/activity')) return { activity: { boot: 'one', version: 2, nodes: {} } }
  return { status: 'ok', model: 'qwen2.5:7b', response_time_ms: 25, models_unloaded: 1 }
}
let request
beforeEach(() => {
  mocks.role = 'admin'; mocks.confirm.mockReset().mockResolvedValue(true)
  request = vi.fn(async path => fixture(path))
  vi.stubGlobal('fetch', vi.fn(async url => ({ ok: true, headers: { get: () => 'application/json' }, json: async () => ({ enabled: !String(url).includes('ocr-policy') }) })))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
async function open() {
  const view = render(<AiControlsDashboard request={request} />)
  await screen.findByText('Last run: qwen2.5:7b on Jagadeesh', { exact: false })
  return view
}
const tab = name => screen.getByRole('button', { name, exact: true })
const heading = name => screen.queryByRole('heading', { name })
const MANAGEMENT = ['AI Model and Installed Models', 'AI Nodes', 'AI Policies', 'Workload Configurations']
const MONITORING = ['System Health and Diagnostics', 'Queue Statistics', 'Live Gateway Activity', 'Recruitment Event History']

describe('AI Controls: two tabs', () => {
  it('has exactly AI Management and Monitoring, management first', async () => {
    await open()
    const tabs = within(screen.getByRole('navigation', { name: 'AI Controls sections' })).getAllByRole('button')
    expect(tabs.map(b => b.textContent)).toEqual(['AI Management', 'Monitoring'])
    expect(tabs[0]).toHaveAttribute('aria-pressed', 'true')
    for (const gone of ['Overview', 'Models', 'Nodes', 'Policies', 'Workloads', 'Queues', 'Logs']) {
      expect(screen.queryByRole('button', { name: gone, exact: true })).not.toBeInTheDocument()
    }
  })

  it('shows each panel on its own tab only', async () => {
    const view = await open()
    for (const name of MANAGEMENT) expect(heading(name)).toBeInTheDocument()
    for (const name of MONITORING) expect(heading(name)).not.toBeInTheDocument()
    captureLayout('ai-controls-management', view.container)
    fireEvent.click(tab('Monitoring'))
    expect(tab('Monitoring')).toHaveAttribute('aria-pressed', 'true')
    for (const name of MONITORING) expect(heading(name)).toBeInTheDocument()
    for (const name of MANAGEMENT) expect(heading(name)).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'AI Mail model' })).not.toBeInTheDocument()
    captureLayout('ai-controls-monitoring', view.container)
  })

  it('renders every component exactly once across both tabs', async () => {
    await open()
    // Both panels are mounted (the inactive one hidden), so counting the whole
    // document counts the whole UI: nothing may appear on both tabs.
    const once = { hidden: true }
    for (const name of [...MANAGEMENT, ...MONITORING]) expect(screen.getAllByRole('heading', { name, ...once })).toHaveLength(1)
    expect(screen.getAllByRole('combobox', { name: 'AI Mail model', ...once })).toHaveLength(1)
    expect(screen.getAllByRole('region', { name: 'AI Mail model selection', ...once })).toHaveLength(1)
    expect(document.querySelectorAll('.sot-ai-node')).toHaveLength(3)
    expect(screen.getAllByText('Mailbox sync queued')).toHaveLength(1)
    expect(screen.getAllByText('Mailboxes in ERROR')).toHaveLength(1)
    expect(screen.getAllByText('AI nodes reachable')).toHaveLength(1)
    expect(screen.getAllByText('Gateway', { exact: true })).toHaveLength(1)
    expect(screen.getAllByText(/^Activity stream:/)).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Test Connection', ...once })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Test Model Response', ...once })).toHaveLength(1)
    // The validator is shown in Workload Configurations and nowhere else, and the
    // effective mail model only in the selector.
    expect(screen.getAllByText('validator:9b')).toHaveLength(1)
    expect(screen.queryByText('Mail classifier effective selection')).not.toBeInTheDocument()
    expect(screen.queryByText(/^Reported primary/)).not.toBeInTheDocument()
  })

  it('uses existing read APIs without running inference or mutations on load', async () => {
    await open()
    expect(screen.getByText('4.7 GB')).toBeInTheDocument()
    expect(request.mock.calls.filter(([path]) => path === '/api/ai-recruitment/mail-model')).toHaveLength(1)
    expect(request.mock.calls.every(([, opts]) => !opts?.method || opts.method === 'GET')).toBe(true)
    expect(request.mock.calls.some(([path]) => path.includes('/test'))).toBe(false)
    expect(screen.queryByText('Total AI Jobs (24h)')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /restart|pull|delete|load model/i, hidden: true })).not.toBeInTheDocument()
    fireEvent.click(tab('Monitoring'))
    expect(screen.getByText('3 / 3')).toBeInTheDocument()
    expect(request.mock.calls.some(([path]) => path.includes('/test'))).toBe(false)
  })

  it('shows exact installed and loaded inventory, sizes and node filtering', async () => {
    await open()
    expect(screen.getByText('6.14 GB')).toBeInTheDocument()
    expect(screen.getByText('Loaded', { exact: true })).toBeInTheDocument()
    fireEvent.change(screen.getByRole('combobox', { name: 'Model inventory node' }), { target: { value: 'jagadeesh' } })
    expect(screen.queryByText('6.14 GB')).not.toBeInTheDocument()
    expect(screen.getByText('Not loaded', { exact: true })).toBeInTheDocument()
  })

  it('reuses the mail selector Test then Save contract including revision', async () => {
    await open()
    fireEvent.click(screen.getByRole('button', { name: 'Test model & failover' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save AI Mail model' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Save AI Mail model' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/ai-recruitment/mail-model', { method: 'PUT', body: JSON.stringify({ model: 'qwen2.5:7b', revision: 0 }) }))
  })

  it('keeps the prior selection on failed model test and Save disabled', async () => {
    await open()
    request.mockImplementation(async path => { if (path.endsWith('/test')) throw new Error('Model needs two reachable nodes'); return fixture(path) })
    fireEvent.click(screen.getByRole('button', { name: 'Test model & failover' }))
    await screen.findByText(/Previous configured model retained/)
    expect(screen.getByRole('button', { name: 'Save AI Mail model' })).toBeDisabled()
  })

  it('uses existing node actions with unload confirmation and no new controls', async () => {
    await open()
    const card = screen.getByText('Jagadeesh', { selector: 'strong' }).closest('article')
    fireEvent.click(within(card).getByRole('button', { name: 'Set primary' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/ai-recruitment/ollama/nodes/jagadeesh/primary', { method: 'POST', body: '{}' }))
    await waitFor(() => expect(within(card).getByRole('button', { name: 'Unload' })).toBeEnabled())
    mocks.confirm.mockResolvedValueOnce(false)
    fireEvent.click(within(card).getByRole('button', { name: 'Unload' }))
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalled())
    expect(request.mock.calls.some(([p]) => p.endsWith('/unload'))).toBe(false)
    fireEvent.click(within(card).getByRole('button', { name: 'Unload' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/ai-recruitment/ollama/nodes/jagadeesh/unload', { method: 'POST', body: '{}' }))
  })

  it('keeps an action notice visible on either tab', async () => {
    await open()
    fireEvent.click(tab('Monitoring'))
    fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }))
    const notice = await screen.findByText('Connection check passed. This does not prove inference.')
    expect(notice).toHaveAttribute('role', 'status')
    fireEvent.click(tab('AI Management'))
    expect(notice).toBeVisible()
  })

  it('shows unavailable rather than fabricated zero metrics when an API fails', async () => {
    request.mockImplementation(async path => { if (path.endsWith('/metrics')) throw new Error('Admin access required'); return fixture(path) })
    await open()
    expect(screen.getByRole('alert')).toHaveTextContent('metrics: Admin access required')
    fireEvent.click(tab('Monitoring'))
    const queues = screen.getByRole('heading', { name: 'Queue Statistics' }).closest('section')
    expect(within(queues).getAllByText('Unavailable')).toHaveLength(7)
    expect(screen.queryByText('5,800')).not.toBeInTheDocument()
  })

  it('reports health from the existing status, node and mailbox APIs', async () => {
    await open()
    fireEvent.click(tab('Monitoring'))
    const health = screen.getByRole('heading', { name: 'System Health and Diagnostics' }).closest('section')
    expect(within(health).getByText('Healthy')).toBeInTheDocument()
    expect(within(health).getByText('Configured model qwen2.5:7b')).toBeInTheDocument()
    expect(within(health).getByText('3 / 3')).toBeInTheDocument()
    expect(within(health).getByText('1', { selector: 'strong' })).toBeInTheDocument()
    expect(within(health).getByText(new Date('2026-10-29T05:27:51Z').toLocaleTimeString())).toBeInTheDocument()
    expect(within(health).getByText('42')).toBeInTheDocument()
    const queues = screen.getByRole('heading', { name: 'Queue Statistics' }).closest('section')
    expect(within(queues).getByText('5,800').closest('article')).toHaveClass('is-bad')
    expect(within(queues).getByText('80,199').closest('article')).toHaveClass('is-good')
  })

  it('keeps event history distinct from live work and does not invent Naukri jobs', async () => {
    await open()
    fireEvent.click(tab('Monitoring'))
    expect(screen.getByText('qwen2.5:7b@jagadeesh')).toBeInTheDocument()
    expect(screen.getByText('AUTO_VALIDATED')).toHaveClass('ai-controls-badge', 'is-good')
    expect(screen.getByText(/Live activity unavailable; not evidence/)).toBeInTheDocument()
    act(() => mocks.status('Live'))
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/ai-recruitment/ollama/activity'))
    expect(screen.getByText('Activity stream: Live')).toBeInTheDocument()
    act(() => mocks.activity({ boot: 'one', version: 5, nodes: { rtx4060: [{ label: 'Mail analysis', workload: 'recruitment_mail_primary', started_at: 1793190000 }] } }))
    expect(screen.getByText('recruitment_mail_primary')).toBeInTheDocument()
    act(() => mocks.activity({ boot: 'one', version: 4, nodes: {} }))
    expect(screen.getByText('recruitment_mail_primary')).toBeInTheDocument()
    act(() => mocks.activity({ boot: 'two', version: 0, nodes: {} }))
    expect(screen.queryByText('recruitment_mail_primary')).not.toBeInTheDocument()
  })

  it('reads existing policy audits on demand and preserves strict policy role controls', async () => {
    await open()
    fireEvent.click(screen.getByRole('button', { name: 'View OCR history' }))
    await screen.findByText('synthetic-admin')
    expect(request).toHaveBeenCalledWith('/ai/ocr-policy/audit?limit=20')
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByText('synthetic-admin')).not.toBeInTheDocument()
    cleanup(); mocks.role = 'handler'
    await open()
    expect(screen.getByRole('heading', { name: 'AI Policies' })).toBeInTheDocument()
    expect(screen.getByText('Admin only')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'View OCR history' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'View Pure Ollama history' })).not.toBeInTheDocument()
  })

  it('runs generic diagnostics only on explicit action', async () => {
    await open()
    fireEvent.click(tab('Monitoring'))
    fireEvent.click(screen.getByRole('button', { name: 'Test Model Response' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/ai-recruitment/ollama/test-model', { method: 'POST', body: '{}' }))
    expect(await screen.findByText(/Model response passed:/)).toHaveAttribute('role', 'status')
  })

  it('merges residency and breakers into one node diagnostics table', async () => {
    request.mockImplementation(async path => {
      const body = fixture(path)
      if (path.endsWith('/ollama/nodes')) return { ...body, nodes: body.nodes.map(n => ({ ...n, gpu: { size_bytes: 6e9, size_vram_bytes: 0 } })) }
      return body
    })
    await open()
    const table = screen.getByText('Node diagnostics · readiness, breakers and residency').closest('details')
    expect(table).not.toHaveAttribute('open')
    fireEvent.click(screen.getByText('Node diagnostics · readiness, breakers and residency'))
    expect(within(table).getAllByText('0.00 GB VRAM / 6.00 GB model')).toHaveLength(3)
    expect(within(table).getAllByText('Not cooling down')).toHaveLength(3)
    expect(within(table).getAllByText('0 consecutive failures')).toHaveLength(3)
    expect(within(table).getAllByText('fixture-version')).toHaveLength(3)
    expect(screen.queryByText(/8 GB GPU/)).not.toBeInTheDocument()
  })

  it('names a reported primary only when no listed node carries the badge', async () => {
    request.mockImplementation(async path => path.endsWith('/ollama/nodes')
      ? { ...fixture(path), primary_node: 'retired_node', nodes: nodes.map(n => ({ ...n, primary: false })) } : fixture(path))
    await open()
    expect(screen.getByText('retired_node', { selector: 'strong' })).toBeInTheDocument()
  })

  it('immediately reflects a saved model without changing validator controls', async () => {
    await open()
    request.mockImplementation(async (path, options) => options?.method === 'PUT'
      ? { selection: { model: 'other:8b', revision: 1 } } : fixture(path))
    fireEvent.change(screen.getByRole('combobox', { name: 'AI Mail model' }), { target: { value: 'other:8b' } })
    fireEvent.click(screen.getByRole('button', { name: 'Test model & failover' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save AI Mail model' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Save AI Mail model' }))
    await waitFor(() => expect(screen.getByText('other:8b', { selector: '.sot-mail-model-current strong' })).toBeInTheDocument())
    expect(screen.getByText('other:8b', { selector: 'td' }).closest('tr')).toHaveClass('is-selected')
    expect(screen.queryByRole('combobox', { name: /validator/i })).not.toBeInTheDocument()
  })

  it('keeps an unsaved model draft while the other tab is open', async () => {
    await open()
    fireEvent.change(screen.getByRole('combobox', { name: 'AI Mail model' }), { target: { value: 'other:8b' } })
    fireEvent.click(tab('Monitoring'))
    fireEvent.click(tab('AI Management'))
    expect(screen.getByRole('combobox', { name: 'AI Mail model' })).toHaveValue('other:8b')
    expect(request.mock.calls.filter(([p]) => p === '/api/ai-recruitment/mail-model')).toHaveLength(1)
  })

  it('paginates inventory and events without fetching another API', async () => {
    request.mockImplementation(async path => {
      const body = fixture(path)
      if (path.endsWith('/mail-model')) return { ...body, nodes: [{ ...inventory[0], models: Array.from({ length: 14 }, (_, i) => ({ name: `synthetic-${i}:8b`, size_bytes: 5e9 })) }] }
      if (path.includes('/review?')) return { events: Array.from({ length: 12 }, (_, i) => ({ ai_model: `event-${i}:8b` })) }
      return body
    })
    await open()
    expect(screen.getByText('1–6 of 14')).toBeInTheDocument()
    captureLayout('ai-controls-inventory-paged', document.querySelector('.ai-controls'))
    expect(screen.queryByText('synthetic-6:8b', { selector: 'td' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Model inventory next page' }))
    expect(screen.getByText('7–12 of 14')).toBeInTheDocument()
    expect(screen.getByText('synthetic-6:8b', { selector: 'td' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Model inventory next page' }))
    expect(screen.getByText('13–14 of 14')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Model inventory next page' })).toBeDisabled()
    fireEvent.click(tab('Monitoring'))
    expect(screen.getByText('1–5 of 12')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Recruitment events next page' }))
    expect(screen.getByText('6–10 of 12')).toBeInTheDocument()
    expect(request.mock.calls.filter(([p]) => p.includes('/review?'))).toHaveLength(1)
    expect(request.mock.calls.filter(([p]) => p.endsWith('/mail-model'))).toHaveLength(1)
  })

  it('uses cookie authentication and reports HTTP errors without alternate authentication', async () => {
    fetch.mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({ detail: 'Admin access required' }) })
    await expect(aiControlsRequest('/api/ai-recruitment/metrics')).rejects.toThrow('Admin access required')
    expect(fetch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ credentials: 'include', cache: 'no-store' }))
  })
})
