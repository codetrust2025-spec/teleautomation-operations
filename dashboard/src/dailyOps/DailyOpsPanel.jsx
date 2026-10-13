import React, { useCallback, useEffect, useRef, useState } from 'react'
import { API } from '../config.js'
import { STATUS_TABS } from './interviewStatuses.js'
import { useAuth } from '../context/AuthContext.jsx'
import { InterviewRoster } from './InterviewRoster.jsx'
import { ROSTER_SORT_OPTIONS, readRosterSort, rosterSortLabel, saveRosterSort } from './rosterSort.js'
import { PendingWorksStrip } from './PendingWorksStrip.jsx'
import { DateCalendarPicker } from './DateCalendarPicker.jsx'
import { DailyOpsFilterMenu } from './DailyOpsFilterMenu.jsx'
import { addDaysIso, isValidIsoDay, todayIso } from './calendarDates.js'
import { Icon } from '../components/ui/Icon.jsx'

const ATTENDEES = ['Nikhila', 'Bhavana', 'Tool']
const ROUNDS = ['L1', 'L2', 'HR', 'Final', 'Screening']

function KpiCard({ label, value, tone = 'default', loading = false, active = false, onClick, title }) {
  return (
    <button
      type="button"
      className={`ops-dash-kpi ops-dash-kpi--${tone}${loading ? ' ops-dash-kpi--loading' : ''}${active ? ' ops-dash-kpi--active' : ''}`}
      onClick={onClick}
      aria-pressed={active}
      title={title}
    >
      <span className="ops-dash-kpi__label">{label}</span>
      <strong className="ops-dash-kpi__value">{loading ? '…' : value}</strong>
    </button>
  )
}

/**
 * What a status counter counts. The tabs and the "Candidate tasks" pill beside
 * them both used to say "pending" -- one for interviews in this view without a
 * status, the other for open work across every candidate -- with nothing on
 * screen to tell the two apart.
 */
export function kpiScope(tab) {
  if (tab.id === 'scheduled') return 'Every interview in the current view'
  if (tab.filterValue === 'pending') return 'Interviews in the current view with no status recorded yet'
  return `Interviews in the current view marked ${tab.label}`
}

const PIE_COLORS = ['#3b82f6', '#22c55e', '#f59e0b', '#a855f7', '#06b6d4', '#f43f5e', '#84cc16']
const candidateColor = (index, count) => count <= PIE_COLORS.length ? PIE_COLORS[index] : `hsl(${Math.round((index * 360) / count)} 78% 55%)`

function BookingPie({ overview = {}, selectedTechnology = '', onTechnologySelect }) {
  const [open, setOpen] = useState(false)
  const [hovered, setHovered] = useState(null)
  const [selected, setSelected] = useState(null)
  useEffect(() => {
    if (!open) return undefined
    const closeOnEscape = event => event.key === 'Escape' && setOpen(false)
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [open])
  const total = Number(overview.total || 0)
  const rawCandidates = overview.by_candidate || []
  const candidates = rawCandidates
  let cursor = 0
  const stops = candidates.map((item, index) => {
    const start = cursor
    cursor += total ? (Number(item.count || 0) / total) * 100 : 0
    return `${candidateColor(index, candidates.length)} ${start}% ${cursor}%`
  })
  const background = total && stops.length ? `conic-gradient(${stops.join(', ')})` : 'conic-gradient(#273244 0 100%)'
  let segmentCursor = 0
  const segments = candidates.map((item, index) => {
    const percent = total ? (Number(item.count || 0) / total) * 100 : 0
    const segment = { ...item, percent, offset: segmentCursor, color: candidateColor(index, candidates.length) }
    segmentCursor += percent
    return segment
  })
  const activeCandidate = hovered || selected
  const displayedLevels = selected?.levels || overview.by_level || []
  const displayedTechnologies = selected?.technologies || overview.by_technology || []
  const selectCandidate = segment => setSelected(current => current?.name === segment.name ? null : segment)

  return (
    <><section className="ops-booking-pie" aria-label={`${total} interviews booked across all candidates`}>
      <div className="ops-booking-pie__chart" style={{ background }}>
        <span><strong>{total}</strong><small>booked</small></span>
      </div>
      <div className="ops-booking-pie__details">
        <strong className="ops-booking-pie__title">Bookings by candidate</strong>
        <div className="ops-booking-pie__legend">
          {candidates.map((item, index) => <span key={item.name}><i style={{ background: candidateColor(index, candidates.length) }} /><b>{item.name}</b><em>{item.count}</em></span>)}
        </div>
        <div className="ops-booking-pie__levels"><small>Levels</small>{(overview.by_level || []).map(item => <span key={item.name}><b>{item.name}</b>{item.count}</span>)}</div>
      </div>
      <button type="button" className="ops-booking-pie__open" onClick={() => setOpen(true)}>Open analytics</button>
    </section>
    {open && <div className="ops-booking-modal" role="presentation" onMouseDown={event => event.target === event.currentTarget && setOpen(false)}>
      <section className="ops-booking-modal__panel" role="dialog" aria-modal="true" aria-label="Interview booking analytics">
        <header><div><h2>Interview booking analytics</h2><p>Candidate distribution and interview levels for the selected period</p></div><button type="button" onClick={() => setOpen(false)} aria-label="Close analytics">&#10005;</button></header>
        <div className="ops-booking-modal__body">
          <div className="ops-booking-modal__donut-wrap">
            <svg className="ops-booking-modal__donut" viewBox="0 0 120 120" role="img" aria-label="Bookings by candidate">
              <circle cx="60" cy="60" r="46" pathLength="100" className="ops-booking-modal__track" />
              {segments.map(segment => <circle key={segment.name} cx="60" cy="60" r="46" pathLength="100" fill="none" stroke={segment.color} strokeWidth={activeCandidate?.name === segment.name ? 19 : 16} strokeDasharray={`${segment.percent} ${100 - segment.percent}`} strokeDashoffset={-segment.offset} transform="rotate(-90 60 60)" className="ops-booking-modal__segment" onMouseEnter={() => setHovered(segment)} onMouseLeave={() => setHovered(null)} onClick={() => selectCandidate(segment)}><title>{segment.name}: {segment.count} bookings ({segment.percent.toFixed(1)}%)</title></circle>)}
            </svg>
            <div className="ops-booking-modal__center">{activeCandidate ? <><strong>{activeCandidate.count}</strong><span>{activeCandidate.name}</span><small>{activeCandidate.percent.toFixed(1)}%</small></> : <><strong>{total}</strong><span>Total bookings</span><small>Click a candidate</small></>}</div>
          </div>
          <div className="ops-booking-modal__legend">{segments.map(segment => <button type="button" key={segment.name} onMouseEnter={() => setHovered(segment)} onMouseLeave={() => setHovered(null)} onClick={() => selectCandidate(segment)} className={activeCandidate?.name === segment.name ? 'is-active' : ''} aria-pressed={selected?.name === segment.name}><i style={{ background: segment.color }} /><span>{segment.name}</span><strong>{segment.count}</strong><em>{segment.percent.toFixed(1)}%</em></button>)}</div>
        </div>
        <footer className="ops-booking-modal__levels"><h3>Interview levels {selected ? `· ${selected.name}` : '· All candidates'}</h3><div>{displayedLevels.map(item => <span key={item.name}><b>{item.name}</b><strong>{item.count}</strong></span>)}</div></footer>
        <footer className="ops-booking-modal__levels ops-booking-modal__technologies"><h3>Tech stack {selected ? `· ${selected.name}` : '· All candidates'} <small>Click to filter the table</small></h3><div>{displayedTechnologies.map(item => <button type="button" key={item.name} className={selectedTechnology === item.name ? 'is-active' : ''} onClick={() => onTechnologySelect?.(selectedTechnology === item.name ? '' : item.name)}><b>{item.name}</b><strong>{item.count}</strong></button>)}</div></footer>
      </section>
    </div>}</>
  )
}

export function DailyOpsPanel({
  loggedInSlots = [],
  activeAccount,
  accountInfo = {},
  onSelectAccount,
  onStartAll,
  startAllBusy = false,
  showFleetControls = false,
  onNavCandidates,
}) {
  const { role, reference } = useAuth()
  // Daily Ops is shared across all authenticated operators.  Do not prefill
  // a handler's own name as an attendee filter or their roster looks empty
  // whenever another handler owns the booked slot.
  const handlerScoped = false

  // Default behavior: show all upcoming interviews automatically (including
  // today and future dates) until the user selects a date.
  // After date selection: show only interviews scheduled for the selected date.
  // Clearing the date returns to all upcoming interviews.
  const [selectedDate, setSelectedDate] = useState('')
  const [attendeeFilter, setAttendeeFilter] = useState('')
  const [roundFilter, setRoundFilter] = useState('')
  const [technologyFilter, setTechnologyFilter] = useState('')
  const [candidateSearch, setCandidateSearch] = useState('')
  const [candidateFilter, setCandidateFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  // Sort By is a view preference, kept across reloads in this browser; Reset
  // leaves it alone, as it clears filters rather than the way they are read.
  const [sortOrder, setSortOrder] = useState(readRosterSort)
  const changeSortOrder = value => { setSortOrder(value); saveRosterSort(value) }
  const [globalStats, setGlobalStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [rosterCounts, setRosterCounts] = useState(null)
  const [refreshNonce, setRefreshNonce] = useState(0)
  const [summaryReload, setSummaryReload] = useState(0)

  /**
   * One calendar day becomes the filter. The table switches to that date's
   * records. Clearing it returns to all upcoming interviews.
   */
  function applyExactDate(iso) {
    if (!isValidIsoDay(iso)) return
    setSelectedDate(iso)
  }

  function clearDate() {
    setSelectedDate('')
  }

  // Upcoming only when no specific date is selected
  const upcomingOnly = !selectedDate

  const loadGlobal = useCallback(async () => {
    setLoading(true)
    try {
      const today = todayIso()
      const range = selectedDate
        ? { from: selectedDate, to: selectedDate }
        : { from: today, to: addDaysIso(today, 30) }
      const params = new URLSearchParams({ from: range.from, to: range.to })
      if (attendeeFilter) params.set('attendee', attendeeFilter)
      if (roundFilter) params.set('round', roundFilter)
      if (technologyFilter) params.set('technology', technologyFilter)
      // The dropdown is a selection and the search box is a search: sent as
      // one `search`, "Ram Charan M S" also matched "Rama Krishna" on "ram".
      if (candidateFilter) params.set('candidate', candidateFilter)
      const search = candidateSearch.trim()
      if (search) params.set('search', search)
      if (upcomingOnly) params.set('upcoming_only', 'true')
      const res = await fetch(`${API}/candidates/interviews/global?${params}`, { credentials: 'include' })
      if (!(res.headers.get('content-type') || '').includes('application/json')) {
        throw new Error(`Global data ${res.status}`)
      }
      const data = await res.json()
      if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Failed to load global data')
      setGlobalStats(data)
      setError('')
    } catch (err) {
      setError(err.message || 'Failed to load dashboard')
    } finally {
      setLoading(false)
    }
  }, [selectedDate, attendeeFilter, roundFilter, technologyFilter, candidateSearch, candidateFilter, upcomingOnly, summaryReload])

  // Refresh has to move the counters, and they come from the roster's rows,
  // so it reloads the roster as well as the range summary. Declared after
  // loadGlobal: naming it in a dependency array before it exists is a
  // temporal-dead-zone error at render.
  const refreshAll = useCallback(() => {
    setRefreshNonce(value => value + 1)
    loadGlobal()
  }, [loadGlobal])

  useEffect(() => { loadGlobal() }, [loadGlobal])

  // The status tabs describe the rows on screen, so they read the roster's own
  // tally. globalStats is a summary over a whole date range and ignores the
  // status filter, which is why the Pending tab could say 8 above a table of
  // 6. Everything else here still comes from the range summary.
  const interviews = rosterCounts || globalStats?.interviews || {}
  // These two lists are the whole range's vocabulary, not the day's tally, so
  // they stay on the range summary. Only the counters moved.
  const rangeInterviews = globalStats?.interviews || {}
  const technologyOptions = (rangeInterviews.by_technology || []).map(item => item.name).sort()
  const candidateOptions = rangeInterviews.by_candidate || []
  const activeFilterCount = [attendeeFilter, roundFilter, technologyFilter, candidateSearch.trim(), candidateFilter].filter(Boolean).length

  function clearFilters() {
    setAttendeeFilter('')
    setRoundFilter('')
    setTechnologyFilter('')
    setCandidateSearch('')
    setCandidateFilter('')
  }

  // Everything Reset puts back, counted for its badge: a picked date, the status tab,
  // and the filters beside the search box.
  const resetCount = activeFilterCount
    + (selectedDate ? 1 : 0)
    + (statusFilter ? 1 : 0)

  /** One click back to the page as it opens, then a fresh read of both the
   *  counters and the table -- also when nothing was set, so it doubles as a
   *  full refresh. */
  function resetAll() {
    clearFilters()
    setStatusFilter('')
    setSelectedDate('')
    setSummaryReload(value => value + 1)
    setRefreshNonce(value => value + 1)
  }

  return (
    <div className="daily-ops-page daily-ops-page--dashboard">

      {/* ── Compact top bar: title + KPIs + pending pill ─────────────── */}
      <div className="ops-topbar">
        <div className="ops-topbar__left">
          <h1 className="ops-dash-title">Daily ops</h1>
          <span className="ops-dash-sub ops-topbar__sub">Interview roster</span>
        </div>

        {/* One tab per status the backend stores, built from the same list the
            row dropdown uses so the two cannot disagree. Scheduled stays first
            and separate: it is the booking state every row in this roster already
            has, so it counts them all and clears the attendance filter. */}
        <div className="ops-topbar__kpis">
          {STATUS_TABS.map(tab => {
            const token = tab.filterValue
            const isScheduled = tab.id === 'scheduled'
            const active = isScheduled ? statusFilter === '' : statusFilter === token
            return (
              <KpiCard
                key={tab.id || tab.value || 'pending'}
                label={tab.label}
                value={interviews[tab.countKey] ?? 0}
                tone={tab.kpiTone}
                title={kpiScope(tab)}
                loading={loading}
                active={active}
                onClick={() => setStatusFilter(active && !isScheduled ? '' : token)}
              />
            )
          })}
        </div>

        <div className="ops-topbar__right">
          <BookingPie overview={globalStats?.booking_overview} selectedTechnology={technologyFilter} onTechnologySelect={setTechnologyFilter} />
          <PendingWorksStrip compact onOpenCandidates={onNavCandidates} />
        </div>
      </div>

      {/* ── Controls row: all filters in one line ───────────────────── */}
      <div className="ops-roster-controls" aria-label="Roster controls">
        <div className="ops-roster-controls__range">
          <div className="ops-roster-control ops-roster-control--date">
            <span>Date</span>
            <DateCalendarPicker
              value={selectedDate}
              onSelectDate={applyExactDate}
              onClear={clearDate}
            />
          </div>
        </div>

        <div className="ops-roster-controls__filters">
          <label className="ops-roster-search">
            <span className="ops-roster-search__icon" aria-hidden="true">
              <Icon name="search" size={15} />
            </span>
            <input
              placeholder="Search candidate or phone..."
              value={candidateSearch}
              onChange={e => setCandidateSearch(e.target.value)}
              aria-label="Candidate search"
            />
          </label>

          <DailyOpsFilterMenu
            candidateFilter={candidateFilter}
            attendeeFilter={attendeeFilter}
            roundFilter={roundFilter}
            technologyFilter={technologyFilter}
            sortOrder={sortOrder}
            candidateOptions={candidateOptions}
            attendeeOptions={ATTENDEES}
            roundOptions={ROUNDS}
            technologyOptions={technologyOptions}
            onApply={({ candidate, attendee, round, technology, sort }) => {
              setCandidateFilter(candidate)
              if (candidate) setCandidateSearch('')
              setAttendeeFilter(attendee)
              setRoundFilter(round)
              setTechnologyFilter(technology)
              if (sort) changeSortOrder(sort)
            }}
            onResetAll={clearFilters}
          />

          <div className="ops-roster-controls__actions">
            <button
              type="button"
              className="ops-roster-clear ops-roster-reset"
              onClick={resetAll}
              title="Reset date, search, candidate, attendee, level, profile and status, and reload"
              aria-label={resetCount > 0 ? `Reset all filters (${resetCount} set)` : 'Reset all filters'}
            >
              <Icon name="reset" size={13} strokeWidth={2.1} />
              <span className="ops-roster-reset__label">Reset</span>
              {resetCount > 0 && <span className="ops-roster-reset__count">{resetCount}</span>}
            </button>
            <button
              type="button"
              className="ops-roster-refresh"
              onClick={refreshAll}
              disabled={loading}
            >
              <span aria-hidden="true"><Icon name="refresh" size={14} strokeWidth={2.1} /></span>
              {loading ? 'Updating' : 'Refresh'}
            </button>
          </div>
        </div>
      </div>

      {error && <p className="admin-error ops-dash-error" role="alert">{error}</p>}

      {/* ── Table fills the rest ─────────────────────────────────────── */}
      <div className="ops-dashboard ops-dashboard--v3 ops-table-area">
        <InterviewRoster
          refreshNonce={refreshNonce}
          key={`${selectedDate}|${upcomingOnly}`}
          variant="dashboard"
          dashboardFromDate={selectedDate || todayIso()}
          dashboardToDate={selectedDate || addDaysIso(todayIso(), 30)}
          dashboardAttendeeFilter={attendeeFilter}
          dashboardRoundFilter={roundFilter}
          dashboardTechnologyFilter={technologyFilter}
          dashboardCandidate={candidateFilter}
          dashboardCandidateSearch={candidateSearch}
          dashboardStatusFilter={statusFilter}
          dashboardSortOrder={sortOrder}
          upcomingOnly={upcomingOnly}
          unresolvedOnly={false}
          onRosterCountsChange={setRosterCounts}
          onRosterMutate={loadGlobal}
        />
      </div>
    </div>
  )
}
