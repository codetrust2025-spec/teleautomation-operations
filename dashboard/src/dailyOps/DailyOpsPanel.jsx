import React, { useCallback, useEffect, useRef, useState } from 'react'
import { API } from '../config.js'
import { STATUS_TABS } from './interviewStatuses.js'
import { useAuth } from '../context/AuthContext.jsx'
import { InterviewRoster } from './InterviewRoster.jsx'
import { PendingWorksStrip } from './PendingWorksStrip.jsx'
import { ALL_TIME_RANGE, PRESETS, detectPresetFromRange, resolvePresetRange } from './dateRangePresets.js'
import { DateCalendarPicker } from './DateCalendarPicker.jsx'
import { isValidIsoDay, monthRangeIso, parseMonthKey } from './calendarDates.js'
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

  const initialRange = resolvePresetRange('upcoming')
  const [fromDate, setFromDate] = useState(initialRange.from)
  const [toDate, setToDate] = useState(initialRange.to)
  const [rangePreset, setRangePreset] = useState('upcoming')
  const [attendeeFilter, setAttendeeFilter] = useState('')
  const [roundFilter, setRoundFilter] = useState('')
  const [technologyFilter, setTechnologyFilter] = useState('')
  const [candidateSearch, setCandidateSearch] = useState('')
  const [candidateFilter, setCandidateFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  // One list of every interview still waiting for an outcome, however old.
  // It answers "what have we not closed?", which no date range can: the
  // forgotten ones are precisely the ones outside every range worth picking.
  const [unresolvedOnly, setUnresolvedOnly] = useState(false)
  const [globalStats, setGlobalStats] = useState(null)
  // On a phone the period tabs are a scrolling strip, so the one that is
  // selected can sit off the right edge — All unresolved, being last, always
  // did. Nothing scrolls on a desktop, where the whole row fits.
  const presetsRef = useRef(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [rosterCounts, setRosterCounts] = useState(null)
  const [refreshNonce, setRefreshNonce] = useState(0)
  const [summaryReload, setSummaryReload] = useState(0)

  function applyMonth(monthValue) {
    // Choosing a period is choosing the dated view again.
    setUnresolvedOnly(false)
    if (monthValue === 'all') {
      setFromDate(ALL_TIME_RANGE.from)
      setToDate(ALL_TIME_RANGE.to)
      setRangePreset('allTime')
      return
    }
    const parsed = parseMonthKey(monthValue)
    if (!parsed) return
    // Built from UTC noon rather than `new Date(y, m, d).toISOString()`, which
    // hands back the previous day for anyone east of Greenwich — the whole of
    // IST included, so the old first-of-the-month landed on the last of the
    // month before.
    const { from, to } = monthRangeIso(parsed.year, parsed.monthIndex)
    setFromDate(from)
    setToDate(to)
    setRangePreset(`month:${monthValue}`)
  }

  /**
   * One calendar day becomes the filter. `from === to` is the shape the roster
   * already reads as a single day, so the table switches to that date's
   * records without any new plumbing behind it.
   */
  function applyExactDate(iso) {
    if (!isValidIsoDay(iso)) return
    setUnresolvedOnly(false)
    setFromDate(iso)
    setToDate(iso)
    // A picked day that happens to be today is the Today preset — say so, so
    // the period row and the date control never disagree about what is shown.
    setRangePreset(detectPresetFromRange(iso, iso))
  }

  const upcomingOnly = rangePreset === 'upcoming'

  function applyPreset(presetId) {
    const range = resolvePresetRange(presetId)
    if (!range) return
    setUnresolvedOnly(false)
    setRangePreset(presetId)
    setFromDate(range.from)
    setToDate(range.to)
  }

  /** Every row here is Pending by definition, so a status filter left over
   *  from the dated view would empty the list the moment it opened. */
  function showUnresolved() {
    setUnresolvedOnly(true)
    setStatusFilter('')
  }

  function applyManualFrom(value) {
    setFromDate(value)
    setRangePreset(detectPresetFromRange(value, toDate))
  }

  function applyManualTo(value) {
    setToDate(value)
    setRangePreset(detectPresetFromRange(fromDate, value))
  }

  // When range is custom, disable upcoming_only filter to show all interviews in the range
  const effectiveUpcomingOnly = !unresolvedOnly && rangePreset === 'upcoming' ? upcomingOnly : false

  const loadGlobal = useCallback(async () => {
    setLoading(true)
    try {
      const range = unresolvedOnly ? ALL_TIME_RANGE : { from: fromDate, to: toDate }
      const params = new URLSearchParams({ from: range.from, to: range.to })
      if (attendeeFilter) params.set('attendee', attendeeFilter)
      if (roundFilter) params.set('round', roundFilter)
      if (technologyFilter) params.set('technology', technologyFilter)
      // The dropdown is a selection and the search box is a search: sent as
      // one `search`, "Ram Charan M S" also matched "Rama Krishna" on "ram".
      if (candidateFilter) params.set('candidate', candidateFilter)
      const search = candidateSearch.trim()
      if (search) params.set('search', search)
      if (unresolvedOnly) params.set('unresolved_only', 'true')
      else if (effectiveUpcomingOnly) params.set('upcoming_only', 'true')
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
  }, [fromDate, toDate, attendeeFilter, roundFilter, technologyFilter, candidateSearch, candidateFilter, effectiveUpcomingOnly, unresolvedOnly, summaryReload])

  // Refresh has to move the counters, and they come from the roster's rows,
  // so it reloads the roster as well as the range summary. Declared after
  // loadGlobal: naming it in a dependency array before it exists is a
  // temporal-dead-zone error at render.
  const refreshAll = useCallback(() => {
    setRefreshNonce(value => value + 1)
    loadGlobal()
  }, [loadGlobal])

  useEffect(() => { loadGlobal() }, [loadGlobal])

  // `nearest` scrolls only when the tab is actually out of view, and only the
  // strip: a desktop, where nothing overflows, never moves. Optional call
  // because jsdom has no scrollIntoView.
  useEffect(() => {
    presetsRef.current?.querySelector('[aria-selected="true"]')
      ?.scrollIntoView?.({ inline: 'nearest', block: 'nearest' })
  }, [rangePreset, unresolvedOnly])

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
  const monthOptions = globalStats?.available_months || []
  const selectedMonth = rangePreset.startsWith('month:') ? rangePreset.slice(6) : ''
  // The date control reads the range rather than keeping a second copy of it,
  // so the chosen day survives a reload of the table and every preset that
  // resolves to a single day shows that day here.
  const exactDate = fromDate && fromDate === toDate ? fromDate : ''
  const activeFilterCount = [attendeeFilter, roundFilter, technologyFilter, candidateSearch.trim(), candidateFilter].filter(Boolean).length

  function clearFilters() {
    setAttendeeFilter('')
    setRoundFilter('')
    setTechnologyFilter('')
    setCandidateSearch('')
    setCandidateFilter('')
  }

  // Everything Reset puts back, counted for its badge: the period (Upcoming is
  // where the page opens), a picked date, All unresolved, the status tab, and
  // the five filters beside the search box.
  const resetCount = activeFilterCount
    + (unresolvedOnly || rangePreset !== 'upcoming' ? 1 : 0)
    + (statusFilter ? 1 : 0)

  /** One click back to the page as it opens, then a fresh read of both the
   *  counters and the table -- also when nothing was set, so it doubles as a
   *  full refresh. */
  function resetAll() {
    clearFilters()
    setStatusFilter('')
    const range = resolvePresetRange('upcoming')
    setUnresolvedOnly(false)
    setRangePreset('upcoming')
    setFromDate(range.from)
    setToDate(range.to)
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
            row dropdown uses so the two cannot disagree. Cancelled,
            Rescheduled and Re-Service had no tab at all, which made them
            settable but not filterable. Scheduled stays first and separate:
            it is the booking state every row in this roster already has, so
            it counts them all and clears the attendance filter. */}
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
        <div className="ops-roster-control-group ops-roster-control-group--period">
        <span className="ops-roster-control-group__label">Period</span>
        <div className="ops-date-range__presets" role="tablist" aria-label="Date range" ref={presetsRef}>
          {PRESETS.map(preset => (
            <button
              key={preset.id}
              type="button"
              role="tab"
              aria-selected={!unresolvedOnly && rangePreset === preset.id}
              className={`ops-date-range__preset${!unresolvedOnly && rangePreset === preset.id ? ' ops-date-range__preset--active' : ''}`}
              onClick={() => applyPreset(preset.id)}
            >
              {preset.label}
            </button>
          ))}
          {/* Not a period: it is every interview still waiting for an outcome,
              at any date. Sits with the periods because it is the same choice
              — what the table below is a list of. */}
          <button
            type="button"
            role="tab"
            aria-selected={unresolvedOnly}
            className={`ops-date-range__preset ops-date-range__preset--unresolved${unresolvedOnly ? ' ops-date-range__preset--active' : ''}`}
            title="Every interview still waiting for a status update, however old"
            onClick={() => unresolvedOnly ? applyPreset('upcoming') : showUnresolved()}
          >
            All unresolved
          </button>
        </div>
        </div>

        {/* All unresolved is not a period, so a date has nothing to pick here.
            The class lets a phone, where the controls have no room to spare,
            drop the control entirely; a desktop keeps it where it was. */}
        <div className={`ops-roster-control ops-roster-control--date${unresolvedOnly ? ' ops-roster-control--not-applicable' : ''}`}>
          <span>Date</span>
          <DateCalendarPicker
            value={exactDate}
            monthValue={selectedMonth}
            allTime={rangePreset === 'allTime'}
            availableMonths={monthOptions}
            onSelectDate={applyExactDate}
            onSelectMonth={applyMonth}
            onClear={() => applyPreset('upcoming')}
          />
        </div>

        <div className="ops-date-range__inputs ops-date-range__inputs--redesigned ops-date-range__inputs--removed" aria-hidden="true">
          <span className="ops-date-range__label">Range</span>
          <input className="cand-input ops-ctrl-date" type="date" value={fromDate} onChange={e => applyManualFrom(e.target.value)} aria-label="From date" />
          <span className="ops-date-range__sep">—</span>
          <input className="cand-input ops-ctrl-date" type="date" value={toDate}   onChange={e => applyManualTo(e.target.value)}   aria-label="To date" />
        </div>

        </div>

        <div className="ops-roster-controls__filters">
        <label className="ops-roster-search"><span className="ops-roster-search__icon" aria-hidden="true"><Icon name="search" size={15} /></span><input placeholder="Search candidate or phone..." value={candidateSearch} onChange={e => setCandidateSearch(e.target.value)} aria-label="Candidate search" /></label>
        <label className="ops-roster-control ops-roster-control--candidate"><span>Candidate</span><select className="cand-input ops-ctrl-select" value={candidateFilter} onChange={e => { setCandidateFilter(e.target.value); setCandidateSearch('') }} aria-label="Candidate filter"><option value="">All candidates</option>{candidateOptions.map(item => <option key={item.name} value={item.name}>{item.name} ({item.scheduled})</option>)}</select></label>

        {!handlerScoped && (
          <label className="ops-roster-control"><span>Attendee</span><select
            className="cand-input ops-ctrl-select"
            value={attendeeFilter}
            onChange={e => setAttendeeFilter(e.target.value)}
            aria-label="Attendee filter"
          >
            <option value="">Everyone</option>
            {ATTENDEES.map(name => <option key={name} value={name}>{name}</option>)}
          </select></label>
        )}
        <label className="ops-roster-control ops-roster-control--level"><span>Level</span><select
          className="cand-input ops-ctrl-select"
          value={roundFilter}
          onChange={e => setRoundFilter(e.target.value)}
          aria-label="Candidate interview level filter"
        >
          <option value="">All levels</option>
          {ROUNDS.map(r => <option key={r} value={r}>{r}</option>)}
        </select></label>
        <label className="ops-roster-control"><span>Profile</span><select
          className="cand-input ops-ctrl-select"
          value={technologyFilter}
          onChange={e => setTechnologyFilter(e.target.value)}
          aria-label="Technology filter"
        >
          <option value="">All profiles</option>
          {technologyOptions.map(t => <option key={t} value={t}>{t}</option>)}
        </select></label>
        <div className="ops-roster-controls__actions">
          {/* Reset is always here: period, date, search, candidate, attendee,
              level, profile and the status tab, all at once. "Clear" only
              reached the five filters and only appeared once one was set. */}
          <button
            type="button"
            className="ops-roster-clear ops-roster-reset"
            onClick={resetAll}
            title="Reset period, date, search, candidate, attendee, level, profile and status, and reload"
            aria-label={resetCount > 0 ? `Reset all filters (${resetCount} set)` : 'Reset all filters'}
          >
            <Icon name="reset" size={13} strokeWidth={2.1} />
            <span className="ops-roster-reset__label">Reset</span>
            {resetCount > 0 && <span className="ops-roster-reset__count">{resetCount}</span>}
          </button>
          <button type="button" className="ops-roster-refresh" onClick={refreshAll} disabled={loading}><span aria-hidden="true"><Icon name="refresh" size={14} strokeWidth={2.1} /></span>{loading ? 'Updating' : 'Refresh'}</button>
        </div>
        </div>
      </div>

      {unresolvedOnly && (
        <p className="ops-unresolved-note" role="status">
          <strong>All unresolved</strong> — every interview still waiting for a status update, oldest first.
          The period and date filters do not apply here; a row leaves this list as soon as you set its status.
        </p>
      )}

      {error && <p className="admin-error ops-dash-error" role="alert">{error}</p>}

      {/* ── Table fills the rest ─────────────────────────────────────── */}
      <div className="ops-dashboard ops-dashboard--v3 ops-table-area">
        <InterviewRoster
          refreshNonce={refreshNonce}
          key={`${fromDate}|${toDate}|${upcomingOnly}|${unresolvedOnly}`}
          variant="dashboard"
          dashboardFromDate={fromDate}
          dashboardToDate={toDate}
          dashboardAttendeeFilter={attendeeFilter}
          dashboardRoundFilter={roundFilter}
          dashboardTechnologyFilter={technologyFilter}
          dashboardCandidate={candidateFilter}
          dashboardCandidateSearch={candidateSearch}
          dashboardStatusFilter={statusFilter}
          upcomingOnly={upcomingOnly && !unresolvedOnly}
          unresolvedOnly={unresolvedOnly}
          onRosterCountsChange={setRosterCounts}
          onRosterMutate={loadGlobal}
        />
      </div>
    </div>
  )
}
