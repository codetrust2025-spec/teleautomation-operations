import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'
import { useConfirm } from '../context/ConfirmContext.jsx'
import { formatClockTime } from '../utils/istTime.js'
import { bookingSourceMeta as sharedBookingSourceMeta } from '../utils/bookingSource.js'
import { addDaysIso, todayIso } from './calendarDates.js'
import { publishPendingWorkChanged } from './PendingWorksProvider.jsx'
import { STATUS_OPTIONS, countStatusRows, emptyStatusCounts, matchesStatusFilter, statusLabel, statusTone } from './interviewStatuses.js'

const ATTENDEES = ['Nikhila', 'Bhavana', 'Tool']

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
  const [date, setDate] = useState(row.date || '')
  const [time, setTime] = useState(row.time || '')
  const [timeEnd, setTimeEnd] = useState(row.time_end || '')
  const [notes, setNotes] = useState(row.notes || '')
  const [round, setRound] = useState(row.interview_round || '')
  const [technology, setTechnology] = useState((row.technology || '').trim())
  const [error, setError] = useState('')
  const attendeeOnly = mode === 'attendee'
  const attendeeWithStatus = mode === 'attendee-with-status'
  // Feedback only makes sense once an interview actually happened.
  const wantsFeedback = attendeeWithStatus && targetStatus === 'attended'
  async function submit(event) {
    event.preventDefault()
    if (attendeeWithStatus && !remark.trim()) { setError('Please add a note about the interview.'); return }
    if (wantsFeedback && !feedback) { setError('Select whether the interview feedback was positive or negative.'); return }
    setError('')
    try {
      if (attendeeOnly) {
        await onSave({ attendee })
      } else if (attendeeWithStatus) {
        await onSave({ attendee, status: targetStatus, remark: remark.trim(), feedback: wantsFeedback ? feedback : '' })
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
          {(attendeeOnly || attendeeWithStatus) ? <><label className="cand-field cand-field--span2"><span className="cand-field-label">Attendee{attendeeWithStatus ? ' (who attended the interview?)' : ''}</span><select className="cand-input" value={attendee} onChange={event => setAttendee(event.target.value)} required autoFocus>{ATTENDEES.map(name => <option key={name} value={name}>{name}</option>)}</select></label>{wantsFeedback && <label className="cand-field cand-field--span2"><span className="cand-field-label">Interview feedback *</span><select className="cand-input" value={feedback} onChange={event => setFeedback(event.target.value)} required><option value="">Select feedback</option><option value="positive">Positive</option><option value="negative">Negative</option></select></label>}{attendeeWithStatus && <label className="cand-field cand-field--span2"><span className="cand-field-label">Note / remark *</span><input className="cand-input" value={remark} onChange={event => setRemark(event.target.value)} placeholder="e.g. Interview went well, next round scheduled" required /></label>}</> : <><label className="cand-field"><span className="cand-field-label">Date</span><input className="cand-input" type="date" value={date} onChange={event => setDate(event.target.value)} required /></label><label className="cand-field"><span className="cand-field-label">Start time</span><input className="cand-input" type="time" value={time} onChange={event => setTime(event.target.value)} required /></label><label className="cand-field"><span className="cand-field-label">End time</span><input className="cand-input" type="time" value={timeEnd} onChange={event => setTimeEnd(event.target.value)} required /></label><label className="cand-field"><span className="cand-field-label">Interview round</span><select className="cand-input" value={round} onChange={event => setRound(event.target.value)}><option value="">Select round</option><option value="L1">L1</option><option value="L2">L2</option><option value="HR">HR</option><option value="Final">Final</option><option value="Screening">Screening</option></select></label><label className="cand-field cand-field--span2"><span className="cand-field-label">Technology *</span><select className="cand-input" value={technology} onChange={event => setTechnology(event.target.value)} required><option value="">Select technology</option>{technology && !TECHNOLOGIES.includes(technology) && <option value={technology}>{technology}</option>}{TECHNOLOGIES.map(name => <option key={name} value={name}>{name}</option>)}</select></label><label className="cand-field cand-field--span2"><span className="cand-field-label">Notes</span><input className="cand-input" value={notes} onChange={event => setNotes(event.target.value)} /></label></>}
          {error && <p className="admin-error cand-field--span2">{error}</p>}
        </div>
        <footer className="cand-modal-footer"><button type="button" className="cand-btn cand-btn--ghost" onClick={onClose} disabled={busy}>Cancel</button><button type="submit" className={`cand-btn cand-btn--primary${attendeeWithStatus && (targetStatus === 'not_attended' || targetStatus === 'cancelled') ? ' cand-btn--danger' : ''}`} disabled={busy}>{busy ? 'Saving…' : attendeeWithStatus ? targetLabel : 'Save changes'}</button></footer>
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
  dashboardCandidateTypeFilter = '',
  dashboardStatusFilter = '',
  upcomingOnly = false,
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
  const [awaitingRows, setAwaitingRows] = useState([])
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

  const effectiveAttendee = isDashboard ? dashboardAttendeeFilter : attendeeFilter
  const effectiveSearch = isDashboard ? dashboardCandidateSearch : candidateFilter
  const effectiveChannel = isDashboard ? dashboardCandidateTypeFilter : channelFilter
  const effectiveRound = isDashboard ? dashboardRoundFilter : ''
  const effectiveTechnology = isDashboard ? dashboardTechnologyFilter : ''

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true)
    try {
      const params = new URLSearchParams()
      let url = `${API}/candidates/interviews/daily`
      if (hasRange && !isSingleDayRange) {
        url = `${API}/candidates/interviews/monitor`
        params.set('from', dashboardFromDate)
        params.set('to', dashboardToDate)
      } else {
        params.set('date', hasRange ? dashboardFromDate : day)
      }
      if (effectiveAttendee) params.set('attendee', effectiveAttendee)
      const search = effectiveSearch.trim()
      if (search) params.set('search', search)
      if (effectiveChannel) params.set('channel', effectiveChannel)
      if (effectiveRound) params.set('round', effectiveRound)
      if (effectiveTechnology) params.set('technology', effectiveTechnology)
      if (upcomingOnly) params.set('upcoming_only', 'true')

      const res = await fetch(`${url}?${params}`, { credentials: 'include', cache: 'no-store' })
      if (!(res.headers.get('content-type') || '').includes('application/json')) {
        throw new Error(`Server returned ${res.status} — hard refresh and try again`)
      }
      const data = await res.json()
      if (!res.ok || data.status !== 'ok') {
        throw new Error(data.message || data.detail || `Failed to load roster (${res.status})`)
      }
      setRows(data.interviews || [])
      const awaiting = data.awaiting_interviews || []
      setAwaitingRows(awaiting)
      // Counted from the rows just loaded rather than read off the payload:
      // the top counter and the sidebar badge disagreed with the table and
      // with each other because all three measured different things.
      const nextCounts = countStatusRows(data.interviews || [], resolvedStatus)
      setCounts(nextCounts)
      rosterCountsRef.current?.(nextCounts, { isUpcomingView: upcomingOnly })
      const totalPending = upcomingOnly
        ? (nextCounts.pending_count || 0) + awaiting.length
        : nextCounts.pending_count
      publishPendingWorkChanged(totalPending)
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
    effectiveChannel,
    effectiveRound,
    effectiveTechnology,
    hasRange,
    isSingleDayRange,
    upcomingOnly,
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

  async function saveAttendance(row, status, attendee, remark, feedback) {
    setBusyId(row.id)
    setError('')
    try {
      const body = { status: status || '', remark: remark || row.interview_attendance_remark || '' }
      if (status && (status === 'attended' || status === 'not_attended')) {
        body.attendee = attendee || row.interview_attendee_resolved || row.interview_attendee || 'Bhavana'
      }
      if (feedback !== undefined) body.feedback = feedback || ''
      const res = await fetch(`${API}/candidates/${row.id}/interview-attendance`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Update failed')
      setEditing(null)
      await load({ silent: true })
      notifyRosterChanged()
    } catch (err) {
      setError(err.message || 'Update failed')
    } finally {
      setBusyId(null)
    }
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
    effectiveSearch.trim() && `search "${effectiveSearch.trim()}"`,
  ].filter(Boolean)
  const emptyHeadline = hasRange && dashboardFromDate !== dashboardToDate
    ? `No interviews between ${formatDayLabel(dashboardFromDate)} and ${formatDayLabel(dashboardToDate)}`
    : `No interviews on ${formatDayLabel(dashboardFromDate || day)}`
  const emptyHint = activeFilterLabels.length
    ? `Nothing matches ${activeFilterLabels.join(' · ')} — clear the filters, or pick another date.`
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

      {loading && rows.length === 0 && awaitingRows.length === 0 ? (
        <p className="ops-checklist-empty">Loading interview roster…</p>
      ) : rows.length === 0 && awaitingRows.length === 0 ? (
        <div className="ops-checklist-empty ops-roster-empty" role="status">
          <strong>{emptyHeadline}</strong>
          <span>{emptyHint}</span>
        </div>
      ) : (
        <>
          {rows.length === 0 && upcomingOnly ? (
            <div className="ops-checklist-empty ops-roster-empty" role="status">
              <strong>{emptyHeadline}</strong>
              <span>{emptyHint}</span>
            </div>
          ) : rows.length > 0 ? (
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
                            <strong>{row.name}</strong>
                            {row.phone && <span className="ops-interview-phone">{row.phone}</span>}
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
                          <td data-label="Round">{row.interview_round || 'Not specified in email'}</td>
                          {!handlerView && !effectiveAttendee && (
                            <td data-label="Attendee">{row.interview_attendee_resolved || row.interview_attendee || 'Bhavana'}</td>
                          )}
                          <td data-label="Attendance" className="ops-interview-attendance-cell">
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
          ) : null}

          {upcomingOnly && awaitingRows.filter(row => matchesStatusFilter(resolvedStatus(row), dashboardStatusFilter)).length > 0 && (
            <section className="ops-awaiting-outcome-section" aria-label="Awaiting outcome interviews">
              <div className="ops-awaiting-outcome-header">
                <div className="ops-awaiting-outcome-title">
                  <h3>Awaiting outcome</h3>
                  <span className="ops-awaiting-badge">
                    {awaitingRows.filter(row => matchesStatusFilter(resolvedStatus(row), dashboardStatusFilter)).length}
                  </span>
                </div>
                <p className="ops-awaiting-outcome-desc">
                  Past interviews with no final attendance status logged. Record an outcome to resolve them.
                </p>
              </div>
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
                      {awaitingRows.filter(row => matchesStatusFilter(resolvedStatus(row), dashboardStatusFilter)).map(row => {
                        const status = resolvedStatus(row)
                        const bookingSource = bookingSourceMeta(row)
                        return (
                          <tr key={`awaiting-${row.id}`} className={`ops-interview-row ops-interview-row--${statusTone(status)} ops-interview-row--awaiting`}>
                            <td data-label="Date" className="ops-interview-date">
                              {formatDayLabel(row.date)}
                            </td>
                            <td data-label="Time" className="ops-interview-time">
                              {[row.time, row.time_end].filter(Boolean).map(formatClockTime).join(' – ') || '—'}
                            </td>
                            <td data-label="Candidate">
                              <strong>{row.name}</strong>
                              {row.phone && <span className="ops-interview-phone">{row.phone}</span>}
                              <span
                                className={`ops-booking-source ops-booking-source--${bookingSource.tone}`}
                                title={bookingSource.title}
                              >
                                {bookingSource.label}
                              </span>
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
                            <td data-label="Round">{row.interview_round || 'Not specified in email'}</td>
                            {!handlerView && !effectiveAttendee && (
                              <td data-label="Attendee">{row.interview_attendee_resolved || row.interview_attendee || 'Bhavana'}</td>
                            )}
                            <td data-label="Attendance" className="ops-interview-attendance-cell">
                              <div className="ops-interview-attendance-form">
                                <AttendanceSelect
                                  value={status === 'pending' ? '' : status}
                                  disabled={busyId === row.id}
                                  ariaLabel={`Attendance for ${row.name}`}
                                  onChange={async (val) => {
                                    if (!val) { saveAttendance(row, val, row.interview_attendee_resolved || row.interview_attendee || 'Bhavana'); return }
                                    const label = statusLabel(val)
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
            </section>
          )}
        </>
      )}
      {editing && <SlotEditModal row={editing.row} mode={editing.mode} targetStatus={editing.targetStatus} targetLabel={editing.targetLabel} busy={busyId === editing.row.id} onClose={() => setEditing(null)} onSave={values => editing.mode === 'attendee' ? saveAttendee(editing.row, values.attendee) : editing.mode === 'attendee-with-status' ? saveAttendance(editing.row, values.status, values.attendee, values.remark, values.feedback) : saveSlot(editing.row, values)} />}
      {screenshotRow && <SlotScreenshotModal row={screenshotRow} onClose={() => setScreenshotRow(null)} />}
    </section>
  )
}
