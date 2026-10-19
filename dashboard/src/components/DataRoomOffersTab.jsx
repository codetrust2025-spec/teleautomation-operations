import React, { useState } from 'react'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { useDialogA11y } from '../hooks/useDialogA11y.js'
import { offerLetterDownloadApiUrl, offerLetterPreviewApiUrl } from '../utils/offerLetterUrls.js'
import { OfferPreviewModal, offerIsImage } from './OfferPreviewModal.jsx'
import { Icon } from './ui/Icon.jsx'

const API_BASE =
  typeof window !== 'undefined' && window.location.port === '3000'
    ? ''
    : typeof window !== 'undefined'
      ? `${window.location.protocol}//${window.location.host}`
      : ''

async function readApiResponse(response) {
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) return response.json()
  const text = await response.text()
  if (response.status === 413) {
    throw new Error('The file is too large for the server. Maximum file size is 25 MB.')
  }
  throw new Error(
    response.ok
      ? 'The server returned an invalid response. Please try again.'
      : `Upload failed (${response.status}). ${text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 120)}`,
  )
}

// What a person knows that the PDF does not say for itself.
//
// The other five boxes asked for values the upload already produces:
// `create_offer_letter_from_pdf` slugs the id from the filename, reads the
// filename, size and date from the bytes, records `uploaded_at`, and writes the
// PDF to DATA_DIR/data_room/offer_letters_cache. `drive_file_id` is set to ""
// by that path and never filled in by hand. Asking for them offered no
// information and one way to get each wrong.
//
// They are still stored. `update_vault_item` merges the keys it is given, so
// omitting them from the PATCH preserves whatever the row already holds --
// which also repairs a quieter fault: the old form sent `size_kb: ''` for any
// record without one, overwriting a real value with an empty string.
const OFFER_FIELDS = [
  { key: 'candidate', label: 'Candidate name' },
  { key: 'company_name', label: 'Company name' },
  { key: 'notes', label: 'Notes (optional)', type: 'textarea', rows: 3, full: true },
]

// Only these three are ever sent. Everything else on the row is the upload's.
const EDITABLE_KEYS = ['candidate', 'company_name', 'notes']

// An offer letter is the PDF, or a photo or scan of the letter. The server
// decides from the bytes; this only saves a wasted upload.
const OFFER_FILE_ACCEPT = 'application/pdf,.pdf,image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp'
const isOfferFile = file => ['application/pdf', 'image/png', 'image/jpeg', 'image/webp'].includes(file.type) ||
  /\.(pdf|png|jpe?g|webp)$/i.test(file.name || '')

const viewLabel = row => (offerIsImage(row) ? 'View image' : 'View PDF')
const rowName = row => row.candidate || row.filename || row.id

/** How many pages the stored file holds, as the server read it from the file. */
function pagesLabel(row) {
  const file = row.file
  if (!file) return '—'
  if (!file.stored) return 'No file'
  if (file.kind === 'image') return 'Image'
  if (file.pages === null || file.pages === undefined) return 'Unreadable'
  return `${file.pages} page${file.pages === 1 ? '' : 's'}`
}

// `suspended` while the letter's preview is open over this dialog, so the two
// do not both answer Escape or both hold the Tab key.
function VaultModal({ title, fields, form, onChange, onSave, onClose, error, uploadPanel, saving, suspended = false }) {
  // Mounted only while open, so the dialog is open for its whole life.
  const dialogRef = useDialogA11y(!suspended, onClose)
  return (
    <div className="dr-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="dr-modal cand-card" ref={dialogRef} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <h2 className="cand-title">{title}</h2>
        {error && <p className="dr-error">{error}</p>}
        {uploadPanel}
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
          <button type="button" className="cand-btn cand-btn--primary" onClick={onSave} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

export function DataRoomOffersTab({ offers = [], onReload }) {
  const { confirm } = useConfirm()
  const [modal, setModal] = useState(null)
  const [modalError, setModalError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadId, setUploadId] = useState(null)
  const [uploadError, setUploadError] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [analysisMessage, setAnalysisMessage] = useState('')
  const [preview, setPreview] = useState(null)

  const openAdd = () => {
    setModalError('')
    setAnalysisMessage('')
    setModal({ mode: 'create', form: { candidate: '', company_name: '', notes: '' } })
  }

  const openEdit = (row) => {
    setModalError('')
    // Only the editable three are loaded. The rest of the row stays on the
    // server and is preserved by the merge, so opening and saving an existing
    // record cannot blank its filename, size, date or drive id.
    setModal({ mode: 'edit', id: row.id, row, form: { candidate: row.candidate || '', company_name: row.company_name || '', notes: row.notes || '' } })
  }

  const handleDelete = async (row) => {
    const ok = await confirm({ title: 'Delete offer letter?', message: `Remove "${row.filename || row.id}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return
    await fetch(`${API_BASE}/data-room/credentials/vault/offer_letters/${row.id}`, { method: 'DELETE', credentials: 'include' })
    onReload()
  }

  const handleSave = async () => {
    const { mode, form, id } = modal
    // A new offer letter is the PDF. `upload-analyze` writes the file, derives
    // the row and switches this modal to edit, so reaching Save in create mode
    // means no PDF was chosen and there is nothing to catalogue.
    if (mode === 'create') {
      setModalError('Choose the offer letter file first.')
      return
    }
    // Only the editable keys. update_vault_item merges, so everything the
    // upload derived is preserved by not being sent.
    const body = Object.fromEntries(EDITABLE_KEYS.map(key => [key, form[key] ?? '']))
    const url = `${API_BASE}/data-room/credentials/vault/offer_letters/${id}`
    const method = 'PATCH'
    setSaving(true)
    setModalError('')
    try {
      const res = await fetch(url, { method, credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      const data = await readApiResponse(res)
      if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }
      setModal(null)
      setAnalysisMessage('')
      onReload()
    } catch (error) {
      setModalError(error.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleInitialUpload = async (file) => {
    if (!file) return
    if (!isOfferFile(file)) {
      setModalError('Upload the offer letter as a PDF, PNG, JPG or WebP file.')
      return
    }
    if (file.size > 25 * 1024 * 1024) {
      setModalError('The file is too large. Maximum size is 25 MB.')
      return
    }
    setAnalyzing(true)
    setModalError('')
    setAnalysisMessage('')
    const body = new FormData()
    body.append('file', file)
    try {
      const res = await fetch(`${API_BASE}/data-room/offer-letters/upload-analyze`, {
        method: 'POST',
        credentials: 'include',
        body,
      })
      const data = await readApiResponse(res)
      if (!res.ok || data.status !== 'ok') {
        throw new Error(data.message || 'Upload or analysis failed')
      }
      const row = data.offer_letter || {}
      setModal({
        mode: 'edit',
        id: row.id,
        row,
        form: {
          candidate: row.candidate || '',
          company_name: row.company_name || '',
          notes: row.notes || '',
        },
      })
      setAnalysisMessage(data.message || 'PDF saved and fields auto-filled.')
      onReload()
    } catch (error) {
      setModalError(error.message || 'Upload or analysis failed')
    } finally {
      setAnalyzing(false)
    }
  }

  const handleUpload = async (rowId, file) => {
    if (!file) return
    if (!isOfferFile(file)) {
      setUploadError('Upload the offer letter as a PDF, PNG, JPG or WebP file.')
      return
    }
    setUploadId(rowId)
    setUploading(true)
    setUploadError('')
    const fd = new FormData()
    fd.append('file', file)
    try {
      const res = await fetch(`${API_BASE}/data-room/offer-letters/${rowId}/upload`, { method: 'POST', credentials: 'include', body: fd })
      const data = await readApiResponse(res)
      if (data.status !== 'ok') setUploadError(data.message || 'Upload failed')
      else onReload()
    } catch (e) {
      setUploadError(String(e))
    } finally {
      setUploading(false)
      setUploadId(null)
    }
  }

  return (
    <section className="dr-section dr-section--active dr-offers-tab">
      <div className="dr-tab-header">
        <div>
          <h2 className="dr-section-title">Offer Letters</h2>
          <p className="dr-section-desc">Catalogued offer letters for proof in Data Room vault.</p>
        </div>
        <button type="button" className="cand-btn cand-btn--primary" onClick={openAdd}>+ Add offer</button>
      </div>

      <div className="dr-tab-stats">
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Total offers</span>
          </div>
          <div className="dr-tab-stat-value">{offers.length}</div>
          <div className="dr-tab-stat-sub">Catalogued offer letters for proof</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Unique candidates</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--green">{new Set(offers.map(o => o.candidate).filter(Boolean)).size}</div>
          <div className="dr-tab-stat-sub">Distinct candidate names</div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Companies</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--purple">{new Set(offers.map(o => o.company_name).filter(Boolean)).size}</div>
          <div className="dr-tab-stat-sub">Unique company names</div>
        </div>
      </div>

      {uploadError && <p className="dr-error">{uploadError}</p>}
      {(() => {
        const photos = offers.filter(row => row.file?.photo_of_one_page)
        if (!photos.length) return null
        return (
          <p className="dr-offer-photo-notice" role="note">
            {photos.length === 1 ? '1 offer letter is a photo' : `${photos.length} offer letters are photos`} of
            one page, stored by the WhatsApp import in place of the PDF {photos.length === 1 ? 'it names' : 'each names'}.
            Their other pages and annexures are not on the server. Upload each original PDF with ↑ to replace its photo.
          </p>
        )
      })()}

      <div className="dr-tab-table-wrap">
        <table className="dr-tab-table">
          <thead>
            <tr>
              <th>Candidate</th>
              <th>File</th>
              <th>Company</th>
              <th>Modified</th>
              <th>Pages</th>
              <th>Size</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {offers.length === 0 && (
              <tr><td colSpan={7} className="dr-empty">No offer letters yet.</td></tr>
            )}
            {offers.map(row => (
              <tr key={row.id}>
                <td data-label="Candidate" className="dr-offer-candidate">{row.candidate || '—'}</td>
                <td data-label="File"><strong className="dr-offer-filename">{row.filename || row.id}</strong></td>
                <td data-label="Company">{row.company_name || '—'}</td>
                <td data-label="Modified">{row.date_modified || '—'}</td>
                <td data-label="Pages" className="dr-offer-pages">
                  <span>{pagesLabel(row)}</span>
                  {row.file?.password_protected && (
                    <span className="dr-offer-flag dr-offer-flag--lock" title="Uploaded with a password: the preview asks for it">Password</span>
                  )}
                  {row.file?.photo_of_one_page && (
                    <span
                      className="dr-offer-flag dr-offer-flag--photo"
                      title={`A photo of one page. The original PDF${row.file.original_name ? ` (${row.file.original_name})` : ''} was not stored.`}
                    >
                      Photo of page 1
                    </span>
                  )}
                </td>
                <td data-label="Size">{row.size_kb ? `${row.size_kb} KB` : '—'}</td>
                <td data-label="Actions">
                  <div className="dr-acct-actions dr-offer-actions">
                    <button
                      type="button"
                      className="cand-btn cand-btn--sm cand-btn--primary dr-offer-view-btn"
                      onClick={() => setPreview(row)}
                      aria-label={`${viewLabel(row)} for ${rowName(row)}`}
                    >
                      <Icon name="file" size={14} />
                      {viewLabel(row)}
                    </button>
                    <a
                      href={offerLetterDownloadApiUrl(row, API_BASE)}
                      download
                      className="dr-offer-icon-btn"
                      title="Download"
                      aria-label={`Download ${offerIsImage(row) ? 'image' : 'PDF'} for ${rowName(row)}`}
                    >
                      <Icon name="download" size={14} />
                    </a>
                    <button type="button" className="cand-btn cand-btn--sm" onClick={() => openEdit(row)}>Edit</button>
                    <button type="button" className="cand-btn cand-btn--sm cand-btn--danger" onClick={() => handleDelete(row)}>Delete</button>
                    <label className={`cand-btn cand-btn--sm${uploading && uploadId === row.id ? ' cand-btn--disabled' : ''}`} title="Upload a new PDF or image">
                      {uploading && uploadId === row.id ? '…' : '↑'}
                      <input type="file" accept={OFFER_FILE_ACCEPT} style={{ display: 'none' }} onChange={(e) => { const chosen = e.target.files[0]; e.target.value = ''; handleUpload(row.id, chosen) }} />
                    </label>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal && (
        <VaultModal
          title={modal.mode === 'create' ? 'Add offer letter' : 'Edit offer letter'}
          fields={OFFER_FIELDS}
          form={modal.form}
          onChange={(f) => setModal(s => ({ ...s, form: f }))}
          onSave={handleSave}
          onClose={() => setModal(null)}
          error={modalError}
          saving={saving}
          suspended={Boolean(preview)}
          uploadPanel={modal.mode === 'create' ? (
            <div className={`dr-offer-upload-panel${analyzing ? ' dr-offer-upload-panel--busy' : ''}`}>
              <div className="dr-offer-upload-copy">
                <strong>{analyzing ? 'Saving and reading the offer letter…' : 'Upload the offer letter'}</strong>
                <span>
                  A PDF, or a photo or scan of the letter (PNG, JPG or WebP). The
                  file is stored as it is, then the candidate and company are
                  filled in from it for you to check.
                </span>
              </div>
              <label className={`cand-btn cand-btn--primary${analyzing ? ' cand-btn--disabled' : ''}`}>
                {analyzing ? 'Analyzing…' : 'Choose file'}
                <input
                  type="file"
                  accept={OFFER_FILE_ACCEPT}
                  disabled={analyzing}
                  style={{ display: 'none' }}
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    event.target.value = ''
                    handleInitialUpload(file)
                  }}
                />
              </label>
            </div>
          ) : (
            /* A saved record: what it holds, and a way to open it. The
               filename, size and date are read from the PDF, so they are shown
               rather than asked for. Preview used to appear only in the moment
               after an upload; it belongs on every edit. */
            <div className="dr-offer-attached">
              <span className="dr-offer-attached-file">
                {modal.row?.filename
                  ? `${modal.row.filename}${modal.row.size_kb ? ` · ${modal.row.size_kb} KB` : ''}${modal.row.date_modified ? ` · ${modal.row.date_modified}` : ''}`
                  : 'No file attached to this record.'}
              </span>
              {modal.row?.has_pdf && (
                <button type="button" className="cand-btn cand-btn--sm" onClick={() => setPreview(modal.row)}>
                  {viewLabel(modal.row)}
                </button>
              )}
              {analysisMessage && <p className="dr-offer-analysis-ok">{analysisMessage}</p>}
            </div>
          )}
        />
      )}

      {preview && (
        <OfferPreviewModal
          offer={preview}
          previewUrl={offerLetterPreviewApiUrl(preview, API_BASE)}
          downloadUrl={offerLetterDownloadApiUrl(preview, API_BASE)}
          onClose={() => setPreview(null)}
        />
      )}
    </section>
  )
}
