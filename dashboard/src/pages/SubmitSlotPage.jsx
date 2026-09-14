import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Spinner } from '../Loader.jsx'
import { SubmitSlotFileDrop } from './SubmitSlotFileDrop.jsx'
import { bookingSourceMeta } from '../utils/bookingSource.js'

const API_BASE = typeof window !== 'undefined' && window.location.port === '3000'
  ? ''
  : (typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.host}` : '')

function formatFriendlyDate(iso) {
  if (!iso) return ''
  try {
    const d = new Date(`${iso}T12:00:00`)
    return d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  } catch { return iso }
}

function formatFriendlyTime(hhmm) {
  if (!hhmm) return ''
  // Already in 12h format? (e.g., "02:00 PM")
  if (/\d{1,2}:\d{2}\s*(AM|PM|am|pm)/i.test(hhmm)) return hhmm
  const [h, m] = hhmm.split(':').map(Number)
  if (Number.isNaN(h)) return hhmm
  const d = new Date()
  d.setHours(h, m || 0, 0, 0)
  return d.toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit', hour12: true })
}

/** Convert any time to 12-hour "hh:mm AM/PM" format */
function normalizeTo12h(val) {
  if (!val) return ''
  val = val.trim()
  // Already 12h? e.g., "02:00 PM", "2:30 pm"
  const m12 = val.match(/^(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)$/i)
  if (m12) { return `${m12[1].padStart(2,'0')}:${m12[2]} ${m12[3].toUpperCase()}` }
  // Short 12h: "2 PM"
  const ms = val.match(/^(\d{1,2})\s*(AM|PM|am|pm)$/i)
  if (ms) { return `${ms[1].padStart(2,'0')}:00 ${ms[2].toUpperCase()}` }
  // 24h: "14:00"
  const m24 = val.match(/^(\d{1,2}):(\d{2})$/)
  if (m24) {
    let h = parseInt(m24[1]), min = m24[2]
    if (h === 0) return `12:${min} AM`
    if (h < 12) return `${String(h).padStart(2,'0')}:${min} AM`
    if (h === 12) return `12:${min} PM`
    return `${String(h-12).padStart(2,'0')}:${min} PM`
  }
  return val
}

/** Convert 12h "02:00 PM" to 24h "14:00" for native inputs or submission */
function to24h(val) {
  if (!val) return ''
  val = val.trim()
  // Already 24h?
  if (/^\d{1,2}:\d{2}$/.test(val)) return val
  const m = val.match(/^(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)$/i)
  if (!m) return val
  let h = parseInt(m[1]), min = m[2], ap = m[3].toUpperCase()
  if (ap === 'AM' && h === 12) h = 0
  else if (ap === 'PM' && h !== 12) h += 12
  return `${String(h).padStart(2,'0')}:${min}`
}

function platformLabel(platform) {
  const map = { teams: 'Microsoft Teams', zoom: 'Zoom', gmail: 'Gmail', google_calendar: 'Google Calendar', barraiser: 'BarRaiser' }
  return map[platform] || platform || ''
}

/** Fix dates where AI/OCR returned wrong year (e.g. 2023 instead of 2026) */
function fixPastYear(dateStr) {
  if (!dateStr) return dateStr
  try {
    const d = new Date(dateStr + 'T00:00:00')
    const today = new Date(); today.setHours(0,0,0,0)
    const diffDays = (today - d) / (1000*60*60*24)
    if (diffDays > 7) {
      // Date is more than 7 days in the past — likely wrong year
      const corrected = new Date(today.getFullYear(), d.getMonth(), d.getDate())
      if ((today - corrected) / (1000*60*60*24) <= 7) return corrected.toISOString().slice(0,10)
      if (corrected > today) return corrected.toISOString().slice(0,10)
      // Still in past with current year, try next year
      const next = new Date(today.getFullYear() + 1, d.getMonth(), d.getDate())
      return next.toISOString().slice(0,10)
    }
    return dateStr
  } catch { return dateStr }
}

/** De-duplicate chip values case-insensitively, removing empties */
function uniqueNonEmptyTags(values) {
  const seen = new Set()
  return values.filter(Boolean).map(v => String(v).trim()).filter(v => {
    const key = v.toLowerCase()
    if (!v || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

const ROUND_OPTIONS = ['Screening', 'L1', 'L2', 'Final', 'HR']

function candidateNameKey(value) {
  return String(value || '').trim().toLocaleLowerCase().replace(/[^a-z0-9]/g, '')
}

function formatDayHeader(iso) {
  if (!iso) return ''
  try {
    const d = new Date(`${iso}T12:00:00`)
    const today = new Date()
    const tomorrow = new Date(); tomorrow.setDate(today.getDate() + 1)
    const dateStr = d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' })
    if (d.toDateString() === today.toDateString()) return `Today · ${dateStr}`
    if (d.toDateString() === tomorrow.toDateString()) return `Tomorrow · ${dateStr}`
    return d.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'short', year: 'numeric' })
  } catch { return iso }
}

function groupSlotsByDate(slots) {
  const groups = new Map()
  for (const slot of slots) {
    const key = (slot.date || '').slice(0, 10)
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(slot)
  }
  return [...groups.entries()].map(([date, items]) => ({ date, items }))
}

function dedupeCandidates(rows) {
  const byName = new Map()
  for (const row of rows || []) {
    const name = String(row?.name || '').trim()
    const key = candidateNameKey(name)
    if (!name || !key) continue
    const current = byName.get(key)
    if (!current || (current.name === current.name.toUpperCase() && name !== name.toUpperCase())) {
      byName.set(key, { ...row, name })
    }
  }
  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name, 'en', { sensitivity: 'base' }))
}

/** Technology for a round-wise booking.
 *
 * /bookings/confirm refuses a round-wise booking without one, and the invite
 * only sometimes names it, so it has to be askable. The vocabulary is whatever
 * the roster already uses, but anything can be typed — a booking must never be
 * blocked because a technology is missing from a list.
 */
function SlotTechnologyPicker({ options, value, onChange, disabled }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const query = value.trim().toLocaleLowerCase()
  const matches = useMemo(
    () => options.filter(t => t.toLocaleLowerCase().includes(query)),
    [options, query],
  )
  useEffect(() => {
    function close(e) { if (!rootRef.current?.contains(e.target)) setOpen(false) }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [])
  return (
    <div ref={rootRef} className="sbs-picker">
      <div className="sbs-picker__input-wrap">
        <svg className="sbs-picker__icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M8 18 3 12l5-6M16 6l5 6-5 6" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
        <input className="sbs-input sbs-name-input" value={value}
          onChange={e => { onChange(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          placeholder="Choose or type the technology" disabled={disabled}
          aria-autocomplete="list" aria-expanded={open} aria-controls="sbs-technology-options" />
        <button type="button" className="sbs-picker__toggle" onClick={() => setOpen(v => !v)} disabled={disabled} aria-label="Show technologies" aria-expanded={open}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M6 9l6 6 6-6"/></svg>
        </button>
      </div>
      {open && (
        <div id="sbs-technology-options" className="sbs-picker__menu" role="listbox">
          {matches.length ? matches.map(t => (
            <button key={t} type="button" role="option"
              aria-selected={t.toLocaleLowerCase() === query}
              className="sbs-picker__option"
              onClick={() => { onChange(t); setOpen(false) }}>{t}</button>
          )) : <p className="sbs-picker__empty">Type the technology to continue.</p>}
        </div>
      )}
    </div>
  )
}

function SlotCandidatePicker({ candidates, value, onChange, disabled }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const options = useMemo(() => dedupeCandidates(candidates), [candidates])
  const query = value.trim().toLocaleLowerCase()
  const matches = useMemo(() => options.filter(c => c.name.toLocaleLowerCase().includes(query)), [options, query])
  useEffect(() => {
    function close(e) { if (!rootRef.current?.contains(e.target)) setOpen(false) }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [])
  return (
    <div ref={rootRef} className="sbs-picker">
      <div className="sbs-picker__input-wrap">
        <svg className="sbs-picker__icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/>
        </svg>
        <input className="sbs-input sbs-name-input" value={value}
          onChange={e => { onChange(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          placeholder="Choose or type your name" disabled={disabled}
          autoComplete="name" aria-autocomplete="list" aria-expanded={open} aria-controls="sbs-candidate-options" />
        <button type="button" className="sbs-picker__toggle" onClick={() => setOpen(v => !v)} disabled={disabled} aria-label="Show names" aria-expanded={open}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M6 9l6 6 6-6"/></svg>
        </button>
      </div>
      {open && (
        <div id="sbs-candidate-options" className="sbs-picker__menu" role="listbox">
          {matches.length ? matches.map(c => (
            <button key={candidateNameKey(c.name)} type="button" role="option"
              aria-selected={c.name.toLocaleLowerCase() === query}
              className="sbs-picker__option"
              onClick={() => { onChange(c.name); setOpen(false) }}>{c.name}</button>
          )) : <p className="sbs-picker__empty">Type a new name to continue.</p>}
        </div>
      )}
    </div>
  )
}

function formatFriendlyShortDate(iso) {
  if (!iso) return ''
  try {
    const d = new Date(`${iso}T12:00:00`)
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
  } catch { return iso }
}

/** Compact payment result row — single line: ✓ ₹2,000 | UTR xxxx | PhonePe | 18 Aug */
function PaymentAiResultCard({ ai }) {
  if (!ai) return null
  const verified = ai.verified
  const amount = ai.amount ? `₹${Number(ai.amount).toLocaleString('en-IN')}` : null
  const utr = ai.utr_number || ai.reference_number || (ai.transaction_id ? String(ai.transaction_id).slice(-8) : null)
  const app = ai.payment_app || 'PhonePe'
  const date = ai.payment_date ? formatFriendlyShortDate(ai.payment_date) : ''
  const status = ai.status || 'unknown'
  const isOk = verified || status === 'success'

  const parts = [
    amount,
    utr ? `UTR ${utr}` : null,
    app,
    date
  ].filter(Boolean)

  return (
    <div className={`sbs-pay-item ${isOk ? 'sbs-pay-item--ok' : 'sbs-pay-item--warn'}`}>
      <span className="sbs-pay-item__icon">{isOk ? '✓' : '⚠'}</span>
      <span className="sbs-pay-item__text">{parts.join(' | ')}</span>
    </div>
  )
}

/** Bring a field into view and put the cursor in it.
 *
 * `block: 'center'` rather than the default: on a phone the sticky Confirm bar
 * covers the bottom of the form, and scrolling a field just into view can leave
 * it underneath. `preventScroll` on the focus stops the browser undoing the
 * smooth scroll with a jump of its own.
 */
/** Read an API response without assuming it is JSON.
 *
 * A 502 from the proxy arrives as HTML and a 413 arrives with no body at all;
 * `res.json()` throws on both, and the caller's catch then reported those as a
 * network fault. This carries the status into the message instead of inventing
 * a cause for it.
 */
async function readApiResponse(response) {
  // Try to parse first and ask what it was afterwards. Keying off the declared
  // content-type instead turned every response without that header into an
  // empty object -- the body was there and readable, and the caller got {}.
  //
  // A body can only be read once, so keep a clone for the failure path where
  // one is available; stubs and older environments simply lose the excerpt.
  const spare = typeof response.clone === 'function' ? response.clone() : null
  try {
    return await response.json()
  } catch {
    // Not JSON: a proxy error page, most often.
  }
  let text = ''
  try {
    text = await (spare || response).text()
  } catch {
    text = ''
  }
  if (response.ok) return {}
  const detail = text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 120)
  throw new Error(`The server returned ${response.status}.${detail ? ` ${detail}` : ''}`)
}

function focusField(ref) {
  const node = ref?.current
  if (!node) return
  // Both calls are optional. jsdom implements neither, and a container element
  // is not focusable at all -- neither is a reason for a submit to blow up, so
  // the field simply does not move rather than the click failing.
  if (typeof node.scrollIntoView === 'function') {
    try {
      node.scrollIntoView({ behavior: 'smooth', block: 'center' })
    } catch {
      /* older engines reject the options object; position is cosmetic */
    }
  }
  if (typeof node.focus === 'function') {
    try {
      node.focus({ preventScroll: true })
    } catch {
      /* preventScroll is not universal, and focus is a convenience here */
    }
  }
}

export function SubmitSlotPage() {
  const [tab, setTab] = useState('book')
  const [candidates, setCandidates] = useState([])
  const [booked, setBooked] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [parsing, setParsing] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [name, setName] = useState('')
  const [roundWisePhone, setRoundWisePhone] = useState('')
  const [parsedSlot, setParsedSlot] = useState(null)
  const [slotFile, setSlotFile] = useState(null)
  const [slotPreview, setSlotPreview] = useState('')
  // A fee paid in instalments produces one proof per transfer, so the booking
  // carries a list of proof ids rather than a single one.
  const [paymentProofIds, setPaymentProofIds] = useState([])
  const [paymentFiles, setPaymentFiles] = useState([])
  const [paymentTotals, setPaymentTotals] = useState(null)
  const [paymentRequirement, setPaymentRequirement] = useState(null)
  const [sessionFile, setSessionFile] = useState(null)
  const [sessionPreview, setSessionPreview] = useState('')
  const [manualDate, setManualDate] = useState('')
  const [manualTime, setManualTime] = useState('')
  const [interviewRound, setInterviewRound] = useState('')
  const [technology, setTechnology] = useState('')
  const [serviceType, setServiceType] = useState('profile_service')
  const [showServiceDrop, setShowServiceDrop] = useState(false)
  // Which one field the form is currently asking for, not merely that a
  // submit was attempted. The boolean lit every hint at once, so an empty form
  // answered a single click with six warnings and a global alert on top --
  // "show only the first error" and "no duplicate global + field error" are the
  // same fix: name the field, and say it in one place.
  const [missingField, setMissingField] = useState('')
  const [aiExtraction, setAiExtraction] = useState(null)
  const [aiBlocked, setAiBlocked] = useState('')
  // One ref per field the validation sequence can stop at, so the first
  // problem can be brought into view instead of being highlighted somewhere
  // off screen.
  const nameRef = useRef(null)
  // Client name renders as a text input for round-wise and a picker for
  // profile service, so the wrapper is what the sequence scrolls to; nameRef
  // is only for putting the cursor in the box when that box exists.
  const nameFieldRef = useRef(null)
  const phoneRef = useRef(null)
  const technologyRef = useRef(null)
  const roundRef = useRef(null)
  const paymentRef = useRef(null)
  const inviteRef = useRef(null)
  const [userEditedFields, setUserEditedFields] = useState({})
  const [paymentAiResults, setPaymentAiResults] = useState([])
  const [paymentRejected, setPaymentRejected] = useState([])
  const [paymentAnalysing, setPaymentAnalysing] = useState(false)

  const effectiveName = name.trim()
  const selected = useMemo(() => {
    if (!effectiveName) return null
    const key = effectiveName.toLowerCase()
    return dedupeCandidates(candidates).find(c => c.name.toLowerCase() === key) || null
  }, [effectiveName, candidates])

  // Suggestions only — the roster's existing technologies, offered so the common
  // ones are one tap away. Anything else can still be typed.
  const technologyOptions = useMemo(() => {
    const seen = new Map()
    for (const row of candidates || []) {
      const value = String(row?.technology || '').trim()
      if (!value || value.toLowerCase() === 'unspecified') continue
      if (!seen.has(value.toLowerCase())) seen.set(value.toLowerCase(), value)
    }
    return [...seen.values()].sort((a, b) => a.localeCompare(b, 'en', { sensitivity: 'base' }))
  }, [candidates])

  const effectiveTechnology = (technology || parsedSlot?.technology || '').trim()

  const bookingSlot = useMemo(() => {
    const effectiveDate = manualDate || parsedSlot?.date || ''
    const effectiveTime = manualTime || parsedSlot?.time || ''
    const effectiveEnd = parsedSlot?.time_end || ''
    if (effectiveDate && effectiveTime) return { ...parsedSlot, date: effectiveDate, time: to24h(effectiveTime), time_end: to24h(effectiveEnd), interview_round: interviewRound }
    return null
  }, [parsedSlot, manualDate, manualTime, interviewRound])

  const effectiveBookingDate = manualDate || parsedSlot?.date || ''
  const isPastDate = (() => {
    if (!effectiveBookingDate) return false
    const today = new Date(); today.setHours(0,0,0,0)
    const d = new Date(effectiveBookingDate + 'T00:00:00'); d.setHours(0,0,0,0)
    return d < today
  })()

  const showManualSlotFields = Boolean(
    slotFile && !parsing && (!aiExtraction || aiExtraction.manual_fields_required || aiExtraction.confidence_score < 70 || isPastDate)
  )
  // Uploading a proof is no longer the same as having paid: instalments only
  // clear the fee once they add up, and the server decides when they do.
  const paymentComplete = Boolean(paymentProofIds.length && paymentTotals?.payment_complete)
  // Round-wise candidates are typed in and need not exist on the profile
  // roster, so there is no roster row to read a balance from. Reading one
  // anyway is what hid the payment card for every new round-wise candidate.
  // The backend rule answers instead — and a lookup that fails must not hide
  // the payment step, because round-wise always owes the round tariff unless a
  // Re-Service grant waives it.
  const roundWise = serviceType === 'round_wise'
  const paymentRequired = roundWise
    ? (paymentRequirement?.payment_required ?? true)
    : Boolean(selected?.needs_payment_proof)
  // Never computed here — every figure comes from the backend.
  const paymentAmountDue = roundWise
    ? (paymentRequirement?.amount_due ?? paymentTotals?.amount_due ?? null)
    : (selected?.balance_due || 0)
  // Round-wise has no fixed price. The backend tariff is only the floor --
  // payment_complete clears once verified payments reach it -- and the final
  // amount is agreed between the referrer and the client, so presenting the
  // tariff as the amount owed told candidates a number that was often wrong.
  const roundWiseMinimum = roundWise && paymentAmountDue > 0
    ? `₹${paymentAmountDue.toLocaleString('en-IN')}`
    : null
  const needsPaymentProof = Boolean(paymentRequired && !paymentComplete)

  // Asked of the backend, never derived here. The Re-Service waiver depends on
  // who is booking and for which round, so it is re-asked as those change.
  useEffect(() => {
    if (!roundWise) { setPaymentRequirement(null); return undefined }
    // Identity changes invalidate an earlier answer immediately. In
    // particular, a previous candidate's Re-Service waiver must never remain
    // visible while the new requirement is being fetched.
    setPaymentRequirement(null)
    let cancelled = false
    const timer = setTimeout(async () => {
      try {
        const params = new URLSearchParams({
          service_type: 'round_wise',
          name: effectiveName,
          phone: roundWisePhone.trim(),
          candidate_id: selected?.id || '',
          interview_round: interviewRound,
        })
        let res = await fetch(`${API_BASE}/public/slots/payment-requirement?${params}`, { cache: 'no-store' })
        // Supports a rolling release where the new frontend arrives before the
        // canonical endpoint. The compatibility route delegates to the same
        // backend authority once both versions are present.
        if (!res.ok) {
          res = await fetch(`${API_BASE}/public/slots/payment-info?${params}`, { cache: 'no-store' })
        }
        const data = await res.json()
        if (!cancelled && data.status === 'ok') {
          setPaymentRequirement({
            ...data,
            payment_required: data.payment_required ?? data.needs_payment,
            re_service: data.re_service ?? data.waived,
          })
        }
      } catch { /* keep whatever is known; the step stays reachable either way */ }
    }, 250)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [roundWise, effectiveName, roundWisePhone, interviewRound, selected?.id])

  const resetPaymentProofs = useCallback(() => {
    setPaymentProofIds([])
    setPaymentFiles([])
    setPaymentTotals(null)
    setPaymentAiResults([])
    setPaymentRejected([])
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [cRes, bRes] = await Promise.all([
        fetch(`${API_BASE}/public/slots/candidates`, { cache: 'no-store' }),
        fetch(`${API_BASE}/public/slots/booked`, { cache: 'no-store' }),
      ])
      const cData = await cRes.json()
      const bData = await bRes.json()
      if (cData.status === 'ok') setCandidates(dedupeCandidates(cData.candidates || []))
      if (bData.status === 'ok') setBooked(bData.slots || [])
    } catch { setError('Could not load — check your connection.') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => () => {
    if (slotPreview) URL.revokeObjectURL(slotPreview)
    if (sessionPreview) URL.revokeObjectURL(sessionPreview)
  }, [slotPreview, sessionPreview])

  async function parseScreenshot(file) {
    if (!file) { setParsedSlot(null); setAiExtraction(null); setAiBlocked(''); return }
    setParsing(true); setError(''); setSuccess(''); setAiExtraction(null); setAiBlocked('')
    try {
      // Try AI extraction first
      const fd = new FormData(); fd.append('file', file)
      const res = await fetch(`${API_BASE}/public/slots/extract-invite-ai`, { method: 'POST', body: fd })
      const data = await res.json()

      if (res.ok && data.status === 'ok' && data.data) {
        const ext = data.data
        setAiExtraction(ext)

        // Check if it's a payment screenshot
        if (ext.is_payment_screenshot) {
          setAiBlocked('This looks like a payment screenshot. Please upload the interview invite screenshot here.')
          setParsedSlot(null)
          setParsing(false)
          return
        }
        // Check if it doesn't look like an invite
        if (ext.looks_like_interview_invite === false) {
          setAiBlocked('This image does not look like an interview invite.')
          setParsedSlot(null)
          setParsing(false)
          return
        }

        // Auto-fill fields (only if user hasn't manually edited them)
        const slot = {}
        const fixedDate = fixPastYear(ext.interview_date)
        if (fixedDate && !userEditedFields.date) slot.date = fixedDate
        if ((ext.start_time || ext.time) && !userEditedFields.time) slot.time = normalizeTo12h(ext.start_time || ext.time)
        if ((ext.end_time || ext.time_end) && !userEditedFields.time_end) slot.time_end = normalizeTo12h(ext.end_time || ext.time_end)
        if (ext.meeting_platform) slot.platform = ext.meeting_platform
        if (ext.technology) slot.technology = ext.technology
        if (ext.technology && !technology && !userEditedFields.technology) {
          setTechnology(ext.technology)
        }
        if (ext.interview_round && !interviewRound && !userEditedFields.round) {
          setInterviewRound(ext.interview_round)
          slot.interview_round = ext.interview_round
        }

        console.log('[Invite extraction]', { raw: ext, mapped: slot })
        setParsedSlot(slot)
        if (!userEditedFields.date) setManualDate(fixedDate || '')
        if (!userEditedFields.time) setManualTime(normalizeTo12h(ext.start_time || ext.time || ''))
        setParsing(false)
        return
      }
    } catch (e) {
      console.warn('AI extraction failed, falling back to OCR:', e)
    }

    // Fallback to existing OCR endpoint
    try {
      const fd2 = new FormData(); fd2.append('file', file)
      const res2 = await fetch(`${API_BASE}/public/slots/parse-screenshot`, { method: 'POST', body: fd2 })
      const data2 = await res2.json()
      if (!res2.ok) { setParsedSlot(null); setError('Auto-read failed — enter date & time manually.'); return }
      const slot = data2.slot || null
      // Normalize all times to 12h format and fix wrong year
      if (slot) {
        if (slot.date) slot.date = fixPastYear(slot.date)
        if (slot.time) slot.time = normalizeTo12h(slot.time)
        if (slot.time_end) slot.time_end = normalizeTo12h(slot.time_end)
      }
      setParsedSlot(slot)
      if (!interviewRound) setInterviewRound(slot?.interview_round || '')
      setManualDate(''); setManualTime('')
    } catch { setParsedSlot(null); setError('Network error while reading screenshot') }
    finally { setParsing(false) }
  }

  async function onSlotFileChange(file) {
    if (slotPreview) URL.revokeObjectURL(slotPreview)
    setSlotFile(file || null); setParsedSlot(null); setManualDate(''); setManualTime(''); setSuccess(''); setAiExtraction(null); setAiBlocked(''); setUserEditedFields({})
    if (file) { setSlotPreview(URL.createObjectURL(file)); await parseScreenshot(file) }
    else setSlotPreview('')
  }

  async function onSessionFileChange(file) {
    if (sessionPreview) URL.revokeObjectURL(sessionPreview)
    setSessionFile(file || null)
    if (file) setSessionPreview(URL.createObjectURL(file)); else setSessionPreview('')
  }

  async function uploadPaymentProof() {
    if (!effectiveName || !paymentFiles.length) { setError('Enter your name and attach at least one payment screenshot first.'); return }
    setBusy(true); setError(''); setSuccess(''); setPaymentRejected([]); setPaymentAnalysing(true)
    try {
      const fd = new FormData()
      fd.append('name', effectiveName)
      // The proof is stored under the booking it belongs to. Sending only the
      // name filed every round-wise receipt as profile service, and
      // /bookings/confirm — which looks the proof up in the round-wise context,
      // by candidate id or phone — could then never find it.
      fd.append('service_type', serviceType)
      fd.append('candidate_id', selected?.id || '')
      if (roundWise) fd.append('phone', roundWisePhone.trim())
      if (effectiveTechnology) fd.append('technology', effectiveTechnology)
      if (interviewRound) fd.append('interview_round', interviewRound)
      // Every screenshot goes in one request; ids already saved are sent back
      // so instalments uploaded across several attempts still add together.
      paymentFiles.forEach(f => fd.append('files', f))
      fd.append('existing_proof_ids', paymentProofIds.join(','))
      const res = await fetch(`${API_BASE}/public/slots/payment-proof`, { method: 'POST', body: fd })
      // Not res.json(): a 502 from the proxy arrives as HTML, and parsing it
      // threw into the bare catch below, which called it a network fault. That
      // is the same misreport that hid a working booking on the confirm path.
      const data = await readApiResponse(res)
      const rejected = data.rejected || []
      setPaymentRejected(rejected)
      if (!res.ok) {
        // The per-screenshot reasons are already listed against the files they
        // belong to. Repeating one of them as a page-level alert showed the
        // same sentence twice on one screen, once with a filename and once
        // without, which reads as two problems.
        const duplicated = rejected.some(item => item.message && item.message === data.message)
        if (!rejected.length || !duplicated) setError(data.message || 'Payment upload failed')
        return
      }
      setPaymentProofIds(data.proof_ids || [])
      setPaymentTotals(data)
      setPaymentFiles([])
      // Every accepted screenshot keeps the AI reading the backend already ran.
      setPaymentAiResults([
        ...paymentAiResults,
        ...(data.ai_extractions || []).filter(ai => ai && ai.is_payment_screenshot),
      ])
      if (data.payment_complete) setSuccess('Payment proof saved — you can confirm your slot.')
    } catch (err) {
      // Only a fetch that never got an answer is a network fault. Anything
      // else says what it was, rather than sending the payer to check their
      // connection over a receipt the server actually refused.
      setError(err instanceof TypeError ? 'Network error — try again' : (err?.message || 'Payment upload failed'))
    }
    finally { setBusy(false); setPaymentAnalysing(false) }
  }

  // Clear the message the moment that field is satisfied, rather than leaving
  // it up until the next Confirm. Otherwise the form still says "enter the
  // client name" while the name is being typed into the box beside it.
  useEffect(() => {
    if (!missingField) return
    const satisfied = {
      name: !!effectiveName,
      phone: serviceType !== 'round_wise' || !!roundWisePhone.trim(),
      technology: serviceType !== 'round_wise' || !!effectiveTechnology,
      round: !!interviewRound,
      payment: !needsPaymentProof,
      invite: !!slotFile,
    }[missingField]
    if (satisfied) setMissingField('')
  }, [missingField, effectiveName, serviceType, roundWisePhone, effectiveTechnology,
      interviewRound, needsPaymentProof, slotFile])

  async function submitBook(ev) {
    ev.preventDefault()
    if (parsing) {
      setError('Please wait until invite reading is complete.')
      return
    }
    // Validate down the form, and stop at the first thing that is missing.
    //
    // This was one OR across every rule: it flagged all of them at once, said
    // nothing about any of them, and left the browser to pick where to go --
    // which meant Interview round, the only control carrying `required`, even
    // with the three fields above it empty. Being told to select a round while
    // the name box is blank is not an explanation of what to do next.
    //
    // The rules themselves are unchanged; they are only ordered and reported
    // one at a time. Payment sits after the candidate details deliberately:
    // what is owed depends on the service type, the round and the candidate,
    // so validating it earlier would judge an amount that is not settled yet.
    const steps = [
      { key: 'name', ref: nameRef, fallbackRef: nameFieldRef, ok: !!effectiveName },
      { key: 'phone', ref: phoneRef,
        ok: serviceType !== 'round_wise' || !!roundWisePhone.trim() },
      { key: 'technology', ref: technologyRef,
        ok: serviceType !== 'round_wise' || !!effectiveTechnology },
      { key: 'round', ref: roundRef, ok: !!interviewRound },
      { key: 'payment', ref: paymentRef, ok: !needsPaymentProof },
      { key: 'invite', ref: inviteRef, ok: !!slotFile },
    ]
    const firstMissing = steps.find(step => !step.ok)
    if (firstMissing) {
      // The message goes beside the field, and nowhere else. Putting it in the
      // page-level alert as well said the same thing twice on one screen, once
      // with the field in front of it and once without.
      setMissingField(firstMissing.key)
      setError('')
      focusField(firstMissing.ref?.current ? firstMissing.ref : firstMissing.fallbackRef || firstMissing.ref)
      return
    }
    setMissingField('')
    if (isPastDate) {
      setError('Interview date is in the past. Please select today or a future date.')
      return
    }
    setBusy(true); setError(''); setSuccess('')
    // Whether the server accepted the booking, so a fault in the code that runs
    // afterwards cannot be reported as a booking failure.
    let booked = false
    try {
      const fd = new FormData()
      fd.append('name', effectiveName)
      fd.append('service_type', serviceType)
      if (bookingSlot?.date) fd.append('date', bookingSlot.date)
      if (bookingSlot?.time) fd.append('time', bookingSlot.time)
      if (bookingSlot?.time_end) fd.append('time_end', bookingSlot.time_end)
      if (bookingSlot?.interview_round) fd.append('interview_round', bookingSlot.interview_round)
      if (effectiveTechnology) fd.append('technology', effectiveTechnology)
      if (serviceType === 'round_wise') fd.append('phone', roundWisePhone.trim())
      fd.append('candidate_id', selected?.id || '')
      if (paymentProofIds.length) fd.append('payment_proof_ids', paymentProofIds.join(','))
      // Confirmation must be idempotent: a retry or double submit has to resolve
      // to the same booking rather than creating a second one.
      fd.append(
        'idempotency_key',
        [
          effectiveName.trim().toLowerCase(),
          serviceType,
          roundWisePhone.trim(),
          bookingSlot?.date || '',
          bookingSlot?.time || '',
          bookingSlot?.time_end || '',
          bookingSlot?.interview_round || '',
          paymentProofIds.join(','),
        ].join('|'),
      )
      fd.append('file', slotFile)
      // /public/slots/book is retired and answers 410. /bookings/confirm is the
      // only public booking creation boundary.
      // `readApiResponse` rather than `res.json()`: a 502 from the proxy
      // arrives as HTML and `res.json()` throws on it, which the catch below
      // then reported as a network fault. It carries the status into the
      // message instead of inventing a cause.
      const res = await fetch(`${API_BASE}/bookings/confirm`, { method: 'POST', body: fd })
      const data = await readApiResponse(res)
      if (!res.ok) { setError(data.payment_due ? (data.message || 'Payment required.') : (data.message || 'Could not book slot')); return }
      booked = true
      if (slotPreview) URL.revokeObjectURL(slotPreview)
      setSlotFile(null); setSlotPreview(''); setParsedSlot(null); setManualDate(''); setManualTime(''); setInterviewRound(''); setTechnology(''); setServiceType('profile_service'); resetPaymentProofs()
      setName(''); setRoundWisePhone('')
      setMissingField('')
      setSuccess(`Slot confirmed for ${data.candidate?.name || effectiveName}.`)
      // Refresh data first, then switch to confirmed tab after 2 seconds
      await refresh()
      setTimeout(() => { setTab('confirmed'); setSuccess('') }, 2000)
    } catch (err) {
      // Only a fetch that never got an answer is a network error. Everything
      // else -- a body that would not parse, a fault in this handler -- used to
      // be reported as one, which is how a ReferenceError on a line after the
      // booking succeeded came out as "Network error — try again" while the
      // slot existed on the server and the message asked for it again.
      if (booked) {
        setSuccess(`Slot confirmed for ${effectiveName}.`)
        setError('')
      } else if (err instanceof TypeError) {
        setError('Network error — try again')
      } else {
        setError(err?.message || 'Could not book slot')
      }
    }
    finally { setBusy(false) }
  }

  const TrustBadges = () => (
    <div className="sbs-trust-compact">
      <span>🔒 Secure &amp; Private</span>
      <span className="sbs-trust-dot">•</span>
      <span>⚡ Smart Detection</span>
      <span className="sbs-trust-dot">•</span>
      <span>✓ Instant Confirmation</span>
    </div>
  )

  return (
    <div className="sbs-screen">
      <div className="sbs-glow" aria-hidden="true" />
      <div className="sbs-card">
        <header className="sbs-header">
          <div className="sbs-header__text">
            <h1 className="sbs-header__title">Book Interview Slot</h1>
            <p className="sbs-header__sub">Pick the right slot, upload invite, and confirm.</p>
          </div>
          <div className="sbs-header__icon" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
              <rect x="3" y="4" width="18" height="18" rx="3"/>
              <path d="M16 2v4M8 2v4M3 10h18" strokeLinecap="round"/>
            </svg>
          </div>
        </header>

        <div className="sbs-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === 'book'}
            className={`sbs-tab${tab === 'book' ? ' sbs-tab--active' : ''}`}
            onClick={() => { setTab('book'); setError(''); setSuccess('') }}>
            Book slot
          </button>
          <button type="button" role="tab" aria-selected={tab === 'confirmed'}
            className={`sbs-tab${tab === 'confirmed' ? ' sbs-tab--active' : ''}`}
            onClick={() => { setTab('confirmed'); setError(''); setSuccess('') }}>
            Confirmed slots
          </button>
        </div>

        {loading ? (
          <div className="sbs-loading"><Spinner size={28} /></div>
        ) : tab === 'confirmed' ? (
          /* ── Confirmed slots tab ─────────────────────────── */
          <div className="sbs-body">
            <section className="sbs-section">
              <div className="sbs-step-head">
                <div>
                  <h2 className="sbs-step-title">Confirmed upcoming slots</h2>
                  <p className="sbs-step-sub">{booked.length > 0 ? `${booked.length} interview${booked.length !== 1 ? 's' : ''} scheduled` : 'No confirmed slots yet.'}</p>
                </div>
              </div>
              {booked.length === 0 ? (
                <div className="sbs-confirmed-empty">
                  <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.25" opacity="0.3">
                    <rect x="3" y="4" width="18" height="18" rx="3"/><path d="M16 2v4M8 2v4M3 10h18" strokeLinecap="round"/>
                  </svg>
                  <p>No confirmed slots yet. Book your first slot.</p>
                  <button type="button" className="sbs-cta sbs-cta--ready" style={{maxWidth:'200px'}}
                    onClick={() => { setTab('book'); setError(''); setSuccess('') }}>
                    Book a slot
                  </button>
                </div>
              ) : (
                <div className="sbs-slot-list">
                  {groupSlotsByDate(booked).map(({ date, items }) => (
                    <div key={date} className="sbs-date-group">
                      <div className="sbs-date-group__header">
                        <span className="sbs-date-group__label">{formatDayHeader(date)}</span>
                        <span className="sbs-date-group__count">{items.length} slot{items.length !== 1 ? 's' : ''}</span>
                      </div>
                      <div className="sbs-date-group__cards">
                        {items.map((slot, i) => (
                          <div key={i} className="sbs-confirmed-card">
                            <div className="sbs-slot-card__icon sbs-slot-card__icon--active">
                              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
                                <rect x="3" y="4" width="18" height="18" rx="3"/>
                                <path d="M16 2v4M8 2v4M3 10h18" strokeLinecap="round"/>
                              </svg>
                            </div>
                            <div className="sbs-slot-card__body">
                              <div className="sbs-slot-card__name">{slot.name}</div>
                              <div className="sbs-slot-card__time">
                                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2" strokeLinecap="round"/></svg>
                                <span>{formatFriendlyTime(slot.time)}{slot.time_end ? ` – ${formatFriendlyTime(slot.time_end)}` : ''}</span>
                              </div>
                            </div>
                            <div className="sbs-confirmed-card__right">
                              {slot.interview_round && <span className={`sbs-slot-card__round sbs-slot-card__round--${(slot.interview_round || '').toLowerCase().replace(/\s+/g, '')}`}>{slot.interview_round}</span>}
                              <span className="sbs-confirmed-card__status">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M20 6L9 17l-5-5"/></svg>
                                Booked
                              </span>
                              {(() => {
                                const meta = bookingSourceMeta(slot.interview_booking_source)
                                return (
                                  <span
                                    className={`sbs-source-badge sbs-source-badge--${meta.tone}`}
                                    title={meta.title}
                                  >
                                    {meta.label}
                                  </span>
                                )
                              })()}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
            <TrustBadges />
          </div>
        ) : (
          /* ── Book slot tab — direct booking form only ─────── */
          <div className="sbs-body">
            {/* noValidate: the browser validated only the one control that
                  carried `required` -- Interview round -- so it jumped
                  there past three empty fields above it and said
                  "Please select an item in the list.", which names
                  neither the field nor what to do. Every rule here is
                  the application's. */}
            <form className="sbs-form" noValidate onSubmit={submitBook}>
              <div className="sbs-field">
                <span className="sbs-label">Service type</span>
                <div className="sbs-select-wrap sbs-select-wrap--custom">
                  <button type="button" className="sbs-select sbs-select--custom" onClick={() => setShowServiceDrop(v => !v)} disabled={busy || parsing}>
                    <span>{serviceType === "round_wise" ? "Round-wise" : "Profile service"}</span>
                    <svg className="sbs-select__arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 9l6 6 6-6"/></svg>
                  </button>
                  {showServiceDrop && (
                    <ul className="sbs-dropdown">
                      <li className={`sbs-dropdown__item${serviceType === "round_wise" ? " sbs-dropdown__item--active" : ""}`} onMouseDown={e => e.preventDefault()} onClick={e => { e.stopPropagation(); setServiceType("round_wise"); setShowServiceDrop(false); setName(""); setTechnology(""); resetPaymentProofs(); }}>Round-wise</li>
                      <li className={`sbs-dropdown__item${serviceType === "profile_service" ? " sbs-dropdown__item--active" : ""}`} onMouseDown={e => e.preventDefault()} onClick={e => { e.stopPropagation(); setServiceType("profile_service"); setShowServiceDrop(false); setName(""); setTechnology(""); resetPaymentProofs(); }}>Profile service</li>
                    </ul>
                  )}
                </div>
              </div>

              <label ref={nameFieldRef} className="sbs-field">
                <span className="sbs-label">Client name</span>
                {serviceType === "round_wise" ? (
                  <input ref={nameRef} className="sbs-input" type="text" value={name} onChange={e => { setName(e.target.value); resetPaymentProofs(); }} placeholder="Type client name" disabled={busy || parsing} />
                ) : (
                  <SlotCandidatePicker candidates={candidates} value={name} onChange={v => { setName(v); resetPaymentProofs() }} disabled={busy || parsing} />
                )}
                {missingField === 'name'
                  ? <span className="sbs-hint sbs-hint--warn" role="alert">Enter the client name for this round.</span>
                  : <span className="sbs-hint">{serviceType === "round_wise" ? "Type the client name for this round." : "Pick from the list or type a new client name."}</span>}
              </label>

              {serviceType === "round_wise" && (
                // /bookings/confirm rejects a round-wise booking without a valid
                // phone identity, so it has to be collected here.
                <label ref={phoneRef} className="sbs-field">
                  <span className="sbs-label">Candidate phone <span className="sbs-required" aria-hidden="true">*</span></span>
                  <input
                    className="sbs-input"
                    type="tel"
                    inputMode="tel"
                    value={roundWisePhone}
                    // The phone is the round-wise proof's identity, so changing
                    // it makes a proof already on file belong to someone else.
                    onChange={e => { setRoundWisePhone(e.target.value); resetPaymentProofs() }}
                    placeholder="10-digit phone number"
                    disabled={busy || parsing}
                  />
                  {missingField === 'phone'
                    ? <span className="sbs-hint sbs-hint--warn" role="alert">Enter the candidate phone number.</span>
                    : <span className="sbs-hint">Identifies the candidate across rounds.</span>}
                </label>
              )}

              {serviceType === "round_wise" && (
                // /bookings/confirm rejects a round-wise booking without a
                // technology and tells the candidate to select one, so there
                // has to be somewhere to select it. The invite fills it in when
                // it names the technology; often it does not.
                <label ref={technologyRef} className="sbs-field">
                  <span className="sbs-label">Technology <span className="sbs-required" aria-hidden="true">*</span></span>
                  <SlotTechnologyPicker
                    options={technologyOptions}
                    value={technology}
                    onChange={value => { setTechnology(value); setUserEditedFields(prev => ({ ...prev, technology: true })) }}
                    disabled={busy || parsing}
                  />
                  {missingField === 'technology'
                    ? <span className="sbs-hint sbs-hint--warn" role="alert">Choose the technology for this interview.</span>
                    : <span className="sbs-hint">Read from the invite when it says; otherwise pick or type it.</span>}
                </label>
              )}

              <label className="sbs-field">
                <span className="sbs-label">Interview round <span className="sbs-required" aria-hidden="true">*</span></span>
                <div className={`sbs-select-wrap${missingField === 'round' ? ' sbs-select-wrap--required' : ''}`}>
                  <select ref={roundRef} className="sbs-select" value={interviewRound} onChange={e => setInterviewRound(e.target.value)} disabled={busy || parsing}>
                    <option value="">Select round (L1, L2…)</option>
                    {ROUND_OPTIONS.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </div>
                {missingField === 'round' && <span className="sbs-hint sbs-hint--warn" role="alert">Choose the interview round.</span>}
              </label>

              {/* Amber only when something is actually wrong: a screenshot was
                  refused, or the sequence is asking for one. In every other
                  state this is an ordinary field on the form. */}
              {paymentRequired && (
                <div className={`sbs-pay-card${paymentRejected.length || missingField === 'payment' ? ' sbs-pay-card--warn' : ''}`}>
                  {/* One row carries the whole control surface: what is owed,
                      and the action for it. Save used to sit below as a
                      full-width block that was present and disabled even with
                      nothing to save, which is most of the time. */}
                  <div ref={paymentRef} className="sbs-pay-head">
                    <span>{roundWise ? 'Payment' : 'Payment due'}</span>
                    <span className="sbs-pay-head__end">
                      {/* A profile balance is what this candidate actually owes,
                          so it keeps its figure. */}
                      {!roundWise && (
                        <strong>{paymentAmountDue == null ? '—' : `₹${paymentAmountDue.toLocaleString('en-IN')}`}</strong>
                      )}
                      {/* Short on screen because it sits in a narrow header beside
                          the amount; the full name is kept for assistive tech,
                          where "Save" on its own says too little. */}
                      {!paymentComplete && paymentFiles.length > 0 && (
                        <button type="button" className="sbs-secondary-btn sbs-pay-save" aria-label={`Save payment proof${paymentFiles.length > 1 ? 's' : ''}`} disabled={busy || parsing || paymentAnalysing} onClick={uploadPaymentProof}>
                          {paymentAnalysing
                            ? <><Spinner size={12} />&nbsp;Analysing…</>
                            : `Save${paymentFiles.length > 1 ? ` ${paymentFiles.length}` : ''}`}
                        </button>
                      )}
                    </span>
                  </div>
                  {roundWise && (
                    <p className="sbs-pay-note">
                      {`Payment amount will be finalized based on referrer and client discussion.${roundWiseMinimum ? ` Minimum charge starts from ${roundWiseMinimum}.` : ''}`}
                    </p>
                  )}
                  {paymentProofIds.length > 0 && (
                    <>
                      <p className={paymentComplete ? 'sbs-pay-ok' : 'sbs-pay-partial'}>
                        {paymentComplete
                          ? `Payment proof on file ✓ · ₹${(paymentTotals?.verified_total || 0).toLocaleString('en-IN')} across ${paymentProofIds.length} screenshot${paymentProofIds.length === 1 ? '' : 's'}`
                          : roundWise
                            ? `₹${(paymentTotals?.verified_total || 0).toLocaleString('en-IN')} verified so far · ₹${(paymentTotals?.remaining_due || 0).toLocaleString('en-IN')} more to reach the ${roundWiseMinimum ? `${roundWiseMinimum} ` : ''}minimum`
                            : `₹${(paymentTotals?.verified_total || 0).toLocaleString('en-IN')} verified so far · ₹${(paymentTotals?.remaining_due || 0).toLocaleString('en-IN')} still to upload`}
                      </p>
                      <div className="sbs-pay-list">
                        {paymentAiResults.map((ai, index) => (
                          <PaymentAiResultCard key={ai.utr_number || ai.transaction_id || index} ai={ai} />
                        ))}
                      </div>
                    </>
                  )}
                  {paymentRejected.map((item, index) => (
                    <span className="sbs-hint sbs-hint--warn" key={`${item.filename}-${index}`}>
                      {item.filename}: {item.message}
                    </span>
                  ))}
                  {!paymentComplete && (
                    <>
                      {/* Split payments are normal here — one screenshot per
                          transfer, and the AI totals them for this booking. */}
                      {/* No label: the header above already names this, and a
                          second caption for one drop zone was pure height. */}
                      <SubmitSlotFileDrop
                        compact
                        multiple
                        hint="Paid in parts? Attach each screenshot — they are added up."
                        files={paymentFiles}
                        disabled={busy || parsing}
                        busy={busy || paymentAnalysing}
                        onFiles={next => { setPaymentFiles(next); setPaymentRejected([]) }}
                      />
                      {missingField === 'payment' && <span className="sbs-hint sbs-hint--warn" role="alert">{roundWise ? 'Attach a payment screenshot that covers at least the minimum charge.' : 'Attach a payment screenshot that covers the amount due.'}</span>}
                    </>
                  )}
                </div>
              )}

              <div ref={inviteRef} className="sbs-field">
                <span className="sbs-label">Interview invite screenshot</span>
                <SubmitSlotFileDrop hint="Teams, Gmail, Calendar, or Zoom — date and time must be visible." file={slotFile} previewUrl={slotPreview} disabled={busy} busy={parsing} onFile={onSlotFileChange} />
                {missingField === 'invite' && <span className="sbs-hint sbs-hint--warn" role="alert">Attach the interview invite screenshot.</span>}
              </div>

              {parsing && <div className="sbs-status sbs-status--loading"><Spinner size={18} /><span>Reading invite with AI… this may take a few minutes</span></div>}

              {aiBlocked && <div className="sbs-alert sbs-alert--error" role="alert">{aiBlocked}</div>}

              {aiExtraction && !aiBlocked && aiExtraction.confidence_score > 0 && (
                <div className="sbs-detected-compact">
                  <span className="sbs-detected-compact__check">✓</span>
                  <span className="sbs-detected-compact__text">
                    {[
                      aiExtraction.interview_date ? formatFriendlyDate(aiExtraction.interview_date) : '',
                      aiExtraction.start_time ? aiExtraction.start_time + (aiExtraction.end_time ? ` – ${aiExtraction.end_time}` : '') : '',
                      aiExtraction.interview_round ? `${aiExtraction.interview_round} Discussion` : '',
                      aiExtraction.meeting_platform ? platformLabel(aiExtraction.meeting_platform) : '',
                      aiExtraction.confidence_score ? `${aiExtraction.confidence_score}%` : ''
                    ].filter(Boolean).join(' • ')}
                  </span>
                  {aiExtraction.warnings && aiExtraction.warnings.length > 0 && (
                    <div className="sbs-detected-compact__warnings">{aiExtraction.warnings.map((w, i) => <span key={i} className="sbs-hint sbs-hint--warn">{w}</span>)}</div>
                  )}
                </div>
              )}

              {!aiExtraction && parsedSlot?.date && parsedSlot?.time && (
                <div className="sbs-detected-compact">
                  <span className="sbs-detected-compact__check">✓</span>
                  <span className="sbs-detected-compact__text">
                    {[
                      formatFriendlyDate(parsedSlot.date),
                      formatFriendlyTime(parsedSlot.time) + (parsedSlot.time_end ? ` – ${formatFriendlyTime(parsedSlot.time_end)}` : ''),
                      parsedSlot.interview_round ? `${parsedSlot.interview_round} Discussion` : '',
                      parsedSlot.platform ? platformLabel(parsedSlot.platform) : ''
                    ].filter(Boolean).join(' • ')}
                  </span>
                </div>
              )}

              {showManualSlotFields && (
                <div className="sbs-manual">
                  <p className="sbs-manual__hint">{parsedSlot?.date ? 'Verify detected date & time — correct below if wrong.' : 'Include the date line in your screenshot or enter manually.'}</p>
                  <div className="sbs-manual__grid">
                    <label className="sbs-field"><span className="sbs-label">Interview date</span><input className="sbs-input" type="date" value={manualDate || parsedSlot?.date || ''} onChange={e => { setManualDate(e.target.value); setUserEditedFields(f => ({...f, date: true})); }} disabled={busy || parsing} /></label>
                    <label className="sbs-field"><span className="sbs-label">Start time</span><input className="sbs-input" type="text" placeholder="e.g. 02:00 PM" value={normalizeTo12h(manualTime || parsedSlot?.time || '')} onChange={e => { setManualTime(e.target.value); setUserEditedFields(f => ({...f, time: true})); }} disabled={busy || parsing} /></label>
                  </div>
                  {isPastDate && <span className="sbs-hint sbs-hint--warn">Interview date is in the past. Please select today or a future date.</span>}
                </div>
              )}

              {error && <p className="sbs-alert sbs-alert--error" role="alert">{error}</p>}
              {success && <div className="sbs-alert sbs-alert--success sbs-success-anim"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{flexShrink:0}}><path d="M20 6L9 17l-5-5" strokeLinecap="round" strokeLinejoin="round"/></svg><span>{success}</span></div>}

              <button type="submit" className="sbs-cta sbs-cta--ready" disabled={busy || parsing || isPastDate || !!aiBlocked}>
                {busy ? <Spinner size={18} /> : parsing ? 'Reading invite...' : 'Confirm booking'}
              </button>
            </form>
            <TrustBadges />
          </div>
        )}
      </div>
      <p className="sbs-foot">TeleAutomation · secure slot booking</p>
    </div>
  )
}
