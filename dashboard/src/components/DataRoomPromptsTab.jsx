import React, { useCallback, useState } from 'react'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { useDialogA11y } from '../hooks/useDialogA11y.js'
import { copyToClipboard } from '../utils/copyToClipboard.js'
import { useRowEdit, ROW_EDIT_CLASS } from '../utils/rowEdit.js'

const API_BASE =
  typeof window !== 'undefined' && window.location.port === '3000'
    ? ''
    : typeof window !== 'undefined'
      ? `${window.location.protocol}//${window.location.host}`
      : ''

function slugId(text) {
  return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || `item_${Date.now()}`
}

const PROMPT_FIELDS = [
  { key: 'id', label: 'ID (stable slug)', placeholder: 'e.g. intro_message' },
  { key: 'title', label: 'Title' },
  { key: 'source', label: 'Source / context' },
  { key: 'body', label: 'Prompt body', type: 'textarea', rows: 8, full: true },
]

function VaultModal({ onDelete = null, title, fields, form, onChange, onSave, onClose, error }) {
  // Mounted only while open, so the dialog is open for its whole life.
  const dialogRef = useDialogA11y(true, onClose)
  return (
    <div className="dr-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="dr-modal cand-card" ref={dialogRef} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <h2 className="cand-title">{title}</h2>
        {error && <p className="dr-error">{error}</p>}
        <div className="dr-form-grid">
          {fields.map((f) => (
            <label key={f.key} className={f.full ? 'dr-form-full' : ''}>
              {f.label}
              {f.type === 'textarea' ? (
                <textarea className="cand-input" rows={f.rows || 3} value={form[f.key] || ''} onChange={(e) => onChange({ ...form, [f.key]: e.target.value })} />
              ) : (
                <input className="cand-input" type={f.type || 'text'} placeholder={f.placeholder || ''} value={form[f.key] || ''} readOnly={f.readOnly} onChange={(e) => onChange({ ...form, [f.key]: e.target.value })} />
              )}
            </label>
          ))}
        </div>
        <div className="dr-modal-actions">
          {onDelete && <button type="button" className="cand-btn cand-btn--ghost row-modal__delete" onClick={onDelete}>Delete</button>}
          <button type="button" className="cand-btn" onClick={onClose}>Cancel</button>
          <button type="button" className="cand-btn cand-btn--primary" onClick={onSave}>Save</button>
        </div>
      </div>
    </div>
  )
}

export function DataRoomPromptsTab({ prompts = [], onReload }) {
  const { confirm } = useConfirm()
  const [activeKey, setActiveKey] = useState(null)
  const [modal, setModal] = useState(null)
  const [modalError, setModalError] = useState('')
  const [expanded, setExpanded] = useState(null)

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
    setModal({ mode: 'edit', id: row.id, row, form: { id: row.id, title: row.title || '', source: row.source || '', body: row.body || '' } })
  }

  const rowProps = useRowEdit(openEdit)

  const handleDelete = async (row) => {
    const ok = await confirm({ title: 'Delete prompt?', message: `Remove "${row.title || row.id}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return false
    await fetch(`${API_BASE}/data-room/credentials/vault/prompts/${row.id}`, { method: 'DELETE', credentials: 'include' })
    onReload()
    return true
  }

  const handleSave = async () => {
    const { mode, form, id } = modal
    const body = { ...form }
    if (mode === 'create' && !body.id.trim()) body.id = slugId(body.title)
    const url = mode === 'create'
      ? `${API_BASE}/data-room/credentials/vault/prompts`
      : `${API_BASE}/data-room/credentials/vault/prompts/${id}`
    const method = mode === 'create' ? 'POST' : 'PATCH'
    const res = await fetch(url, { method, credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const data = await res.json()
    if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }
    setModal(null)
    onReload()
  }

  return (
    <section className="dr-section dr-section--active dr-prompts-tab">
      <div className="dr-tab-header">
        <div>
          <h2 className="dr-section-title">Prompts</h2>
          <p className="dr-section-desc">AI prompts, interview scripts, and reusable text blocks.</p>
        </div>
        <button type="button" className="cand-btn cand-btn--primary" onClick={openAdd}>+ Add prompt</button>
      </div>

      <div className="dr-tab-stats">
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Total prompts</span>
          </div>
          <div className="dr-tab-stat-value">{prompts.length}</div>
          <div className="dr-tab-stat-sub">AI prompts, scripts, and templates</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Total characters</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--green">{prompts.reduce((s, p) => s + (p.body || '').length, 0).toLocaleString()}</div>
          <div className="dr-tab-stat-sub">Combined prompt body length</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Avg length</span>
          </div>
          <div className="dr-tab-stat-value">{prompts.length ? Math.round(prompts.reduce((s, p) => s + (p.body || '').length, 0) / prompts.length).toLocaleString() : 0}</div>
          <div className="dr-tab-stat-sub">Average chars per prompt</div>
        </div>
      </div>

      <div className="dr-tab-table-wrap">
        <table className="dr-tab-table">
          <thead>
            <tr>
              <th>Title</th>
              <th>Source</th>
              <th>Length</th>
              <th>Preview</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {prompts.length === 0 && (
              <tr><td colSpan={5} className="dr-empty">No prompts yet.</td></tr>
            )}
            {prompts.map(row => (
              <tr key={row.id} className={`${ROW_EDIT_CLASS}${expanded === row.id ? ' dr-row--expanded' : ''}`} {...rowProps(row, row.title || row.id)}>
                <td><strong>{row.title || row.id}</strong></td>
                <td className="dr-muted">{row.source || '—'}</td>
                <td>{(row.body || '').length} chars</td>
                <td className="dr-prompt-preview">{(row.body || '').slice(0, 60)}{(row.body || '').length > 60 ? '…' : ''}</td>
                <td>
                  <div className="dr-acct-actions">
                    <button
                      type="button"
                      className={`dr-copy-btn${activeKey === `prompt-${row.id}` ? ' dr-copy-btn--copied' : ''}`}
                      onClick={() => onCopy(`prompt-${row.id}`, row.body || '')}
                    >
                      {activeKey === `prompt-${row.id}` ? '✓ Copied' : 'Copy'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal && (
        <VaultModal
          title={modal.mode === 'create' ? 'Add prompt' : 'Edit prompt'}
          fields={PROMPT_FIELDS.map(f => modal.mode === 'edit' && f.key === 'id' ? { ...f, readOnly: true } : f)}
          form={modal.form}
          onChange={(f) => setModal(s => ({ ...s, form: f }))}
          onSave={handleSave}
          onClose={() => setModal(null)}
          onDelete={modal.mode === 'edit' ? async () => { if (await handleDelete(modal.row)) setModal(null) } : null}
          error={modalError}
        />
      )}
    </section>
  )
}
