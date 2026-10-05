import React, { useMemo, useState } from 'react'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { useDialogA11y } from '../hooks/useDialogA11y.js'

const API_BASE =
  typeof window !== 'undefined' && window.location.port === '3000'
    ? ''
    : typeof window !== 'undefined'
      ? `${window.location.protocol}//${window.location.host}`
      : ''

// What a source said about a candidate or the operation, with where it said
// it. These records are notes: they never change a candidate, slot or payment,
// so anything that disagrees with production is marked for review here rather
// than "fixed" somewhere else.
export const INTERVIEW_CATEGORIES = {
  interview: 'Interview',
  payment: 'Payment',
  profile: 'Profile',
  process: 'Process',
  compliance: 'Compliance',
}

export const REVIEW_STATUSES = {
  recorded: 'Recorded',
  needs_review: 'Needs review',
  conflict: 'Conflict',
}

const NEEDS_ATTENTION = new Set(['needs_review', 'conflict'])

const FIELDS = [
  { key: 'candidate', label: 'Candidate (as in source)' },
  { key: 'production_candidate', label: 'Production candidate (if matched)' },
  { key: 'category', label: 'Category', options: INTERVIEW_CATEGORIES },
  { key: 'event_date', label: 'Event date', type: 'date' },
  { key: 'summary', label: 'Summary', full: true },
  { key: 'details', label: 'Details', type: 'textarea', rows: 3, full: true },
  { key: 'review_status', label: 'Review status', options: REVIEW_STATUSES },
  { key: 'review_reason', label: 'Why it needs review' },
  { key: 'source_file', label: 'Source file' },
  { key: 'source_timestamp', label: 'Source date / time' },
  { key: 'source_ref', label: 'Source reference', full: true },
]

const EMPTY = Object.fromEntries(FIELDS.map(f => [f.key, '']))

function newId(form) {
  const base = `${form.candidate || 'note'}_${form.event_date || ''}_${form.summary || ''}`
    .toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40)
  return `${base || 'note'}_${Date.now().toString(36)}`
}

function RecordModal({ title, form, onChange, onSave, onClose, error }) {
  // Mounted only while open, so the dialog is open for its whole life.
  const dialogRef = useDialogA11y(true, onClose)
  return (
    <div className="dr-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="dr-modal cand-card" ref={dialogRef} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <h2 className="cand-title">{title}</h2>
        {error && <p className="dr-error">{error}</p>}
        <div className="dr-form-grid">
          {FIELDS.map((f) => (
            <label key={f.key} className={f.full ? 'dr-form-full' : ''}>
              {f.label}
              {f.options ? (
                <select className="cand-select" value={form[f.key] || ''} onChange={(e) => onChange({ ...form, [f.key]: e.target.value })}>
                  <option value="">—</option>
                  {Object.entries(f.options).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              ) : f.type === 'textarea' ? (
                <textarea className="cand-input" rows={f.rows || 3} value={form[f.key] || ''} onChange={(e) => onChange({ ...form, [f.key]: e.target.value })} />
              ) : (
                <input className="cand-input" type={f.type || 'text'} value={form[f.key] || ''} onChange={(e) => onChange({ ...form, [f.key]: e.target.value })} />
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

export function DataRoomInterviewDataTab({ records = [], onReload }) {
  const { confirm } = useConfirm()
  const [modal, setModal] = useState(null)
  const [modalError, setModalError] = useState('')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')

  const flagged = records.filter(r => NEEDS_ATTENTION.has(r.review_status)).length
  const candidates = new Set(records.map(r => (r.production_candidate || r.candidate || '').trim().toLowerCase()).filter(Boolean)).size

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase()
    return [...records]
      .filter(r => !statusFilter || (statusFilter === 'attention' ? NEEDS_ATTENTION.has(r.review_status) : r.review_status === statusFilter))
      .filter(r => !categoryFilter || r.category === categoryFilter)
      .filter(r => !q || [r.candidate, r.production_candidate, r.summary, r.details, r.review_reason, r.source_file]
        .some(v => String(v || '').toLowerCase().includes(q)))
      .sort((a, b) => String(b.event_date || '').localeCompare(String(a.event_date || '')))
  }, [records, search, statusFilter, categoryFilter])

  const openAdd = () => {
    setModalError('')
    setModal({ mode: 'create', form: { ...EMPTY, category: 'interview', review_status: 'recorded' } })
  }

  const openEdit = (row) => {
    setModalError('')
    setModal({ mode: 'edit', id: row.id, form: Object.fromEntries(FIELDS.map(f => [f.key, row[f.key] || ''])) })
  }

  const handleDelete = async (row) => {
    const ok = await confirm({ title: 'Delete record?', message: `Remove "${row.summary || row.id}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return
    await fetch(`${API_BASE}/data-room/credentials/vault/interview_data/${row.id}`, { method: 'DELETE', credentials: 'include' })
    onReload()
  }

  const handleSave = async () => {
    const { mode, form, id } = modal
    if (!String(form.summary || '').trim()) { setModalError('A summary is required.'); return }
    const body = { ...form }
    if (mode === 'create') body.id = newId(form)
    const url = mode === 'create'
      ? `${API_BASE}/data-room/credentials/vault/interview_data`
      : `${API_BASE}/data-room/credentials/vault/interview_data/${id}`
    const res = await fetch(url, { method: mode === 'create' ? 'POST' : 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const data = await res.json()
    if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }
    setModal(null)
    onReload()
  }

  return (
    <section className="dr-section dr-section--active dr-interview-tab">
      <div className="dr-tab-header">
        <div>
          <h2 className="dr-section-title">Interview Data</h2>
          <p className="dr-section-desc">
            Candidate and interview history taken from chats and documents, each with its source.
            Notes only: nothing here changes candidates, slots or payments.
          </p>
        </div>
        <button type="button" className="cand-btn cand-btn--primary" onClick={openAdd}>+ Add record</button>
      </div>

      <div className="dr-tab-stats">
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header"><span className="dr-tab-stat-title">Records</span></div>
          <div className="dr-tab-stat-value">{records.length}</div>
          <div className="dr-tab-stat-sub">Each with its source file and time</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header"><span className="dr-tab-stat-title">Needs review</span></div>
          <div className={`dr-tab-stat-value${flagged ? ' dr-tab-stat-value--yellow' : ''}`}>{flagged}</div>
          <div className="dr-tab-stat-sub">Conflicts and uncertain items</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header"><span className="dr-tab-stat-title">Candidates</span></div>
          <div className="dr-tab-stat-value dr-tab-stat-value--purple">{candidates}</div>
          <div className="dr-tab-stat-sub">Distinct names</div>
        </div>
      </div>

      <div className="dr-interview-filters">
        <input type="search" className="cand-input" placeholder="Search candidate, note, source…" aria-label="Search interview data" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select className="cand-select" aria-label="Filter by review status" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="attention">Needs review + conflicts</option>
          {Object.entries(REVIEW_STATUSES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <select className="cand-select" aria-label="Filter by category" value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
          <option value="">All categories</option>
          {Object.entries(INTERVIEW_CATEGORIES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </div>

      <div className="dr-tab-table-wrap">
        <table className="dr-tab-table dr-interview-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Candidate</th>
              <th>Category</th>
              <th>Record</th>
              <th>Status</th>
              <th>Source</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && (
              <tr><td colSpan={7} className="dr-empty">{records.length ? 'No records match these filters.' : 'No interview data yet.'}</td></tr>
            )}
            {shown.map(row => (
              <tr key={row.id} className={NEEDS_ATTENTION.has(row.review_status) ? 'dr-interview-row--flagged' : ''}>
                <td data-label="Date">{row.event_date || '—'}</td>
                <td data-label="Candidate">
                  <strong>{row.candidate || '—'}</strong>
                  {row.production_candidate && row.production_candidate !== row.candidate && (
                    <div className="dr-muted">Production: {row.production_candidate}</div>
                  )}
                </td>
                <td data-label="Category">{INTERVIEW_CATEGORIES[row.category] || row.category || '—'}</td>
                <td data-label="Record" className="dr-interview-record">
                  <div>{row.summary || '—'}</div>
                  {row.details && <div className="dr-muted">{row.details}</div>}
                </td>
                <td data-label="Status">
                  <span className={`dr-interview-status dr-interview-status--${row.review_status || 'recorded'}`}>
                    {REVIEW_STATUSES[row.review_status] || 'Recorded'}
                  </span>
                  {row.review_reason && <div className="dr-muted dr-interview-reason">{row.review_reason}</div>}
                </td>
                <td data-label="Source" className="dr-interview-source">
                  <div>{row.source_file || '—'}</div>
                  {row.source_timestamp && <div className="dr-muted">{row.source_timestamp}</div>}
                  {row.source_ref && <div className="dr-muted">{row.source_ref}</div>}
                </td>
                <td data-label="Actions">
                  <div className="dr-acct-actions">
                    <button type="button" className="cand-btn cand-btn--sm" onClick={() => openEdit(row)}>Edit</button>
                    <button type="button" className="cand-btn cand-btn--sm cand-btn--danger" onClick={() => handleDelete(row)}>Delete</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal && (
        <RecordModal
          title={modal.mode === 'create' ? 'Add interview record' : 'Edit interview record'}
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
