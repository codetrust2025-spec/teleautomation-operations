import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { useDialogA11y } from '../hooks/useDialogA11y.js'
import { copyToClipboard } from '../utils/copyToClipboard.js'
import { formatCredentialBlock } from './dataRoomCredentialBlock.js'
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
  if (row.primary === true || row.is_primary === true) return true
  const label = String(row.label || '').toLowerCase()
  return label.includes('primary') || label.includes('current') || label.includes('2026')
}

// What an operator actually has to type.
//
// The id is a stable slug the backend requires but nobody chooses: `slugId`
// derives one from the title. It is generated and sent, never asked for.
//
// The service is one of three, chosen from a list (19 Oct). It was free text
// shown with a "Gmail" fallback, so 26 Naukri logins read as Gmail. It is never
// worked out from the username: a Naukri login is very often a Gmail address.
// Sniffed again in the backend from the bytes; this is only the picker filter.
const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp']
const MAX_IMAGE_BYTES = 5 * 1024 * 1024

// Tool (20 Oct): the logins of tools the team works with. Stored as a service
// account's service, as the others are.
export const SERVICE_TYPES = ['Naukri', 'Gmail', 'Telegram', 'Tool']
// The services that are categories of their own. Tool is not: a tool login and
// a dashboard login are one category, Dashboard / Tool Logins (20 Oct).
export const SEPARATE_SERVICES = ['Naukri', 'Gmail', 'Telegram']
export const LOGINS_LABEL = 'Dashboard / Tool Logins'
const LOGIN_BADGE = 'Dashboard / Tool'
const DEFAULT_SERVICE = 'Naukri'
export const SERVICE_FILTER_KEY = 'ta.dataRoom.accountsService'
// The filter value for accounts with no service yet, so that All Accounts is
// always the sum of the options below it.
export const UNSET_SERVICE = 'unset'
// The admin's and the handlers' logins to this dashboard, with the tool logins:
// one Account type, Dashboard / Tool Logins, in the filter and on every row.
// Only the category is shared. A dashboard login is still a handler, saved and
// signed in with as before; a tool login is still a service account. Neither is
// turned into the other, so nothing is copied and no one signs in differently.
export const LOGIN_TYPE = 'login'

/** The account's service when it is one of the four, otherwise ''. */
const serviceOf = row => (SERVICE_TYPES.includes(row.service) ? row.service : '')

/** The Account type filter remembered for this browser session, or All Accounts. A Tool remembered from before is now the logins. */
function readServiceFilter() {
  try {
    const saved = window.sessionStorage.getItem(SERVICE_FILTER_KEY)
    if (saved === 'Tool') return LOGIN_TYPE
    return saved === 'all' || saved === UNSET_SERVICE || saved === LOGIN_TYPE || SEPARATE_SERVICES.includes(saved) ? saved : 'all'
  } catch {
    return 'all'
  }
}

/**
 * Copy text that has to be fetched first. Safari only lets a page write to the
 * clipboard inside the click itself, so the fetch is handed to the clipboard as
 * a pending item where that is supported; elsewhere it is fetched, then copied.
 */
async function copyFetched(getText) {
  if (typeof window !== 'undefined' && window.ClipboardItem && navigator.clipboard?.write) {
    try {
      const blob = getText().then(text => new Blob([text], { type: 'text/plain' }))
      await navigator.clipboard.write([new window.ClipboardItem({ 'text/plain': blob })])
      return true
    } catch {
      // fall through to the plain path
    }
  }
  return copyToClipboard(await getText())
}

const SERVICE_HINT = 'A Naukri login is Naukri even when its username is a Gmail address.'
const LOGIN_HINT = 'Signs in to this dashboard as a handler. The password is stored hashed: it cannot be shown again.'
const TOOL_HINT = 'A login to another tool or site, kept here with its password.'

/**
 * The fields of the Add / Edit dialog. A service account: name, Account type,
 * username, password, notes. A dashboard login: the handler's own fields, and
 * on Add the same Account type picker, so either kind is added from one button.
 * A login is never turned into a service account or back: they are stored apart.
 */
/** A dialog for a dashboard login (a handler) or for a service account. */
const modalKind = modal => (modal?.kind === 'login' || (modal?.mode === 'create' && modal?.form?.service === LOGIN_TYPE) ? 'login' : 'account')

function accountFields({ kind, mode, service }) {
  // Dashboard / Tool Login is one type with two kinds of record behind it, so
  // Add says which: a tool login (a service account) or a login to this dashboard
  // (a handler). Editing a service account offers only what it can be.
  const loginGroup = {
    group: `${LOGINS_LABEL.replace(/s$/, '')}`,
    options: [
      { value: 'Tool', label: 'Tool login (another tool or site)' },
      ...(mode === 'create' ? [{ value: LOGIN_TYPE, label: 'Dashboard login (signs in here)' }] : []),
    ],
  }
  const typeField = {
    key: 'service', label: 'Account type', type: 'select', placeholder: 'Choose an account type',
    options: [...SEPARATE_SERVICES, loginGroup],
    hint: kind === 'login' ? LOGIN_HINT : service === 'Tool' ? TOOL_HINT : SERVICE_HINT,
  }
  if (kind === 'login') {
    return [
      { key: 'reference', label: 'Reference (display name)', full: mode === 'edit' },
      ...(mode === 'create' ? [typeField] : []),
      { key: 'username', label: 'Username', readOnly: mode === 'edit', autoComplete: 'off' },
      { key: 'password', label: mode === 'edit' ? 'Password (leave blank to keep)' : 'Password', type: 'password', autoComplete: 'new-password' },
      { key: 'notes', label: 'Notes (optional)', type: 'textarea', rows: 2, full: true },
    ]
  }
  return [
    { key: 'label', label: 'Account name', placeholder: 'e.g. Bhavana Naukri', full: true },
    typeField,
    { key: 'username', label: 'Username / email', placeholder: 'name@example.com' },
    { key: 'password', label: 'Password', type: 'password' },
    { key: 'notes', label: 'Notes (optional)', type: 'textarea', rows: 3, full: true },
  ]
}

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
              {f.type === 'select' ? (
                <>
                  <select
                    className="cand-input dr-select"
                    aria-label={f.label}
                    aria-describedby={f.hint ? `dr-field-hint-${f.key}` : undefined}
                    value={form[f.key] || ''}
                    onChange={(e) => onChange({ ...form, [f.key]: e.target.value })}
                  >
                    {!form[f.key] && <option value="" disabled>{f.placeholder}</option>}
                    {f.options.map(option => {
                      const toOption = (o) => {
                        const { value, label } = typeof o === 'string' ? { value: o, label: o } : o
                        return <option key={value} value={value}>{label}</option>
                      }
                      return option.group
                        ? <optgroup key={option.group} label={option.group}>{option.options.map(toOption)}</optgroup>
                        : toOption(option)
                    })}
                  </select>
                  {f.hint && <small id={`dr-field-hint-${f.key}`} className="dr-field-hint">{f.hint}</small>}
                </>
              ) : f.type === 'textarea' ? (
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
                  autoComplete={f.autoComplete}
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

const EXPORT_FORMATS = [
  { value: 'xlsx', label: 'Excel (.xlsx)' },
  { value: 'csv', label: 'CSV (.csv)' },
  { value: 'txt', label: 'Text (.txt)' },
]

/** The file name the server chose (Content-Disposition), or the fallback. */
export function downloadName(disposition, fallback) {
  const match = /filename="?([^";]+)"?/i.exec(disposition || '')
  return match ? match[1] : fallback
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Released once the browser has taken the file; guarded, as not every environment has it.
  window.setTimeout(() => URL.revokeObjectURL?.(url), 1000)
}

/**
 * Download All Accounts. Every account's name, username and password, exactly
 * as stored, as Excel, CSV or text, for the admin. The file holds the passwords in plain text, so the
 * download waits for one explicit confirmation that says so; the server builds
 * it in memory, records who took it, and keeps no copy.
 */
function AccountsExportModal({ count, onClose }) {
  const dialogRef = useDialogA11y(true, onClose)
  const [format, setFormat] = useState('xlsx')
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState('')
  const formatLabel = EXPORT_FORMATS.find(f => f.value === format)?.label || format
  const plural = count === 1 ? '' : 's'

  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKeyDown = (event) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose])

  const download = async () => {
    setError('')
    setDone('')
    setBusy(true)
    try {
      const res = await fetch(`${API_BASE}/data-room/credentials/service-accounts/export?format=${format}`, { credentials: 'include' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'The download failed. Try again.')
      }
      const blob = await res.blob()
      saveBlob(blob, downloadName(res.headers.get('content-disposition'), `teleautomation-accounts.${format}`))
      setDone(`Downloaded ${count} account${plural}. Keep the file safe and delete it when you are done.`)
    } catch (err) {
      setError(err instanceof TypeError ? 'Network error. Try again.' : err.message)
    } finally {
      setBusy(false)
      setConfirming(false)
    }
  }

  return createPortal(
    <div className="dr-modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="dr-modal cand-card dr-export-modal"
        ref={dialogRef} role="dialog"
        aria-modal="true"
        aria-labelledby="dr-export-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="dr-export-modal-title" className="cand-title">Download all accounts</h2>
        {confirming ? (
          <div className="dr-export-confirm" role="alertdialog" aria-labelledby="dr-export-confirm-title" aria-describedby="dr-export-confirm-text">
            <h3 id="dr-export-confirm-title" className="dr-export-confirm__title">Download every password?</h3>
            <p id="dr-export-confirm-text" className="dr-export-confirm__text">
              This {formatLabel} file will contain all {count} password{plural} in plain text. Anyone who has the file can
              sign in to these accounts. Save it only on a device you trust, never share it, and delete it when you are done.
            </p>
            <div className="dr-modal-actions">
              <button type="button" className="cand-btn" onClick={() => setConfirming(false)} disabled={busy}>Back</button>
              <button type="button" className="cand-btn cand-btn--danger" onClick={download} disabled={busy}>
                {busy ? 'Preparing…' : 'Yes, download'}
              </button>
            </div>
          </div>
        ) : (
          <>
            <p className="dr-export-lead">
              All {count} service account{plural}: account name, username and password.
              Dashboard logins are not included: their passwords are stored hashed.
            </p>
            <p className="dr-export-warning" role="note">
              The file holds every password in plain text. It is made for you when you download it and is not kept on the server.
            </p>
            <fieldset className="dr-export-formats">
              <legend>Format</legend>
              {EXPORT_FORMATS.map(f => (
                <label key={f.value} className={`dr-export-format${format === f.value ? ' dr-export-format--on' : ''}`}>
                  <input type="radio" name="dr-export-format" value={f.value} checked={format === f.value} onChange={() => { setFormat(f.value); setDone('') }} />
                  {f.label}
                </label>
              ))}
            </fieldset>
            {error && <p className="dr-error" role="alert">{error}</p>}
            {done && <p className="dr-export-done" role="status">{done}</p>}
            <div className="dr-modal-actions">
              <button type="button" className="cand-btn" onClick={onClose}>Close</button>
              <button type="button" className="cand-btn cand-btn--primary" onClick={() => { setError(''); setDone(''); setConfirming(true) }} disabled={count === 0}>
                Download all
              </button>
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  )
}

/**
 * Data Room -> Accounts & Logins: every credential in one table (20 Oct). The
 * dashboard's own logins (the admin and the handlers, `logins`) and the service
 * accounts (`accounts`), each row with the columns and actions of its kind, and
 * one Account type filter over all of them.
 */
export function DataRoomAccountsTab({ accounts = [], logins = null, site = '', vpsHost = '', onReload, canExport = false }) {
  const { confirm } = useConfirm()
  const [activeKey, setActiveKey] = useState(null)
  const [modal, setModal] = useState(null)
  const [modalError, setModalError] = useState('')
  const [search, setSearch] = useState('')
  const [exporting, setExporting] = useState(false)
  const closeExport = useCallback(() => setExporting(false), [])
  // Remembered for the session, so it holds while moving around the Data Room.
  const [serviceFilter, setServiceFilter] = useState(readServiceFilter)
  const chooseServiceFilter = (value) => {
    setServiceFilter(value)
    try { window.sessionStorage.setItem(SERVICE_FILTER_KEY, value) } catch { /* private mode: not remembered */ }
  }
  // Passwords are not in the account list (19 Oct). One is read when it is
  // shown, copied or edited, and kept here until the list is loaded again.
  const passwords = useRef({})
  const [shown, setShown] = useState({})
  const [rowError, setRowError] = useState('')
  useEffect(() => {
    passwords.current = {}
    // Unchanged when nothing is shown, so a fresh empty list cannot re-render forever.
    setShown(current => (Object.keys(current).length ? {} : current))
  }, [accounts])
  const fetchPassword = useCallback(async (id) => {
    if (Object.prototype.hasOwnProperty.call(passwords.current, id)) return passwords.current[id]
    const res = await fetch(`${API_BASE}/data-room/credentials/service-accounts/${encodeURIComponent(id)}/password`, { credentials: 'include' })
    const data = await res.json().catch(() => ({}))
    if (!res.ok || data.status !== 'ok') throw new Error(data.detail || 'The password could not be read.')
    passwords.current[id] = data.password
    return data.password
  }, [])
  const toggleShown = async (row) => {
    setRowError('')
    if (shown[row.id] !== undefined) {
      setShown(current => { const next = { ...current }; delete next[row.id]; return next })
      return
    }
    try {
      const password = await fetchPassword(row.id)
      setShown(current => ({ ...current, [row.id]: password }))
    } catch (err) {
      setRowError(err.message)
    }
  }
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

  // "Copied" shows for a moment; the timer goes with the tab, so it never fires after.
  const copiedTimer = useRef(null)
  useEffect(() => () => window.clearTimeout(copiedTimer.current), [])
  const showCopied = useCallback((key, ok) => {
    setActiveKey(ok ? key : null)
    window.clearTimeout(copiedTimer.current)
    if (ok) copiedTimer.current = window.setTimeout(() => setActiveKey(k => (k === key ? null : k)), 1600)
  }, [])
  const onCopy = useCallback(async (key, text) => {
    showCopied(key, await copyToClipboard(text))
  }, [showCopied])
  const onCopyFetched = useCallback(async (key, getText) => {
    setRowError('')
    let ok = false
    try { ok = await copyFetched(getText) } catch (err) { setRowError(err.message) }
    showCopied(key, ok)
  }, [showCopied])

  const openAdd = () => {
    setModalError('')
    clearImageState()
    // The type the list is narrowed to. Under Dashboard / Tool Logins that is a
    // tool login: a new sign-in to this dashboard is chosen on purpose, not by default.
    const service = activeFilter === LOGIN_TYPE ? 'Tool' : SEPARATE_SERVICES.includes(activeFilter) ? activeFilter : DEFAULT_SERVICE
    setModal({ mode: 'create', form: { id: '', label: '', reference: '', service, username: '', password: '', notes: '' } })
  }

  const openEditLogin = (login) => {
    setModalError('')
    clearImageState()
    setModal({ mode: 'edit', kind: 'login', username: login.username, form: { username: login.username, reference: login.reference || '', password: '', notes: login.notes || '' } })
  }

  const deleteLogin = async (login) => {
    const ok = await confirm({ title: 'Remove handler?', message: `Delete handler "${login.reference || login.username}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return
    await fetch(`${API_BASE}/data-room/credentials/handlers/${login.username}`, { method: 'DELETE', credentials: 'include' })
    onReload()
  }

  // A handler is saved where it always was, apart from the service accounts.
  const saveLogin = async () => {
    const { mode, form, username } = modal
    if (mode === 'create' && !form.username.trim()) { setModalError('Enter a username.'); return }
    if (mode === 'create' && !form.password) { setModalError('Enter a password.'); return }
    const body = { username: form.username, reference: form.reference, notes: form.notes }
    if (form.password) body.password = form.password
    const url = mode === 'create'
      ? `${API_BASE}/data-room/credentials/handlers`
      : `${API_BASE}/data-room/credentials/handlers/${username}`
    const res = await fetch(url, { method: mode === 'create' ? 'POST' : 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const data = await res.json()
    if (data.status !== 'ok') { setModalError(data.message || 'Save failed'); return }
    setModal(null)
    onReload()
  }

  const openEdit = (row) => {
    setModalError('')
    clearImageState()
    // An account whose service was never recorded opens with none chosen, so
    // saving it asks for one rather than guessing.
    setModal({ mode: 'edit', id: row.id, row, originalPassword: null, form: { id: row.id, label: row.label || '', service: SERVICE_TYPES.includes(row.service) ? row.service : '', username: row.username || '', password: '', notes: row.notes || '' } })
    if (row.has_password) {
      fetchPassword(row.id)
        .then(password => setModal(m => (m && m.id === row.id && m.mode === 'edit' && m.originalPassword === null
          ? { ...m, originalPassword: password, form: { ...m.form, password: m.form.password || password } }
          : m)))
        .catch(() => { /* the field stays empty: a blank password keeps the stored one */ })
    }
  }

  const handleDelete = async (row) => {
    const ok = await confirm({ title: 'Delete account?', message: `Remove "${row.label || row.id}"?`, confirmLabel: 'Delete', variant: 'danger' })
    if (!ok) return
    await fetch(`${API_BASE}/data-room/credentials/vault/service_accounts/${row.id}`, { method: 'DELETE', credentials: 'include' })
    onReload()
  }

  const handleSave = async () => {
    if (modalKind(modal) === 'login') { await saveLogin(); return }
    const { mode, form, id, originalPassword } = modal
    if (!SERVICE_TYPES.includes(form.service)) { setModalError('Choose a service type.'); return }
    const body = { ...form }
    delete body.reference
    if (mode === 'create' && !body.id.trim()) body.id = slugId(body.label || body.username)
    // An edit sends the password only when it was changed; blank keeps the stored one.
    if (mode === 'edit' && (body.password === '' || body.password === originalPassword)) delete body.password
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

  const activeAccounts = accounts.filter(r => accountStatus(r) === 'active').length
  const oldAccounts = accounts.filter(r => accountStatus(r) === 'old').length
  const primaryAccounts = accounts.filter(r => isPrimary(r)).length

  // One list: the admin, the handlers, then the service accounts. A login row
  // is never given a service, and a service account never a role.
  const admin = logins?.admin || null
  const handlers = logins?.handlers || []
  const items = useMemo(() => [
    ...(admin ? [{ kind: 'login', key: 'login:admin', isAdmin: true, row: admin }] : []),
    ...handlers.map(h => ({ kind: 'login', key: `login:${h.username}`, isAdmin: false, row: h })),
    ...accounts.map(row => ({ kind: 'account', key: row.id, row })),
  ], [admin, handlers, accounts])
  // A dashboard login and a tool login are one Account type.
  const typeOf = item => (item.kind === 'login' || serviceOf(item.row) === 'Tool' ? LOGIN_TYPE : serviceOf(item.row) || UNSET_SERVICE)

  // Every record is counted exactly once: a dashboard or tool login, Naukri,
  // Gmail, Telegram, or Not set until someone chooses one. All Accounts is always the sum.
  const serviceCounts = useMemo(() => {
    const counts = { [LOGIN_TYPE]: 0, ...Object.fromEntries(SEPARATE_SERVICES.map(name => [name, 0])), [UNSET_SERVICE]: 0 }
    for (const item of items) counts[typeOf(item)] += 1
    return counts
  }, [items])
  const loginCount = serviceCounts[LOGIN_TYPE]
  const unsetCount = serviceCounts[UNSET_SERVICE]
  // Not set is offered only while some account has no service; once none is
  // left, a remembered Not set falls back to All Accounts.
  const activeFilter = serviceFilter === UNSET_SERVICE && !unsetCount ? 'all' : serviceFilter

  // The Account type filter and the search narrow the same list.
  const query = search.trim().toLowerCase()
  const matches = (item) => {
    const r = item.row
    const words = item.kind === 'login'
      ? [r.reference, r.username, item.isAdmin ? 'admin' : 'handler', 'dashboard login', LOGINS_LABEL]
      : [r.label, r.username, serviceOf(r), ...(serviceOf(r) === 'Tool' ? ['tool login', LOGINS_LABEL] : [])]
    return words.some(w => String(w || '').toLowerCase().includes(query))
  }
  const filtered = items
    .filter(item => activeFilter === 'all' || typeOf(item) === activeFilter)
    .filter(item => !query || matches(item))
  const filterName = activeFilter === UNSET_SERVICE ? 'unclassified' : activeFilter
  const emptyText = activeFilter === LOGIN_TYPE
    ? (query ? 'No dashboard or tool logins match your search.' : 'No dashboard or tool logins.')
    : activeFilter !== 'all'
      ? (query ? `No ${filterName} accounts match your search.`
        : activeFilter === 'Gmail' ? 'No Gmail accounts. Naukri logins that use a Gmail address are under Naukri.'
          : `No ${filterName} accounts.`)
      : (query ? 'No accounts match your search.' : 'No accounts found.')

  return (
    <section className="dr-section dr-section--active dr-accounts-tab">
      {/* Header */}
      <div className="dr-tab-header">
        <div>
          {/* Named on screen by its tab, Accounts & Logins. */}
          <h2 className="dr-section-title sr-only">Accounts and logins</h2>
          <p className="dr-section-desc">Dashboard and tool logins, and Naukri, Gmail and Telegram accounts.</p>
          {logins && (
            <p className="dr-acct-meta">
              <span className="dr-creds-site" title={site || undefined}>
                <span className="dr-creds-site-label">Site</span>
                {site
                  ? <a href={site} target="_blank" rel="noopener noreferrer">{site.replace(/^https?:\/\//, '')}</a>
                  : <span className="dr-muted">not configured</span>}
              </span>
              {vpsHost && (
                <span className="dr-creds-site dr-acct-vps">
                  <span className="dr-creds-site-label">VPS SSH</span>
                  <code>root@{vpsHost}</code>
                  <CopyChip label="SSH host" text={`root@${vpsHost}`} copyKey="vps-host" activeKey={activeKey} onCopy={onCopy} />
                </span>
              )}
            </p>
          )}
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
          <select
            className="cand-input dr-service-select"
            aria-label="Account type"
            value={activeFilter}
            onChange={(e) => chooseServiceFilter(e.target.value)}
          >
            <option value="all">All Accounts ({items.length})</option>
            <option value={LOGIN_TYPE}>{LOGINS_LABEL} ({serviceCounts[LOGIN_TYPE]})</option>
            {SEPARATE_SERVICES.map(name => <option key={name} value={name}>{name} ({serviceCounts[name]})</option>)}
            {unsetCount > 0 && <option value={UNSET_SERVICE}>Not set ({unsetCount})</option>}
          </select>
          {/* The admin's, by role: the server refuses everyone else too. */}
          {canExport && (
            <button type="button" className="cand-btn dr-add-btn dr-export-btn" onClick={() => setExporting(true)}>
              <Icon name="download" size={14} strokeWidth={2.2} />
              Download all accounts
            </button>
          )}
          <button type="button" className="cand-btn cand-btn--primary dr-add-btn" onClick={openAdd}>
            <Icon name="plus" size={14} strokeWidth={2.2} />
            Add account
          </button>
        </div>
      </div>
      {exporting && <AccountsExportModal count={accounts.length} onClose={closeExport} />}
      {unsetCount > 0 && activeFilter !== UNSET_SERVICE && (
        <div className="dr-svc-notice" role="status">
          <span>
            {unsetCount === 1 ? '1 account has' : `${unsetCount} accounts have`} no service type yet.
            Choose Naukri, Gmail, Telegram or Dashboard / Tool Login with Edit so {unsetCount === 1 ? 'it is' : 'they are'} counted under {unsetCount === 1 ? 'its' : 'their'} type.
          </span>
          <button type="button" className="cand-btn cand-btn--sm" onClick={() => chooseServiceFilter(UNSET_SERVICE)}>
            Show {unsetCount === 1 ? 'it' : 'them'}
          </button>
        </div>
      )}

      {/* Stats row */}
      <div className="dr-tab-stats">
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Total accounts</span>
          </div>
          <div className="dr-tab-stat-value">{items.length}</div>
          <div className="dr-tab-stat-sub">
            {logins
              ? `${loginCount} dashboard / tool login${loginCount === 1 ? '' : 's'} · ${items.length - loginCount} other account${items.length - loginCount === 1 ? '' : 's'}`
              : `${activeAccounts} active · ${oldAccounts} deprecated`}
          </div>
        </div>
        <div className="dr-tab-stat">
          <div className="dr-tab-stat-header">
            <span className="dr-tab-stat-title">Active</span>
          </div>
          <div className="dr-tab-stat-value dr-tab-stat-value--green">{activeAccounts}</div>
          <div className="dr-tab-stat-sub">Service accounts in use</div>
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
              <tr className="dr-acct-empty-row"><td colSpan={7} className="dr-empty">{emptyText}</td></tr>
            )}
            {filtered.map(item => {
              if (item.kind === 'login') {
                const login = item.row
                // The admin's stored reference says what it can do ("Full dashboard"), which
                // its role line already says: it is named by its role instead.
                const name = item.isAdmin ? 'Admin' : (login.reference || login.username || 'Handler')
                return (
                  <tr key={item.key} className="dr-acct-login-row">
                    <td data-label="Account">
                      <div className="dr-acct-cell">
                        <span className="dr-acct-dot dr-acct-dot--active" />
                        <div>
                          <span className="dr-acct-name">{name}</span>
                          <span className="dr-acct-role">{item.isAdmin ? 'Full dashboard' : 'Handler'}</span>
                        </div>
                      </div>
                    </td>
                    <td data-label="Type">
                      <span className="dr-acct-svc dr-acct-svc--dashboard" title="Dashboard login: signs in to this dashboard">{LOGIN_BADGE}</span>
                    </td>
                    <td data-label="Username">
                      <div className="dr-acct-cred-cell">
                        <code title={login.username || undefined}>{login.username || '—'}</code>
                        {login.username && <CopyChip label="username" text={login.username} copyKey={`${item.key}-user`} activeKey={activeKey} onCopy={onCopy} />}
                      </div>
                    </td>
                    <td data-label="Password">
                      {/* Stored as a hash: there is nothing to show or copy. */}
                      <code
                        className="dr-creds-pass dr-creds-pass--none"
                        title={item.isAdmin ? 'Stored hashed. Change it from the top-bar account menu.' : 'Stored hashed: it cannot be shown. Set a new one with Edit.'}
                      >
                        Hashed
                      </code>
                    </td>
                    {/* Nothing recorded for a login: the line is left out of its card on a phone. */}
                    <td className="dr-muted dr-acct-cell--empty" data-label="Status">—</td>
                    <td className="dr-muted dr-acct-cell--empty" data-label="Last used">—</td>
                    <td data-label="Actions">
                      <div className="dr-acct-actions">
                        <button
                          type="button"
                          className={`dr-copy-btn dr-copy-btn--all${activeKey === `${item.key}-all` ? ' dr-copy-btn--copied' : ''}`}
                          title="Copy the site, username and role"
                          onClick={() => onCopy(`${item.key}-all`, formatCredentialBlock(site, login, { isAdmin: item.isAdmin }))}
                        >
                          <Icon name={activeKey === `${item.key}-all` ? 'check' : 'copy'} size={13} />
                          {activeKey === `${item.key}-all` ? 'Copied' : 'Copy all'}
                        </button>
                        {item.isAdmin ? (
                          <button type="button" className="cand-btn cand-btn--sm cand-btn--ghost dr-acct-nowrap" title="The admin password is changed from the top-bar account menu, not here">
                            Account menu
                          </button>
                        ) : (
                          <>
                            <button type="button" className="cand-btn cand-btn--sm" onClick={() => openEditLogin(login)} aria-label={`Edit ${name}`}>Edit</button>
                            <button type="button" className="cand-btn cand-btn--sm cand-btn--danger" onClick={() => deleteLogin(login)} aria-label={`Delete ${name}`}>Delete</button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              }
              const row = item.row
              const status = accountStatus(row)
              const primary = isPrimary(row)
              return (
                <tr key={item.key}>
                  <td data-label="Account">
                    <div className="dr-acct-cell">
                      <span className={`dr-acct-dot dr-acct-dot--${status}`} />
                      <div>
                        <span className="dr-acct-name">{row.label || row.id}</span>
                        {serviceOf(row) === 'Tool' && <span className="dr-acct-role">Tool login</span>}
                        {primary && <span className="dr-acct-primary-badge">★ Primary</span>}
                      </div>
                    </div>
                  </td>
                  <td data-label="Type">
                    {serviceOf(row) === 'Tool'
                      ? <span className="dr-acct-svc dr-acct-svc--dashboard" title="Tool login: a login to another tool or site">{LOGIN_BADGE}</span>
                      : <span className={`dr-acct-svc dr-acct-svc--${(serviceOf(row) || 'none').toLowerCase()}`}>{serviceOf(row) || 'Not set'}</span>}
                  </td>
                  <td data-label="Username">
                    <div className="dr-acct-cred-cell">
                      <code title={row.username || undefined}>{row.username || '—'}</code>
                      {row.username && <CopyChip label="username" text={row.username} copyKey={`${row.id}-user`} activeKey={activeKey} onCopy={onCopy} />}
                    </div>
                  </td>
                  <td data-label="Password">
                    <div className="dr-acct-cred-cell">
                      {row.has_password ? (
                        <>
                          <code className="dr-creds-pass">{shown[row.id] !== undefined ? shown[row.id] : '••••••••'}</code>
                          <button
                            type="button"
                            className="dr-copy-btn dr-pass-toggle"
                            aria-label={`${shown[row.id] !== undefined ? 'Hide' : 'Show'} password for ${row.label || row.id}`}
                            onClick={() => toggleShown(row)}
                          >
                            {shown[row.id] !== undefined ? 'Hide' : 'Show'}
                          </button>
                          <CopyChip label="password" text="" copyKey={`${row.id}-pass`} activeKey={activeKey}
                            onCopy={(key) => onCopyFetched(key, () => fetchPassword(row.id))} />
                        </>
                      ) : <code className="dr-creds-pass">—</code>}
                    </div>
                  </td>
                  <td data-label="Status">
                    <span className={`dr-acct-status dr-acct-status--${status}`}>
                      ● {status === 'active' ? 'Active' : 'Old'}
                    </span>
                  </td>
                  <td className={`dr-muted${row.last_used ? '' : ' dr-acct-cell--empty'}`} data-label="Last used">{row.last_used || '—'}</td>
                  <td data-label="Actions">
                    <div className="dr-acct-actions">
                      <button
                        type="button"
                        className={`dr-copy-btn dr-copy-btn--all${activeKey === `${row.id}-all` ? ' dr-copy-btn--copied' : ''}`}
                        title="Copy the account name, username, password and notes"
                        onClick={() => onCopyFetched(`${row.id}-all`, async () => {
                          const password = row.has_password ? await fetchPassword(row.id) : ''
                          return [row.label || row.id, row.service ? `Service: ${row.service}` : '', row.username ? `Username: ${row.username}` : '', password ? `Password: ${password}` : '', row.notes || ''].filter(Boolean).join('\n')
                        })}
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

      {rowError && <p className="dr-error" role="alert">{rowError}</p>}

      {/* Tip bar */}
      <div className="dr-tip-bar">
        <span className="dr-tip-icon">ℹ</span>
        <span>Tip: Use quick actions (Copy, Copy all) to speed up access. Accounts named with &ldquo;current&rdquo; or &ldquo;primary&rdquo; are marked as the primary account.</span>
      </div>

      {modal && (
        <VaultModal
          title={modalKind(modal) === 'login'
            ? (modal.mode === 'create' ? 'Add dashboard login' : 'Edit dashboard login')
            : (modal.mode === 'create' ? 'Add service account' : 'Edit service account')}
          // `id` stays on the form object so an edit preserves what a row
          // already has; it is simply not asked for.
          fields={accountFields({ kind: modalKind(modal), mode: modal.mode, service: modal.form?.service })}
          form={modal.form}
          onChange={(f) => setModal(s => {
            // Switching Add between a service account and a dashboard login
            // keeps the name typed so far: an account's name is a login's reference.
            const wasLogin = s.form.service === LOGIN_TYPE
            const isLogin = f.service === LOGIN_TYPE
            if (s.mode === 'create' && wasLogin !== isLogin) {
              f = isLogin ? { ...f, reference: f.reference || f.label } : { ...f, label: f.label || f.reference }
            }
            return { ...s, form: f }
          })}
          onSave={handleSave}
          onClose={() => { clearImageState(); setModal(null) }}
          error={modalError}
          imagePanel={modalKind(modal) === 'login' ? null : (
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
