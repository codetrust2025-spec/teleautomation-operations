import React, { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { useDialogA11y } from '../hooks/useDialogA11y.js'
import { copyToClipboard } from '../utils/copyToClipboard.js'
import { Icon } from './ui/Icon.jsx'

const API_BASE =
  typeof window !== 'undefined' && window.location.port === '3000'
    ? ''
    : typeof window !== 'undefined'
      ? `${window.location.protocol}//${window.location.host}`
      : ''

function slugId(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40) || `item_${Date.now()}`
}

function CopyChip({ label, text, copyKey, activeKey, onCopy }) {
  const copied = activeKey === copyKey
  return (
    <button
      type="button"
      className={`dr-copy-btn dr-copy-btn--icon${copied ? ' dr-copy-btn--copied' : ''}`}
      title={copied ? `Copied ${label}` : `Copy ${label}`}
      aria-label={copied ? `Copied ${label}` : `Copy ${label}`}
      onClick={() => onCopy(copyKey, text)}
    >
      <Icon name={copied ? 'check' : 'copy'} size={13} />
    </button>
  )
}

function accountStatus(row) {
  const label = String(row.label || '').toLowerCase()
  if (label.includes('deprecated') || label.includes('legacy')) return 'old'
  if (label.includes('current') || label.includes('2026')) return 'active'
  return 'active'
}

function isPrimary(row) {
  const label = String(row.label || '').toLowerCase()
  return label.includes('current') || label.includes('2026')
}

// What an operator actually has to type.
//
// `id` and `service` were asked for on this form and neither needed to be. The
// id is a stable slug the backend requires but nobody chooses: `slugId` already
// derived one from the title whenever the box was left blank, so the field only
// ever offered a way to get it wrong. `service` was free text rendered with a
// "Gmail" fallback in the table, which is what almost every row said anyway.
//
// The id is still generated and still sent; it is just no longer asked for.
// Sniffed again in the backend from the bytes; this is only the picker filter.
const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp']
const MAX_IMAGE_BYTES = 5 * 1024 * 1024

const SVC_FIELDS = [
  { key: 'label', label: 'Account name', placeholder: 'e.g. Karthik Gmail (current)', full: true },
  { key: 'username', label: 'Username / email', placeholder: 'name@example.com' },
  { key: 'password', label: 'Password', type: 'password' },
  { key: 'notes', label: 'Notes (optional)', type: 'textarea', rows: 3, full: true },
]

function VaultModal({ title, fields, form, onChange, onSave, onClose, error, imagePanel }) {
  // Mounted only while open, so the dialog is open for its whole life.
  const dialogRef = useDialogA11y(true, onClose)
  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose])

  return createPortal(
    <div className="dr-modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="dr-modal cand-card"
        ref={dialogRef} role="dialog"
        aria-modal="true"
        aria-labelledby="dr-vault-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="dr-vault-modal-title" className="cand-title">{title}</h2>
        {error && <p className="dr-error">{error}</p>}
        {imagePanel}
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
    </div>,
    document.body,
  )
}

export function DataRoomAccountsTab({ accounts = [], onReload }) {
  const { confirm } = useConfirm()
  const [activeKey, setActiveKey] = useState(null)
  const [modal, setModal] = useState(null)
  const [modalError, setModalError] = useState('')
  const [search, setSearch] = useState('')
  // The chosen screenshot is held here until Save. A new account has no id to
  // upload against yet, so the file waits for the row to exist; an edit uses
  // the same path so both behave identically.
  const [pendingImage, setPendingImage] = useState(null)
  const [previewUrl, setPreviewUrl] = useState('')
  const [removeImage, setRemoveImage] = useState(false)

  const clearImageState = useCallback(() => {
    setPendingImage(null)
    setRemoveImage(false)
    setPreviewUrl((url) => {
      if (url) URL.revokeObjectURL(url)
      return ''
    })
  }, [])

  const chooseImage = (file) => {
    if (!file) return
    if (!IMAGE_TYPES.includes(file.type)) {
      setModalError('Choose a PNG, JPG, JPEG or WebP image.')
      return
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setModalError('Image is too large. Maximum size is 5 MB.')
      return
    }
    setModalError('')
    setRemoveImage(false)
    setPendingImage(file)
    setPreviewUrl((url) => {
      if (url) URL.revokeObjectURL(url)
      return URL.createObjectURL(file)
    })
  }

  const onCopy = useCallback(async (key, text) => {
    const ok = await copyToClipboard(text)
    setActiveKey(ok ? key : null)
    if (ok) window.setTimeout(() => setActiveKey(k => (k === key ? null : k)), 1600)
  }, [])

  const openAdd = () => {
    setModalError('')
    clearImageState()
    setModal({ mode: 'create', form: { id: '', label: '', service: '', username: '', password: '', notes: '' } })
  }

  const openEdit = (row) => {
    setModalError('')
    clearImageState()
    setModal({ mode: 'edit', id: row.id, row, form: { id: row.id, label: row.label || '', service: row.service || '', username: row.username || '', password: row.password || '', notes: row.notes || '' } })
  }

  const handleDelete = async (row) => {
    const ok = await confirm({ title: 'Delete account?', message: `Remove "${row.label || row.id}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return
    await fetch(`${API_BASE}/data-room/credentials/vault/service_accounts/${row.id}`, { method: 'DELETE', credentials: 'include' })
    onReload()
  }

  const handleSave = async () => {
    const { mode, form, id } = modal
    const body = { ...form }
    if (mode === 'create' && !body.id.trim()) body.id = slugId(body.label || body.username)
    const url = mode === 'create'
      ? `${API_BASE}/data-room/credentials/vault/service_accounts`
      : `${API_BASE}/data-room/credentials/vault/service_accounts/${id}`
    const method = mode === 'create' ? 'POST' : 'PATCH'
    const res = await fetch(url, { method, credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const data = await res.json()
    if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }

    // The screenshot is applied after the row exists, so a new account has an
    // id to attach it to. A failure here leaves the account saved and says so
    // rather than discarding the rest of the edit.
    const targetId = mode === 'create' ? body.id : id
    if (removeImage && !pendingImage) {
      const gone = await fetch(`${API_BASE}/data-room/service-accounts/${targetId}/image`, { method: 'DELETE', credentials: 'include' })
        .then(r => r.json()).catch(() => null)
      if (!gone || gone.status !== 'ok') { setModalError('Account saved, but the image could not be removed.'); return }
    } else if (pendingImage) {
      const fd = new FormData()
      fd.append('file', pendingImage)
      const up = await fetch(`${API_BASE}/data-room/service-accounts/${targetId}/image`, { method: 'POST', credentials: 'include', body: fd })
        .then(r => r.json()).catch(() => null)
      if (!up || up.status !== 'ok') { setModalError((up && up.message) || 'Account saved, but the image upload failed.'); return }
    }

    clearImageState()
    setModal(null)
    onReload()
  }

  const totalAccounts = accounts.length
  const activeAccounts = accounts.filter(r => accountStatus(r) === 'active').length
  const oldAccounts = accounts.filter(r => accountStatus(r) === 'old').length
  const primaryAccounts = accounts.filter(r => isPrimary(r)).length

  const filtered = search.trim()
    ? accounts.filter(r => {
        const q = search.toLowerCase()
        return (r.label || '').toLowerCase().includes(q) ||
          (r.username || '').toLowerCase().includes(q) ||
          (r.service || '').toLowerCase().includes(q)
      })
    : accounts

  return (
    <section className="dr-section dr-section--active dr-accounts-tab">
      {/* Header */}
      <div className="dr-tab-header">
        <div>
          <h2 className="dr-section-title">Accounts</h2>
          <p className="dr-section-desc">Manage Gmail and service accounts used across operations.</p>
        </div>
        <div className="dr-tab-header-actions">
          <div className="dr-search-wrap">
            <span className="dr-search-icon"><Icon name="search" size={14} /></span>
            <input
              type="search"
              className="cand-input dr-search-input"
              placeholder="Search accounts..."
              aria-label="Search accounts"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <button type="button" className="cand-btn cand-btn--primary dr-add-btn" onClick={openAdd}>
            <Icon name="plus" size={14} strokeWidth={2.2} />
            Add account
          </button>
        </div>
      </div>

      {/* Stats row */}
      <div className="dr-tab-stats">
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Total accounts</span>
          </div>
          <div className="dr-tab-stat-value">{totalAccounts}</div>
          <div className="dr-tab-stat-sub">{activeAccounts} active · {oldAccounts} deprecated</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Active</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--green">{activeAccounts}</div>
          <div className="dr-tab-stat-sub">Currently in use</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Locked / Old</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--yellow">{oldAccounts}</div>
          <div className="dr-tab-stat-sub">Deprecated or locked accounts</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Primary accounts</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--purple">{primaryAccounts}</div>
          <div className="dr-tab-stat-sub">Marked as current primary</div>
        </div>
      </div>

      {/* Table */}
      <div className="dr-tab-table-wrap">
        <table className="dr-tab-table dr-acct-table">
          <thead>
            <tr>
              <th className="dr-acct-th--account">Account</th>
              <th className="dr-acct-th--type">Type</th>
              <th className="dr-acct-th--user">Username</th>
              <th className="dr-acct-th--pass">Password</th>
              <th className="dr-acct-th--status">Status</th>
              <th className="dr-acct-th--used">Last used</th>
              <th className="dr-acct-th--actions">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr><td colSpan={7} className="dr-empty">No accounts found.</td></tr>
            )}
            {filtered.map(row => {
              const status = accountStatus(row)
              const primary = isPrimary(row)
              return (
                <tr key={row.id}>
                  <td>
                    <div className="dr-acct-cell">
                      <span className={`dr-acct-dot dr-acct-dot--${status}`} />
                      <div>
                        <span className="dr-acct-name">{row.label || row.id}</span>
                        {primary && <span className="dr-acct-primary-badge">★ Primary</span>}
                      </div>
                    </div>
                  </td>
                  <td>{row.service || 'Gmail'}</td>
                  <td>
                    <div className="dr-acct-cred-cell">
                      <code title={row.username || undefined}>{row.username || '—'}</code>
                      {row.username && <CopyChip label="username" text={row.username} copyKey={`${row.id}-user`} activeKey={activeKey} onCopy={onCopy} />}
                    </div>
                  </td>
                  <td>
                    <div className="dr-acct-cred-cell">
                      <code className="dr-creds-pass">{row.password || '—'}</code>
                      {row.password && <CopyChip label="password" text={row.password} copyKey={`${row.id}-pass`} activeKey={activeKey} onCopy={onCopy} />}
                    </div>
                  </td>
                  <td>
                    <span className={`dr-acct-status dr-acct-status--${status}`}>
                      ● {status === 'active' ? 'Active' : 'Old'}
                    </span>
                  </td>
                  <td className="dr-muted">{row.last_used || '—'}</td>
                  <td>
                    <div className="dr-acct-actions">
                      <button
                        type="button"
                        className={`dr-copy-btn dr-copy-btn--all${activeKey === `${row.id}-all` ? ' dr-copy-btn--copied' : ''}`}
                        title="Copy the account name, username, password and notes"
                        onClick={() => onCopy(`${row.id}-all`, [row.label || row.id, row.service ? `Service: ${row.service}` : '', row.username ? `Username: ${row.username}` : '', row.password ? `Password: ${row.password}` : '', row.notes || ''].filter(Boolean).join('\n'))}
                      >
                        <Icon name={activeKey === `${row.id}-all` ? 'check' : 'copy'} size={13} />
                        {activeKey === `${row.id}-all` ? 'Copied' : 'Copy all'}
                      </button>
                      <button type="button" className="cand-btn cand-btn--sm" onClick={() => openEdit(row)} aria-label={`Edit ${row.label || row.id}`}>Edit</button>
                      <button type="button" className="cand-btn cand-btn--sm cand-btn--danger" onClick={() => handleDelete(row)} aria-label={`Delete ${row.label || row.id}`}>Delete</button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Tip bar */}
      <div className="dr-tip-bar">
        <span className="dr-tip-icon">ℹ</span>
        <span>Tip: Use quick actions (Copy, Copy all) to speed up access. Keep the CURRENT account as primary.</span>
      </div>

      {modal && (
        <VaultModal
          title={modal.mode === 'create' ? 'Add service account' : 'Edit service account'}
          // `id` and `service` stay on the form object so an edit preserves
          // what a row already has; they are simply not asked for.
          fields={SVC_FIELDS}
          form={modal.form}
          onChange={(f) => setModal(s => ({ ...s, form: f }))}
          onSave={handleSave}
          onClose={() => { clearImageState(); setModal(null) }}
          error={modalError}
          imagePanel={(
            <div className="dr-acct-image-panel">
              {previewUrl ? (
                <img className="dr-acct-image-preview" src={previewUrl} alt="Selected screenshot preview" />
              ) : modal.row?.has_image && !removeImage ? (
                <img
                  className="dr-acct-image-preview"
                  src={`${API_BASE}/data-room/service-accounts/${modal.id}/image`}
                  alt={`Screenshot for ${modal.row.label || modal.id}`}
                />
              ) : (
                <div className="dr-acct-image-empty" aria-hidden>No image</div>
              )}
              <div className="dr-acct-image-actions">
                <strong>Screenshot</strong>
                <span className="dr-acct-image-hint">PNG, JPG, JPEG or WebP, up to 5 MB.</span>
                <div className="dr-acct-image-buttons">
                  <label className="cand-btn cand-btn--sm">
                    {previewUrl || (modal.row?.has_image && !removeImage) ? 'Replace' : 'Choose image'}
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      style={{ display: 'none' }}
                      onChange={(event) => {
                        const file = event.target.files?.[0]
                        event.target.value = ''
                        chooseImage(file)
                      }}
                    />
                  </label>
                  {modal.row?.has_image && !removeImage && !previewUrl && (
                    <a
                      className="cand-btn cand-btn--sm"
                      href={`${API_BASE}/data-room/service-accounts/${modal.id}/image`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      View image
                    </a>
                  )}
                  {(previewUrl || (modal.row?.has_image && !removeImage)) && (
                    <button
                      type="button"
                      className="cand-btn cand-btn--sm cand-btn--danger"
                      onClick={() => { clearImageState(); setRemoveImage(true) }}
                    >
                      Remove
                    </button>
                  )}
                </div>
                {removeImage && !previewUrl && (
                  <span className="dr-acct-image-hint">Image will be removed when you save.</span>
                )}
              </div>
            </div>
          )}
        />
      )}
    </section>
  )
}
