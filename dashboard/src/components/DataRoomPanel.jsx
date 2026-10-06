import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { useDialogA11y } from '../hooks/useDialogA11y.js'
import { useAuth } from '../context/AuthContext.jsx'
import { formatIstDateTime } from '../utils/istTime.js'
import { DataRoomAccountsTab } from './DataRoomAccountsTab.jsx'
import { DataRoomPromptsTab } from './DataRoomPromptsTab.jsx'
import { DataRoomLinksTab } from './DataRoomLinksTab.jsx'
import { DataRoomOffersTab } from './DataRoomOffersTab.jsx'
import { OPERATIONS_PUBLIC_URL } from '../config.js'

const API_BASE =
  typeof window !== 'undefined' && window.location.port === '3000'
    ? ''
    : typeof window !== 'undefined'
      ? `${window.location.protocol}//${window.location.host}`
      : ''

const TYPE_LABELS = {
  support_provider: 'Support provider',
  vendor_candidates: 'Vendor / candidates',
  partnership: 'Partnership',
  recruitment: 'Recruitment / dev',
  referral: 'Referral',
  other: 'Other',
}

const STATUS_LABELS = {
  new: 'New',
  reviewing: 'Reviewing',
  aligned: 'Aligned',
  on_hold: 'On hold',
  closed: 'Closed',
  not_relevant: 'Not relevant',
}

const EMPTY_FORM = {
  opportunity_type: 'partnership',
  status: 'new',
  name: '',
  phone: '',
  whatsapp: '',
  email: '',
  preferred_contact: 'whatsapp',
  username: '',
  account_id: '',
  tech_stack: '',
  volume_hint: '',
  summary: '',
  notes: '',
  inbox_ref: '',
}

function waHref(digits) {
  const d = String(digits || '').replace(/\D/g, '')
  if (d.length < 10) return null
  const n = d.length > 10 && d.startsWith('91') ? d : d.length === 10 ? `91${d}` : d
  return `https://wa.me/${n.replace(/\D/g, '')}`
}

function ContactReach({ row }) {
  const wa = row.whatsapp || (row.phone ? row.phone.replace(/\D/g, '').slice(-10) : '')
  const waUrl = waHref(wa)
  const lines = []
  if (row.phone) {
    lines.push(
      <div key="phone" className="dr-reach-line">
        <span className="dr-reach-label">Phone</span>
        <a href={`tel:${String(row.phone).replace(/\s/g, '')}`}>{row.phone}</a>
      </div>,
    )
  }
  if (waUrl) {
    lines.push(
      <div key="wa" className="dr-reach-line">
        <span className="dr-reach-label">WhatsApp</span>
        <a href={waUrl} target="_blank" rel="noopener noreferrer">{row.whatsapp || row.phone}</a>
      </div>,
    )
  }
  if (row.email) {
    lines.push(
      <div key="email" className="dr-reach-line">
        <span className="dr-reach-label">Email</span>
        <a href={`mailto:${row.email}`}>{row.email}</a>
      </div>,
    )
  }
  if (lines.length) return <div className="dr-contact">{lines}</div>
  return (
    <span className="dr-needs-contact" title="Capture WhatsApp, phone, or email — not Telegram">
      Add contact details
    </span>
  )
}

function fmtWhen(iso) {
  return formatIstDateTime(iso)
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const el = document.createElement('textarea')
      el.value = text
      el.style.position = 'fixed'
      el.style.opacity = '0'
      document.body.appendChild(el)
      el.select()
      document.execCommand('copy')
      document.body.removeChild(el)
      return true
    } catch {
      return false
    }
  }
}

export function formatCredentialBlock(site, row, { isAdmin = false } = {}) {
  const lines = [
    `Site: ${site}`,
    `Username: ${row.username}`,
  ]
  // Handler passwords are hashed and the server no longer returns them, so
  // there is nothing here to copy and nothing to read back. Printing the line
  // anyway produced "Password: undefined", which reads as a broken row rather
  // than as a password that deliberately cannot be recovered.
  if (row.password) {
    lines.push(`Password: ${row.password}`)
  } else {
    lines.push('Password: not recoverable — set a new one to share it')
  }
  if (isAdmin) {
    lines.push('Role: admin (full dashboard)')
  } else {
    lines.push('Role: handler')
    if (row.reference) lines.push(`Reference: ${row.reference}`)
  }
  return lines.join('\n')
}

function CopyChip({ label, text, copyKey, activeKey, onCopy }) {
  const copied = activeKey === copyKey
  return (
    <button
      type="button"
      className={`dr-copy-btn${copied ? ' dr-copy-btn--copied' : ''}`}
      title={`Copy ${label}`}
      onClick={() => onCopy(copyKey, text)}
    >
      {copied ? 'Copied' : label}
    </button>
  )
}

function CredentialRow({ rowKey, site, row, isAdmin, activeKey, onCopy, onEdit, onDelete }) {
  const block = formatCredentialBlock(site, row, { isAdmin })
  return (
    <tr>
      <td><span className={`dr-creds-role${isAdmin ? ' dr-creds-role--admin' : ''}`}>{isAdmin ? 'Admin' : 'Handler'}</span></td>
      <td>{row.reference || (isAdmin ? 'Full dashboard' : '—')}</td>
      <td>
        <code>{row.username}</code>
        <CopyChip label="User" text={row.username} copyKey={`${rowKey}-user`} activeKey={activeKey} onCopy={onCopy} />
      </td>
      <td>
        <code className="dr-creds-pass">{row.password}</code>
        <CopyChip label="Pass" text={row.password} copyKey={`${rowKey}-pass`} activeKey={activeKey} onCopy={onCopy} />
      </td>
      <td className="dr-creds-copy-all">
        <CopyChip label="Copy all" text={block} copyKey={`${rowKey}-all`} activeKey={activeKey} onCopy={onCopy} />
      </td>
      <td className="dr-actions">
        {onEdit ? (
          <button type="button" className="cand-btn cand-btn--sm" onClick={onEdit}>Edit</button>
        ) : (
          <span className="dr-muted" title="Use Change password from the account menu">Account menu</span>
        )}
        {!isAdmin && (
          <button type="button" className="cand-btn cand-btn--sm cand-btn--danger" onClick={onDelete}>Delete</button>
        )}
      </td>
    </tr>
  )
}

const EMPTY_HANDLER_FORM = { username: '', password: '', reference: '', notes: '' }

function HandlerModal({ mode, form, onChange, onSave, onClose, error }) {
  // Mounted only while open, so the dialog is open for its whole life.
  const dialogRef = useDialogA11y(true, onClose)
  return (
    <div className="dr-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="dr-modal cand-card" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="dr-handler-modal-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="dr-handler-modal-title" className="cand-title">
          {mode === 'create' ? 'Add handler login' : 'Edit handler login'}
        </h2>
        {error && <p className="dr-error">{error}</p>}
        <div className="dr-form-grid">
          <label>
            Username
            <input className="cand-input" value={form.username} readOnly={mode === 'edit'}
              onChange={(e) => onChange({ ...form, username: e.target.value })} />
          </label>
          <label>
            Reference (display name)
            <input className="cand-input" value={form.reference}
              onChange={(e) => onChange({ ...form, reference: e.target.value })} />
          </label>
          <label>
            Password{mode === 'edit' ? ' (leave blank to keep)' : ''}
            <input className="cand-input" type="password" autoComplete="new-password" value={form.password}
              onChange={(e) => onChange({ ...form, password: e.target.value })} />
          </label>
          <label className="dr-form-full">
            Notes (optional)
            <input className="cand-input" value={form.notes}
              onChange={(e) => onChange({ ...form, notes: e.target.value })} />
          </label>
        </div>
        <div className="dr-modal-actions">
          <button type="button" className="cand-btn" onClick={onClose}>Cancel</button>
          <button type="button" className="cand-btn cand-btn--primary" onClick={onSave}>Save</button>
        </div>
      </div>
    </div>
  )
}

function CredentialsSection({ creds, loading, active, onReload }) {
  const { confirm } = useConfirm()
  const [activeKey, setActiveKey] = useState(null)
  const [handlerModal, setHandlerModal] = useState(null) // { mode:'create'|'edit', form, username? }
  const [modalError, setModalError] = useState('')

  const onCopy = useCallback(async (key, text) => {
    const ok = await copyToClipboard(text)
    setActiveKey(ok ? key : null)
    if (ok) {
      window.setTimeout(() => setActiveKey((k) => (k === key ? null : k)), 1600)
    }
  }, [])

  const openAddHandler = () => {
    setModalError('')
    setHandlerModal({ mode: 'create', form: { ...EMPTY_HANDLER_FORM } })
  }

  const openEditHandler = (h) => {
    setModalError('')
    setHandlerModal({ mode: 'edit', username: h.username, form: { username: h.username, password: '', reference: h.reference || '', notes: h.notes || '' } })
  }

  const saveHandler = async () => {
    const { mode, form, username } = handlerModal
    const url = mode === 'create'
      ? `${API_BASE}/data-room/credentials/handlers`
      : `${API_BASE}/data-room/credentials/handlers/${username}`
    const method = mode === 'create' ? 'POST' : 'PATCH'
    const body = { ...form }
    if (mode === 'edit' && !body.password) delete body.password
    const res = await fetch(url, { method, credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const data = await res.json()
    if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }
    setHandlerModal(null)
    onReload()
  }

  const deleteHandler = async (h) => {
    const ok = await confirm({ title: 'Remove handler?', message: `Delete handler "${h.reference || h.username}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return
    await fetch(`${API_BASE}/data-room/credentials/handlers/${h.username}`, { method: 'DELETE', credentials: 'include' })
    onReload()
  }

  if (loading) return <p className="dr-muted">Loading credentials…</p>
  if (!creds) return null
  const site = creds.site_url || OPERATIONS_PUBLIC_URL
  const admin = creds.admin
  const handlers = creds.handlers || []
  return (
    <section
      className={`dr-section dr-credentials-section${active ? ' dr-section--active' : ''}`}
      aria-labelledby="dr-creds-title"
    >
      <div className="dr-section-head dr-section-head--row">
        <div>
          <h2 id="dr-creds-title" className="dr-section-title">Dashboard logins</h2>
          <p className="dr-section-desc">
            Admin and handler credentials for the configured Operations site. Use Copy on each row.
          </p>
        </div>
        <button type="button" className="cand-btn cand-btn--primary" onClick={openAddHandler}>
          + Add handler
        </button>
      </div>
      <p className="dr-muted dr-creds-site">
        Site: {site ? <a href={site} target="_blank" rel="noopener noreferrer">{site}</a> : 'not configured'}
      </p>
      <div className="cand-table-wrap">
        <table className="cand-table dr-table dr-creds-table">
          <thead>
            <tr>
              <th>Role</th>
              <th>Name / reference</th>
              <th>Username</th>
              <th>Password</th>
              <th>Copy</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {admin && (
              <CredentialRow
                rowKey="admin"
                site={site}
                row={admin}
                isAdmin
                activeKey={activeKey}
                onCopy={onCopy}
                onEdit={null}
                onDelete={() => {}}
              />
            )}
            {handlers.map((h) => (
              <CredentialRow
                key={h.username}
                rowKey={h.username}
                site={site}
                row={h}
                activeKey={activeKey}
                onCopy={onCopy}
                onEdit={() => openEditHandler(h)}
                onDelete={() => deleteHandler(h)}
              />
            ))}
          </tbody>
        </table>
      </div>
      {creds.vps_host && (
        <p className="dr-muted dr-creds-vps">
          VPS SSH: <code>root@{creds.vps_host}</code>
          <CopyChip
            label="Copy host"
            text={`root@${creds.vps_host}`}
            copyKey="vps-host"
            activeKey={activeKey}
            onCopy={onCopy}
          />
        </p>
      )}

      {handlerModal && (
        <HandlerModal
          mode={handlerModal.mode}
          form={handlerModal.form}
          onChange={(f) => setHandlerModal((s) => ({ ...s, form: f }))}
          onSave={saveHandler}
          onClose={() => setHandlerModal(null)}
          error={modalError}
        />
      )}
    </section>
  )
}

const DATA_ROOM_TABS = [
  { id: 'logins', label: 'Logins', adminOnly: false },
  { id: 'partners', label: 'Opportunities', adminOnly: false },
  { id: 'accounts', label: 'Accounts', adminOnly: false },
  { id: 'prompts', label: 'Prompts', adminOnly: false },
  { id: 'links', label: 'Key Links', adminOnly: false },
  { id: 'offers', label: 'Offers', adminOnly: false },
]

export function DataRoomPanel() {
  const { confirm } = useConfirm()
  const { role } = useAuth()
  const isAdmin = role !== 'handler'
  const [rows, setRows] = useState([])
  const [stats, setStats] = useState(null)
  const [credentials, setCredentials] = useState(null)
  const [credsLoading, setCredsLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [search, setSearch] = useState('')
  const [editor, setEditor] = useState(null)
  const closeEditor = useCallback(() => setEditor(null), [])
  const editorDialogRef = useDialogA11y(Boolean(editor), closeEditor)
  const [activeTab, setActiveTab] = useState('accounts')

  const visibleTabs = useMemo(
    () => DATA_ROOM_TABS.filter(tab => isAdmin || !tab.adminOnly),
    [isAdmin],
  )

  const tabCounts = useMemo(() => {
    const accounts = credentials?.service_accounts || []
    const prompts = credentials?.prompts || []
    const resources = credentials?.resources || []
    const offers = credentials?.offer_letters || []
    return {
      logins: (credentials?.handlers?.length || 0) + (credentials?.admin ? 1 : 0),
      partners: stats?.total ?? rows.length,
      accounts: accounts.length,
      prompts: prompts.length,
      links: resources.length,
      offers: offers.length,
    }
  }, [credentials, stats, rows.length])

  useEffect(() => {
    if (!isAdmin) {
      setActiveTab('partners')
      return
    }
    if (!visibleTabs.some(tab => tab.id === activeTab)) {
      setActiveTab(visibleTabs[0]?.id || 'partners')
    }
  }, [isAdmin, activeTab, visibleTabs])

  const loadCredentials = useCallback(async () => {
    if (!isAdmin) {
      setCredentials(null)
      return
    }
    setCredsLoading(true)
    try {
      const res = await fetch(`${API_BASE}/data-room/credentials`, { credentials: 'include' })
      const data = await res.json()
      if (res.status === 403) {
        setCredentials(null)
        return
      }
      if (data.status === 'ok') {
        setCredentials(data.credentials || null)
      }
    } catch {
      setCredentials(null)
    } finally {
      setCredsLoading(false)
    }
  }, [isAdmin])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const q = new URLSearchParams()
      if (statusFilter) q.set('status', statusFilter)
      if (typeFilter) q.set('opportunity_type', typeFilter)
      if (search.trim()) q.set('query', search.trim())
      const suffix = q.toString() ? `?${q}` : ''
      const [partnersRes] = await Promise.all([
        fetch(`${API_BASE}/data-room${suffix}`, { credentials: 'include' }),
        loadCredentials(),
      ])
      const data = await partnersRes.json()
      if (data.status !== 'ok') {
        throw new Error(data.message || 'Failed to load data room')
      }
      setRows(data.opportunities || [])
      setStats(data.stats || null)
    } catch (e) {
      setError(e.message || String(e))
    } finally {
      setLoading(false)
    }
  }, [statusFilter, typeFilter, search, loadCredentials])

  useEffect(() => {
    load()
  }, [load])

  const openNew = () => setEditor({ mode: 'create', form: { ...EMPTY_FORM } })

  const openEdit = (row) => {
    setEditor({
      mode: 'edit',
      id: row.id,
      form: {
        opportunity_type: row.opportunity_type || 'other',
        status: row.status || 'new',
        name: row.name || '',
        phone: row.phone || '',
        whatsapp: row.whatsapp || '',
        email: row.email || '',
        preferred_contact: row.preferred_contact || 'whatsapp',
        username: row.username || '',
        account_id: row.account_id || '',
        tech_stack: row.tech_stack || '',
        volume_hint: row.volume_hint || '',
        summary: row.summary || '',
        notes: row.notes || '',
        inbox_ref: row.inbox_ref || row.linked_crm_key || '',
      },
    })
  }

  const saveEditor = async () => {
    if (!editor) return
    const body = { ...editor.form }
    const url =
      editor.mode === 'create'
        ? `${API_BASE}/data-room`
        : `${API_BASE}/data-room/${editor.id}`
    const method = editor.mode === 'create' ? 'POST' : 'PATCH'
    const res = await fetch(url, {
      method,
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json()
    if (data.status !== 'ok') {
      setError(data.message || 'Save failed')
      return
    }
    setEditor(null)
    load()
  }

  const removeRow = async (row) => {
    const ok = await confirm({
      title: 'Remove opportunity?',
      message: `Delete "${row.name || row.summary || row.id}" from the data room?`,
      confirmLabel: 'Delete',
      variant: 'danger',
    })
    if (!ok) return
    await fetch(`${API_BASE}/data-room/${row.id}`, {
      method: 'DELETE',
      credentials: 'include',
    })
    load()
  }

  const statCards = useMemo(() => {
    const total = stats?.total ?? rows.length
    const byStatus = stats?.by_status || {}
    const needsContact = stats?.needs_contact ?? rows.filter((r) => !r.contact_complete).length
    return [
      { label: 'Total leads', value: total },
      { label: 'New', value: byStatus.new || 0 },
      { label: 'Needs contact', value: needsContact },
      { label: 'Aligned', value: byStatus.aligned || 0 },
    ]
  }, [stats, rows.length])

  return (
    <div className="cand-page dr-page">
      <header className="cand-header">
        <div className="cand-header-titles">
          <h1 className="cand-title">Data room</h1>
          <p className="cand-subtitle">
            Three tabs: <strong>Logins</strong> (admin), <strong>Vault</strong> (accounts &amp; links), and{' '}
            <strong>Opportunities</strong> (partner leads).
          </p>
        </div>
        <div className="cand-header-actions">
          {activeTab === 'partners' && (
            <button type="button" className="cand-btn cand-btn--primary" onClick={openNew}>
              Add opportunity
            </button>
          )}
          <button type="button" className="cand-btn" onClick={load} disabled={loading}>
            Refresh
          </button>
        </div>
      </header>

      <nav className="dr-tabs" aria-label="Data room sections">
        {visibleTabs.map(tab => (
          <button
            key={tab.id}
            type="button"
            className={`dr-tab${activeTab === tab.id ? ' dr-tab--active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
            <span className="dr-tab-count">{tabCounts[tab.id] ?? 0}</span>
          </button>
        ))}
      </nav>

      {error && <p className="dr-error dr-page-error" role="alert">{error}</p>}

      {activeTab === 'logins' && (
        <CredentialsSection creds={credentials} loading={credsLoading} active onReload={loadCredentials} />
      )}

      {activeTab === 'accounts' && credentials && (
        <DataRoomAccountsTab accounts={credentials.service_accounts || []} onReload={loadCredentials} />
      )}

      {activeTab === 'prompts' && credentials && (
        <DataRoomPromptsTab prompts={credentials.prompts || []} onReload={loadCredentials} />
      )}

      {activeTab === 'links' && credentials && (
        <DataRoomLinksTab resources={credentials.resources || []} onReload={loadCredentials} />
      )}

      {activeTab === 'offers' && credentials && (
        <DataRoomOffersTab offers={credentials.offer_letters || []} onReload={loadCredentials} />
      )}

      {activeTab === 'partners' && (
      <section className="dr-section dr-partners-section dr-section--active" aria-labelledby="dr-partners-title">
        <div className="dr-section-head">
          <h2 id="dr-partners-title" className="dr-section-title">Operations opportunities</h2>
          <p className="dr-section-desc">
            Partner leads with phone, WhatsApp, or email — not Telegram IDs. Karthik auto-captures vendor and support-provider threads.
          </p>
        </div>

      <div className="cand-stats">
        {statCards.map((c) => (
          <div key={c.label} className="cand-stat-card">
            <div className="cand-stat-label">{c.label}</div>
            <div className="cand-stat-value">{c.value}</div>
          </div>
        ))}
      </div>

      <div className="dr-filters cand-toolbar">
        <input
          type="search"
          className="cand-input"
          placeholder="Search name, tech, notes…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          className="cand-select"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {Object.entries(STATUS_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <select
          className="cand-select"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          aria-label="Filter by type"
        >
          <option value="">All types</option>
          {Object.entries(TYPE_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
      </div>

      {loading && activeTab === 'partners' && <p className="dr-muted">Loading…</p>}

      <div className="cand-table-wrap">
        <table className="cand-table dr-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Name</th>
              <th>Reach (phone / WhatsApp / email)</th>
              <th>Tech & offer</th>
              <th>Status</th>
              <th>Last contact</th>
              <th>Summary</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {!loading && rows.length === 0 && (
              <tr>
                <td colSpan={8} className="dr-empty">
                  No opportunities yet. Partner DMs are captured automatically when Karthik detects vendor or support-provider threads.
                </td>
              </tr>
            )}
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <span className={`dr-badge dr-badge--${row.opportunity_type}`}>
                    {TYPE_LABELS[row.opportunity_type] || row.opportunity_type}
                  </span>
                </td>
                <td>
                  <strong>{row.name || '—'}</strong>
                  {row.username && <div className="dr-muted">@{row.username}</div>}
                </td>
                <td>
                  <ContactReach row={row} />
                </td>
                <td>
                  <div>{row.tech_stack || '—'}</div>
                  {row.volume_hint && (
                    <div className="dr-muted dr-route">{row.volume_hint}</div>
                  )}
                </td>
                <td>
                  <span className={`dr-status dr-status--${row.status}`}>
                    {STATUS_LABELS[row.status] || row.status}
                  </span>
                </td>
                <td>{fmtWhen(row.last_contact_at)}</td>
                <td
                  className="dr-summary"
                  title={row.opportunity_type === 'dashboard_login' ? (row.notes || '') : (row.source_snippet || '')}
                >
                  {row.summary || row.source_snippet?.slice(0, 80) || '—'}
                </td>
                <td className="dr-actions">
                  <button type="button" className="cand-btn cand-btn--sm" onClick={() => openEdit(row)}>
                    Edit
                  </button>
                  <button type="button" className="cand-btn cand-btn--sm cand-btn--danger" onClick={() => removeRow(row)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </section>
      )}

      {editor && (
        <div className="dr-modal-backdrop" role="presentation" onClick={() => setEditor(null)}>
          <div
            className="dr-modal cand-card"
            ref={editorDialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="dr-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="dr-modal-title" className="cand-title">
              {editor.mode === 'create' ? 'Add opportunity' : 'Edit opportunity'}
            </h2>
            <div className="dr-form-grid">
              <label>
                Type
                <select
                  className="cand-select"
                  value={editor.form.opportunity_type}
                  onChange={(e) => setEditor((s) => ({ ...s, form: { ...s.form, opportunity_type: e.target.value } }))}
                >
                  {Object.entries(TYPE_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </select>
              </label>
              <label>
                Status
                <select
                  className="cand-select"
                  value={editor.form.status}
                  onChange={(e) => setEditor((s) => ({ ...s, form: { ...s.form, status: e.target.value } }))}
                >
                  {Object.entries(STATUS_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </select>
              </label>
              <label>
                Name
                <input
                  className="cand-input"
                  value={editor.form.name}
                  onChange={(e) => setEditor((s) => ({ ...s, form: { ...s.form, name: e.target.value } }))}
                />
              </label>
              <label>
                Phone (primary contact)
                <input
                  className="cand-input"
                  type="tel"
                  placeholder="+91 98765 43210"
                  value={editor.form.phone}
                  onChange={(e) => setEditor((s) => ({ ...s, form: { ...s.form, phone: e.target.value } }))}
                />
              </label>
              <label>
                WhatsApp (10-digit or +91)
                <input
                  className="cand-input"
                  type="tel"
                  placeholder="9876543210"
                  value={editor.form.whatsapp}
                  onChange={(e) => setEditor((s) => ({ ...s, form: { ...s.form, whatsapp: e.target.value } }))}
                />
              </label>
              <label>
                Email
                <input
                  className="cand-input"
                  type="email"
                  placeholder="partner@example.com"
                  value={editor.form.email}
                  onChange={(e) => setEditor((s) => ({ ...s, form: { ...s.form, email: e.target.value } }))}
                />
              </label>
              <label>
                Preferred contact
                <select
                  className="cand-select"
                  value={editor.form.preferred_contact}
                  onChange={(e) => setEditor((s) => ({ ...s, form: { ...s.form, preferred_contact: e.target.value } }))}
                >
                  <option value="whatsapp">WhatsApp</option>
                  <option value="phone">Phone call</option>
                  <option value="email">Email</option>
                </select>
              </label>
              <label>
                Telegram @username (optional)
                <input
                  className="cand-input"
                  value={editor.form.username}
                  onChange={(e) => setEditor((s) => ({ ...s, form: { ...s.form, username: e.target.value } }))}
                />
              </label>
              <label>
                Tech stack
                <input
                  className="cand-input"
                  value={editor.form.tech_stack}
                  onChange={(e) => setEditor((s) => ({ ...s, form: { ...s.form, tech_stack: e.target.value } }))}
                />
              </label>
              <label>
                Volume hint
                <input
                  className="cand-input"
                  value={editor.form.volume_hint}
                  onChange={(e) => setEditor((s) => ({ ...s, form: { ...s.form, volume_hint: e.target.value } }))}
                />
              </label>
              <label className="dr-form-full">
                Summary
                <textarea
                  className="cand-input"
                  rows={2}
                  value={editor.form.summary}
                  onChange={(e) => setEditor((s) => ({ ...s, form: { ...s.form, summary: e.target.value } }))}
                />
              </label>
              <label className="dr-form-full dr-form-internal">
                Inbox reference (internal only — not for contacting partner)
                <input
                  className="cand-input"
                  value={editor.form.inbox_ref}
                  readOnly
                  title="Links to CRM inbox thread; add phone/WhatsApp/email above for real contact"
                />
              </label>
              <label className="dr-form-full">
                Notes
                <textarea
                  className="cand-input"
                  rows={4}
                  value={editor.form.notes}
                  onChange={(e) => setEditor((s) => ({ ...s, form: { ...s.form, notes: e.target.value } }))}
                />
              </label>
            </div>
            <div className="dr-modal-actions">
              <button type="button" className="cand-btn" onClick={() => setEditor(null)}>Cancel</button>
              <button type="button" className="cand-btn cand-btn--primary" onClick={saveEditor}>Save</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
