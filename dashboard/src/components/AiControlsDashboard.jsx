import React, { useCallback, useEffect, useRef, useState } from 'react'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { subscribeNodeActivity, subscribeMailStatus } from '../notifications/mailEventStream.js'
import AiMailModelSelector from './AiMailModelSelector.jsx'
import { AiNodeManager } from './RecruitmentMailPanelRedesign.jsx'
import { PureOllamaToggle } from './PureOllamaToggle.jsx'
import { OcrToggle } from './OcrToggle.jsx'
import { Icon } from './ui/Icon.jsx'
import './AiControlsDashboard.css'

// All requests use already-shipped Operations routes and cookie authentication.
// A missing field is unavailable, never a zero or a deployment-time constant.
export async function aiControlsRequest(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json' }, ...options,
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : body.message || `Request failed (${response.status})`)
  return body
}

const SOURCES = {
  nodes: '/api/ai-recruitment/ollama/nodes',
  status: '/api/ai-recruitment/ollama/status',
  catalog: '/api/ai-recruitment/mail-model',
  metrics: '/api/ai-recruitment/metrics',
  config: '/api/ai-recruitment/config',
  mailboxes: '/api/candidate-mailboxes/health',
  events: '/api/ai-recruitment/review?limit=20',
}
const TABS = ['Overview', 'Models', 'Nodes', 'Policies', 'Workloads', 'Queues', 'Logs']
const number = value => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : 'Unavailable'
const gb = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${(value / 1e9).toFixed(2)} GB` : 'Unavailable'
const when = value => {
  if (!value) return 'Not recorded'
  const date = new Date(typeof value === 'number' ? value * 1000 : value)
  return Number.isNaN(date.getTime()) ? 'Not recorded' : date.toLocaleString()
}
const text = value => value === true ? 'Enabled' : value === false ? 'Disabled' : value ?? 'Unavailable'

function Card({ title, children, className = '' }) {
  return <section className={`ai-control-card ${className}`}><header><h2>{title}</h2></header><div className="ai-control-card-body">{children}</div></section>
}
function Rows({ headings, rows, empty = 'No records returned by this API.' }) {
  return <div className="ai-control-table-wrap"><table><thead><tr>{headings.map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>
    {rows.length ? rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j} data-label={headings[j]}>{cell}</td>)}</tr>) : <tr><td colSpan={headings.length}>{empty}</td></tr>}
  </tbody></table></div>
}

export function AiControlsDashboard({ request = aiControlsRequest }) {
  const auth = useAuth()
  const { confirm } = useConfirm()
  const [tab, setTab] = useState('Overview')
  const [data, setData] = useState({})
  const [errors, setErrors] = useState({})
  const [loading, setLoading] = useState(true)
  const [updated, setUpdated] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [nodeFilter, setNodeFilter] = useState('all')
  const [activity, setActivity] = useState({ boot: '', version: -1, nodes: {} })
  const [stream, setStream] = useState('Connecting')
  const [history, setHistory] = useState(null)
  const inFlight = useRef(null)
  const alive = useRef(true)
  const acceptActivity = useCallback(snapshot => {
    if (!alive.current || !snapshot || typeof snapshot.version !== 'number') return
    setActivity(current => snapshot.boot !== current.boot || snapshot.version >= current.version
      ? { boot: snapshot.boot, version: snapshot.version, nodes: snapshot.nodes || {} } : current)
  }, [])
  const refresh = useCallback(async () => {
    if (inFlight.current) return inFlight.current
    const operation = (async () => {
      setLoading(true)
      const entries = Object.entries(SOURCES)
      const results = await Promise.allSettled(entries.map(([, path]) => request(path)))
      if (!alive.current) return
      const values = {}, failures = {}
      results.forEach((result, i) => {
        const key = entries[i][0]
        if (result.status === 'fulfilled') values[key] = result.value
        else { values[key] = null; failures[key] = result.reason?.message || 'Unavailable' }
      })
      setData(values); setErrors(failures); setUpdated(new Date()); setLoading(false)
      if (values.nodes?.activity) acceptActivity(values.nodes.activity)
      else setActivity({ boot: '', version: -1, nodes: {} })
    })()
    inFlight.current = operation
    try { await operation } finally { inFlight.current = null }
  }, [request, acceptActivity])
  useEffect(() => {
    alive.current = true
    refresh()
    const timer = window.setInterval(() => { if (!document.hidden) refresh() }, 60000)
    return () => { alive.current = false; window.clearInterval(timer) }
  }, [refresh])
  useEffect(() => subscribeNodeActivity(acceptActivity), [acceptActivity])
  useEffect(() => subscribeMailStatus(status => {
    if (!alive.current) return
    setStream(status)
    if (status === 'Live') request('/api/ai-recruitment/ollama/activity').then(body => acceptActivity(body.activity)).catch(() => {})
  }), [request, acceptActivity])

  const action = async operation => {
    setBusy(true); setNotice('')
    try { await operation(); await refresh() } catch (error) { if (alive.current) setNotice(error.message) }
    finally { if (alive.current) setBusy(false) }
  }
  const makePrimary = node => action(async () => {
    const override = !node.ready
    if (override && !window.confirm(`${node.label} is not passing its model health check. Making it primary can stop AI workflows. Set it as primary anyway?`)) return
    await request(`/api/ai-recruitment/ollama/nodes/${node.id}/primary${override ? '?override=true' : ''}`, { method: 'POST', body: '{}' })
    setNotice(`${node.label} primary selection saved for one hour. Model preferences may still route elsewhere.`)
  })
  const unload = async node => {
    if (!await confirm({ title: `Unload models on ${node.label}?`, message: 'This frees GPU/RAM but does not power off the laptop. Models reload automatically on the next request.', confirmLabel: 'Unload models', variant: 'warn' })) return
    await action(async () => {
      const result = await request(`/api/ai-recruitment/ollama/nodes/${node.id}/unload`, { method: 'POST', body: '{}' })
      setNotice(`${node.label}: ${result.models_unloaded ?? 'reported'} configured model(s) unloaded.`)
    })
  }
  const test = kind => action(async () => {
    const result = await request(`/api/ai-recruitment/ollama/test-${kind}`, { method: 'POST', body: '{}' })
    if (result.status !== 'ok') throw new Error(result.error_message || result.ollama?.error_message || 'Ollama test failed')
    setNotice(kind === 'model' ? `Model response passed: ${result.model || 'model not reported'} (${number(result.response_time_ms)} ms).` : 'Connection check passed. This does not prove inference.')
  })
  const readHistory = async policy => {
    setHistory({ policy, entries: null })
    try {
      const result = await request(`/ai/${policy}-policy/audit?limit=20`)
      if (alive.current) setHistory({ policy, entries: result.entries || [] })
    } catch (error) { if (alive.current) setHistory({ policy, error: error.message }) }
  }
  const nodes = data.nodes?.nodes || []
  const catalog = data.catalog?.nodes || []
  const selection = data.catalog?.selection
  const metrics = data.metrics?.metrics || {}
  const models = data.status?.models || {}
  const status = data.status?.ollama
  const visible = name => tab === 'Overview' || tab === name
  const rows = catalog.filter(n => nodeFilter === 'all' || n.id === nodeFilter).flatMap(n => n.models.map(model => {
    const live = nodes.find(item => item.id === n.id)
    const loaded = live?.loaded_models
    const isLoaded = Array.isArray(loaded) ? loaded.some(name => name.replace(/:latest$/, '') === model.name.replace(/:latest$/, '')) : null
    return [model.name, n.label, gb(model.size_bytes), n.available ? 'Installed' : 'Unavailable', isLoaded === null ? 'Unavailable' : isLoaded ? 'Loaded' : 'Not loaded']
  }))
  const size = catalog.flatMap(n => n.models).find(m => m.name === selection?.model)?.size_bytes
  const active = Object.entries(activity.nodes).flatMap(([id, calls]) => calls.map(call => [nodes.find(n => n.id === id)?.label || id, call.label || call.kind, call.workload, when(call.started_at)]))
  const errorCount = Array.isArray(data.mailboxes?.mailboxes) ? data.mailboxes.mailboxes.filter(m => m.connection_status === 'ERROR').length : null

  return <div className="ai-controls sot-page sot-mailboxes-page">
    <div className="ai-controls-toolbar"><p>Models, nodes and routing · existing Operations controls</p><div><span>Last refreshed: {updated ? updated.toLocaleTimeString() : 'Not yet'}</span><button disabled={loading} onClick={refresh}><Icon name="refresh" />{loading ? 'Refreshing…' : 'Refresh'}</button></div></div>
    <nav className="ai-controls-tabs" aria-label="AI Controls sections">{TABS.map(name => <button key={name} aria-pressed={tab === name} onClick={() => setTab(name)}>{name}</button>)}</nav>
    {!!Object.keys(errors).length && <div className="ai-controls-errors" role="alert">{Object.entries(errors).map(([key, message]) => <p key={key}>{key}: {message}</p>)}</div>}
    {notice && <p role="status" className="ai-controls-notice">{notice}</p>}
    {tab === 'Overview' && <div className="ai-controls-stats">
      {[
        ['Selected AI Mail model', selection?.model || 'Unavailable', gb(size), 'code'],
        ['AI nodes reachable', data.nodes ? `${nodes.filter(n => n.endpoint_reachable).length} / ${nodes.length}` : 'Unavailable', 'Reachability, not inference proof', 'database'],
        ['Mailbox sync queued', number(metrics.queue_depth), 'Current queue depth · not AI jobs', 'clipboard'],
        ['Mailboxes in ERROR', number(errorCount), 'Current mailbox health response', 'bell'],
      ].map(([label, value, detail, icon]) => <article key={label}><span className="ai-controls-stat-icon"><Icon name={icon} size={24} /></span><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></article>)}
    </div>}
    <div className="ai-controls-grid">
      {visible('Models') && <div className="ai-controls-stack">
        <Card title="AI Model Selection · Mail Classification"><AiMailModelSelector request={request} refreshToken={updated?.getTime()} onSaved={saved => setData(current => ({ ...current, catalog: { ...current.catalog, selection: saved } }))} /><p className="ai-controls-note">Independent validator: <strong>{models.validator || 'Unavailable'}</strong> · read-only. Model selection affects AI Mail only.</p></Card>
        <Card title="Installed and Loaded Models"><label className="ai-controls-filter">Node <select aria-label="Model inventory node" value={nodeFilter} onChange={e => setNodeFilter(e.target.value)}><option value="all">All nodes</option>{catalog.map(n => <option key={n.id} value={n.id}>{n.label}</option>)}</select></label>
          <Rows headings={['Model', 'Node', 'Disk size', 'Availability', 'Residency']} rows={rows} />
          <p className="ai-controls-note">Sizes are installed bytes in decimal GB, not GPU capacity. No pull, delete or explicit load control exists here.</p>
        </Card>
      </div>}
      {visible('Nodes') && <div className="ai-controls-stack">
        <Card title="AI Nodes"><AiNodeManager nodes={nodes} activity={stream === 'Live' ? activity.nodes : {}} busy={busy} onMakePrimary={makePrimary} onUnload={unload} />
          <p className="ai-controls-note">Primary is a one-hour preference. Model-specific preferences and decision affinity can route requests elsewhere. A resident model is not proof of active work.</p>
          <Rows headings={['Node', 'Loaded models', 'Reported model residency', 'Ollama']} rows={nodes.map(n => [n.label, n.loaded_models?.join(', ') || 'None reported', n.gpu ? `${gb(n.gpu.size_vram_bytes)} VRAM / ${gb(n.gpu.size_bytes)} model` : 'Unavailable', n.ollama_version || 'Unavailable'])} />
          <p className="ai-controls-note">Residency describes the first loaded model reported by the existing health API; physical GPU type, capacity and utilization are not exposed.</p>
        </Card>
        <Card title="Routing and Failover · Read Only"><p>Reported primary: <strong>{nodes.find(n => n.id === data.nodes?.primary_node)?.label || data.nodes?.primary_node || 'Unavailable'}</strong></p>
          <Rows headings={['Node', 'Required models', 'Breaker', 'Failures', 'Cooldown']} rows={nodes.map(n => [n.label, n.required_models ? Object.entries(n.required_models).map(([model, installed]) => `${model}: ${installed ? 'installed' : 'missing'}`).join(' · ') : 'Unavailable', n.breaker ? n.breaker.in_cooldown ? 'Cooling down' : 'Not cooling down' : 'Unavailable', number(n.breaker?.consecutive_failures), n.breaker ? `${number(n.breaker.cooldown_remaining_s)}s` : 'Unavailable'])} />
          <p className="ai-controls-note">Per-model pin configuration and complete routing order have no existing read API. They are not inferred from the primary label. Failover has not been induced by this view.</p>
        </Card>
      </div>}
      {visible('Policies') && <Card title="AI Policies"><div className="ai-control-policy"><PureOllamaToggle /><p>ON sends inbound mail to Ollama after duplicate/direction checks; OFF restores rules-first detection. Safety validation remains.</p>{auth?.role === 'admin' && <button onClick={() => readHistory('pure-ollama')}>View Pure Ollama history</button>}</div><div className="ai-control-policy"><OcrToggle /><p>Global policy for local Tesseract execution across workflows. This is not an external-provider switch.</p>{auth?.role === 'admin' && <button onClick={() => readHistory('ocr')}>View OCR history</button>}</div>
        {history && <div aria-live="polite"><h3>{history.policy === 'ocr' ? 'OCR' : 'Pure Ollama'} policy history</h3>{history.error ? <p role="alert">{history.error}</p> : history.entries ? <Rows headings={['Time', 'Actor', 'Previous', 'New']} rows={history.entries.map(e => [when(e.at), e.actor || 'Not recorded', text(e.previous), text(e.new)])} /> : <p>Loading history…</p>}</div>}
      </Card>}
      {visible('Workloads') && <Card title="Workload Models and Configuration · Read Only"><Rows headings={['Workload', 'Configured model']} rows={[
        ['Mail classifier environment route', models.primary || 'Unavailable'], ['Mail classifier effective selection', selection?.model || 'Unavailable'], ['Independent mail validator', models.validator || 'Unavailable'], ['Recruitment document vision', models.vision || 'Unavailable'], ['Mail fallback', models.fallback || 'Unavailable'],
      ]} /><Rows headings={['Feature configuration', 'API value']} rows={['enabled', 'mailbox_sync_enabled', 'interview_auto_booking_enabled', 'offer_verification_enabled', 'oauth_configured', 'gmail_pubsub_configured'].map(key => [key.replaceAll('_', ' '), text(data.config?.[key])])} />
        <p className="ai-controls-note">Interview, payment and resume overrides, engine timeouts, model pins and external fallback configuration are backend-only and not returned by these APIs. No editing controls are added.</p>
      </Card>}
      {visible('Queues') && <Card title="Queue Statistics"><Rows headings={['Counter', 'Value', 'Scope']} rows={[
        ['Mailbox sync queued', number(metrics.queue_depth), 'Current'], ['AI retry pending', number(metrics.ai_retry_pending_total), 'Current'], ['AI processing failed', number(metrics.ai_processing_failure_total), 'Current parked rows'], ['Mailbox sync completed', number(metrics.mailbox_sync_success_total), 'Cumulative'], ['Mailbox sync failed / dead-letter', number(metrics.mailbox_sync_failure_total), 'Cumulative'], ['Average AI event processing', metrics.ai_processing_duration_ms_avg == null ? 'Unavailable' : `${Number(metrics.ai_processing_duration_ms_avg).toFixed(0)} ms`, 'All stored events'], ['Websocket connections', number(metrics.websocket_connection_count), 'Current'],
      ]} /><p className="ai-controls-note">The metrics API does not expose running/queued AI totals, a 24-hour success total, trends or queue cancellation. No such values or actions are synthesized.</p></Card>}
      {visible('Logs') && <div className="ai-controls-stack"><Card title="Live Gateway Activity"><p>Activity stream: <strong>{stream}</strong></p><Rows headings={['Node', 'Kind', 'Workload', 'Started']} rows={stream === 'Live' ? active : []} empty={stream === 'Live' ? 'No active gateway calls reported.' : 'Live activity unavailable; not evidence of an idle node.'} /></Card>
        <Card title="Recent Recruitment Event History"><Rows headings={['Recorded', 'Model / node provenance', 'Validation', 'Duration']} rows={(data.events?.events || []).map(e => [when(e.created_at), e.ai_model || 'Not recorded', e.validation_status || 'Not recorded', e.processing_duration_ms == null ? 'Not recorded' : `${e.processing_duration_ms} ms`])} />
          <p className="ai-controls-note">Up to 20 events from the existing recruitment review API, not a complete AI job log. No Naukri jobs or general server logs are supplied by this endpoint.</p>
        </Card></div>}
      {(tab === 'Overview' || tab === 'Nodes' || tab === 'Logs') && <Card title="AI Health and Existing Tests"><Rows headings={['Diagnostic', 'API value']} rows={[
        ['Gateway status', status?.status || 'Unavailable'], ['Configured health model', status?.configured_model || 'Unavailable'], ['Last health check', when(status?.last_checked_at)], ['Last successful inference', when(status?.last_successful_request_at)], ['Successful requests', number(status?.successful_requests)], ['Failed requests', number(status?.failed_requests)], ['Last error', status ? status.error_code || 'None reported' : 'Unavailable'],
      ]} />{status?.error_message && <p>{status.error_message}</p>}<div className="ai-controls-test-actions"><button disabled={busy} onClick={() => test('connection')}>Test Connection</button><button disabled={busy} onClick={() => test('model')}>Test Model Response</button></div><p className="ai-controls-note">Tests run only when clicked. Model tests perform inference and can load a model. Counters are process-local and reset on restart.</p></Card>}
    </div>
  </div>
}
