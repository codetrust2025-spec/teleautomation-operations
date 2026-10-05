import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { formatClockTime } from '../utils/istTime.js'
import { bookingSourceMeta as sharedBookingSourceMeta } from '../utils/bookingSource.js'
import { addDaysIso, todayIso } from './calendarDates.js'
import { ALL_TIME_RANGE } from './dateRangePresets.js'
import { publishPendingWorkChanged } from './PendingWorksProvider.jsx'
import { STATUS_OPTIONS, countStatusRows, emptyStatusCounts, matchesStatusFilter, statusLabel, statusTone } from './interviewStatuses.js'
import { DarkSelect } from './DarkSelect.jsx'

const ATTENDEES = ['Nikhila', 'Bhavana', 'Tool']

// How an attended interview went, best to worst. Values match the server's
// normalise_interview_feedback scale; "negative" is shared with the legacy
// two-value scale so old rows stay valid.
const FEEDBACK_OPTIONS = [
  { value: 'excellent', label: 'Excellent' },
  { value: 'good', label: 'Good' },
  { value: 'average', label: 'Average' },
  { value: 'needs_improvement', label: 'Needs Improvement' },
  { value: 'negative', label: 'Negative' },
]

// The outcome of an attended interview, independent of how it went. Values
// match the server's normalise_interview_result set.
const RESULT_OPTIONS = [
  { value: 'awaiting_result', label: 'Awaiting result' },
  { value: 'next_round', label: 'Next round' },
  { value: 'selected', label: 'Selected' },
  { value: 'rejected', label: 'Rejected' },
]

// A stored legacy feedback value ("positive") is not on the new scale, so the
// dropdown would show the placeholder for it. Offer it as a one-off option so
// an operator editing such a row sees the real stored value rather than a
// blank, without polluting the scale for new records.
function feedbackOptionsFor(current) {
  if (current && !FEEDBACK_OPTIONS.some(o => o.value === current)) {
    const label = current.charAt(0).toUpperCase() + current.slice(1).replace(/_/g, ' ')
    return [...FEEDBACK_OPTIONS, { value: current, label: `${label} (legacy)` }]
  }
  return FEEDBACK_OPTIONS
}

// How long a row the operator just resolved stays hidden from a list of
// unresolved rows that still carries it. Long enough to cover a reader that
// is a moment behind, short enough that it can never look like data loss.
const RESOLVED_SUPPRESSION_MS = 60000

const TECHNOLOGIES = [
  '.NET', 'Angular', 'Automation Testing', 'AWS Admin', 'AWS Cloud', 'AWS DevOps',
  'Azure Admin', 'Azure DevOps', 'Business Analyst', 'Cloud', 'Cloud DevOps',
  'Data Analyst', 'Data Engineer', 'Databricks', 'DevOps', 'ETL', 'Full Stack',
  'Java Backend', 'ML Engineer', 'MERN stack', 'Node JS', 'Oracle Fusion (Func)',
  'Oracle Fusion (Tech Con)', 'Power BI', 'Python', 'React JS', 'Salesforce',
  'SAP BASIS', 'SAP HANA', 'SAP MM', 'SAP Sales', 'ServiceNow', 'Snowflake',
  'SQL', 'Testing',
].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))


// `todayIso` comes from calendarDates.js: the roster, the period presets and
// the date picker all have to name the same day, and a UTC slice does not
// between midnight and 05:30 IST.
function tomorrowIso() {
  return addDaysIso(todayIso(), 1)
}

function formatDayLabel(iso) {
  if (!iso) return '—'
  try {
    return new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString('en-IN', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      timeZone: 'Asia/Kolkata',
    })
  } catch {
    return iso
  }
}

function resolvedStatus(row) {
  return (row?.interview_attendance_status_resolved || row?.interview_attendance_status || '').trim().toLowerCase()
}


function AttendanceSelect({ value, disabled, onChange, ariaLabel }) {
  return (
    <select
      className="cand-input ops-attendance-select"
      value={value || ''}
      disabled={disabled}
      onChange={e => onChange(e.target.value)}
      aria-label={ariaLabel}
    >
      {STATUS_OPTIONS.map(opt => (
        <option key={opt.value || 'pending'} value={opt.value}>{opt.label}</option>
      ))}
    </select>
  )
}

/**
 * One mapping for the roster and the confirmed-slots page.
 *
 * This used to fall back to "Candidate booked" for anything that was not
 * ai_auto_booked, which labelled legacy rows with a source they never
 * recorded. The shared helper reports those as plain "Booked" instead.
 */
function bookingSourceMeta(row) {
  return sharedBookingSourceMeta(row?.interview_booking_source)
}

function SlotScreenshotModal({ row, onClose }) {
  const proof = row?.slot_screenshot_proof
  useEffect(() => {
    const closeOnEscape = event => event.key === 'Escape' && onClose()
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [onClose])
  if (!proof) return null
  const imageUrl = `${API}${proof.url}`
  return createPortal(
    <div className="ops-slot-shot-modal" role="presentation" onMouseDown={event => event.target === event.currentTarget && onClose()}>
      <section className="ops-slot-shot-modal__panel" role="dialog" aria-modal="true" aria-label={`Booking screenshot for ${row.name}`}>
        <header><div><h2>{row.name}</h2><p>{formatDayLabel(row.date)} · {[row.time, row.time_end].filter(Boolean).map(formatClockTime).join(' – ')}{row.interview_round ? ` · ${row.interview_round}` : ''}</p></div><button type="button" onClick={onClose} aria-label="Close screenshot">&#10005;</button></header>
        <div className="ops-slot-shot-modal__image"><img src={imageUrl} alt={`Interview booking screenshot for ${row.name}`} /></div>
        <footer><span>{proof.original_name || 'Interview invite screenshot'}</span><a href={imageUrl} target="_blank" rel="noopener noreferrer">Open original</a></footer>
      </section>
    </div>,
    document.body,
  )
}

function RowActions({ row, busy, canEditAttendee, onEditAttendee, onEditSlot, onRemove }) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState(null)
  const triggerRef = useRef(null)
  const menuRef = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    function closeOnOutsideClick(event) {
      if (!triggerRef.current?.contains(event.target) && !menuRef.current?.contains(event.target)) setOpen(false)
    }
    function closeOnEscape(event) { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => { document.removeEventListener('mousedown', closeOnOutsideClick); document.removeEventListener('keydown', closeOnEscape) }
  }, [open])
  function toggleMenu() {
    if (open) { setOpen(false); return }
    const rect = triggerRef.current?.getBoundingClientRect()
    if (rect) setPosition({ top: rect.bottom + 4, left: Math.max(8, rect.right - 196) })
    setOpen(true)
  }
  const menu = open && position && createPortal(
    <ul ref={menuRef} className="ops-row-menu__list ops-row-menu__list--portal" style={{ top: position.top, left: position.left }} role="menu">
      {canEditAttendee && <li role="none"><button type="button" role="menuitem" className="ops-row-menu__item" onClick={() => { setOpen(false); onEditAttendee(row) }}>Edit attendee</button></li>}
      <li role="none"><button type="button" role="menuitem" className="ops-row-menu__item" onClick={() => { setOpen(false); onEditSlot(row) }}>Edit slot</button></li>
      <li role="none"><button type="button" role="menuitem" className="ops-row-menu__item ops-row-menu__item--danger" onClick={() => { setOpen(false); onRemove(row) }}>Remove slot</button></li>
    </ul>, document.body)
  return (
    <div className="ops-row-menu">
      <button ref={triggerRef} type="button" className="ops-row-menu__trigger" aria-label={`Actions for ${row.name}`} aria-haspopup="menu" aria-expanded={open} disabled={busy} onClick={toggleMenu}>⋮</button>
      {menu}
    </div>
  )
}

function SlotEditModal({ row, mode, targetStatus, targetLabel, busy, onClose, onSave }) {
  const [attendee, setAttendee] = useState(row.interview_attendee_resolved || row.interview_attendee || 'Bhavana')
  const [remark, setRemark] = useState(row.interview_attendance_remark || '')
  const [feedback, setFeedback] = useState(row.interview_feedback || '')
  const [result, setResult] = useState(row.interview_result || '')
  const [date, setDate] = useState(row.date || '')
  const [time, setTime] = useState(row.time || '')
  const [timeEnd, setTimeEnd] = useState(row.time_end || '')
  const [notes, setNotes] = useState(row.notes || '')
  const [round, setRound] = useState(row.interview_round || '')
  const [technology, setTechnology] = useState((row.technology || '').trim())
  const [error, setError] = useState('')
  const attendeeOnly = mode === 'attendee'
  const attendeeWithStatus = mode === 'attendee-with-status'
  // Feedback and result only make sense once an interview actually happened.
  const wantsFeedback = attendeeWithStatus && targetStatus === 'attended'
  // Everything the "Mark as Attended" form needs before it can be saved. The
  // Attended button reads the same predicate it enforces, so a disabled button
  // and a rejected submit can never disagree.
  const feedbackValid = !wantsFeedback || !!feedback
  const resultValid = !wantsFeedback || !!result
  const noteValid = !attendeeWithStatus || !!remark.trim()
  const attendeeValid = !!attendee
  const canSubmit = attendeeValid && feedbackValid && resultValid && noteValid
  async function submit(event) {
    event.preventDefault()
    if (attendeeWithStatus && !remark.trim()) { setError('Please add a note about the interview.'); return }
    if (wantsFeedback && !feedback) { setError('Select how the interview went (Interview feedback).'); return }
    if (wantsFeedback && !result) { setError('Select the interview result.'); return }
    setError('')
    try {
      if (attendeeOnly) {
        await onSave({ attendee })
      } else if (attendeeWithStatus) {
        await onSave({ attendee, status: targetStatus, remark: remark.trim(), feedback: wantsFeedback ? feedback : '', result: wantsFeedback ? result : '' })
      } else {
        if (!technology) { setError('Please select the interview technology.'); return }
        await onSave({ date, time, time_end: timeEnd, notes, interview_round: round, technology })
      }
      onClose()
    } catch (err) {
      setError(err.message || 'Save failed')
    }
  }
  return (
    <div className="cand-modal-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}>
      <form className="cand-modal ops-slot-modal" onSubmit={submit}>
        <header className="cand-modal-header"><div><h3 className="cand-modal-title">{attendeeWithStatus ? `Mark as "${targetLabel}"?` : attendeeOnly ? 'Edit attendee' : 'Edit interview slot'}</h3><p className="cand-modal-sub">{attendeeWithStatus ? `Select attendee and update attendance for ${row.name}` : row.name}</p></div><button type="button" className="cand-modal-close" onClick={onClose} aria-label="Close">×</button></header>
        <div className="cand-modal-body">
          {(attendeeOnly || attendeeWithStatus) ? <>
            <div className="cand-field cand-field--span2">
              <span className="cand-field-label" id="ops-att-attendee-label">Attendee{attendeeWithStatus ? ' (who attended the interview?)' : ''}</span>
              <DarkSelect
                ariaLabel={attendeeWithStatus ? 'Attendee (who attended the interview?)' : 'Attendee'}
                value={attendee}
                options={ATTENDEES.map(name => ({ value: name, label: name }))}
                onChange={setAttendee}
                placeholder="Select attendee"
                required
              />
            </div>
            {wantsFeedback && <div className="cand-field cand-field--span2">
              <span className="cand-field-label">Interview feedback <span className="cand-field-required-tag">Required</span></span>
              <DarkSelect
                ariaLabel="Interview feedback"
                value={feedback}
                options={feedbackOptionsFor(feedback)}
                onChange={setFeedback}
                placeholder="Select feedback"
                required
              />
            </div>}
            {wantsFeedback && <div className="cand-field cand-field--span2">
              <span className="cand-field-label">Interview result <span className="cand-field-required-tag">Required</span></span>
              <DarkSelect
                ariaLabel="Interview result"
                value={result}
                options={RESULT_OPTIONS}
                onChange={setResult}
                placeholder="Select result"
                required
              />
            </div>}
            {attendeeWithStatus && <label className="cand-field cand-field--span2"><span className="cand-field-label">Note / remark <span className="cand-field-required-tag">Required</span></span><input className="cand-input" value={remark} onChange={event => setRemark(event.target.value)} placeholder="e.g. Interview went well, next round scheduled" required /></label>}
          </> : <><label className="cand-field"><span className="cand-field-label">Date</span><input className="cand-input" type="date" value={date} onChange={event => setDate(event.target.value)} required /></label><label className="cand-field"><span className="cand-field-label">Start time</span><input className="cand-input" type="time" value={time} onChange={event => setTime(event.target.value)} required /></label><label className="cand-field"><span className="cand-field-label">End time</span><input className="cand-input" type="time" value={timeEnd} onChange={event => setTimeEnd(event.target.value)} required /></label><label className="cand-field"><span className="cand-field-label">Interview round</span><select className="cand-input" value={round} onChange={event => setRound(event.target.value)}><option value="">Select round</option><option value="L1">L1</option><option value="L2">L2</option><option value="HR">HR</option><option value="Final">Final</option><option value="Screening">Screening</option></select></label><label className="cand-field cand-field--span2"><span className="cand-field-label">Technology *</span><select className="cand-input" value={technology} onChange={event => setTechnology(event.target.value)} required><option value="">Select technology</option>{technology && !TECHNOLOGIES.includes(technology) && <option value={technology}>{technology}</option>}{TECHNOLOGIES.map(name => <option key={name} value={name}>{name}</option>)}</select></label><label className="cand-field cand-field--span2"><span className="cand-field-label">Notes</span><input className="cand-input" value={notes} onChange={event => setNotes(event.target.value)} /></label></>}
          {error && <p className="admin-error cand-field--span2">{error}</p>}
        </div>
        <footer className="cand-modal-footer"><button type="button" className="cand-btn cand-btn--ghost" onClick={onClose} disabled={busy}>Cancel</button><button type="submit" className={`cand-btn cand-btn--primary${attendeeWithStatus && (targetStatus === 'not_attended' || targetStatus === 'cancelled') ? ' cand-btn--danger' : ''}${!canSubmit ? ' cand-btn--disabled' : ''}`} disabled={busy || !canSubmit} aria-disabled={busy || !canSubmit}>{busy ? 'Saving…' : attendeeWithStatus ? targetLabel : 'Save changes'}</button></footer>
      </form>
    </div>
  )
}

export function InterviewRoster({
  variant = 'default',
  focusDay = null,
  onFocusDayApplied,
  dashboardDay,
  onDashboardDayChange,
  dashboardFromDate,
  dashboardToDate,
  dashboardAttendeeFilter = '',
  dashboardRoundFilter = '',
  dashboardTechnologyFilter = '',
  dashboardCandidateSearch = '',
  // The Candidate dropdown's choice. Exact, unlike the search text above.
  dashboardCandidate = '',
  dashboardCandidateTypeFilter = '',
  dashboardStatusFilter = '',
  upcomingOnly = false,
  // The global Pending view: every interview still waiting for an outcome, at
  // any date. It replaces the date range rather than narrowing it.
  unresolvedOnly = false,
  onRosterMutate,
  onRosterCountsChange,
  // Bumped by the panel's Refresh. The counters are tallied from these rows
  // now, so refreshing the summary alone would leave them on the old numbers.
  refreshNonce = 0,
}) {
  const { role, enabled, reference } = useAuth()
  const { confirm } = useConfirm()
  const canManage = !enabled || role === 'admin' || role === 'handler'
  const canEditAttendee = !enabled || role === 'admin'
  const handlerView = role === 'handler' && !!reference?.trim()
  const isDashboard = variant === 'dashboard'
  const hasRange = isDashboard && dashboardFromDate && dashboardToDate
  const isSingleDayRange = hasRange && dashboardFromDate === dashboardToDate

  const [localDay, setLocalDay] = useState(todayIso())
  const day = isDashboard ? (dashboardDay ?? localDay) : localDay
  const setDay = isDashboard ? (onDashboardDayChange ?? setLocalDay) : setLocalDay

  const [rows, setRows] = useState([])
  // A counter per status, from the same list the tabs read. Naming three of
  // them here left the dashboard showing 0 for Cancelled, Rescheduled and
  // Re-Service in the window before the global summary arrives -- and for good
  // if that request fails.
  const [counts, setCounts] = useState(() => emptyStatusCounts())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState(null)
  const [editing, setEditing] = useState(null)
  const [screenshotRow, setScreenshotRow] = useState(null)
  const [attendeeFilter, setAttendeeFilter] = useState('')
  const [candidateFilter, setCandidateFilter] = useState('')
  const [channelFilter, setChannelFilter] = useState('')
  const [candidateOptions, setCandidateOptions] = useState([])

  const rosterCountsRef = useRef(onRosterCountsChange)
  rosterCountsRef.current = onRosterCountsChange

  // Pending and All unresolved list rows *because* they have no outcome, so
  // recording one takes the row off the list. Waiting for a reload to say so
  // is not good enough: a poll whose request left before the write answers
  // after it, with the row still in it, and the row reappears seconds after
  // the operator watched it go — indistinguishable from the update being lost.
  // The save is authoritative for the row it wrote, so the row goes at once
  // and stays gone while any lagging reader still carries it.
  //
  // Bounded in time, because suppression is a claim about a request in flight,
  // not about the data: if the row is still coming back a minute later then
  // something else is true and the screen should show it rather than hide it.
  const resolvedHere = useRef(new Map())
  const pendingOnlyView = unresolvedOnly || upcomingOnly

  /** Rows the operator resolved a moment ago and the server has not caught up on. */
  function stillSuppressed(row) {
    const at = resolvedHere.current.get(row?.id)
    return at !== undefined && Date.now() - at < RESOLVED_SUPPRESSION_MS
  }

  /** Put a set of rows on screen: one place that filters, counts and announces.
   *
   *  Every counter is tallied from the rows actually shown, so the tabs above
   *  the table and the sidebar badge cannot drift from it.
   */
  const applyRoster = useCallback((nextRows, { fromServer = true } = {}) => {
    const suppress = pendingOnlyView && resolvedHere.current.size > 0
    const visible = suppress ? nextRows.filter(row => !stillSuppressed(row)) : nextRows
    if (fromServer && resolvedHere.current.size) {
      // Forgotten as soon as the server agrees, or once the window is up.
      // Only a payload can settle that: the local list this same save just
      // filtered the row out of says nothing about what the server holds, and
      // reading it as agreement would drop the guard before the first reload.
      const present = new Set(nextRows.map(row => row.id))
      for (const [id, at] of [...resolvedHere.current]) {
        if (!present.has(id) || Date.now() - at >= RESOLVED_SUPPRESSION_MS) resolvedHere.current.delete(id)
      }
    }
    setRows(visible)
    // Counted from the rows just shown rather than read off the payload:
    // the top counter and the sidebar badge disagreed with the table and
    // with each other because all three measured different things.
    const nextCounts = countStatusRows(visible, resolvedStatus)
    setCounts(nextCounts)
    rosterCountsRef.current?.(nextCounts, { isUpcomingView: upcomingOnly })
    const totalPending = nextCounts.pending_count
    // The sidebar badge counts pending interviews in its own seven-day
    // window. This view counts every unresolved interview ever, which is a
    // different and much larger number, so it asks the badge to re-read its
    // own rather than handing it one that would be wrong.
    if (unresolvedOnly) publishPendingWorkChanged()
    else publishPendingWorkChanged(totalPending)
  }, [unresolvedOnly, upcomingOnly, pendingOnlyView])

  const effectiveAttendee = isDashboard ? dashboardAttendeeFilter : attendeeFilter
  // A chosen candidate goes as `candidate` (one person, exactly); only typed
  // text goes as `search`, which matches any word of it.
  const effectiveSearch = isDashboard ? dashboardCandidateSearch : ''
  const effectiveCandidate = isDashboard ? dashboardCandidate : candidateFilter
  const effectiveChannel = isDashboard ? dashboardCandidateTypeFilter : channelFilter
  const effectiveRound = isDashboard ? dashboardRoundFilter : ''
  const effectiveTechnology = isDashboard ? dashboardTechnologyFilter : ''

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true)
    try {
      const params = new URLSearchParams()
      let url = `${API}/candidates/interviews/daily`
      if (unresolvedOnly) {
        // Asks for the whole span explicitly, so the request says what it
        // reads even though the server ignores the range for this view.
        url = `${API}/candidates/interviews/monitor`
        params.set('from', ALL_TIME_RANGE.from)
        params.set('to', ALL_TIME_RANGE.to)
      } else if (hasRange && !isSingleDayRange) {
        url = `${API}/candidates/interviews/monitor`
        params.set('from', dashboardFromDate)
        params.set('to', dashboardToDate)
      } else {
        params.set('date', hasRange ? dashboardFromDate : day)
      }
      if (effectiveAttendee) params.set('attendee', effectiveAttendee)
      if (effectiveCandidate) params.set('candidate', effectiveCandidate)
      const search = effectiveSearch.trim()
      if (search) params.set('search', search)
      if (effectiveChannel) params.set('channel', effectiveChannel)
      if (effectiveRound) params.set('round', effectiveRound)
      if (effectiveTechnology) params.set('technology', effectiveTechnology)
      if (unresolvedOnly) params.set('unresolved_only', 'true')
      else if (upcomingOnly) params.set('upcoming_only', 'true')

      const res = await fetch(`${url}?${params}`, { credentials: 'include', cache: 'no-store' })
      if (!(res.headers.get('content-type') || '').includes('application/json')) {
        throw new Error(`Server returned ${res.status} — hard refresh and try again`)
      }
      const data = await res.json()
      if (!res.ok || data.status !== 'ok') {
        throw new Error(data.message || data.detail || `Failed to load roster (${res.status})`)
      }
      applyRoster(data.interviews || [])
      setError('')
    } catch (err) {
      if (!silent) {
        setError(err.message || 'Failed to load')
        setRows([])
      }
    } finally {
      if (!silent) setLoading(false)
    }
  }, [
    day,
    dashboardFromDate,
    dashboardToDate,
    effectiveAttendee,
    effectiveSearch,
    effectiveCandidate,
    effectiveChannel,
    effectiveRound,
    effectiveTechnology,
    hasRange,
    isSingleDayRange,
    upcomingOnly,
    unresolvedOnly,
    applyRoster,
  ])

  const loadCandidateOptions = useCallback(async () => {
    if (hasRange) return
    try {
      const params = new URLSearchParams({ from: day, to: day })
      if (attendeeFilter) params.set('attendee', attendeeFilter)
      if (channelFilter) params.set('channel', channelFilter)
      const res = await fetch(`${API}/candidates/interviews/filter-options?${params}`, { credentials: 'include' })
      if (!(res.headers.get('content-type') || '').includes('application/json')) return
      const data = await res.json()
      if (!res.ok || data.status !== 'ok') return
      setCandidateOptions(data.options || [])
    } catch {
      setCandidateOptions([])
    }
  }, [day, attendeeFilter, channelFilter, hasRange])

  useEffect(() => { load() }, [load, refreshNonce])
  useEffect(() => {
    const refresh = () => load({ silent: true })
    window.addEventListener('teleautomation:slot-booking-updated', refresh)
    return () => window.removeEventListener('teleautomation:slot-booking-updated', refresh)
  }, [load])
  useEffect(() => {
    const timer = setInterval(() => load({ silent: true }), 5000)
    return () => clearInterval(timer)
  }, [load])
  useEffect(() => { loadCandidateOptions() }, [loadCandidateOptions])
  useEffect(() => {
    if (focusDay) {
      setDay(focusDay.slice(0, 10))
      onFocusDayApplied?.()
    }
  }, [focusDay, onFocusDayApplied, setDay])

  // The roster's own reload plus the signal the sidebar badge listens for:
  // marking attendance changes the pending count, and the provider behind that
  // badge otherwise polls on a two-minute timer.
  function notifyRosterChanged() {
    onRosterMutate?.()
    publishPendingWorkChanged()
  }

  async function saveAttendance(row, status, attendee, remark, feedback, result) {
    setBusyId(row.id)
    setError('')
    try {
      const body = { status: status || '', remark: remark || row.interview_attendance_remark || '' }
      if (status && (status === 'attended' || status === 'not_attended')) {
        body.attendee = attendee || row.interview_attendee_resolved || row.interview_attendee || 'Bhavana'
      }
      if (feedback !== undefined) body.feedback = feedback || ''
      // The interview result rides with feedback: both describe an attended
      // sitting and the server keeps them only while the round stays attended.
      if (result !== undefined) body.result = result || ''
      const res = await fetch(`${API}/candidates/${row.id}/interview-attendance`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Update failed')
      setEditing(null)
      // Only after the server said yes: a failed update leaves the row where
      // it is, with the error above the table.
      applySavedStatus(row.id, status || '')
      await load({ silent: true })
      notifyRosterChanged()
    } catch (err) {
      setError(err.message || 'Update failed')
    } finally {
      setBusyId(null)
    }
  }

  /** Show the outcome the operator just recorded, before any reload says so.
   *
   *  On a list of rows that have no outcome, recording one removes the row and
   *  drops the count by one there and then. On a dated list, where the row
   *  stays, its status changes in place. Either way nothing on screen waits
   *  for the next fetch, and a reload that still disagrees does not undo it.
   */
  function applySavedStatus(rowId, status) {
    const leaves = Boolean(status) && pendingOnlyView
    if (leaves) resolvedHere.current.set(rowId, Date.now())
    const updated = list => leaves
      ? list.filter(row => row.id !== rowId)
      : list.map(row => row.id === rowId
        ? { ...row, interview_attendance_status: status, interview_attendance_status_resolved: status }
        : row)
    applyRoster(updated(rows), { fromServer: false })
  }

  async function saveAttendee(row, attendee) {
    setBusyId(row.id)
    try {
      const res = await fetch(`${API}/candidates/${row.id}/interview-attendee`, {
        method: 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ attendee }),
      })
      const data = await res.json()
      if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Update failed')
      setEditing(null)
      await load({ silent: true })
      notifyRosterChanged()
    } finally { setBusyId(null) }
  }

  async function saveSlot(row, values) {
    setBusyId(row.id)
    try {
      const res = await fetch(`${API}/candidates/interviews/slots/${row.id}`, {
        method: 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values),
      })
      const data = await res.json()
      if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Update failed')
      setEditing(null)
      await load({ silent: true })
      notifyRosterChanged()
    } finally { setBusyId(null) }
  }

  async function removeSlot(row) {
    const ok = await confirm({
      title: 'Remove interview slot?',
      message: `Remove slot for ${row.name}? The candidate record stays in Candidates.`,
      confirmLabel: 'Remove',
      variant: 'danger',
    })
    if (!ok) return
    setBusyId(row.id)
    setError('')
    try {
      const res = await fetch(`${API}/candidates/interviews/slots/${row.id}`, { method: 'DELETE', credentials: 'include' })
      const data = await res.json()
      if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Remove failed')
      await load({ silent: true })
      notifyRosterChanged()
    } catch (err) { setError(err.message || 'Remove failed') } finally { setBusyId(null) }
  }

  const title = isDashboard
    ? (hasRange && dashboardFromDate !== dashboardToDate
      ? `${formatDayLabel(dashboardFromDate)} – ${formatDayLabel(dashboardToDate)}`
      : formatDayLabel(dashboardFromDate || day))
    : "Today's interview roster"

  // An empty table has to name the day it is empty for, or a date picked in
  // the calendar and a date the roster is actually showing look the same.
  const activeFilterLabels = [
    effectiveAttendee && `attendee ${effectiveAttendee}`,
    effectiveRound && `level ${effectiveRound}`,
    effectiveTechnology && `profile ${effectiveTechnology}`,
    effectiveCandidate && `candidate ${effectiveCandidate}`,
    effectiveSearch.trim() && `search "${effectiveSearch.trim()}"`,
  ].filter(Boolean)
  // This list is not about a date, so an empty one must not name one.
  const emptyHeadline = unresolvedOnly
    ? 'Nothing is waiting for a status update'
    : hasRange && dashboardFromDate !== dashboardToDate
      ? `No interviews between ${formatDayLabel(dashboardFromDate)} and ${formatDayLabel(dashboardToDate)}`
      : `No interviews on ${formatDayLabel(dashboardFromDate || day)}`
  const emptyHint = activeFilterLabels.length
    ? `Nothing matches ${activeFilterLabels.join(' · ')} — clear the filters${unresolvedOnly ? '.' : ', or pick another date.'}`
    : unresolvedOnly
      ? 'Every interview on record has an outcome against it.'
      : 'Pick another date in the calendar, or switch the period above.'

  const scopeHint = handlerView
    ? `${reference} — your interview roster`
    : effectiveAttendee
      ? `Attendee: ${effectiveAttendee}`
      : 'All Referrers'

  return (
    <section className={isDashboard ? 'ops-dash-roster' : 'admin-card admin-card--full ops-interview-roster'}>
      {!isDashboard && (
      <header className="ops-checklist-header">
        <>
            <div>
              <h2>{title}</h2>
              <p className="admin-hint">
                <strong>{scopeHint}</strong> · <strong>{formatDayLabel(day)}</strong>
                {' · '}<strong>{counts.attended_count}</strong> attended · <strong>{counts.count}</strong> scheduled
              </p>
            </div>
            <div className="ops-checklist-header-actions">
              <input
                className="cand-input ops-checklist-date"
                type="date"
                value={day}
                onChange={e => setDay(e.target.value)}
                aria-label="Interview day"
              />
              <select
                className="cand-input ops-checklist-ref-select"
                value={attendeeFilter}
                onChange={e => setAttendeeFilter(e.target.value)}
                aria-label="Filter by attendee"
              >
                <option value="">All attendees</option>
                {ATTENDEES.map(name => <option key={name} value={name}>{name}</option>)}
              </select>
              <select
                className="cand-input ops-checklist-ref-select"
                value={candidateFilter}
                onChange={e => setCandidateFilter(e.target.value)}
                aria-label="Filter by candidate name"
              >
                <option value="">All candidates</option>
                {candidateOptions.map(({ value, label }) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
              {canManage && (
                <>
                  <button type="button" className="btn btn--primary btn--sm" onClick={() => setDay(tomorrowIso())}>
                    + Add slot for tomorrow
                  </button>
                </>
              )}
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => load()}>Refresh</button>
            </div>
          </>
      </header>
      )}

      {error && <p className="admin-error" role="alert">{error}</p>}

      {loading && rows.length === 0 ? (
        <p className="ops-checklist-empty">Loading interview roster…</p>
      ) : rows.length === 0 ? (
        <div className="ops-checklist-empty ops-roster-empty" role="status">
          <strong>{emptyHeadline}</strong>
          <span>{emptyHint}</span>
        </div>
      ) : (
        <div className={`ops-interview-table-wrap ta-table-responsive ta-table-responsive--cards${isDashboard ? ' ops-dash-table-wrap' : ' ops-interview-table-wrap--bounded'}`}>
          <div className="ta-table-responsive__scroll">
            <table className={`ops-interview-table${isDashboard ? ' ops-dash-table ops-dash-table--v3' : ''}`}>
          <thead>
            <tr>
              <th>Date</th>
              <th>Time</th>
              <th>Candidate</th>
              <th>Technology</th>
              <th>Round</th>
              {!handlerView && !effectiveAttendee && <th>Attendee</th>}
              <th>Attendance</th>
              <th>Screenshot</th>
              <th>Notes</th>
              {canManage && <th aria-label="Actions" />}
            </tr>
          </thead>
          <tbody>
            {rows.filter(row => matchesStatusFilter(resolvedStatus(row), dashboardStatusFilter)).map(row => {
              const status = resolvedStatus(row)
              const bookingSource = bookingSourceMeta(row)
              return (
                <tr key={row.id} className={`ops-interview-row ops-interview-row--${statusTone(status)}`}>
                  <td data-label="Date" className="ops-interview-date">
                    {formatDayLabel(row.date)}
                  </td>
                  <td data-label="Time" className="ops-interview-time">
                    {[row.time, row.time_end].filter(Boolean).map(formatClockTime).join(' – ') || '—'}
                  </td>
                  <td data-label="Candidate">
                    {/* Name and number are one thing to read, so they are one
                        box to lay out: on a phone they stay together and the
                        booking-source chip wraps under them instead of being
                        squeezed onto their line. `display:contents` keeps this
                        wrapper out of the desktop's layout entirely. */}
                    <span className="ops-interview-identity">
                      <strong>{row.name}</strong>
                      {row.phone && <span className="ops-interview-phone">{row.phone}</span>}
                    </span>
                    <span
                      className={`ops-booking-source ops-booking-source--${bookingSource.tone}`}
                      title={bookingSource.title}
                    >
                      {bookingSource.label}
                    </span>
                    {/* An assessment holds a roster slot like an interview
                        but is a test the candidate sits alone, inside a
                        window. The row has to say which it is. */}
                    {row.booking_type === 'Assessment' && (
                      <span
                        className="ops-booking-type ops-booking-type--assessment"
                        title="Online assessment, booked inside the window the invitation allowed"
                      >
                        Assessment
                      </span>
                        )}
                      </td>
                      <td data-label="Technology">{row.technology || '—'}</td>
                      <td data-label="Round">{row.interview_round || 'Round not specified'}</td>
                      {!handlerView && !effectiveAttendee && (
                        <td data-label="Attendee">{row.interview_attendee_resolved || row.interview_attendee || 'Bhavana'}</td>
                      )}
                      <td data-label="Status" className="ops-interview-attendance-cell">
                        <div className="ops-interview-attendance-form">
                          <AttendanceSelect
                            value={status === 'pending' ? '' : status}
                            disabled={busyId === row.id}
                            ariaLabel={`Attendance for ${row.name}`}
                            onChange={async (val) => {
                              if (!val) { saveAttendance(row, val, row.interview_attendee_resolved || row.interview_attendee || 'Bhavana'); return }
                              const label = statusLabel(val)
                              // Show attendee selection before confirming
                              setEditing({ row, mode: 'attendee-with-status', targetStatus: val, targetLabel: label })
                            }}
                          />
                          <span className={`ops-status-pill ops-status-pill--${statusTone(status)}`}>
                            {statusLabel(status)}
                          </span>
                        </div>
                      </td>
                      <td data-label="Screenshot" className="ops-slot-shot-cell">
                        {row.slot_screenshot_proof
                          ? <button type="button" className="ops-slot-shot-thumb" onClick={() => setScreenshotRow(row)} title={`View booking screenshot for ${row.name}`}><img src={`${API}${row.slot_screenshot_proof.url}`} alt="" loading="lazy" /><span>View</span></button>
                          : <span className="ops-slot-shot-empty">Not available</span>}
                      </td>
                      <td data-label="Notes" className="ops-interview-notes-cell">
                        {row.interview_feedback && (
                          <span className={`ops-feedback-pill ops-feedback-pill--${row.interview_feedback}`}>
                            {row.interview_feedback === 'positive' ? 'Positive' : 'Negative'}
                          </span>
                        )}
                        {row.interview_attendance_remark
                          ? <span className="ops-interview-notes-text" title={row.interview_attendance_remark}>{row.interview_attendance_remark}</span>
                          : (row.interview_feedback ? null : '—')}
                      </td>
                      {canManage && <td data-label="Actions" className="ops-dash-attend-cell"><RowActions row={row} busy={busyId === row.id} canEditAttendee={canEditAttendee} onEditAttendee={() => setEditing({ row, mode: 'attendee' })} onEditSlot={() => setEditing({ row, mode: 'slot' })} onRemove={removeSlot} /></td>}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {editing && <SlotEditModal row={editing.row} mode={editing.mode} targetStatus={editing.targetStatus} targetLabel={editing.targetLabel} busy={busyId === editing.row.id} onClose={() => setEditing(null)} onSave={values => editing.mode === 'attendee' ? saveAttendee(editing.row, values.attendee) : editing.mode === 'attendee-with-status' ? saveAttendance(editing.row, values.status, values.attendee, values.remark, values.feedback, values.result) : saveSlot(editing.row, values)} />}
      {screenshotRow && <SlotScreenshotModal row={screenshotRow} onClose={() => setScreenshotRow(null)} />}
    </section>
  )
}
