import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { formatIstDateTime } from '../utils/istTime.js'

// Data Room -> Daybreak. The Naukri accounts here are the logins Daybreak's workers (RTX 4060, Jagadeesh) use.
// Operations seals each password to every enrolled worker and hands the sealed copies to Daybreak's own
// queue; each worker collects and acknowledges its copy. This tab shows, per account, how far it has got:
// matched to a Daybreak candidate (Synced), acknowledged by every worker (Credential Delivered), seen
// signing in to Naukri as exactly that account in a real run (Connected) -- or why not (Needs Attention).
// It never shows or sends a password.

const API_BASE =
  typeof window !== 'undefined' && window.location.port === '3000'
    ? ''
    : typeof window !== 'undefined'
      ? `${window.location.protocol}//${window.location.host}`
      : ''

export const STATUS_LABELS = {
  synced: 'Synced',
  delivered: 'Credential Delivered',
  connected: 'Connected',
  needs_attention: 'Needs Attention',
}

const BATCH = 10
// Codes that a fresh delivery can fix; anything else (no match, duplicates, consent) needs a person first.
const REDELIVERABLE = new Set(['changed_since_delivery', 'delivery_expired', 'login', 'credentials'])

function when(value) {
  return value ? formatIstDateTime(value) : ''
}

async function call(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    ...options,
    headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`)
  return body
}

function Worker({ worker, onApprove, onRevoke, busy }) {
  const [typing, setTyping] = useState(false)
  const [fingerprint, setFingerprint] = useState('')
  return (
    <li className="dr-db-computer">
      <div className="dr-db-computer-head">
        <strong>{worker.name}</strong>
        <span className={`dr-status dr-db-exec--${worker.approved ? 'approved' : 'pending'}`}>{worker.approved ? 'Key approved' : 'Key not approved'}</span>
        {!worker.enabled && <span className="dr-muted">disabled in Daybreak</span>}
        {worker.enabled && worker.recipient === false && <span className="dr-muted">not receiving new logins</span>}
      </div>
      <div className="dr-db-computer-meta">
        <span title={worker.fingerprint}>Key fingerprint <code>{worker.fingerprint.slice(0, 16)}…</code>{worker.key && <> · {worker.key}</>}</span>
        {worker.last_seen && <span>Last seen {when(worker.last_seen)}</span>}
        {worker.approved && <span>Approved {when(worker.approved_at)} by {worker.approved_by}</span>}
        {worker.key_problem && <span className="dr-db-reason">Key cannot be approved: {worker.key_problem}</span>}
      </div>
      {!worker.approved && !typing && !worker.key_problem && (
        <button type="button" className="dr-add-btn" onClick={() => setTyping(true)} disabled={busy}>Approve key…</button>
      )}
      {!worker.approved && typing && (
        <form
          className="dr-db-approve"
          onSubmit={event => {
            event.preventDefault()
            onApprove(worker, fingerprint)
          }}
        >
          <label>
            Type the publicKeyCanonicalSha256 that machine-check printed on {worker.name} (at least 16 characters)
            <input value={fingerprint} onChange={e => setFingerprint(e.target.value)} placeholder="first 16+ characters" autoComplete="off" />
          </label>
          <button type="submit" className="dr-add-btn" disabled={busy || fingerprint.replace(/[^0-9a-f]/gi, '').length < 16}>Approve</button>
          <button type="button" className="dr-copy-btn" onClick={() => setTyping(false)}>Cancel</button>
        </form>
      )}
      {worker.approved && (
        <button type="button" className="dr-copy-btn" onClick={() => onRevoke(worker)} disabled={busy}>Revoke approval</button>
      )}
    </li>
  )
}

export function DataRoomDaybreakTab() {
  const { confirm } = useConfirm()
  const [view, setView] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [filter, setFilter] = useState('')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState(() => new Set())

  const load = useCallback(async () => {
    try {
      setView(await call('/data-room/daybreak'))
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // While a worker still has a copy to collect, look again every 30 s (Daybreak's answer is cached for a minute).
  const waiting = useMemo(() => (view?.accounts || []).some(row => row.delivery?.waiting_for?.length), [view])
  useEffect(() => {
    if (!waiting) return undefined
    const timer = setInterval(load, 30000)
    return () => clearInterval(timer)
  }, [waiting, load])

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return (view?.accounts || []).filter(row =>
      (!filter || row.status === filter) &&
      (!q || row.label.toLowerCase().includes(q) || row.email.includes(q)))
  }, [view, filter, search])

  // Only the workers Daybreak seals new logins to need an approved key; one it leaves out keeps what it holds.
  const recipients = (view?.workers || []).filter(w => w.enabled && w.recipient !== false)
  const allApproved = recipients.length > 0 && recipients.every(w => w.approved)
  const workerNames = recipients.map(w => w.name).join(' and ')

  const act = async (work, success) => {
    setBusy(true)
    setNotice('')
    try {
      const result = await work()
      setNotice(typeof success === 'function' ? success(result) : success)
      setSelected(new Set())
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const summary = results => {
    const delivered = results.flatMap(r => r.delivered || [])
    const refused = results.flatMap(r => r.refused || [])
    const reasons = [...new Set(refused.map(r => r.detail || r.reason.replace(/_/g, ' ')))]
    return `${delivered.length} sealed to ${workerNames}; each worker collects its copy within 15 minutes.${refused.length ? ` ${refused.length} not delivered: ${reasons.join('; ')}.` : ''}`
  }

  const deliver = async ids => {
    if (!ids.length) return
    const ok = await confirm({
      title: `Deliver ${ids.length} login${ids.length === 1 ? '' : 's'} to ${workerNames}?`,
      message: `Each password is sealed to every enrolled worker and passed through Daybreak's queue. When a worker collects it, it replaces the password that worker held for the candidate. Nothing runs on Naukri because of this.`,
      confirmLabel: 'Deliver',
    })
    if (!ok) return
    await act(async () => {
      const results = []
      for (let i = 0; i < ids.length; i += BATCH) {
        results.push(await call('/data-room/daybreak/deliveries', { method: 'POST', body: JSON.stringify({ account_ids: ids.slice(i, i + BATCH) }) }))
      }
      return results
    }, summary)
  }

  const addToDaybreak = async row => {
    const ok = await confirm({
      title: `Add ${row.label} to Daybreak?`,
      message: `${row.label} (${row.email}) is not a Daybreak candidate. Confirm that this candidate agreed to Daybreak managing their Naukri headline. Daybreak adds them and their login is sealed to ${workerNames}. Daybreak's daily round only runs the candidates on its own list, so nothing is saved to Naukri until someone adds them there.`,
      confirmLabel: 'Candidate agreed · Add',
    })
    if (ok) await act(async () => [await call('/data-room/daybreak/deliveries', {
      method: 'POST', body: JSON.stringify({ create_ids: [row.account_id], consent_confirmed: true }),
    })], summary)
  }

  const approve = (worker, fingerprint) => act(
    () => call(`/data-room/daybreak/workers/${encodeURIComponent(worker.key_id)}/approve`, { method: 'POST', body: JSON.stringify({ fingerprint }) }),
    `${worker.name}'s key approved.`,
  )

  const revoke = async worker => {
    const ok = await confirm({
      title: `Revoke approval of ${worker.name}'s key?`,
      message: 'No login is sealed while an enrolled worker\'s key is not approved. Copies the worker already holds stay in its own vault.',
      confirmLabel: 'Revoke', variant: 'danger',
    })
    if (ok) await act(() => call(`/data-room/daybreak/workers/${encodeURIComponent(worker.key_id)}/revoke`, { method: 'POST' }), `${worker.name}'s approval revoked.`)
  }

  if (!view) return error ? <p className="dr-error" role="alert">{error}</p> : <p className="dr-muted">Loading Daybreak sync…</p>

  const deliverable = row => row.synced && row.attention_codes.every(code => REDELIVERABLE.has(code))
  const selectable = rows.filter(deliverable)
  const rec = view.reconciliation
  return (
    <section className="dr-db" aria-label="Daybreak sync">
      {error && <p className="dr-error" role="alert">{error}</p>}
      {notice && <p className="dr-db-notice" role="status">{notice}</p>}

      <div className="dr-db-counts">
        {Object.entries(STATUS_LABELS).map(([key, label]) => (
          <button key={key} type="button" className={`dr-status dr-db-status--${key}${filter === key ? ' dr-db-count--on' : ''}`}
            onClick={() => setFilter(filter === key ? '' : key)} aria-pressed={filter === key}>
            {label} · {view.counts[key] ?? 0}
          </button>
        ))}
      </div>

      <h3 className="dr-db-h">Daybreak workers</h3>
      {view.remote_control && (
        <p className="dr-muted">
          {view.remote_control.enabled ? 'Daybreak runs on its dedicated workers.' : 'Daybreak has not switched to its dedicated workers yet; they hold the logins ready.'}
          {' '}Read from Daybreak {when(view.fetched_at)}.
        </p>
      )}
      {view.workers.length === 0
        ? <p className="dr-muted">Daybreak has no enrolled worker.</p>
        : <ul className="dr-db-computers">{view.workers.map(worker => (
          <Worker key={worker.key_id} worker={worker} busy={busy} onApprove={approve} onRevoke={revoke} />
        ))}</ul>}

      <h3 className="dr-db-h">Matching</h3>
      <p className="dr-db-rec">
        {rec.data_room_accounts} Naukri accounts · {rec.matched} match a Daybreak candidate by address
        {rec.missing_in_daybreak ? ` · ${rec.missing_in_daybreak} not in Daybreak` : ''}
        {rec.duplicates ? ` · ${rec.duplicates} duplicated` : ''}
        {rec.invalid_usernames ? ` · ${rec.invalid_usernames} without an email username` : ''}
        {rec.names_differ ? ` · ${rec.names_differ} with a different name in Daybreak` : ''}
        {rec.daybreak_only ? ` · ${rec.daybreak_only} in Daybreak only` : ''}
      </p>
      {view.daybreak_only.length > 0 && (
        <details className="dr-db-only">
          <summary>In Daybreak but not in the Data Room ({view.daybreak_only.length})</summary>
          <ul>{view.daybreak_only.map(p => <li key={p.id}>{p.name} · {p.email}</li>)}</ul>
        </details>
      )}

      <div className="dr-tab-header">
        <input className="dr-db-search" type="search" placeholder="Search name or address" value={search} onChange={e => setSearch(e.target.value)} aria-label="Search accounts" />
        <div className="dr-tab-header-actions">
          {!allApproved && <span className="dr-muted">Approve every worker&apos;s key before delivering.</span>}
          <button type="button" className="dr-add-btn" disabled={busy || !allApproved || selected.size === 0}
            onClick={() => deliver([...selected])}>
            Deliver selected ({selected.size})
          </button>
        </div>
      </div>

      <div className="cand-table-wrap">
        <table className="cand-table dr-table dr-db-table">
          <thead>
            <tr>
              <th>
                <input type="checkbox" aria-label="Select all deliverable accounts"
                  checked={selectable.length > 0 && selectable.every(row => selected.has(row.account_id))}
                  onChange={e => setSelected(e.target.checked ? new Set(selectable.map(r => r.account_id)) : new Set())} />
              </th>
              <th>Account</th>
              <th>Status</th>
              <th>Details</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={5} className="dr-empty">No accounts</td></tr>}
            {rows.map(row => (
              <tr key={row.account_id}>
                <td>
                  <input type="checkbox" aria-label={`Select ${row.label}`} disabled={!deliverable(row)}
                    checked={selected.has(row.account_id)}
                    onChange={e => {
                      const next = new Set(selected)
                      if (e.target.checked) next.add(row.account_id)
                      else next.delete(row.account_id)
                      setSelected(next)
                    }} />
                </td>
                <td>
                  <div className="dr-db-name">{row.label}</div>
                  <div className="dr-muted">{row.email}</div>
                  {row.notes.includes('name_differs') && row.profile && <div className="dr-muted">Daybreak: {row.profile.name}</div>}
                </td>
                <td>
                  <span className={`dr-status dr-db-status--${row.status}`}>{STATUS_LABELS[row.status]}</span>
                  <div className="dr-db-steps" aria-label="Progress">
                    <span className={row.synced ? 'dr-db-step--done' : ''}>Synced</span>
                    <span className={row.delivered ? 'dr-db-step--done' : ''}>Delivered</span>
                    <span className={row.connected ? 'dr-db-step--done' : ''}>Connected</span>
                  </div>
                </td>
                <td className="dr-db-details">
                  {row.attention.map(reason => <div key={reason} className="dr-db-reason">{reason}</div>)}
                  {row.delivery && (
                    <div>
                      Sealed {when(row.delivery.at)}
                      {row.delivery.receipts.map(r => ` · ${r.worker} acknowledged ${when(r.at)}`).join('')}
                      {row.delivery.waiting_for.length > 0 ? ` · waiting for ${row.delivery.waiting_for.join(', ')}` : ''}
                    </div>
                  )}
                  {row.connection && <div>Signed in to Naukri on {row.connection.worker} {when(row.connection.signed_in_at)}</div>}
                </td>
                <td className="dr-db-actions">
                  {row.can_add_to_daybreak && allApproved && (
                    <button type="button" className="dr-copy-btn" disabled={busy} onClick={() => addToDaybreak(row)}>Add to Daybreak</button>
                  )}
                  {deliverable(row) && allApproved && (
                    <button type="button" className="dr-copy-btn" disabled={busy} onClick={() => deliver([row.account_id])}>Deliver</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
