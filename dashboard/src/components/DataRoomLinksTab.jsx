import React, { useEffect, useRef, useState } from 'react'
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
  return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || `item_${Date.now()}`
}

const LINK_FIELDS = [
  { key: 'id', label: 'ID (stable slug)', placeholder: 'e.g. drive_folder' },
  { key: 'title', label: 'Title / label' },
  { key: 'url', label: 'URL', placeholder: 'https://' },
  { key: 'notes', label: 'Notes', full: true, type: 'textarea', rows: 4 },
]

/**
 * What a link's notes say about the link itself.
 *
 * The WhatsApp import of 5 Oct appended its provenance to every link's notes --
 * "Source: THRILOK Support WhatsApp archive.", the dataset sentence it also keeps
 * as `source_ref`, and on one link an "Also in WhatsApp group ..." line -- so
 * nine descriptions read as the same paragraph. This drops those sentences for
 * the table only: the stored notes are what Edit shows and what Save sends back.
 */
export function keyLinkDescription(row) {
  let text = String(row?.notes || '').trim()
  if (!text) return ''
  const sourceRef = String(row?.source_ref || '').trim().replace(/\.+$/, '')
  if (sourceRef) text = text.split(sourceRef).join(' ')
  const sourceFile = String(row?.source_file || '').trim()
  const isProvenance = (sentence) => {
    const bare = sentence.trim().replace(/[.\s]+$/, '')
    return !bare
      || /^source\s*:/i.test(bare)
      || (sourceFile && bare === sourceFile)
      || /^also in whatsapp group\b/i.test(bare)
      || /_chat\.txt/i.test(bare)
      || /\bas supplied by you on\b/i.test(bare)
  }
  return text
    .replace(/([.!?])\s+/g, '$1\u0000')
    .split('\u0000')
    .filter((sentence) => !isProvenance(sentence))
    .join(' ')
    .trim()
}

/** The URL as shown in the table: no scheme, no `www.`; the link and the copy keep it whole. */
export function keyLinkDisplayUrl(url) {
  return String(url || '').trim().replace(/^https?:\/\//i, '').replace(/^www\./i, '').replace(/\/$/, '')
}

function VaultModal({ title, fields, form, onChange, onSave, onClose, error }) {
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
          <button type="button" className="cand-btn" onClick={onClose}>Cancel</button>
          <button type="button" className="cand-btn cand-btn--primary" onClick={onSave}>Save</button>
        </div>
      </div>
    </div>
  )
}

export function DataRoomLinksTab({ resources = [], onReload }) {
  const { confirm } = useConfirm()
  const [modal, setModal] = useState(null)
  const [modalError, setModalError] = useState('')
  const [copied, setCopied] = useState(null)
  const [copyStatus, setCopyStatus] = useState('')
  const copiedTimer = useRef(0)
  useEffect(() => () => window.clearTimeout(copiedTimer.current), [])

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
    await fetch(`${API_BASE}/data-room/credentials/vault/resources/${row.id}`, { method: 'DELETE', credentials: 'include' })
    onReload()
  }

  const handleCopy = async (row) => {
    const name = row.title || row.id
    const ok = await copyToClipboard(row.url)
    window.clearTimeout(copiedTimer.current)
    setCopied(ok ? row.id : null)
    setCopyStatus(ok ? `Copied the URL of ${name}` : `Could not copy the URL of ${name}`)
    if (ok) copiedTimer.current = window.setTimeout(() => setCopied((id) => (id === row.id ? null : id)), 1600)
  }

  const handleSave = async () => {
    const { mode, form, id } = modal
    const body = { ...form }
    if (mode === 'create' && !body.id.trim()) body.id = slugId(body.title || body.url)
    const url = mode === 'create'
      ? `${API_BASE}/data-room/credentials/vault/resources`
      : `${API_BASE}/data-room/credentials/vault/resources/${id}`
    const method = mode === 'create' ? 'POST' : 'PATCH'
    const res = await fetch(url, { method, credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const data = await res.json()
    if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }
    setModal(null)
    onReload()
  }

  return (
    <section className="dr-section dr-section--active dr-links-tab">
      <div className="dr-tab-header">
        <div>
          <h2 className="dr-section-title">Key Links</h2>
          <p className="dr-section-desc">Important URLs, Drive folders, tools, and reference links.</p>
        </div>
        <button type="button" className="cand-btn cand-btn--primary" onClick={openAdd}>+ Add link</button>
      </div>

      <div className="dr-tab-stats">
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Total links</span>
          </div>
          <div className="dr-tab-stat-value">{resources.length}</div>
          <div className="dr-tab-stat-sub">Important URLs, Drive folders, and tools</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Drive links</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--green">{resources.filter(r => (r.url || '').includes('drive.google')).length}</div>
          <div className="dr-tab-stat-sub">Google Drive resources</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">GitHub links</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--purple">{resources.filter(r => (r.url || '').includes('github')).length}</div>
          <div className="dr-tab-stat-sub">Repositories and code links</div>
        </div>
      </div>

      <div className="dr-tab-table-wrap">
        <table className="dr-tab-table dr-kl-table">
          <thead>
            <tr>
              <th className="dr-kl-th--title">Title</th>
              <th className="dr-kl-th--url">URL</th>
              <th className="dr-kl-th--desc">Description</th>
              <th className="dr-kl-th--actions">Actions</th>
            </tr>
          </thead>
          <tbody>
            {resources.length === 0 && (
              <tr className="dr-kl-empty-row"><td colSpan={4} className="dr-empty">No links yet.</td></tr>
            )}
            {resources.map(row => {
              const name = row.title || row.id
              const description = keyLinkDescription(row)
              const isCopied = copied === row.id
              return (
                <tr key={row.id} className="dr-kl-row">
                  <td className="dr-kl-title-cell" data-label="Title">
                    <span className="dr-kl-title" title={name}>{name}</span>
                  </td>
                  <td className="dr-kl-url-cell" data-label="URL">
                    {row.url ? (
                      <div className="dr-kl-url-wrap">
                        <a href={row.url} target="_blank" rel="noopener noreferrer" className="dr-link-url dr-kl-url" title={row.url}>
                          {keyLinkDisplayUrl(row.url)}
                        </a>
                        <button
                          type="button"
                          className={`dr-kl-icon-btn dr-kl-copy${isCopied ? ' dr-kl-copy--copied' : ''}`}
                          onClick={() => handleCopy(row)}
                          aria-label={isCopied ? `Copied the URL of ${name}` : `Copy the URL of ${name}`}
                          title={isCopied ? 'Copied' : 'Copy URL'}
                        >
                          <Icon name={isCopied ? 'check' : 'copy'} size={14} />
                        </button>
                      </div>
                    ) : <span className="dr-muted">—</span>}
                  </td>
                  <td className={`dr-kl-desc-cell${description ? '' : ' dr-kl-desc-cell--empty'}`} data-label="Description">
                    {description
                      ? <span className="dr-kl-desc" title={description}>{description}</span>
                      : <span className="dr-muted">—</span>}
                  </td>
                  <td className="dr-kl-actions-cell" data-label="Actions">
                    <div className="dr-kl-actions">
                      {row.url ? (
                        <a
                          href={row.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="dr-kl-icon-btn"
                          aria-label={`Open ${name} in a new tab`}
                          title="Open in a new tab"
                        >
                          <Icon name="external-link" size={15} />
                        </a>
                      ) : (
                        <button type="button" className="dr-kl-icon-btn" disabled aria-label={`Open ${name}: no URL`} title="No URL to open">
                          <Icon name="external-link" size={15} />
                        </button>
                      )}
                      <button type="button" className="dr-kl-icon-btn" onClick={() => openEdit(row)} aria-label={`Edit ${name}`} title="Edit link">
                        <Icon name="pencil" size={15} />
                      </button>
                      <button type="button" className="dr-kl-icon-btn dr-kl-icon-btn--danger" onClick={() => handleDelete(row)} aria-label={`Delete ${name}`} title="Delete link">
                        <Icon name="trash" size={15} />
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="sr-only" role="status" aria-live="polite">{copyStatus}</p>

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
    </section>
  )
}
