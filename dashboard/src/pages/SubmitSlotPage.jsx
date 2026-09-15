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

/** Format elapsed seconds as m:ss or s.s for the analysis stopwatch. */
function formatElapsed(seconds) {
  if (seconds == null) return ''
  const s = Math.max(0, seconds)
  if (s < 60) return `${s.toFixed(1)}s`
  const mins = Math.floor(s / 60)
  const rem = Math.floor(s % 60)
  return `${mins}:${String(rem).padStart(2, '0')}`
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
  const [triedSubmit, setTriedSubmit] = useState(false)
  const [aiExtraction, setAiExtraction] = useState(null)
  const [aiBlocked, setAiBlocked] = useState('')
  const [userEditedFields, setUserEditedFields] = useState({})
  const [paymentAiResults, setPaymentAiResults] = useState([])
  const [paymentRejected, setPaymentRejected] = useState([])
  const [paymentAnalysing, setPaymentAnalysing] = useState(false)

  // ── Analysis stopwatch (display-only; does not affect analysis logic) ──────
  // One stopwatch per analysis kind ('invite' | 'payment'). Started when an
  // analysis begins, frozen on success/failure, and cleared on a full form
  // reset. Purely a UI affordance — it never gates or alters the request.
  const [inviteElapsed, setInviteElapsed] = useState(null)
  const [paymentElapsed, setPaymentElapsed] = useState(null)
  const timerRefs = useRef({ invite: null, payment: null })

  const startTimer = useCallback((kind) => {
    const setElapsed = kind === 'invite' ? setInviteElapsed : setPaymentElapsed
    if (timerRefs.current[kind]) clearInterval(timerRefs.current[kind])
    const startedAt = Date.now()
    setElapsed(0)
    timerRefs.current[kind] = setInterval(() => {
      setElapsed((Date.now() - startedAt) / 1000)
    }, 100)
  }, [])

  const stopTimer = useCallback((kind) => {
    if (timerRefs.current[kind]) {
      clearInterval(timerRefs.current[kind])
      timerRefs.current[kind] = null
    }
  }, [])

  const clearTimer = useCallback((kind) => {
    stopTimer(kind)
    const setElapsed = kind === 'invite' ? setInviteElapsed : setPaymentElapsed
    setElapsed(null)
  }, [stopTimer])

  // Never leak an interval if the component unmounts mid-analysis.
  useEffect(() => () => {
    if (timerRefs.current.invite) clearInterval(timerRefs.current.invite)
    if (timerRefs.current.payment) clearInterval(timerRefs.current.payment)
  }, [])

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

  // Full reset of the Book slot form. Called after a booking is confirmed so
  // the next candidate starts from a clean form. Clears every field, upload,
  // preview, analysis result, timer and validation/temporary UI state. It does
  // NOT touch confirmed bookings (`booked`), the candidate roster, or the
  // active tab — only the transient inputs of the book form.
  const resetBookForm = useCallback(() => {
    // Free object URLs before dropping their references.
    setSlotPreview(prev => { if (prev) URL.revokeObjectURL(prev); return '' })
    setSessionPreview(prev => { if (prev) URL.revokeObjectURL(prev); return '' })
    // Identity + service
    setName('')
    setRoundWisePhone('')
    setServiceType('profile_service')
    setShowServiceDrop(false)
    // Invite upload + parsed slot
    setSlotFile(null)
    setParsedSlot(null)
    setManualDate('')
    setManualTime('')
    setInterviewRound('')
    setTechnology('')
    // Session upload
    setSessionFile(null)
    // AI / analysis results
    setAiExtraction(null)
    setAiBlocked('')
    setUserEditedFields({})
    // Payment proofs + requirement
    resetPaymentProofs()
    setPaymentRequirement(null)
    // Validation + transient flags
    setTriedSubmit(false)
    setError('')
    // Analysis stopwatches
    clearTimer('invite')
    clearTimer('payment')
  }, [resetPaymentProofs, clearTimer])

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
    startTimer('invite')
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
          stopTimer('invite')
          return
        }
        // Check if it doesn't look like an invite
        if (ext.looks_like_interview_invite === false) {
          setAiBlocked('This image does not look like an interview invite.')
          setParsedSlot(null)
          setParsing(false)
          stopTimer('invite')
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
        stopTimer('invite')
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
    finally { setParsing(false); stopTimer('invite') }
  }

  async function onSlotFileChange(file) {
    if (slotPreview) URL.revokeObjectURL(slotPreview)
    setSlotFile(file || null); setParsedSlot(null); setManualDate(''); setManualTime(''); setSuccess(''); setAiExtraction(null); setAiBlocked(''); setUserEditedFields({})
    clearTimer('invite')
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
    startTimer('payment')
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
      // Round-wise proof must be stored under the correct context so
      // /bookings/confirm can retrieve it with matching service_type and phone.
      fd.append('service_type', serviceType)
      if (serviceType === 'round_wise') {
        fd.append('phone', roundWisePhone.trim())
        fd.append('technology', effectiveTechnology)
        fd.append('interview_round', interviewRound)
      }
      const res = await fetch(`${API_BASE}/public/slots/payment-proof`, { method: 'POST', body: fd })
      const data = await res.json()
      setPaymentRejected(data.rejected || [])
      if (!res.ok) { setError(data.message || 'Payment upload failed'); return }
      setPaymentProofIds(data.proof_ids || [])
      setPaymentTotals(data)
      setPaymentFiles([])
      // Every accepted screenshot keeps the AI reading the backend already ran.
      setPaymentAiResults([
        ...paymentAiResults,
        ...(data.ai_extractions || []).filter(ai => ai && ai.is_payment_screenshot),
      ])
      if (data.payment_complete) setSuccess('Payment proof saved — you can confirm your slot.')
    } catch { setError('Network error — try again') }
    finally { setBusy(false); setPaymentAnalysing(false); stopTimer('payment') }
  }

  async function submitBook(ev) {
    ev.preventDefault()
    if (parsing) {
      setError('Please wait until invite reading is complete.')
      return
    }
    if (!effectiveName || !slotFile || !interviewRound || needsPaymentProof
        || (serviceType === 'round_wise' && !roundWisePhone.trim())
        || (serviceType === 'round_wise' && !effectiveTechnology)) {
      setTriedSubmit(true)
      setError('')
      return
    }
    if (isPastDate) {
      setError('Interview date is in the past. Please select today or a future date.')
      return
    }
    setBusy(true); setError(''); setSuccess('')
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
      const res = await fetch(`${API_BASE}/bookings/confirm`, { method: 'POST', body: fd })
      const data = await res.json()
      if (!res.ok) { setError(data.payment_due ? (data.message || 'Payment required.') : (data.message || 'Could not book slot')); return }
      const confirmedName = data.candidate?.name || effectiveName
      // Fully reset the Book slot form; the confirmed booking is already saved
      // server-side and shows on the Confirmed slots tab after refresh.
      resetBookForm()
      setSuccess(`Slot confirmed for ${confirmedName}.`)
      // Refresh data first, then switch to confirmed tab after 2 seconds
      await refresh()
      setTimeout(() => { setTab('confirmed'); setSuccess('') }, 2000)
    } catch { setError('Network error — try again') }
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
            <form className="sbs-form" onSubmit={submitBook}>
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

              <label className="sbs-field">
                <span className="sbs-label">Client name</span>
                {serviceType === "round_wise" ? (
                  <input className="sbs-input" type="text" value={name} onChange={e => { setName(e.target.value); resetPaymentProofs(); }} placeholder="Type client name" disabled={busy || parsing} />
                ) : (
                  <SlotCandidatePicker candidates={candidates} value={name} onChange={v => { setName(v); resetPaymentProofs() }} disabled={busy || parsing} />
                )}
                {triedSubmit && !effectiveName
                  ? <span className="sbs-hint sbs-hint--warn">Enter client name to confirm.</span>
                  : <span className="sbs-hint">{serviceType === "round_wise" ? "Type the client name for this round." : "Pick from the list or type a new client name."}</span>}
              </label>

              {serviceType === "round_wise" && (
                // /bookings/confirm rejects a round-wise booking without a valid
                // phone identity, so it has to be collected here.
                <label className="sbs-field">
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
                  {triedSubmit && !roundWisePhone.trim()
                    ? <span className="sbs-hint sbs-hint--warn">Required — round-wise booking needs the candidate phone.</span>
                    : <span className="sbs-hint">Identifies the candidate across rounds.</span>}
                </label>
              )}

              {serviceType === "round_wise" && (
                // /bookings/confirm rejects a round-wise booking without a
                // technology and tells the candidate to select one, so there
                // has to be somewhere to select it. The invite fills it in when
                // it names the technology; often it does not.
                <label className="sbs-field">
                  <span className="sbs-label">Technology <span className="sbs-required" aria-hidden="true">*</span></span>
                  <SlotTechnologyPicker
                    options={technologyOptions}
                    value={technology}
                    onChange={value => { setTechnology(value); setUserEditedFields(prev => ({ ...prev, technology: true })) }}
                    disabled={busy || parsing}
                  />
                  {triedSubmit && !effectiveTechnology
                    ? <span className="sbs-hint sbs-hint--warn">Required — round-wise booking needs the technology.</span>
                    : <span className="sbs-hint">Read from the invite when it says; otherwise pick or type it.</span>}
                </label>
              )}

              <label className="sbs-field">
                <span className="sbs-label">Interview round <span className="sbs-required" aria-hidden="true">*</span></span>
                <div className={`sbs-select-wrap${triedSubmit && !interviewRound ? ' sbs-select-wrap--required' : ''}`}>
                  <select className="sbs-select" value={interviewRound} onChange={e => setInterviewRound(e.target.value)} disabled={busy || parsing} required>
                    <option value="">Select round (L1, L2…)</option>
                    {ROUND_OPTIONS.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </div>
                {triedSubmit && !interviewRound && <span className="sbs-hint sbs-hint--warn">Required — select a round to confirm.</span>}
              </label>

              {paymentRequired && (
                <div className="sbs-pay-card">
                  <div className="sbs-pay-head">
                    <span>Payment due</span>
                    <strong>{paymentAmountDue == null ? '—' : `₹${paymentAmountDue.toLocaleString('en-IN')}`}</strong>
                  </div>
                  {paymentProofIds.length > 0 && (
                    <>
                      <p className={paymentComplete ? 'sbs-pay-ok' : 'sbs-pay-partial'}>
                        {paymentComplete
                          ? `Payment proof on file ✓ · ₹${(paymentTotals?.verified_total || 0).toLocaleString('en-IN')} across ${paymentProofIds.length} screenshot${paymentProofIds.length === 1 ? '' : 's'}`
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
                      <SubmitSlotFileDrop
                        compact
                        multiple
                        label={paymentProofIds.length ? 'Add remaining payment screenshots' : 'Payment screenshots'}
                        hint="Paid in parts? Attach every payment screenshot — they are added up for this booking."
                        files={paymentFiles}
                        disabled={busy || parsing}
                        busy={busy || paymentAnalysing}
                        onFiles={next => { setPaymentFiles(next); setPaymentRejected([]) }}
                      />
                      <button type="button" className="sbs-secondary-btn" disabled={busy || parsing || paymentAnalysing || !paymentFiles.length} onClick={uploadPaymentProof}>
                        {paymentAnalysing
                          ? <><Spinner size={14} />&nbsp;Analysing {paymentFiles.length > 1 ? `${paymentFiles.length} screenshots` : ''}…&nbsp;{paymentElapsed != null && formatElapsed(paymentElapsed)}</>
                          : `Save payment proof${paymentFiles.length > 1 ? 's' : ''}`}
                      </button>
                      {!paymentAnalysing && paymentElapsed != null && <span className="sbs-timer sbs-timer--inline" aria-live="polite">Analysed in {formatElapsed(paymentElapsed)}</span>}
                      {triedSubmit && needsPaymentProof && <span className="sbs-hint sbs-hint--warn">Upload and save payment proof to confirm.</span>}
                    </>
                  )}
                </div>
              )}

              <div className="sbs-field">
                <span className="sbs-label">Interview invite screenshot</span>
                <SubmitSlotFileDrop hint="Teams, Gmail, Calendar, or Zoom — date and time must be visible." file={slotFile} previewUrl={slotPreview} disabled={busy} busy={parsing} onFile={onSlotFileChange} />
                {triedSubmit && !slotFile && <span className="sbs-hint sbs-hint--warn">Upload your interview invite screenshot to confirm.</span>}
              </div>

              {parsing && <div className="sbs-status sbs-status--loading"><Spinner size={18} /><span>Reading invite with AI… this may take a few minutes</span>{inviteElapsed != null && <span className="sbs-timer" aria-live="polite">{formatElapsed(inviteElapsed)}</span>}</div>}
              {!parsing && inviteElapsed != null && <div className="sbs-status sbs-status--done"><span>Invite read in {formatElapsed(inviteElapsed)}</span></div>}

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
