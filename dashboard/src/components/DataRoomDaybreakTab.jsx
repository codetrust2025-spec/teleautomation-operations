import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { formatIstDateTime } from '../utils/istTime.js'

// Data Room -> Daybreak. The Naukri accounts here are the logins Daybreak's browser uses. This tab shows, for
// each one, how far it has got: matched to a Daybreak candidate (Synced), confirmed held by the approved
// computer (Credential Delivered), and seen signing in to Naukri as exactly that account (Connected) -- or
// why not (Needs Attention). It never shows or sends a password: Operations seals each one to the computer
// itself when that computer checks in.

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

const DELIVERY_LABELS = {
  requested: 'Waiting for the computer',
  sent: 'Sent, waiting for its receipt',
  stored: 'Stored',
  unchanged: 'Already held, unchanged',
  replaced: 'Replaced the old password',
  created: 'Added to Daybreak and stored',
  differs: 'Computer holds a different password',
  rejected: 'Refused by the computer',
  expired: 'Never collected',
}

const KIND_LABELS = { praveen: 'Praveen', rtx: 'RTX 4060', jagadeesh: 'Jagadeesh' }

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

function Computer({ executor, onApprove, onRevoke, busy }) {
  const [typing, setTyping] = useState(false)
  const [fingerprint, setFingerprint] = useState('')
  const [cutover, setCutover] = useState(false)
  const dedicated = executor.kind !== 'praveen'
  return (
    <li className="dr-db-computer">
      <div className="dr-db-computer-head">
        <strong>{executor.name}</strong>
        <span className="dr-muted">{KIND_LABELS[executor.kind] || executor.kind}</span>
        <span className={`dr-status dr-db-exec--${executor.status}`}>{executor.status === 'approved' && executor.active ? 'Approved · active' : executor.status}</span>
      </div>
      <div className="dr-db-computer-meta">
        <span>Fingerprint <code>{executor.fingerprint}</code></span>
        {executor.last_seen_at && <span>Last check-in {when(executor.last_seen_at)}</span>}
        {executor.approved_at && <span>Approved {when(executor.approved_at)} by {executor.approved_by}</span>}
        {executor.daybreak_mode && (
          <span>Daybreak runs on {executor.daybreak_mode === 'remote' ? executor.daybreak_worker || 'a dedicated worker' : 'Praveen'}</span>
        )}
      </div>
      {executor.status === 'pending' && !typing && (
        <button type="button" className="dr-add-btn" onClick={() => setTyping(true)} disabled={busy}>Approve…</button>
      )}
      {executor.status === 'pending' && typing && (
        <form
          className="dr-db-approve"
          onSubmit={event => {
            event.preventDefault()
            onApprove(executor, fingerprint, cutover)
          }}
        >
          <label>
            Type the fingerprint shown on {executor.name} itself
            <input value={fingerprint} onChange={e => setFingerprint(e.target.value)} placeholder="XXXX-XXXX-XXXX-XXXX-XXXX" autoComplete="off" />
          </label>
          {dedicated && (
            <label className="dr-db-check">
              <input type="checkbox" checked={cutover} onChange={e => setCutover(e.target.checked)} />
              Daybreak&apos;s cutover to {executor.name} is complete
            </label>
          )}
          <button type="submit" className="dr-add-btn" disabled={busy || !fingerprint.trim()}>Approve</button>
          <button type="button" className="dr-copy-btn" onClick={() => setTyping(false)}>Cancel</button>
        </form>
      )}
      {executor.status === 'approved' && (
        <button type="button" className="dr-copy-btn" onClick={() => onRevoke(executor)} disabled={busy}>Revoke</button>
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
  const [verify, setVerify] = useState(true)

  const load = useCallback(async () => {
    try {
      setView(await call('/data-room/daybreak'))
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // While a delivery is on its way, look again every 30 s; the computer checks in every 5 minutes.
  const waiting = useMemo(() => (view?.accounts || []).some(row => row.pending), [view])
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

  const deliverySummary = result => {
    const refused = result.refused?.length ? ` ${result.refused.length} refused (${[...new Set(result.refused.map(r => r.reason.replace(/_/g, ' ')))].join(', ')}).` : ''
    return `${result.queued.length} queued for ${result.executor?.name || 'the computer'}; it collects them at its next check-in.${refused}`
  }

  const deliver = async ids => {
    if (!ids.length) return
    const ok = await confirm({
      title: `Deliver ${ids.length} login${ids.length === 1 ? '' : 's'} to ${view?.active_executor?.name}?`,
      message: verify
        ? 'Each password is sealed to that computer. It keeps a different password it already holds unless you choose Replace. It then proves each login with one preview run (signs in, never saves).'
        : 'Each password is sealed to that computer. It keeps a different password it already holds unless you choose Replace.',
      confirmLabel: 'Deliver',
    })
    if (!ok) return
    await act(() => call('/data-room/daybreak/deliveries', {
      method: 'POST', body: JSON.stringify({ account_ids: ids, verify_ids: verify ? ids : [] }),
    }), deliverySummary)
  }

  const replace = async row => {
    const ok = await confirm({
      title: `Replace the password ${view?.active_executor?.name} holds for ${row.label}?`,
      message: 'The computer holds a different password for this candidate. Replace it only if the Data Room has the current one.',
      confirmLabel: 'Replace', variant: 'danger',
    })
    if (ok) await act(() => call('/data-room/daybreak/deliveries', {
      method: 'POST', body: JSON.stringify({ replace_ids: [row.account_id], verify_ids: verify ? [row.account_id] : [] }),
    }), deliverySummary)
  }

  const addToDaybreak = async row => {
    const ok = await confirm({
      title: `Add ${row.label} to Daybreak?`,
      message: `${row.label} (${row.email}) is not a Daybreak candidate. Confirm that this candidate agreed to Daybreak managing their Naukri headline. They are added with automation off and connected once; nothing is saved to Naukri until someone turns automation on in Daybreak.`,
      confirmLabel: 'Candidate agreed · Add',
    })
    if (ok) await act(() => call('/data-room/daybreak/deliveries', {
      method: 'POST', body: JSON.stringify({ create_ids: [row.account_id], consent_confirmed: true }),
    }), deliverySummary)
  }

  const approve = (executor, fingerprint, cutover) => act(
    () => call(`/data-room/daybreak/executors/${encodeURIComponent(executor.id)}/approve`, {
      method: 'POST', body: JSON.stringify({ fingerprint, cutover_confirmed: cutover }),
    }),
    `${executor.name} approved.`,
  )

  const revoke = async executor => {
    const ok = await confirm({
      title: `Revoke ${executor.name}?`,
      message: 'It gets no more logins, and deliveries still waiting for it are cancelled. Passwords it already holds stay in its own vault.',
      confirmLabel: 'Revoke', variant: 'danger',
    })
    if (ok) await act(() => call(`/data-room/daybreak/executors/${encodeURIComponent(executor.id)}/revoke`, { method: 'POST' }), `${executor.name} revoked.`)
  }

  if (!view) return error ? <p className="dr-error" role="alert">{error}</p> : <p className="dr-muted">Loading Daybreak sync…</p>

  const deliverable = row => row.synced && !row.attention_codes.some(code => !['changed_since_delivery', 'delivery_rejected', 'delivery_expired', 'login', 'credentials'].includes(code))
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

      <h3 className="dr-db-h">Computer</h3>
      {view.executors.length === 0
        ? <p className="dr-muted">No computer has registered yet. Praveen registers itself once its companion has the Data Room sync installed.</p>
        : <ul className="dr-db-computers">{view.executors.map(executor => (
          <Computer key={executor.id} executor={executor} busy={busy} onApprove={approve} onRevoke={revoke} />
        ))}</ul>}

      <h3 className="dr-db-h">Matching</h3>
      <p className="dr-db-rec">
        {rec.data_room_accounts} Naukri accounts · {rec.matched} match a Daybreak candidate by address
        {rec.missing_in_daybreak ? ` · ${rec.missing_in_daybreak} not in Daybreak` : ''}
        {rec.duplicates ? ` · ${rec.duplicates} duplicated` : ''}
        {rec.invalid_usernames ? ` · ${rec.invalid_usernames} without an email username` : ''}
        {rec.names_differ ? ` · ${rec.names_differ} with a different name in Daybreak` : ''}
        {rec.daybreak_only ? ` · ${rec.daybreak_only} in Daybreak only` : ''}
        {view.roster_reported_at ? ` (Daybreak's list as of ${when(view.roster_reported_at)})` : ''}
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
          <label className="dr-db-check">
            <input type="checkbox" checked={verify} onChange={e => setVerify(e.target.checked)} />
            Prove the login with one preview run
          </label>
          <button type="button" className="dr-add-btn" disabled={busy || !view.active_executor || selected.size === 0}
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
                  {row.pending && <div>{DELIVERY_LABELS[row.pending.state]} · asked {when(row.pending.requested_at)}</div>}
                  {row.delivery && (
                    <div>
                      {DELIVERY_LABELS[row.delivery.state] || row.delivery.state}
                      {row.delivery.receipt_at ? ` · ${when(row.delivery.receipt_at)}` : ''}
                      {row.delivery.verify_result === 'queued' ? ' · proof run queued' : row.delivery.verify_result === 'refused' ? ' · Daybreak did not queue a proof run' : ''}
                    </div>
                  )}
                  {row.connection?.signed_in_at && (
                    <div>
                      Signed in to Naukri {when(row.connection.signed_in_at)}
                      {row.connection.password_used ? ' with this password' : ' (saved session)'}
                    </div>
                  )}
                </td>
                <td className="dr-db-actions">
                  {row.attention_codes.includes('differs') && (
                    <button type="button" className="dr-copy-btn" disabled={busy} onClick={() => replace(row)}>Replace</button>
                  )}
                  {row.can_add_to_daybreak && view.active_executor && (
                    <button type="button" className="dr-copy-btn" disabled={busy} onClick={() => addToDaybreak(row)}>Add to Daybreak</button>
                  )}
                  {deliverable(row) && view.active_executor && (
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
