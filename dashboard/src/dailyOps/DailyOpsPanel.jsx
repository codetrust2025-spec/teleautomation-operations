import React, { useCallback, useEffect, useRef, useState } from 'react'
import { API } from '../config.js'
import { STATUS_TABS } from './interviewStatuses.js'
import { useAuth } from '../context/AuthContext.jsx'
import { InterviewRoster } from './InterviewRoster.jsx'
import { ROSTER_SORT_OPTIONS, readRosterSort, rosterSortLabel, saveRosterSort } from './rosterSort.js'
import { DateCalendarPicker } from './DateCalendarPicker.jsx'
import { DailyOpsFilterMenu } from './DailyOpsFilterMenu.jsx'
import { addDaysIso, isValidIsoDay, resolveCalendarPreset, todayIso } from './calendarDates.js'
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
 * What a status counter counts: each names its scope, so "Pending" reads as
 * interviews in this view with no status recorded, and nothing else.
 */
export function kpiScope(tab) {
  if (tab.id === 'scheduled') return 'Every interview in the current view'
  if (tab.filterValue === 'pending') return 'Interviews in the current view with no status recorded yet'
  return `Interviews in the current view marked ${tab.label}`
}

const PIE_COLORS = ['#3b82f6', '#22c55e', '#f59e0b', '#a855f7', '#06b6d4', '#f43f5e', '#84cc16']
const candidateColor = (index, count) => count <= PIE_COLORS.length ? PIE_COLORS[index] : `hsl(${Math.round((index * 360) / count)} 78% 55%)`

/**
 * The bookings summary in the top bar: the donut and the count. The detail
 * behind it -- every candidate, level and technology, and the interviews --
 * is the Bookings Analytics page now, not a popup over this one (21 Oct), so
 * the button beside the donut goes there.
 */
function BookingPie({ overview = {} }) {
  const total = Number(overview.total || 0)
  const candidates = overview.by_candidate || []
  let cursor = 0
  const stops = candidates.map((item, index) => {
    const start = cursor
    cursor += total ? (Number(item.count || 0) / total) * 100 : 0
    return `${candidateColor(index, candidates.length)} ${start}% ${cursor}%`
  })
  const background = total && stops.length ? `conic-gradient(${stops.join(', ')})` : 'conic-gradient(#273244 0 100%)'
  const openAnalytics = () => window.dispatchEvent(new CustomEvent('teleautomation:navigate', { detail: { view: 'bookings-analytics' } }))

  return (
    <section className="ops-booking-pie" aria-label={`${total} interviews booked across all candidates`}>
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
      <button type="button" className="ops-booking-pie__open" onClick={openAnalytics} title="Open Bookings Analytics">Open Bookings Analytics</button>
    </section>
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
}) {
  const { role, reference } = useAuth()
  // Daily Ops is shared across all authenticated operators.  Do not prefill
  // a handler's own name as an attendee filter or their roster looks empty
  // whenever another handler owns the booked slot.
  const handlerScoped = false

  // Default behavior: show all upcoming interviews automatically (including
  // today and future dates) until the user selects a date or preset.
  // After date selection: show only interviews scheduled for the selected date.
  // After preset selection: show interviews for the preset range.
  // Clearing the date or preset returns to all upcoming interviews.
  const [selectedDate, setSelectedDate] = useState('')
  const [selectedPreset, setSelectedPreset] = useState('')
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
    setSelectedPreset('')
    setSelectedDate(iso)
  }

  function applyPreset(presetId) {
    setSelectedDate('')
    setSelectedPreset(presetId)
  }

  function clearDate() {
    setSelectedDate('')
    setSelectedPreset('')
  }

  // Upcoming only when neither a specific date nor a preset is selected
  const upcomingOnly = !selectedDate && !selectedPreset

  const loadGlobal = useCallback(async () => {
    setLoading(true)
    try {
      const today = todayIso()
      let range
      if (selectedPreset) {
        const p = resolveCalendarPreset(selectedPreset, today)
        range = p ? { from: p.from, to: p.to } : { from: today, to: addDaysIso(today, 30) }
      } else if (selectedDate) {
        range = { from: selectedDate, to: selectedDate }
      } else {
        range = { from: today, to: addDaysIso(today, 30) }
      }
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
  }, [selectedDate, selectedPreset, attendeeFilter, roundFilter, technologyFilter, candidateSearch, candidateFilter, upcomingOnly, summaryReload])

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

  // Everything Reset puts back, counted for its badge: a picked date/preset, the status tab,
  // and the filters beside the search box.
  const resetCount = activeFilterCount
    + (selectedDate || selectedPreset ? 1 : 0)
    + (statusFilter ? 1 : 0)

  /** One click back to the page as it opens, then a fresh read of both the
   *  counters and the table -- also when nothing was set, so it doubles as a
   *  full refresh. */
  function resetAll() {
    clearFilters()
    setStatusFilter('')
    setSelectedDate('')
    setSelectedPreset('')
    setSummaryReload(value => value + 1)
    setRefreshNonce(value => value + 1)
  }

  return (
    <div className="daily-ops-page daily-ops-page--dashboard">

      {/* ── Compact top bar: status KPIs + bookings by technology. The page
          title lives in the shell header alone; repeating it here named the
          page twice. Candidate tasks are on Candidates (Pending Works), not
          here (20 Oct). ── */}
      <div className="ops-topbar">
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
          <BookingPie overview={globalStats?.booking_overview} />
        </div>
      </div>

      {/* ── Controls row: Search → Calendar → Filters → Reset → Refresh ── */}
      <div className="ops-roster-controls" aria-label="Roster controls">
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

          <div className="ops-roster-control ops-roster-control--date">
            <DateCalendarPicker
              value={selectedDate}
              preset={selectedPreset}
              onSelectDate={applyExactDate}
              onSelectPreset={applyPreset}
              onClear={clearDate}
            />
          </div>

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
          key={`${selectedPreset}|${selectedDate}|${upcomingOnly}`}
          variant="dashboard"
          dashboardFromDate={
            selectedPreset
              ? resolveCalendarPreset(selectedPreset)?.from || todayIso()
              : selectedDate || todayIso()
          }
          dashboardToDate={
            selectedPreset
              ? resolveCalendarPreset(selectedPreset)?.to || todayIso()
              : selectedDate || addDaysIso(todayIso(), 30)
          }
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
