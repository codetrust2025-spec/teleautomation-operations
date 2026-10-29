import React, { useCallback, useEffect, useRef, useState } from 'react'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { subscribeNodeActivity, subscribeMailStatus } from '../notifications/mailEventStream.js'
import AiMailModelSelector from './AiMailModelSelector.jsx'
import AiNodeManager from './AiNodeManager.jsx'
import { PureOllamaToggle } from './PureOllamaToggle.jsx'
import { OcrToggle } from './OcrToggle.jsx'
import { Icon } from './ui/Icon.jsx'

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
// Two tabs, each panel rendered once: what an operator changes, and what they watch.
// The old Overview repeated the model selector, node cards and gateway line from
// the other tabs; nothing on it was unique, so it is gone rather than moved.
const TABS = [['AI Management', 'code'], ['Monitoring', 'chart']]
const number = value => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : 'Unavailable'
const gb = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${(value / 1e9).toFixed(2)} GB` : 'Unavailable'
const toDate = value => {
  if (!value) return null
  const date = new Date(typeof value === 'number' ? value * 1000 : value)
  return Number.isNaN(date.getTime()) ? null : date
}
const when = value => toDate(value)?.toLocaleString() || 'Not recorded'
const text = value => value === true ? 'Enabled' : value === false ? 'Disabled' : value ?? 'Unavailable'
const sameModel = (a, b) => !!a && !!b && a.replace(/:latest$/, '') === b.replace(/:latest$/, '')
// Colour carries meaning only where the value has one: zero failures is good,
// any failure is bad, and a count with no direction stays neutral.
const failureTone = value => typeof value !== 'number' ? '' : value > 0 ? 'bad' : 'good'
const backlogTone = value => typeof value !== 'number' ? '' : value > 0 ? 'warn' : 'good'
const validationTone = value => /REVIEW|REJECT|FAIL/.test(value || '') ? 'warn' : /VALIDATED/.test(value || '') ? 'good' : ''

function Card({ title, subtitle, icon, aside, children, className = '' }) {
  return <section className={`ai-control-card ${className}`}>
    <header>{icon && <span className="ai-control-card-icon"><Icon name={icon} size={18} /></span>}<div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{aside}</header>
    <div className="ai-control-card-body">{children}</div>
  </section>
}
function Tile({ label, value, detail, tone = '' }) {
  return <article className={`ai-controls-tile${tone ? ` is-${tone}` : ''}`}><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</article>
}
function Rows({ headings, rows, empty = 'No records returned by this API.', pageSize = 0, label = 'Records', highlight }) {
  const [page, setPage] = useState(0)
  const pages = pageSize ? Math.max(1, Math.ceil(rows.length / pageSize)) : 1
  const current = Math.min(page, pages - 1)
  const start = pageSize ? current * pageSize : 0
  const shown = pageSize ? rows.slice(start, start + pageSize) : rows
  return <div className="ai-control-table-wrap"><table><thead><tr>{headings.map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>
    {shown.length ? shown.map((row, i) => <tr key={start + i} className={highlight?.(row) ? 'is-selected' : undefined}>{row.map((cell, j) => <td key={j} data-label={headings[j]}>{cell}</td>)}</tr>) : <tr><td colSpan={headings.length}>{empty}</td></tr>}
  </tbody></table>{pageSize > 0 && rows.length > 0 && <nav className="ai-controls-pagination" aria-label={`${label} pagination`}>
    <span>{start + 1}–{Math.min(start + pageSize, rows.length)} of {rows.length}</span>
    <button aria-label={`${label} previous page`} disabled={current === 0} onClick={() => setPage(current - 1)}>Previous</button>
    <button aria-label={`${label} next page`} disabled={current === pages - 1} onClick={() => setPage(current + 1)}>Next</button>
  </nav>}</div>
}
function Disclosure({ summary, open = false, children }) {
  return <details className="ai-controls-advanced" open={open}><summary>{summary}</summary>{children}</details>
}

export function AiControlsDashboard({ request = aiControlsRequest }) {
  const auth = useAuth()
  const { confirm } = useConfirm()
  const [tab, setTab] = useState(TABS[0][0])
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
  const isAdmin = auth?.role === 'admin'
  const nodes = data.nodes?.nodes || []
  const catalog = data.catalog?.nodes || []
  const selection = data.catalog?.selection
  const metrics = data.metrics?.metrics || {}
  const models = data.status?.models || {}
  const status = data.status?.ollama
  const rows = catalog.filter(n => nodeFilter === 'all' || n.id === nodeFilter).flatMap(n => n.models.map(model => {
    const live = nodes.find(item => item.id === n.id)
    const loaded = live?.loaded_models
    const isLoaded = Array.isArray(loaded) ? loaded.some(name => sameModel(name, model.name)) : null
    return [model.name, n.label, gb(model.size_bytes),
      <span className={`ai-controls-status is-${n.available ? 'good' : 'bad'}`}>{n.available ? 'Installed' : 'Unavailable'}</span>,
      isLoaded === null ? 'Unavailable' : isLoaded ? <span className="ai-controls-loaded">Loaded</span> : 'Not loaded']
  }))
  const active = Object.entries(activity.nodes).flatMap(([id, calls]) => calls.map(call => [nodes.find(n => n.id === id)?.label || id, call.label || call.kind, call.workload, when(call.started_at)]))
  const errorCount = Array.isArray(data.mailboxes?.mailboxes) ? data.mailboxes.mailboxes.filter(m => m.connection_status === 'ERROR').length : null
  const reachable = nodes.filter(n => n.endpoint_reachable).length
  const lastSuccess = toDate(status?.last_successful_request_at)
  // node.primary is computed from primary_node by the same API, so the badge on the
  // node row says it; only an id that names no listed node needs saying separately.
  const strayPrimary = data.nodes?.primary_node && !nodes.some(n => n.id === data.nodes.primary_node) ? data.nodes.primary_node : null
  const gateway = status?.status
  const readOnly = <span className="ai-controls-pill">Read only</span>

  const management = <div className="ai-controls-columns">
    <div className="ai-controls-column">
      <Card title="AI Model and Installed Models" subtitle="The model for AI Mail classification: test it, then save" icon="code">
        <AiMailModelSelector request={request} catalogData={data.catalog ?? null} onSaved={saved => setData(current => ({ ...current, catalog: { ...current.catalog, selection: saved } }))} />
        <p className="ai-controls-note">Selection affects AI Mail only. The validator and other workloads are listed, read only, under Workload Configurations.</p>
        <div className="ai-controls-section">
          <div className="ai-controls-section-head">
            <h3>Installed models <span className="ai-controls-count">{data.catalog ? rows.length : '—'}</span></h3>
            <label className="ai-controls-filter">Node <select aria-label="Model inventory node" value={nodeFilter} onChange={e => setNodeFilter(e.target.value)}><option value="all">All nodes</option>{catalog.map(n => <option key={n.id} value={n.id}>{n.label}</option>)}</select></label>
          </div>
          <Rows key={nodeFilter} headings={['Model', 'Node', 'Disk size', 'Availability', 'Residency']} rows={rows} pageSize={6} label="Model inventory" highlight={row => sameModel(row[0], selection?.model)} />
          <Disclosure summary="Inventory details"><p>Sizes are installed bytes in decimal GB, not GPU capacity. No pull, delete or explicit load control exists here.</p></Disclosure>
        </div>
      </Card>
      <Card title="AI Policies" subtitle="Global switches for AI processing and content extraction" icon="check" aside={!isAdmin && <span className="ai-controls-pill">Admin only</span>}>
        <div className="ai-controls-policies">
          <div className="ai-control-policy"><PureOllamaToggle /><p>ON sends inbound mail to Ollama after duplicate/direction checks; OFF restores rules-first detection. Safety validation remains.</p>{isAdmin && <button aria-label="View Pure Ollama history" onClick={() => readHistory('pure-ollama')}>View history</button>}</div>
          <div className="ai-control-policy"><OcrToggle /><p>Global policy for local Tesseract across workflows; not an external-provider switch.</p>{isAdmin && <button aria-label="View OCR history" onClick={() => readHistory('ocr')}>View history</button>}</div>
        </div>
        {history && <div className="ai-controls-section" aria-live="polite">
          <div className="ai-controls-section-head"><h3>{history.policy === 'ocr' ? 'OCR' : 'Pure Ollama'} policy history</h3><button onClick={() => setHistory(null)}>Close</button></div>
          {history.error ? <p role="alert">{history.error}</p> : history.entries ? <Rows headings={['Time', 'Actor', 'Previous', 'New']} rows={history.entries.map(e => [when(e.at), e.actor || 'Not recorded', text(e.previous), text(e.new)])} pageSize={6} label="Policy history" /> : <p>Loading history…</p>}
        </div>}
      </Card>
    </div>
    <div className="ai-controls-column">
      <Card title="AI Nodes" subtitle="Set the primary node, or free a node's GPU memory" icon="database" className="ai-controls-nodes">
        <AiNodeManager nodes={nodes} activity={stream === 'Live' ? activity.nodes : {}} busy={busy} onMakePrimary={makePrimary} onUnload={unload} />
        {strayPrimary && <p className="ai-controls-note">Reported primary <strong>{strayPrimary}</strong> is not one of the listed nodes.</p>}
        <div className="ai-controls-section">
          <h3>Failover and routing {readOnly}</h3>
          <p className="ai-controls-note">Primary is a one-hour preference. Per-model pins and full routing order have no read API; decision affinity can route elsewhere. A resident model is not proof of active work.</p>
          <Disclosure summary="Node diagnostics · readiness, breakers and residency">
            <Rows headings={['Node', 'Required models', 'Breaker', 'Loaded models', 'Ollama']} rows={nodes.map(n => [n.label,
              n.required_models ? Object.entries(n.required_models).map(([model, installed]) => `${model}: ${installed ? 'installed' : 'missing'}`).join(' · ') : 'Unavailable',
              n.breaker ? <span>{n.breaker.in_cooldown ? `Cooling down · ${number(n.breaker.cooldown_remaining_s)}s left` : 'Not cooling down'}<small>{number(n.breaker.consecutive_failures)} consecutive failures</small></span> : 'Unavailable',
              <span>{n.loaded_models?.join(', ') || 'None reported'}<small>{n.gpu ? `${gb(n.gpu.size_vram_bytes)} VRAM / ${gb(n.gpu.size_bytes)} model` : 'Residency unavailable'}</small></span>,
              n.ollama_version || 'Unavailable'])} />
            <p className="ai-controls-note">Residency is the first loaded model reported by the health API; physical GPU capacity and utilization are not exposed.</p>
          </Disclosure>
        </div>
      </Card>
      <Card title="Workload Configurations" subtitle="The model each AI workload is configured to use" icon="list" aside={readOnly}>
        <Rows headings={['Workload', 'Configured model']} rows={[
          ['Mail classifier environment route', models.primary || 'Unavailable'], ['Independent mail validator', models.validator || 'Unavailable'], ['Recruitment document vision', models.vision || 'Unavailable'], ['Mail fallback', models.fallback || 'Unavailable'],
        ]} />
        <p className="ai-controls-note">The mail classifier's effective model is the AI Model selection, so it is not repeated here.</p>
        <Disclosure summary="Feature configuration"><Rows headings={['Feature configuration', 'API value']} rows={['enabled', 'mailbox_sync_enabled', 'interview_auto_booking_enabled', 'offer_verification_enabled', 'oauth_configured', 'gmail_pubsub_configured'].map(key => [key.replaceAll('_', ' '), text(data.config?.[key])])} /><p className="ai-controls-note">Backend-only overrides, timeouts and model pins have no read API here; no editing controls are added.</p></Disclosure>
      </Card>
    </div>
  </div>

  const monitoring = <div className="ai-controls-columns">
    <div className="ai-controls-column">
      <Card title="System Health and Diagnostics" subtitle="Gateway, node reachability and mailbox errors" icon="bolt">
        <div className="ai-controls-tiles ai-controls-health">
          <Tile label="Gateway" value={gateway ? gateway.charAt(0).toUpperCase() + gateway.slice(1) : 'Unavailable'} detail={status?.configured_model ? `Configured model ${status.configured_model}` : 'Model unavailable'} tone={!gateway ? '' : gateway === 'healthy' ? 'good' : 'warn'} />
          <Tile label="AI nodes reachable" value={data.nodes ? `${reachable} / ${nodes.length}` : 'Unavailable'} detail="Reachability, not inference proof" tone={!data.nodes || !nodes.length ? '' : reachable === nodes.length ? 'good' : reachable ? 'warn' : 'bad'} />
          <Tile label="Mailboxes in ERROR" value={number(errorCount)} detail="Current mailbox health" tone={failureTone(errorCount)} />
          <Tile label="Last successful inference" value={lastSuccess ? lastSuccess.toLocaleTimeString() : 'Not recorded'} detail={lastSuccess ? lastSuccess.toLocaleDateString() : 'From gateway status'} tone={lastSuccess ? 'info' : ''} />
        </div>
        <div className="ai-controls-test-actions">
          <button disabled={busy} onClick={() => test('connection')}><Icon name="bolt" />Test Connection</button>
          <button disabled={busy} onClick={() => test('model')}><Icon name="sparkles" />Test Model Response</button>
          <p className="ai-controls-note">Tests run only when clicked; the model test performs inference and can load a model.</p>
        </div>
        <Disclosure summary="Advanced health diagnostics" open>
          <dl className="ai-controls-kv">
            {[['Last health check', when(status?.last_checked_at)], ['Successful requests', number(status?.successful_requests)], ['Failed requests', number(status?.failed_requests)], ['Last error', status ? status.error_code || 'None reported' : 'Unavailable']]
              .map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}
          </dl>
          {status?.error_message && <p>{status.error_message}</p>}
          <p className="ai-controls-note">Counters reset on restart.</p>
        </Disclosure>
      </Card>
      <Card title="Live Gateway Activity" subtitle="AI calls running on the nodes right now" icon="chart" aside={<span className={`ai-controls-pill is-${stream === 'Live' ? 'good' : 'warn'}`}>Activity stream: {stream}</span>}>
        <Rows headings={['Node', 'Kind', 'Workload', 'Started']} rows={stream === 'Live' ? active : []} empty={stream === 'Live' ? 'No active gateway calls reported.' : 'Live activity unavailable; not evidence of an idle node.'} />
        <p className="ai-controls-note">Pushed over the activity stream and re-read whenever it reconnects. Finished calls leave this list.</p>
      </Card>
    </div>
    <div className="ai-controls-column">
      <Card title="Queue Statistics" subtitle="Mailbox sync and AI processing counters" icon="clipboard">
        <p className="ai-controls-group">Current</p>
        <div className="ai-controls-tiles">
          <Tile label="Mailbox sync queued" value={number(metrics.queue_depth)} tone={backlogTone(metrics.queue_depth)} />
          <Tile label="AI retry pending" value={number(metrics.ai_retry_pending_total)} tone={backlogTone(metrics.ai_retry_pending_total)} />
          <Tile label="AI processing failed" value={number(metrics.ai_processing_failure_total)} detail="Parked rows" tone={failureTone(metrics.ai_processing_failure_total)} />
          <Tile label="Websocket connections" value={number(metrics.websocket_connection_count)} />
        </div>
        <p className="ai-controls-group">Cumulative</p>
        <div className="ai-controls-tiles">
          <Tile label="Mailbox sync completed" value={number(metrics.mailbox_sync_success_total)} tone={typeof metrics.mailbox_sync_success_total === 'number' ? 'good' : ''} />
          <Tile label="Mailbox sync failed / dead-letter" value={number(metrics.mailbox_sync_failure_total)} tone={failureTone(metrics.mailbox_sync_failure_total)} />
          <Tile label="Average AI event processing" value={metrics.ai_processing_duration_ms_avg == null ? 'Unavailable' : `${Number(metrics.ai_processing_duration_ms_avg).toFixed(0)} ms`} detail="All stored events" />
        </div>
        <Disclosure summary="Counter scope"><p>No running/queued AI totals, 24-hour success totals, trends or cancellation are exposed by this API.</p></Disclosure>
      </Card>
      <Card title="Recruitment Event History" subtitle="Recent recruitment review events and how they were validated" icon="calendar">
        <Rows headings={['Recorded', 'Model / node provenance', 'Validation', 'Duration']} rows={(data.events?.events || []).map(e => [when(e.created_at), e.ai_model || 'Not recorded',
          e.validation_status ? <span className={`ai-controls-badge${validationTone(e.validation_status) ? ` is-${validationTone(e.validation_status)}` : ''}`}>{e.validation_status}</span> : 'Not recorded',
          e.processing_duration_ms == null ? 'Not recorded' : `${e.processing_duration_ms} ms`])} pageSize={5} label="Recruitment events" />
        <Disclosure summary="History scope"><p>Up to 20 recruitment review events; not a complete AI job log. No Naukri jobs or general server logs are supplied by this API.</p></Disclosure>
      </Card>
    </div>
  </div>

  return <div className="ai-controls sot-page sot-mailboxes-page">
    <div className="ai-controls-toolbar"><p>Operations AI Control Center</p><div><span>Last refreshed: {updated ? updated.toLocaleTimeString() : 'Not yet'}</span><button disabled={loading} onClick={refresh}><Icon name="refresh" />{loading ? 'Refreshing…' : 'Refresh'}</button></div></div>
    <nav className="ai-controls-tabs" aria-label="AI Controls sections">{TABS.map(([name, icon]) => <button key={name} aria-pressed={tab === name} onClick={() => setTab(name)}><Icon name={icon} />{name}</button>)}</nav>
    {!!Object.keys(errors).length && <div className="ai-controls-errors" role="alert">{Object.entries(errors).map(([key, message]) => <p key={key}>{key}: {message}</p>)}</div>}
    {notice && <p role="status" className="ai-controls-notice">{notice}</p>}
    {/* Both panels stay mounted, so an unsaved model draft, a filter or an open
        history survives a look at the other tab. */}
    <section aria-label="AI Management panel" hidden={tab !== 'AI Management'}>{management}</section>
    <section aria-label="Monitoring panel" hidden={tab !== 'Monitoring'}>{monitoring}</section>
  </div>
}
