import React, { useCallback, useState } from 'react'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { useDialogA11y } from '../hooks/useDialogA11y.js'
import { copyToClipboard } from '../utils/copyToClipboard.js'

const API_BASE =
  typeof window !== 'undefined' && window.location.port === '3000'
    ? ''
    : typeof window !== 'undefined'
      ? `${window.location.protocol}//${window.location.host}`
      : ''

// Served by /data-room as `offer_folder_url`; never a literal here.
const DEFAULT_OFFER_FOLDER = ''

// ── Shared helpers ────────────────────────────────────────────────────────────

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

function serviceCardTone(row) {
  const label = String(row.label || '').toLowerCase()
  if (label.includes('deprecated') || label.includes('legacy')) return 'deprecated'
  if (label.includes('current') || label.includes('2026')) return 'current'
  return 'default'
}

function slugId(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40) || `item_${Date.now()}`
}

// ── Generic vault item modal ──────────────────────────────────────────────────

function VaultModal({ title, fields, form, onChange, onSave, onClose, error }) {
  // Mounted only while open, so the dialog is open for its whole life.
  const dialogRef = useDialogA11y(true, onClose)
  return (
    <div className="dr-modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="dr-modal cand-card"
        ref={dialogRef} role="dialog" aria-modal="true"
        aria-labelledby="dr-vault-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="dr-vault-modal-title" className="cand-title">{title}</h2>
        {error && <p className="dr-error">{error}</p>}
        <div className="dr-form-grid">
          {fields.map((f) => (
            <label key={f.key} className={f.full ? 'dr-form-full' : ''}>
              {f.label}
              {f.type === 'textarea' ? (
                <textarea
                  className="cand-input"
                  rows={f.rows || 3}
                  value={form[f.key] || ''}
                  onChange={(e) => onChange({ ...form, [f.key]: e.target.value })}
                />
              ) : (
                <input
                  className="cand-input"
                  type={f.type || 'text'}
                  placeholder={f.placeholder || ''}
                  value={form[f.key] || ''}
                  readOnly={f.readOnly}
                  onChange={(e) => onChange({ ...form, [f.key]: e.target.value })}
                />
              )}
            </label>
          ))}
        </div>
        <div className="dr-modal-actions">
          <button type="button" className="cand-btn" onClick={onClose}>Cancel</button>
          <button type="button" className="cand-btn cand-btn--primary" onClick={onSave}>Save</button>
        </div>
      </div>
    </div>
  )
}

// ── Vault API helpers ─────────────────────────────────────────────────────────

async function vaultCreate(section, body) {
  const res = await fetch(`${API_BASE}/data-room/credentials/vault/${section}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return res.json()
}

async function vaultUpdate(section, id, body) {
  const res = await fetch(`${API_BASE}/data-room/credentials/vault/${section}/${id}`, {
    method: 'PATCH',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return res.json()
}

async function vaultDelete(section, id) {
  const res = await fetch(`${API_BASE}/data-room/credentials/vault/${section}/${id}`, {
    method: 'DELETE',
    credentials: 'include',
  })
  return res.json()
}

// ── Service Accounts block (horizontal scroll row) ────────────────────────────

const SVC_FIELDS = [
  { key: 'id', label: 'ID (stable slug)', placeholder: 'e.g. gmail_karthik_2026' },
  { key: 'label', label: 'Label / title' },
  { key: 'service', label: 'Service (e.g. Gmail)' },
  { key: 'username', label: 'Username / email' },
  { key: 'password', label: 'Password', type: 'password' },
  { key: 'notes', label: 'Notes', full: true },
]

function ServiceAccountsBlock({ accounts, onReload }) {
  const { confirm } = useConfirm()
  const [activeKey, setActiveKey] = useState(null)
  const [modal, setModal] = useState(null)
  const [modalError, setModalError] = useState('')

  const onCopy = useCallback(async (key, text) => {
    const ok = await copyToClipboard(text)
    setActiveKey(ok ? key : null)
    if (ok) window.setTimeout(() => setActiveKey(k => (k === key ? null : k)), 1600)
  }, [])

  const openAdd = () => {
    setModalError('')
    setModal({ mode: 'create', form: { id: '', label: '', service: '', username: '', password: '', notes: '' } })
  }

  const openEdit = (row) => {
    setModalError('')
    setModal({ mode: 'edit', id: row.id, form: { id: row.id, label: row.label || '', service: row.service || '', username: row.username || '', password: row.password || '', notes: row.notes || '' } })
  }

  const handleDelete = async (row) => {
    const ok = await confirm({ title: 'Delete account?', message: `Remove "${row.label || row.id}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return
    await vaultDelete('service_accounts', row.id)
    onReload()
  }

  const handleSave = async () => {
    const { mode, form, id } = modal
    const body = { ...form }
    if (mode === 'create' && !body.id.trim()) body.id = slugId(body.label || body.username)
    const data = mode === 'create'
      ? await vaultCreate('service_accounts', body)
      : await vaultUpdate('service_accounts', id, body)
    if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }
    setModal(null)
    onReload()
  }

  return (
    <div className="dr-vault-svc-row">
      <div className="dr-vault-block-head">
        <h3 className="dr-vault-subtitle">Service accounts</h3>
        <button type="button" className="cand-btn cand-btn--sm cand-btn--primary" onClick={openAdd}>+ Add</button>
      </div>
      {accounts.length === 0 ? (
        <p className="dr-muted">No service accounts yet.</p>
      ) : (
        <div className="dr-svc-scroll-row">
          {accounts.map(row => {
            const tone = serviceCardTone(row)
            const copyAll = [row.label || row.id, row.service ? `Service: ${row.service}` : '', row.username ? `Username: ${row.username}` : '', row.password ? `Password: ${row.password}` : '', row.notes || ''].filter(Boolean).join('\n')
            return (
              <article className={`dr-svc-card dr-svc-card--${tone}`} key={row.id}>
                <div className="dr-svc-card-head">
                  <div>
                    <h4 className="dr-svc-card-title">{row.label || row.id}</h4>
                    {row.service && <div className="dr-svc-service">{row.service}</div>}
                  </div>
                  <div className="dr-vault-item-actions">
                    {tone === 'current' && <span className="dr-svc-badge dr-svc-badge--current">Current</span>}
                    {tone === 'deprecated' && <span className="dr-svc-badge dr-svc-badge--deprecated">Old</span>}
                  </div>
                </div>
                <div className="dr-svc-card-body">
                  {row.username && (
                    <div className="dr-svc-field">
                      <span className="dr-svc-field-label">Username</span>
                      <div className="dr-svc-field-value">
                        <code>{row.username}</code>
                        <CopyChip label="Copy" text={row.username} copyKey={`${row.id}-user`} activeKey={activeKey} onCopy={onCopy} />
                      </div>
                    </div>
                  )}
                  {row.password && (
                    <div className="dr-svc-field">
                      <span className="dr-svc-field-label">Password</span>
                      <div className="dr-svc-field-value">
                        <code className="dr-creds-pass">{row.password}</code>
                        <CopyChip label="Copy" text={row.password} copyKey={`${row.id}-pass`} activeKey={activeKey} onCopy={onCopy} />
                      </div>
                    </div>
                  )}
                  {row.notes && <p className="dr-svc-notes">{row.notes}</p>}
                </div>
                <div className="dr-svc-card-footer">
                  <button type="button" className="cand-btn cand-btn--sm" onClick={() => openEdit(row)}>Edit</button>
                  <button type="button" className="cand-btn cand-btn--sm cand-btn--danger" onClick={() => handleDelete(row)}>Delete</button>
                  <CopyChip label="Copy all" text={copyAll} copyKey={`${row.id}-all`} activeKey={activeKey} onCopy={onCopy} />
                </div>
              </article>
            )
          })}
        </div>
      )}
      {modal && (
        <VaultModal
          title={modal.mode === 'create' ? 'Add service account' : 'Edit service account'}
          fields={SVC_FIELDS.map(f => modal.mode === 'edit' && f.key === 'id' ? { ...f, readOnly: true } : f)}
          form={modal.form}
          onChange={(f) => setModal(s => ({ ...s, form: f }))}
          onSave={handleSave}
          onClose={() => setModal(null)}
          error={modalError}
        />
      )}
    </div>
  )
}

// ── Prompts block (compact list) ──────────────────────────────────────────────

const PROMPT_FIELDS = [
  { key: 'id', label: 'ID (stable slug)', placeholder: 'e.g. intro_message' },
  { key: 'title', label: 'Title' },
  { key: 'source', label: 'Source / context' },
  { key: 'body', label: 'Prompt body', type: 'textarea', rows: 8, full: true },
]

function PromptsBlock({ prompts, onReload }) {
  const { confirm } = useConfirm()
  const [activeKey, setActiveKey] = useState(null)
  const [modal, setModal] = useState(null)
  const [modalError, setModalError] = useState('')

  const onCopy = useCallback(async (key, text) => {
    const ok = await copyToClipboard(text)
    setActiveKey(ok ? key : null)
    if (ok) window.setTimeout(() => setActiveKey(k => (k === key ? null : k)), 1600)
  }, [])

  const openAdd = () => {
    setModalError('')
    setModal({ mode: 'create', form: { id: '', title: '', source: '', body: '' } })
  }

  const openEdit = (row) => {
    setModalError('')
    setModal({ mode: 'edit', id: row.id, form: { id: row.id, title: row.title || '', source: row.source || '', body: row.body || '' } })
  }

  const handleDelete = async (row) => {
    const ok = await confirm({ title: 'Delete prompt?', message: `Remove "${row.title || row.id}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return
    await vaultDelete('prompts', row.id)
    onReload()
  }

  const handleSave = async () => {
    const { mode, form, id } = modal
    const body = { ...form }
    if (mode === 'create' && !body.id.trim()) body.id = slugId(body.title)
    const data = mode === 'create'
      ? await vaultCreate('prompts', body)
      : await vaultUpdate('prompts', id, body)
    if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }
    setModal(null)
    onReload()
  }

  const visiblePrompts = prompts.slice(0, 4)

  return (
    <div className="dr-vault-block dr-vault-col">
      <div className="dr-vault-block-head">
        <h3 className="dr-vault-subtitle">Prompts <span className="dr-vault-count">({prompts.length})</span></h3>
        <button type="button" className="cand-btn cand-btn--sm cand-btn--primary" onClick={openAdd}>+ Add</button>
      </div>
      {prompts.length === 0 ? (
        <p className="dr-muted">No prompts yet.</p>
      ) : (
        <div className="dr-prompt-compact-list">
          {visiblePrompts.map(row => (
            <div className="dr-prompt-compact-row" key={row.id}>
              <div className="dr-prompt-compact-info">
                <span className="dr-prompt-compact-title">{row.title || row.id}</span>
                <span className="dr-prompt-compact-meta">{(row.body || '').length} chars</span>
              </div>
              <div className="dr-prompt-compact-actions">
                <button type="button" className="cand-btn cand-btn--sm" onClick={() => openEdit(row)}>Edit</button>
                <button type="button" className="cand-btn cand-btn--sm cand-btn--danger" onClick={() => handleDelete(row)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}
      {prompts.length > 4 && (
        <a className="dr-vault-view-all" href="#prompts-all">View all prompts →</a>
      )}
      {modal && (
        <VaultModal
          title={modal.mode === 'create' ? 'Add prompt' : 'Edit prompt'}
          fields={PROMPT_FIELDS.map(f => modal.mode === 'edit' && f.key === 'id' ? { ...f, readOnly: true } : f)}
          form={modal.form}
          onChange={(f) => setModal(s => ({ ...s, form: f }))}
          onSave={handleSave}
          onClose={() => setModal(null)}
          error={modalError}
        />
      )}
    </div>
  )
}

// ── Key Links (resources) block — 2-col mini cards ────────────────────────────

const LINK_FIELDS = [
  { key: 'id', label: 'ID (stable slug)', placeholder: 'e.g. drive_folder' },
  { key: 'title', label: 'Title / label' },
  { key: 'url', label: 'URL', placeholder: 'https://' },
  { key: 'notes', label: 'Notes', full: true },
]

function ResourcesBlock({ resources, onReload }) {
  const { confirm } = useConfirm()
  const [modal, setModal] = useState(null)
  const [modalError, setModalError] = useState('')

  const openAdd = () => {
    setModalError('')
    setModal({ mode: 'create', form: { id: '', title: '', url: '', notes: '' } })
  }

  const openEdit = (row) => {
    setModalError('')
    setModal({ mode: 'edit', id: row.id, form: { id: row.id, title: row.title || '', url: row.url || '', notes: row.notes || '' } })
  }

  const handleDelete = async (row) => {
    const ok = await confirm({ title: 'Delete link?', message: `Remove "${row.title || row.url}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return
    await vaultDelete('resources', row.id)
    onReload()
  }

  const handleSave = async () => {
    const { mode, form, id } = modal
    const body = { ...form }
    if (mode === 'create' && !body.id.trim()) body.id = slugId(body.title || body.url)
    const data = mode === 'create'
      ? await vaultCreate('resources', body)
      : await vaultUpdate('resources', id, body)
    if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }
    setModal(null)
    onReload()
  }

  return (
    <div className="dr-vault-block dr-vault-col">
      <div className="dr-vault-block-head">
        <h3 className="dr-vault-subtitle">Key links <span className="dr-vault-count">({resources.length})</span></h3>
        <button type="button" className="cand-btn cand-btn--sm cand-btn--primary" onClick={openAdd}>+ Add</button>
      </div>
      {resources.length === 0 ? (
        <p className="dr-muted">No links yet.</p>
      ) : (
        <ul className="dr-resource-mini-grid">
          {resources.map(row => (
            <li className="dr-resource-mini-card" key={row.id}>
              <a className="dr-resource-mini-title" href={row.url} target="_blank" rel="noopener noreferrer" title={row.title || row.url}>
                {row.title || row.url}
              </a>
              {row.notes && <p className="dr-resource-mini-desc">{row.notes}</p>}
            </li>
          ))}
        </ul>
      )}
      {resources.length > 0 && (
        <a className="dr-vault-view-all" href="#links-all">View all key links →</a>
      )}
      {modal && (
        <VaultModal
          title={modal.mode === 'create' ? 'Add link' : 'Edit link'}
          fields={LINK_FIELDS.map(f => modal.mode === 'edit' && f.key === 'id' ? { ...f, readOnly: true } : f)}
          form={modal.form}
          onChange={(f) => setModal(s => ({ ...s, form: f }))}
          onSave={handleSave}
          onClose={() => setModal(null)}
          error={modalError}
        />
      )}
    </div>
  )
}

// ── Offer Letters block (table, center column) ────────────────────────────────

const OFFER_FIELDS = [
  { key: 'id', label: 'ID (stable slug)', placeholder: 'e.g. luxoft_2024_01' },
  { key: 'filename', label: 'Filename' },
  { key: 'candidate', label: 'Candidate name' },
  { key: 'company_name', label: 'Company name' },
  { key: 'date_modified', label: 'Date modified', placeholder: 'YYYY-MM-DD' },
  { key: 'size_kb', label: 'Size (KB)' },
  { key: 'drive_file_id', label: 'Google Drive file ID' },
  { key: 'notes', label: 'Notes', full: true },
]

function OfferLettersBlock({ offers, onReload }) {
  const { confirm } = useConfirm()
  const [modal, setModal] = useState(null)
  const [modalError, setModalError] = useState('')
  const [uploadId, setUploadId] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')

  const openAdd = () => {
    setModalError('')
    setModal({ mode: 'create', form: { id: '', filename: '', candidate: '', company_name: '', date_modified: '', size_kb: '', drive_file_id: '', notes: '' } })
  }

  const openEdit = (row) => {
    setModalError('')
    setModal({ mode: 'edit', id: row.id, form: { id: row.id, filename: row.filename || '', candidate: row.candidate || '', company_name: row.company_name || '', date_modified: row.date_modified || '', size_kb: String(row.size_kb || ''), drive_file_id: row.drive_file_id || '', notes: row.notes || '' } })
  }

  const handleDelete = async (row) => {
    const ok = await confirm({ title: 'Delete offer letter?', message: `Remove "${row.filename || row.id}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return
    await vaultDelete('offer_letters', row.id)
    onReload()
  }

  const handleSave = async () => {
    const { mode, form, id } = modal
    const body = { ...form }
    if (body.size_kb) body.size_kb = Number(body.size_kb) || body.size_kb
    if (mode === 'create' && !body.id.trim()) body.id = slugId(body.filename || body.candidate)
    const data = mode === 'create'
      ? await vaultCreate('offer_letters', body)
      : await vaultUpdate('offer_letters', id, body)
    if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }
    setModal(null)
    onReload()
  }

  const handleUpload = async (rowId, file) => {
    if (!file) return
    setUploadId(rowId)
    setUploading(true)
    setUploadError('')
    const fd = new FormData()
    fd.append('file', file)
    try {
      const res = await fetch(`${API_BASE}/data-room/offer-letters/${rowId}/upload`, { method: 'POST', credentials: 'include', body: fd })
      const data = await res.json()
      if (data.status !== 'ok') setUploadError(data.message || 'Upload failed')
      else onReload()
    } catch (e) {
      setUploadError(String(e))
    } finally {
      setUploading(false)
      setUploadId(null)
    }
  }

  const visibleOffers = offers.slice(0, 5)

  return (
    <div className="dr-vault-block dr-vault-col dr-vault-col--wide">
      <div className="dr-vault-block-head">
        <h3 className="dr-vault-subtitle">Offer letters <span className="dr-vault-count">({offers.length})</span></h3>
        <button type="button" className="cand-btn cand-btn--sm cand-btn--primary" onClick={openAdd}>+ Add</button>
      </div>
      {uploadError && <p className="dr-error">{uploadError}</p>}
      {offers.length === 0 ? (
        <p className="dr-muted">No offer letters catalogued yet.</p>
      ) : (
        <div className="dr-offer-compact-table-wrap">
          <table className="dr-offer-compact-table">
            <thead>
              <tr>
                <th>File</th>
                <th>Candidate</th>
                <th>Modified</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {visibleOffers.map(row => (
                <tr key={row.id}>
                  <td><span className="dr-offer-filename">{row.filename || row.id}</span></td>
                  <td>{row.candidate || '—'}</td>
                  <td>{row.date_modified || '—'}</td>
                  <td className="dr-offer-actions-cell">
                    <a href={`${API_BASE}/data-room/offer-letters/${row.id}/preview`} target="_blank" rel="noopener noreferrer" className="dr-offer-icon-btn" title="View">👁</a>
                    <a href={`${API_BASE}/data-room/offer-letters/${row.id}/download`} download className="dr-offer-icon-btn" title="Download">⬇</a>
                    <button type="button" className="dr-offer-icon-btn" title="More" onClick={() => openEdit(row)}>⋯</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {offers.length > 5 && (
        <a className="dr-vault-view-all" href="#offers-all">View all offer letters →</a>
      )}
      {modal && (
        <VaultModal
          title={modal.mode === 'create' ? 'Add offer letter' : 'Edit offer letter'}
          fields={OFFER_FIELDS.map(f => modal.mode === 'edit' && f.key === 'id' ? { ...f, readOnly: true } : f)}
          form={modal.form}
          onChange={(f) => setModal(s => ({ ...s, form: f }))}
          onSave={handleSave}
          onClose={() => setModal(null)}
          error={modalError}
        />
      )}
    </div>
  )
}

// ── Main export ───────────────────────────────────────────────────────────────

export function DataRoomVaultSection({ creds, active = true, onReload }) {
  if (!creds) return null

  const accounts = creds.service_accounts || []
  const prompts = creds.prompts || []
  const resources = creds.resources || []
  const offers = creds.offer_letters || []

  return (
    <section
      className={`dr-section dr-vault-section${active ? ' dr-section--active' : ''}`}
      aria-labelledby="dr-vault-title"
    >
      {/* Header row: title + stats chips */}
      <div className="dr-vault-header-row">
        <div className="dr-section-head">
          <h2 id="dr-vault-title" className="dr-section-title">Operations vault</h2>
          <p className="dr-section-desc">
            Gmail accounts, AI prompts, offer-letter catalog, and key links.
          </p>
        </div>
        <div className="dr-vault-stats">
          <div className="dr-vault-stat"><span className="dr-vault-stat-icon">👤</span><div><div className="dr-vault-stat-label">Accounts</div><div className="dr-vault-stat-value">{accounts.length}</div></div></div>
          <div className="dr-vault-stat"><span className="dr-vault-stat-icon">📝</span><div><div className="dr-vault-stat-label">Prompts</div><div className="dr-vault-stat-value">{prompts.length}</div></div></div>
          <div className="dr-vault-stat"><span className="dr-vault-stat-icon">🔗</span><div><div className="dr-vault-stat-label">Key Links</div><div className="dr-vault-stat-value">{resources.length}</div></div></div>
          <div className="dr-vault-stat"><span className="dr-vault-stat-icon">📄</span><div><div className="dr-vault-stat-label">Offers</div><div className="dr-vault-stat-value">{offers.length}</div></div></div>
        </div>
      </div>

      {/* Service Accounts — horizontal scroll row */}
      <ServiceAccountsBlock accounts={accounts} onReload={onReload} />

      {/* 3-column grid: Prompts | Offer Letters | Key Links */}
      <div className="dr-vault-grid">
        <PromptsBlock prompts={prompts} onReload={onReload} />
        <OfferLettersBlock offers={offers} onReload={onReload} />
        <ResourcesBlock resources={resources} onReload={onReload} />
      </div>
    </section>
  )
}
