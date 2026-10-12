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
import { STATUS_OPTIONS, canonicalStatus, countStatusRows, emptyStatusCounts, matchesStatusFilter, statusLabel, statusTone } from './interviewStatuses.js'
import { DarkSelect } from './DarkSelect.jsx'
import { Icon } from '../components/ui/Icon.jsx'
import { DEFAULT_ROSTER_SORT, sortRosterRows } from './rosterSort.js'

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

// What the Notes column shows for a stored feedback value: its own label and
// a tone. The column used to print "Positive" for the legacy "positive" and
// "Negative" for everything else, so a row the operator marked Good or
// Excellent read "Negative". Every value now names itself; only "negative"
// says Negative.
const FEEDBACK_TONES = {
  excellent: 'positive',
  good: 'positive',
  positive: 'positive',
  average: 'neutral',
  needs_improvement: 'caution',
  negative: 'negative',
}

export function feedbackMeta(value) {
  const key = String(value || '').trim()
  if (!key) return null
  const option = FEEDBACK_OPTIONS.find(o => o.value === key)
  const label = option
    ? option.label
    : key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, ' ')
  return { value: key, label, tone: FEEDBACK_TONES[key] || 'neutral' }
}

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

/** "Mon, 12 Oct"; the year is added when it is not this year ("Thu, 16 Sept 2027").
 *  Without it a 2027 interview read as "16 Sept" and Latest to Oldest looked
 *  as if it put September above October (11 Oct). */
export function formatDayLabel(iso) {
  if (!iso) return '—'
  try {
    const day = iso.slice(0, 10)
    return new Date(`${day}T12:00:00`).toLocaleDateString('en-IN', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      ...(day.slice(0, 4) !== todayIso().slice(0, 4) ? { year: 'numeric' } : {}),
      timeZone: 'Asia/Kolkata',
    })
  } catch {
    return iso
  }
}

function resolvedStatus(row) {
  return (row?.interview_attendance_status_resolved || row?.interview_attendance_status || '').trim().toLowerCase()
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

/**
 * The booking screenshot's thumbnail. Some stored screenshots no longer have
 * their file (proofs lost in the August cutover), and the browser drew its
 * broken-image glyph for them. A file that fails to load shows a plain icon
 * instead, and still opens the viewer, which says what is missing.
 */
function SlotShotThumb({ row, onOpen }) {
  const [failed, setFailed] = useState(false)
  return (
    <button
      type="button"
      className={`ops-slot-shot-thumb${failed ? ' ops-slot-shot-thumb--missing' : ''}`}
      onClick={onOpen}
      title={failed ? `Booking screenshot for ${row.name} could not be loaded` : `View booking screenshot for ${row.name}`}
    >
      {failed
        ? <Icon name="image-off" size={18} />
        : <img src={`${API}${row.slot_screenshot_proof.url}`} alt="" loading="lazy" onError={() => setFailed(true)} />}
      <span>View</span>
    </button>
  )
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

// Making the whole row a trigger must never swallow a click meant for a control
// inside it (the attendance select, the screenshot View button, a link, an
// input, the kebab itself, the open menu, …). We decide by walking up from the
// click target to the row with Element.closest() — robust and not coordinate
// based — and bail if we pass through anything interactive first.
const ROW_INTERACTIVE_SELECTOR = [
  'button', 'a', 'select', 'input', 'textarea', 'label',
  '[role="menu"]', '[role="menuitem"]', '[role="dialog"]',
  '.ops-attendance-select', '.ops-slot-shot-thumb',
].join(',')

function rowEventHitsInteractive(event, rowEl) {
  const target = event.target
  if (!(target instanceof Element)) return false
  const hit = target.closest(ROW_INTERACTIVE_SELECTOR)
  // A match only counts when it is inside this row (closest can escape via the
  // portalled menu, which lives on document.body — that is handled separately).
  return !!hit && (!rowEl || rowEl.contains(hit))
}

function SlotEditModal({ row, busy, onClose, onSave, onRemove }) {
  const [attendee, setAttendee] = useState(row.interview_attendee_resolved || row.interview_attendee || 'Bhavana')
  const [feedback, setFeedback] = useState(row.interview_feedback || '')
  const [date, setDate] = useState(row.date || '')
  const [time, setTime] = useState(row.time || '')
  const [timeEnd, setTimeEnd] = useState(row.time_end || '')
  // The slot modal's NOTES field edits the interview's attendance remark —
  // the exact text the roster NOTES column shows — not a separate slot note.
  const [remarkNote, setRemarkNote] = useState(row.interview_attendance_remark || '')
  // Attendance is changed here, with the slot's other fields (12 Oct): the row
  // only shows it. Starting from the stored outcome, a change asks for exactly
  // what the old "Mark as ..." dialog asked for -- who attended (Attended / Not
  // attended), how it went (Attended), and a note -- and saves in the same Save.
  const storedStatus = canonicalStatus(resolvedStatus(row))
  const [slotStatus, setSlotStatus] = useState(storedStatus)
  const [round, setRound] = useState(row.interview_round || '')
  const [technology, setTechnology] = useState((row.technology || '').trim())
  const [error, setError] = useState('')
  // The remark is part of the attendance record, which only exists once an
  // outcome is logged. With no outcome there is nothing to edit, so the field
  // is shown disabled rather than letting a note be typed that cannot be saved.
  const slotNoteStatus = resolvedStatus(row)
  const statusChanged = slotStatus !== storedStatus
  const changeNeedsAttendee = statusChanged && (slotStatus === 'attended' || slotStatus === 'not_attended')
  const changeNeedsFeedback = statusChanged && slotStatus === 'attended'
  const changeNeedsNote = statusChanged && slotStatus !== ''
  const canEditNote = statusChanged
    ? slotStatus !== ''
    : ['attended', 'not_attended', 'cancelled'].includes(slotNoteStatus)
  // Feedback is a separate, read-only value here; never merged into the remark.
  const slotNoteFeedback = feedbackMeta(row.interview_feedback)
  const noteChanged = canEditNote && remarkNote !== (row.interview_attendance_remark || '')
  // The slot's own fields are saved -- and checked -- only when one of them
  // changed. Recording an outcome or a note on an older row then never asks for
  // an end time it never had, and never rewrites the slot it did not touch.
  const slotChanged = date !== (row.date || '') || time !== (row.time || '') || timeEnd !== (row.time_end || '')
    || round !== (row.interview_round || '') || technology !== (row.technology || '').trim()
  const slotFieldsRequired = slotChanged || (!statusChanged && !noteChanged)
  // Everything a change of outcome needs before it can be saved. Save reads the
  // same predicate it enforces, so a disabled button and a rejected submit can
  // never disagree.
  const feedbackValid = !changeNeedsFeedback || !!feedback
  const noteValid = !changeNeedsNote || !!remarkNote.trim()
  const attendeeValid = !changeNeedsAttendee || !!attendee
  const canSubmit = attendeeValid && feedbackValid && noteValid
  async function submit(event) {
    event.preventDefault()
    setError('')
    try {
      if (slotFieldsRequired && !technology) { setError('Please select the interview technology.'); return }
      if (changeNeedsAttendee && !attendee) { setError('Select who attended the interview.'); return }
      if (changeNeedsFeedback && !feedback) { setError('Select how the interview went (Interview feedback).'); return }
      if (changeNeedsNote && !remarkNote.trim()) { setError('Please add a note about the interview.'); return }
      await onSave({
        date, time, time_end: timeEnd, interview_round: round, technology,
        slotChanged,
        // The remark edit rides alongside the slot fields but saves through
        // the attendance endpoint; only sent when it is editable and changed.
        noteRemark: remarkNote,
        noteChanged,
        statusChange: statusChanged ? { status: slotStatus, attendee, feedback: changeNeedsFeedback ? feedback : '' } : null,
      })
      onClose()
    } catch (err) {
      setError(err.message || 'Save failed')
    }
  }
  return (
    <div className="cand-modal-backdrop" onMouseDown={event => event.target === event.currentTarget && onClose()}>
      <form className="cand-modal ops-slot-modal" onSubmit={submit}>
        <header className="cand-modal-header"><div><h3 className="cand-modal-title">Edit interview slot</h3><p className="cand-modal-sub">{row.name}</p></div><button type="button" className="cand-modal-close" onClick={onClose} aria-label="Close">×</button></header>
        <div className="cand-modal-body">
          <><label className="cand-field"><span className="cand-field-label">Date</span><input className="cand-input" type="date" value={date} onChange={event => setDate(event.target.value)} required={slotFieldsRequired} /></label><label className="cand-field"><span className="cand-field-label">Start time</span><input className="cand-input" type="time" value={time} onChange={event => setTime(event.target.value)} required={slotFieldsRequired} /></label><label className="cand-field"><span className="cand-field-label">End time</span><input className="cand-input" type="time" value={timeEnd} onChange={event => setTimeEnd(event.target.value)} required={slotFieldsRequired} /></label><label className="cand-field"><span className="cand-field-label">Interview round</span><select className="cand-input" value={round} onChange={event => setRound(event.target.value)}><option value="">Select round</option><option value="L1">L1</option><option value="L2">L2</option><option value="HR">HR</option><option value="Final">Final</option><option value="Screening">Screening</option></select></label><label className="cand-field cand-field--span2"><span className="cand-field-label">Technology *</span><select className="cand-input" value={technology} onChange={event => setTechnology(event.target.value)} required={slotFieldsRequired}><option value="">Select technology</option>{technology && !TECHNOLOGIES.includes(technology) && <option value={technology}>{technology}</option>}{TECHNOLOGIES.map(name => <option key={name} value={name}>{name}</option>)}</select></label><label className="cand-field cand-field--span2"><span className="cand-field-label">Attendance</span><select className="cand-input ops-slot-status-select" value={slotStatus} onChange={event => setSlotStatus(event.target.value)} aria-label="Attendance">{STATUS_OPTIONS.map(opt => <option key={opt.value || 'pending'} value={opt.value}>{opt.label}</option>)}</select>{statusChanged && <small className="cand-field-hint">{slotStatus ? `Saving marks this interview "${statusLabel(slotStatus)}".` : 'Saving returns this interview to Pending.'}</small>}</label>{changeNeedsAttendee && <div className="cand-field cand-field--span2"><span className="cand-field-label">Attendee (who attended the interview?)</span><DarkSelect ariaLabel="Attendee (who attended the interview?)" value={attendee} options={ATTENDEES.map(name => ({ value: name, label: name }))} onChange={setAttendee} placeholder="Select attendee" required /></div>}{changeNeedsFeedback ? <div className="cand-field cand-field--span2"><span className="cand-field-label">Interview feedback <span className="cand-field-required-tag">Required</span></span><DarkSelect ariaLabel="Interview feedback" value={feedback} options={feedbackOptionsFor(feedback)} onChange={setFeedback} placeholder="Select feedback" required /></div> : (slotNoteFeedback && !statusChanged && <div className="cand-field cand-field--span2"><span className="cand-field-label">Interview feedback</span><span className={`ops-feedback-pill ops-feedback-pill--${slotNoteFeedback.tone}`} data-feedback={slotNoteFeedback.value} title={`Interview feedback: ${slotNoteFeedback.label}`}>{slotNoteFeedback.label}</span></div>)}<label className="cand-field cand-field--span2"><span className="cand-field-label">Notes{changeNeedsNote && <> <span className="cand-field-required-tag">Required</span></>}</span><input className="cand-input ops-slot-notes-input" value={remarkNote} onChange={event => setRemarkNote(event.target.value)} disabled={!canEditNote} aria-label="Notes" placeholder={canEditNote ? '' : 'Available once attendance is recorded'} onFocus={event => { const el = event.target; const end = el.value.length; try { el.setSelectionRange(end, end) } catch { /* non-text inputs */ } }} />{!canEditNote && <small className="cand-field-hint">Notes can be edited once the interview has an attendance outcome.</small>}</label></>
          {error && <p className="admin-error cand-field--span2">{error}</p>}
        </div>
        <footer className="cand-modal-footer">{onRemove && <button type="button" className="cand-btn cand-btn--ghost ops-slot-modal__remove" onClick={() => onRemove(row)} disabled={busy}>Remove slot</button>}<button type="button" className="cand-btn cand-btn--ghost" onClick={onClose} disabled={busy}>Cancel</button><button type="submit" className={`cand-btn cand-btn--primary${!canSubmit ? ' cand-btn--disabled' : ''}`} disabled={busy || !canSubmit} aria-disabled={busy || !canSubmit}>{busy ? 'Saving…' : 'Save changes'}</button></footer>
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
  dashboardSortOrder = DEFAULT_ROSTER_SORT,
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
  // Whole-row activation opens the row's Edit dialog directly (12 Oct: the
  // three-dot Actions button, its column and the one-item menu in between are
  // gone). A click on a control inside the row -- screenshot View, the
  // attendance select, any button or input -- keeps its own behaviour, and so
  // does a text selection being made. A click that only bubbled through React
  // from a portal (a dialog on document.body) is not a click on the row.
  const openRowEdit = useCallback(row => setEditing({ row }), [])
  const activateRow = useCallback((row, event) => {
    const rowEl = event.currentTarget
    if (!(event.target instanceof Node) || !rowEl.contains(event.target)) return
    if (rowEventHitsInteractive(event, rowEl)) return
    const selection = typeof window !== 'undefined' && window.getSelection && window.getSelection()
    if (selection && String(selection).length > 0) return
    openRowEdit(row)
  }, [openRowEdit])
  // Enter / Space open Edit when the row itself has focus; keys aimed at a
  // control inside the row are left to that control.
  const activateRowByKey = useCallback((row, event) => {
    if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return
    if (event.target !== event.currentTarget) return
    event.preventDefault()
    openRowEdit(row)
  }, [openRowEdit])
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
  // What the roster last told the sidebar. The five-second refresh used to
  // announce every load, changed or not, and each announcement made the
  // Pending Works and Slot Booking badges re-read the server: three requests
  // every five seconds for one open Daily Ops tab.
  const lastAnnounced = useRef('')
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
    const announcement = [
      unresolvedOnly ? 'unresolved' : 'pending',
      totalPending,
      visible.map(row => `${row.id}:${row.interview_attendance_status || ''}:${row.date || ''}:${row.time || ''}`).join(','),
    ].join('|')
    if (announcement === lastAnnounced.current) return
    lastAnnounced.current = announcement
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
    // Every five seconds while someone can see it; a hidden tab waits and
    // catches up the moment it is shown again.
    const timer = setInterval(() => { if (!document.hidden) load({ silent: true }) }, 5000)
    const onVisible = () => { if (!document.hidden) load({ silent: true }) }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
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

  // Edit interview slot: the slot fields (date/time/round/technology) save via
  // the slot PATCH, sent only when one of them changed; the attendance outcome
  // and the NOTES field (the attendance remark) save through the attendance
  // endpoint. A note alone re-sends the current status so only the remark
  // changes — feedback and attendee are left untouched (not sent).
  async function saveSlotAndNote(row, values) {
    const { noteRemark, noteChanged, statusChange, slotChanged, ...slotValues } = values
    setBusyId(row.id)
    try {
      if (slotChanged) {
        const res = await fetch(`${API}/candidates/interviews/slots/${row.id}`, {
          method: 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(slotValues),
        })
        const data = await res.json()
        if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Update failed')
      }
      if (statusChange) {
        // The same request the row's attendance dropdown used to send: the
        // attendee for Attended / Not attended, the feedback (empty unless
        // Attended), the note as the remark.
        const status = statusChange.status || ''
        const body = { status, remark: (noteRemark || '').trim() || row.interview_attendance_remark || '' }
        if (status === 'attended' || status === 'not_attended') {
          body.attendee = statusChange.attendee || row.interview_attendee_resolved || row.interview_attendee || 'Bhavana'
        }
        if (status) body.feedback = statusChange.feedback || ''
        const sres = await fetch(`${API}/candidates/${row.id}/interview-attendance`, {
          method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        })
        const sdata = await sres.json()
        if (!sres.ok || sdata.status !== 'ok') throw new Error(sdata.message || 'Attendance update failed')
        applySavedStatus(row.id, status)
      } else if (noteChanged) {
        const currentStatus = resolvedStatus(row)
        const body = { status: currentStatus, remark: (noteRemark || '').trim() }
        // feedback and attendee are intentionally omitted so the server
        // preserves them; the remark is the only field this edit changes.
        const ares = await fetch(`${API}/candidates/${row.id}/interview-attendance`, {
          method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        })
        const adata = await ares.json()
        if (!ares.ok || adata.status !== 'ok') throw new Error(adata.message || 'Note update failed')
      }
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

  // Shown unless one attendee is already chosen (every row would repeat it)
  // or this is a handler's own roster.
  const showAttendeeColumn = !handlerView && !effectiveAttendee

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
          {/* The table is fixed-layout, so without these every column got an
              equal share: Notes as wide as Candidate, Actions as wide as Time.
              One <col> per <th>, in the same order and under the same
              conditions, so the two cannot drift apart. */}
          <colgroup>
            <col className="ops-col ops-col--date" />
            <col className="ops-col ops-col--time" />
            <col className="ops-col ops-col--candidate" />
            <col className="ops-col ops-col--company" />
            <col className="ops-col ops-col--tech" />
            <col className="ops-col ops-col--round" />
            {showAttendeeColumn && <col className="ops-col ops-col--attendee" />}
            <col className="ops-col ops-col--attendance" />
            <col className="ops-col ops-col--screenshot" />
            <col className="ops-col ops-col--notes" />
          </colgroup>
          <thead>
            <tr>
              <th>Date</th>
              <th>Time</th>
              <th>Candidate</th>
              <th>Company</th>
              <th>Technology</th>
              <th>Round</th>
              {showAttendeeColumn && <th>Attendee</th>}
              <th>Attendance</th>
              <th>Screenshot</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {sortRosterRows(rows.filter(row => matchesStatusFilter(resolvedStatus(row), dashboardStatusFilter)), dashboardSortOrder).map(row => {
              const status = resolvedStatus(row)
              const bookingSource = bookingSourceMeta(row)
              return (
                <tr
                  key={row.id}
                  className={`ops-interview-row ops-interview-row--${statusTone(status)}${canManage ? ' ops-interview-row--actionable' : ''}`}
                  {...(canManage ? {
                    onClick: event => activateRow(row, event),
                    onKeyDown: event => activateRowByKey(row, event),
                    tabIndex: 0,
                    'aria-haspopup': 'dialog',
                    'aria-label': `${row.name} — interview row, press Enter to edit`,
                  } : {})}
                >
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
                      <strong title={row.name}>{row.name}</strong>
                      {row.phone && <span className="ops-interview-phone">{row.phone}</span>}
                    </span>
                    {/* Every row said "Candidate booked" on a line of its own.
                        The source is still here, as a small chip that names it
                        on hover and to a screen reader; AI bookings keep a
                        visible "AI" because they are the ones worth noticing.
                        A row with no recorded source shows nothing: "Booked"
                        told nobody anything a roster row does not. */}
                    {bookingSource.known && (
                      <span
                        className={`ops-booking-source ops-booking-source--${bookingSource.tone}`}
                        title={`${bookingSource.label}: ${bookingSource.title}`}
                        role="img"
                        aria-label={bookingSource.label}
                      >
                        <Icon name={bookingSource.tone === 'auto' ? 'sparkles' : 'user-check'} size={11} strokeWidth={2.2} />
                        {bookingSource.tone === 'auto' && <span className="ops-booking-source__text" aria-hidden="true">AI</span>}
                      </span>
                    )}
                    {/* One interview held twice, as the server's one booking
                        rule decides it (12 Oct): same invite, or nothing that
                        tells the two apart. Different interviews at one hour
                        are not flagged. */}
                    {row.duplicate_count > 1 && (
                      <span
                        className="ops-same-slot ops-duplicate-booking"
                        title={`This interview is booked ${row.duplicate_count} times (the same invitation). Keep one and cancel the other${row.duplicate_count > 2 ? 's' : ''}.`}
                      >
                        Duplicate ×{row.duplicate_count}
                      </span>
                    )}
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
                      <td data-label="Company" className="ops-interview-company">
                        {row.interview_company
                          ? <span className="ops-interview-company__name" title={row.interview_company}>{row.interview_company}</span>
                          : <span className="ops-interview-company__none" title="No company recorded for this interview" aria-label="No company recorded">—</span>}
                        {/* The role is recorded far more often than the company,
                            so it shows under either. */}
                        {row.interview_role && <span className="ops-interview-company__role" title={row.interview_role}>{row.interview_role}</span>}
                      </td>
                      <td data-label="Technology">{row.technology || '—'}</td>
                      <td data-label="Round">{row.interview_round || 'Round not specified'}</td>
                      {showAttendeeColumn && (
                        <td data-label="Attendee">{row.interview_attendee_resolved || row.interview_attendee || 'Bhavana'}</td>
                      )}
                      <td data-label="Status" className="ops-interview-attendance-cell">
                        {/* Read-only here (12 Oct): the outcome is changed in the
                            row's Edit dialog, with the note it needs. */}
                        <span className={`ops-attendance-status ops-attendance-status--${statusTone(status)}`} aria-label={`Attendance: ${statusLabel(status)}`}>{statusLabel(status)}</span>
                      </td>
                      <td data-label="Screenshot" className="ops-slot-shot-cell">
                        {row.slot_screenshot_proof
                          ? <SlotShotThumb row={row} onOpen={() => setScreenshotRow(row)} />
                          : <span className="ops-slot-shot-empty" title="No booking screenshot" aria-label="No booking screenshot">—</span>}
                      </td>
                      <td data-label="Notes" className="ops-interview-notes-cell">
                        {(() => {
                          const feedback = feedbackMeta(row.interview_feedback)
                          return feedback && (
                            <span
                              className={`ops-feedback-pill ops-feedback-pill--${feedback.tone}`}
                              data-feedback={feedback.value}
                              title={`Interview feedback: ${feedback.label}`}
                            >
                              {feedback.label}
                            </span>
                          )
                        })()}
                        {row.interview_attendance_remark
                          ? <span className="ops-interview-notes-text" title={row.interview_attendance_remark}>{row.interview_attendance_remark}</span>
                          : (row.interview_feedback ? null : '—')}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {editing && <SlotEditModal row={editing.row} busy={busyId === editing.row.id} onClose={() => setEditing(null)} onRemove={async row => { await removeSlot(row); setEditing(null) }} onSave={values => saveSlotAndNote(editing.row, values)} />}
      {screenshotRow && <SlotScreenshotModal row={screenshotRow} onClose={() => setScreenshotRow(null)} />}
    </section>
  )
}
