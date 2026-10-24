import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { API } from '../config.js'
import { Icon } from '../components/ui/Icon.jsx'
import { DarkSelect } from '../dailyOps/DarkSelect.jsx'
import { INTERVIEW_STATUSES } from '../dailyOps/interviewStatuses.js'
import { formatDayLabel } from '../dailyOps/InterviewRoster.jsx'
import { formatClockTime } from '../utils/istTime.js'
import { SelectedBadge, CandidateSelectedBadge } from '../candidates/SelectedBadge.jsx'
import { SELECTED_LABEL, isSelectedCandidate } from '../candidates/candidateOutcome.js'
import {
  DEFAULT_PRESET,
  RANGE_PRESETS,
  buildReport,
  candidateRows,
  comparisonFor,
  formatPercent,
  formatRange,
  kpiStatuses,
  latestFirst,
  orderedLevels,
  otherStatuses,
  percentChange,
  presetRange,
  ranked,
  reportFilename,
  rowStatus,
  share,
  topWithOthers,
} from './analyticsData.js'
import './bookingsAnalytics.css'

/**
 * Bookings Analytics (21 Oct): the interview booking analytics that used to
 * open as a popup over Daily Ops, as a page of its own beside it.
 *
 * Every figure is read from `GET /candidates/interviews/global` -- the summary
 * the Daily Ops counters come from -- for the period and filters chosen at the
 * top, and the interviews behind any figure from `/candidates/interviews/monitor`,
 * the roster's own read. Nothing on the page is a number of its own.
 *
 * The page is one selection. Choosing a candidate, an interview level or a
 * technology anywhere -- a slice, a row, a bar, a dropdown -- narrows every
 * card to it, while the list it was chosen from keeps showing the others, so
 * the choice can be changed in place. The counters open the interviews they
 * count. Long lists (every candidate, every technology, the interviews) open
 * in views of their own rather than stretching the dashboard.
 */

const PALETTE = ['#3b82f6', '#22c55e', '#f59e0b', '#a855f7', '#06b6d4', '#f43f5e', '#84cc16', '#ec4899', '#14b8a6', '#f97316']
const OTHERS_COLOR = '#64748b'
const TOP_LIMIT = 10
const TECH_LIMIT = 6
// The summary is cheap and the page is for watching: a minute old at most
// while someone can see it, and fresh the moment they come back to it.
const POLL_MS = 60000

const EMPTY_FILTERS = { candidate: '', technology: '', round: '' }

const STATUS_TONE = {
  '': 'amber',
  attended: 'green',
  not_attended: 'red',
  technical_issue: 'orange',
  cancelled: 'slate',
  rescheduled: 'violet',
  released_for_reschedule: 'violet',
  re_service: 'yellow',
}

function rankColor(rank) {
  return rank <= TOP_LIMIT ? PALETTE[(rank - 1) % PALETTE.length] : OTHERS_COLOR
}

async function readJson(path, params, signal) {
  const res = await fetch(`${API}${path}?${params}`, { credentials: 'include', cache: 'no-store', signal })
  if (!(res.headers?.get?.('content-type') || '').includes('application/json')) {
    throw new Error(`The server returned ${res.status} -- refresh the page and try again`)
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok || data.status !== 'ok') throw new Error(data.message || data.detail || 'Could not load the bookings summary')
  return data
}

function queryFor(range, filters) {
  const params = new URLSearchParams({ from: range.from, to: range.to })
  if (filters.candidate) params.set('candidate', filters.candidate)
  if (filters.technology) params.set('technology', filters.technology)
  if (filters.round) params.set('round', filters.round)
  return params
}

/** A candidate list searched by name -- or by "selected", the outcome they show. */
function matchCandidates(rows, query) {
  const needle = query.trim().toLowerCase()
  if (!needle) return rows
  const outcome = needle.replace(/^★\s*/, '') === 'selected'
  return rows.filter(row => row.name.toLowerCase().includes(needle) || (outcome && row.selected))
}

const loadSummary = (range, filters, signal) => readJson('/candidates/interviews/global', queryFor(range, filters), signal)
const loadInterviews = (range, filters, signal) => readJson('/candidates/interviews/monitor', queryFor(range, filters), signal)

function clockNow() {
  return new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' })
}

/* ── Controls ─────────────────────────────────────────────────────────── */

function DateRangeControl({ presetId, range, onPreset, onCustom }) {
  const [open, setOpen] = useState(false)
  const [from, setFrom] = useState(range.from)
  const [to, setTo] = useState(range.to)
  const wrapRef = useRef(null)
  const allTime = presetId === 'all_time'
  useEffect(() => { setFrom(allTime ? '' : range.from); setTo(allTime ? '' : range.to) }, [range.from, range.to, allTime])
  useEffect(() => {
    if (!open) return undefined
    const onDown = event => { if (!wrapRef.current?.contains(event.target)) setOpen(false) }
    const onKey = event => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [open])
  const customValid = /^\d{4}-\d{2}-\d{2}$/.test(from) && /^\d{4}-\d{2}-\d{2}$/.test(to) && from <= to
  return (
    <div className="ba-range" ref={wrapRef}>
      <button type="button" className="ba-control ba-control--range" aria-haspopup="dialog" aria-expanded={open} aria-label={`Period: ${formatRange(range)}`} onClick={() => setOpen(value => !value)}>
        <Icon name="calendar" size={16} />
        <span className="ba-control__text">{formatRange(range)}</span>
        <Icon name="chevron-down" size={14} className="ba-control__caret" />
      </button>
      {open && (
        <div className="ba-range__menu" role="dialog" aria-label="Choose a period">
          <div className="ba-range__presets">
            {RANGE_PRESETS.map(preset => (
              <button type="button" key={preset.id} className={presetId === preset.id ? 'is-active' : ''} aria-pressed={presetId === preset.id} onClick={() => { onPreset(preset.id); setOpen(false) }}>{preset.label}</button>
            ))}
          </div>
          <form className="ba-range__custom" onSubmit={event => { event.preventDefault(); if (customValid) { onCustom({ from, to }); setOpen(false) } }}>
            <label><span>From</span><input type="date" value={from} max={to || undefined} onChange={event => setFrom(event.target.value)} aria-label="From date" /></label>
            <label><span>To</span><input type="date" value={to} min={from || undefined} onChange={event => setTo(event.target.value)} aria-label="To date" /></label>
            <button type="submit" className="ba-button ba-button--primary" disabled={!customValid}>Apply</button>
          </form>
        </div>
      )}
    </div>
  )
}

function FilterSelect({ icon, label, value, options, onChange }) {
  return (
    <div className={`ba-select${value ? ' is-set' : ''}`}>
      <Icon name={icon} size={15} className="ba-select__icon" />
      <DarkSelect ariaLabel={label} value={value} options={options} onChange={onChange} className="ba-control ba-control--select" />
    </div>
  )
}

/* ── Counters ─────────────────────────────────────────────────────────── */

function Trend({ change, label, risingIsGood }) {
  if (!label) return null
  if (change === null) return <span className="ba-trend ba-trend--none" title={`None in the earlier period (${label.replace(/^vs /, '')})`}>New</span>
  const direction = change > 0 ? 'up' : change < 0 ? 'down' : 'flat'
  const good = direction === 'flat' ? 'neutral' : (direction === 'up') === risingIsGood ? 'good' : 'bad'
  return (
    <span className={`ba-trend ba-trend--${good}`} title={label}>
      {direction !== 'flat' && <Icon name={direction === 'up' ? 'arrow-up' : 'arrow-down'} size={11} strokeWidth={2.6} />}
      {change > 0 ? '+' : ''}{change.toFixed(1)}%
    </span>
  )
}

function KpiCard({ label, value, total, tone, previous, comparisonLabel, loading, risingIsGood, onOpen, note }) {
  const change = previous === undefined ? undefined : percentChange(value, previous)
  return (
    <button type="button" className={`ba-kpi ba-kpi--${tone}`} onClick={onOpen} disabled={loading || !value} title={value ? `Show ${note ? 'every interview' : `the ${label.toLowerCase()} interviews`}` : undefined}>
      <span className="ba-kpi__label">{label}</span>
      <strong className="ba-kpi__value">{loading ? '…' : value}</strong>
      <span className="ba-kpi__foot">
        <span className="ba-kpi__share">{loading ? '' : note || <>{formatPercent(share(value, total))}<span className="ba-kpi__of"> of total</span></>}</span>
        {!loading && change !== undefined && <Trend change={change} label={comparisonLabel} risingIsGood={risingIsGood} />}
      </span>
    </button>
  )
}

function OtherOutcomesCard({ interviews, loading, onOpen }) {
  const statuses = otherStatuses()
  return (
    <div className="ba-kpi ba-kpi--other" role="group" aria-label="Other outcomes">
      <span className="ba-kpi__label">Other outcomes</span>
      <ul className="ba-kpi__list">
        {statuses.map(status => {
          const count = Number(interviews[status.countKey]) || 0
          return (
            <li key={status.value}>
              <button type="button" onClick={() => onOpen(status.filterValue)} disabled={loading || !count} title={count ? `Show the ${status.label.toLowerCase()} interviews` : undefined}>
                <span>{status.label}</span><b>{loading ? '…' : count}</b>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/* ── Charts ───────────────────────────────────────────────────────────── */

function Donut({ segments, total, activeName, onPick }) {
  const [hovered, setHovered] = useState(null)
  let offset = 0
  const drawn = segments.map(segment => {
    const part = { ...segment, offset }
    offset += total ? (segment.count / total) * 100 : 0
    return { ...part, arc: total ? (segment.count / total) * 100 : 0 }
  })
  const focus = hovered || drawn.find(segment => segment.name === activeName) || null
  return (
    <div className="ba-donut">
      <svg viewBox="0 0 120 120" className="ba-donut__svg" role="img" aria-label={`Bookings by candidate, ${total} in total`}>
        <circle cx="60" cy="60" r="46" pathLength="100" className="ba-donut__track" />
        {drawn.filter(segment => segment.arc > 0).map(segment => (
          <circle
            key={segment.name}
            cx="60" cy="60" r="46" pathLength="100" fill="none"
            stroke={segment.color}
            strokeWidth={focus?.name === segment.name ? 19 : 15}
            strokeDasharray={`${segment.arc} ${100 - segment.arc}`}
            strokeDashoffset={-segment.offset}
            transform="rotate(-90 60 60)"
            className={`ba-donut__segment${activeName && activeName !== segment.name ? ' is-dim' : ''}`}
            data-name={segment.name}
            onMouseEnter={() => setHovered(segment)}
            onMouseLeave={() => setHovered(null)}
            onClick={() => onPick(segment)}
          >
            <title>{`${segment.name}: ${segment.count} bookings (${formatPercent(segment.percent)})`}</title>
          </circle>
        ))}
      </svg>
      <div className="ba-donut__center" aria-live="polite">
        {focus
          ? <><strong>{focus.count}</strong><span title={focus.name}>{focus.name}</span><small>{formatPercent(focus.percent)}</small></>
          : <><strong>{total}</strong><span>Bookings</span><small>Click a candidate</small></>}
      </div>
    </div>
  )
}

function BarList({ items, active, onPick, colorFor, label, disabledReason }) {
  const max = Math.max(1, ...items.map(item => item.count))
  return (
    <ul className={`ba-bars${active ? ' has-active' : ''}`} aria-label={label}>
      {items.map(item => {
        const isActive = active === item.name
        const color = colorFor(item)
        const reason = disabledReason?.(item)
        return (
          <li key={item.name}>
            <button
              type="button"
              className={`ba-bar${isActive ? ' is-active' : ''}`}
              aria-pressed={isActive}
              disabled={Boolean(reason)}
              title={reason || (isActive ? `Show every ${label.toLowerCase().replace(/s$/, '')} again` : `Show only ${item.name}`)}
              onClick={() => onPick(item)}
              style={{ '--ba-accent': color }}
            >
              <span className="ba-bar__name" title={item.name}>{item.name}</span>
              <span className="ba-bar__count">{item.count}</span>
              <span className="ba-bar__pct">{formatPercent(item.percent)}</span>
              <span className="ba-bar__track" aria-hidden="true"><i style={{ width: `${(item.count / max) * 100}%` }} /></span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

/* ── Candidates ───────────────────────────────────────────────────────── */

function CandidateTable({ rows, limit, selected, onPick, onInterviews, label, emptyText }) {
  const [sort, setSort] = useState({ key: 'count', dir: 'desc' })
  const shown = useMemo(() => {
    const sorted = [...rows].sort((a, b) => {
      const cmp = sort.key === 'name'
        ? a.name.localeCompare(b.name)
        : (Number(a[sort.key]) || 0) - (Number(b[sort.key]) || 0) || b.name.localeCompare(a.name)
      return sort.dir === 'asc' ? cmp : -cmp
    })
    return limit ? sorted.slice(0, limit) : sorted
  }, [rows, sort, limit])
  const max = Math.max(1, ...rows.map(row => row.count))
  const sortBy = key => setSort(current => ({ key, dir: current.key === key && current.dir === 'desc' ? 'asc' : 'desc' }))
  const ariaSort = key => (sort.key === key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined)
  const sortHead = (id, text, className = '') => (
    <th scope="col" className={className} aria-sort={ariaSort(id)}>
      <button type="button" onClick={() => sortBy(id)}>{text}<span className={`ba-sort ba-sort--${sort.key === id ? sort.dir : 'none'}`} aria-hidden="true" /></button>
    </th>
  )
  return (
    <div className="ba-table-wrap">
      <table className="ba-table" aria-label={label}>
        <thead>
          <tr>
            <th scope="col" className="ba-table__rank">#</th>
            {sortHead('name', 'Candidate')}
            {sortHead('count', 'Bookings', 'ba-table__num')}
            {sortHead('attended', 'Attended', 'ba-table__num ba-table__attended')}
            <th scope="col" className="ba-table__share">Share</th>
            <th scope="col" className="ba-table__act"><span className="ba-sr">Interviews</span></th>
          </tr>
        </thead>
        <tbody>
          {shown.map(row => {
            const isActive = selected === row.name
            return (
              <tr key={row.name} className={`${isActive ? 'is-active' : ''}${selected && !isActive ? ' is-dim' : ''}`} onClick={event => { if (!event.target.closest('button')) onPick(row.name) }}>
                <td className="ba-table__rank"><span className="ba-rank" style={{ '--ba-accent': rankColor(row.rank) }}>{row.rank}</span></td>
                <td className="ba-table__name-cell">
                  <button type="button" className="ba-table__name" title={isActive ? 'Show every candidate again' : `Show only ${row.name}`} aria-pressed={isActive} onClick={() => onPick(row.name)}>{row.name}</button>
                  {row.selected && <SelectedBadge />}
                </td>
                <td className="ba-table__num"><b>{row.count}</b></td>
                <td className="ba-table__num ba-table__attended">{Number(row.attended) || 0}</td>
                <td className="ba-table__share">
                  <span className="ba-share"><span>{formatPercent(row.percent)}</span><i aria-hidden="true"><b style={{ width: `${(row.count / max) * 100}%`, background: rankColor(row.rank) }} /></i></span>
                </td>
                <td className="ba-table__act">
                  <button type="button" className="ba-icon-button" onClick={() => onInterviews(row.name)} aria-label={`${row.name}: view ${row.count} interview${row.count === 1 ? '' : 's'}`} title="View interviews">
                    <Icon name="list" size={15} />
                  </button>
                </td>
              </tr>
            )
          })}
          {!shown.length && <tr><td colSpan={6} className="ba-empty">{emptyText}</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function SearchBox({ value, onChange, placeholder, label, title }) {
  return (
    <label className="ba-search">
      <Icon name="search" size={14} />
      <input type="search" value={value} onChange={event => onChange(event.target.value)} placeholder={placeholder} aria-label={label} title={title} />
    </label>
  )
}

/* ── Separate views ───────────────────────────────────────────────────── */

function View({ title, subtitle, onClose, children, wide = false }) {
  const panelRef = useRef(null)
  useEffect(() => {
    const previous = document.activeElement
    panelRef.current?.focus()
    const onKey = event => { if (event.key === 'Escape' && !event.defaultPrevented) onClose() }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (previous && typeof previous.focus === 'function') previous.focus()
    }
  }, [onClose])
  return createPortal(
    <div className="ba-view" onMouseDown={event => event.target === event.currentTarget && onClose()}>
      <section ref={panelRef} tabIndex={-1} className={`ba-view__panel${wide ? ' ba-view__panel--wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="ba-view__head">
          <div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
          <button type="button" className="ba-icon-button" onClick={onClose} aria-label="Close"><Icon name="x" size={17} /></button>
        </header>
        <div className="ba-view__body">{children}</div>
      </section>
    </div>,
    document.body,
  )
}

function AllCandidatesView({ rows, selected, onPick, onInterviews, onClose, period }) {
  const [query, setQuery] = useState('')
  const [selectedOnly, setSelectedOnly] = useState(false)
  const matched = useMemo(() => {
    const found = matchCandidates(rows, query)
    return selectedOnly ? found.filter(row => row.selected) : found
  }, [rows, query, selectedOnly])
  const total = rows.reduce((sum, row) => sum + row.count, 0)
  const selectedCount = rows.filter(row => row.selected).length
  return (
    <View title="All candidates" subtitle={`${rows.length} candidates · ${total} bookings · ${period}`} onClose={onClose}>
      <div className="ba-view__tools">
        <SearchBox value={query} onChange={setQuery} placeholder="Search candidates" label="Search all candidates" />
        <button type="button" className={`ba-status-tab ba-tone--green${selectedOnly ? ' is-active' : ''}`} aria-pressed={selectedOnly} onClick={() => setSelectedOnly(value => !value)} disabled={!selectedCount && !selectedOnly}>
          ★ {SELECTED_LABEL} only<b>{selectedCount}</b>
        </button>
        <span className="ba-muted">{matched.length} shown</span>
      </div>
      <CandidateTable rows={matched} selected={selected} onPick={onPick} onInterviews={onInterviews} label="All candidates by bookings" emptyText="No candidate matches that search." />
    </View>
  )
}

function AllTechnologiesView({ items, active, onPick, onClose, period, total }) {
  const [query, setQuery] = useState('')
  const needle = query.trim().toLowerCase()
  const matched = needle ? items.filter(item => item.name.toLowerCase().includes(needle)) : items
  return (
    <View title="All technologies" subtitle={`${items.length} technologies · ${total} bookings · ${period}`} onClose={onClose}>
      <div className="ba-view__tools"><SearchBox value={query} onChange={setQuery} placeholder="Search technologies" label="Search all technologies" /><span className="ba-muted">{matched.length} shown</span></div>
      <BarList items={matched} active={active} onPick={onPick} colorFor={() => '#14b8a6'} label="Technologies" />
      {!matched.length && <p className="ba-empty">No technology matches that search.</p>}
    </View>
  )
}

const STATUS_FILTERS = [{ filterValue: '', label: 'All' }, ...INTERVIEW_STATUSES]

function InterviewsView({ range, filters, initialStatus, period, onClose }) {
  const [state, setState] = useState({ loading: true, error: '', rows: [] })
  const [status, setStatus] = useState(initialStatus || '')
  const [query, setQuery] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setState(current => ({ ...current, loading: true, error: '' }))
    loadInterviews(range, filters, controller.signal)
      .then(data => setState({ loading: false, error: '', rows: latestFirst(data.interviews) }))
      .catch(error => { if (error.name !== 'AbortError') setState({ loading: false, error: error.message || 'Could not load the interviews', rows: [] }) })
    return () => controller.abort()
  }, [range, filters, reload])
  const counts = useMemo(() => {
    const tally = { '': state.rows.length }
    for (const row of state.rows) {
      const key = rowStatus(row).filterValue
      tally[key] = (tally[key] || 0) + 1
    }
    return tally
  }, [state.rows])
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return state.rows.filter(row => {
      if (status && rowStatus(row).filterValue !== status) return false
      if (!needle) return true
      if (needle.replace(/^★\s*/, '') === 'selected' && isSelectedCandidate(row)) return true
      return [row.name, row.interview_company, row.technology_resolved || row.technology, row.interview_round, row.interview_attendee_resolved || row.interview_attendee]
        .some(value => String(value || '').toLowerCase().includes(needle))
    })
  }, [state.rows, status, query])
  const scope = [filters.candidate, filters.round, filters.technology].filter(Boolean).join(' · ')
  return (
    <View title={filters.candidate ? `${filters.candidate}: interviews` : 'Interviews'} subtitle={`${scope ? `${scope} · ` : ''}${period}`} onClose={onClose} wide>
      <div className="ba-view__tools">
        {/* The fields go in the tooltip: spelled out as the placeholder they
            needed 336px of a phone's 245 in Verdana and were cut at "level,". */}
        <SearchBox value={query} onChange={setQuery} placeholder="Search interviews" label="Search interviews" title="Search by name, company, level, technology or attendee" />
        <span className="ba-muted">{state.loading ? 'Loading…' : `${shown.length} of ${state.rows.length}`}</span>
      </div>
      <div className="ba-status-tabs" role="tablist" aria-label="Interview status">
        {STATUS_FILTERS.filter(entry => entry.filterValue === '' || counts[entry.filterValue] || entry.filterValue === status).map(entry => (
          <button key={entry.filterValue || 'all'} type="button" role="tab" aria-selected={status === entry.filterValue} className={`ba-status-tab ba-tone--${entry.filterValue ? STATUS_TONE[entry.value] : 'blue'}${status === entry.filterValue ? ' is-active' : ''}`} onClick={() => setStatus(entry.filterValue)}>
            {entry.label}<b>{counts[entry.filterValue] || 0}</b>
          </button>
        ))}
      </div>
      {state.error && <div className="ba-error" role="alert"><span>{state.error}</span><button type="button" className="ba-link" onClick={() => setReload(n => n + 1)}>Try again</button></div>}
      <div className="ba-table-wrap ba-table-wrap--interviews">
        <table className="ba-table ba-table--interviews" aria-label="Interviews">
          <thead>
            <tr><th scope="col">Date</th><th scope="col">Time</th><th scope="col">Candidate</th><th scope="col">Level</th><th scope="col">Technology</th><th scope="col">Company</th><th scope="col">Attendee</th><th scope="col">Status</th></tr>
          </thead>
          <tbody>
            {shown.map(row => {
              const entry = rowStatus(row)
              return (
                <tr key={row.id}>
                  <td data-label="Date">{formatDayLabel(row.date)}</td>
                  <td data-label="Time">{[row.time, row.time_end].filter(Boolean).map(formatClockTime).join(' – ') || '—'}</td>
                  <td data-label="Candidate" className="ba-table__strong">{row.name || '—'}<CandidateSelectedBadge row={row} /></td>
                  <td data-label="Level">{row.interview_round || 'Unspecified'}</td>
                  <td data-label="Technology">{row.technology_resolved || row.technology || 'Unspecified'}</td>
                  <td data-label="Company">{row.interview_company || '—'}</td>
                  <td data-label="Attendee">{row.interview_attendee_resolved || row.interview_attendee || '—'}</td>
                  <td data-label="Status"><span className={`ba-badge ba-tone--${STATUS_TONE[entry.value]}`}>{entry.label}</span></td>
                </tr>
              )
            })}
            {!state.loading && !shown.length && <tr><td colSpan={8} className="ba-empty">{state.rows.length ? 'No interview matches.' : 'No interviews in this selection.'}</td></tr>}
          </tbody>
        </table>
      </div>
    </View>
  )
}

/* ── The page ─────────────────────────────────────────────────────────── */

export function BookingsAnalyticsPanel() {
  const [presetId, setPresetId] = useState(DEFAULT_PRESET)
  const [customRange, setCustomRange] = useState(null)
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [data, setData] = useState({ summary: null, previous: null, techFacet: null, levelFacet: null })
  const [status, setStatus] = useState({ loading: true, refreshing: false, error: '', updatedAt: '' })
  const [search, setSearch] = useState('')
  const [view, setView] = useState(null)

  const range = useMemo(() => (presetId === 'custom' ? customRange : presetRange(presetId)), [presetId, customRange])
  const comparison = useMemo(() => comparisonFor(presetId, range), [presetId, range])
  const requestRef = useRef(0)

  const load = useCallback(async ({ silent = false } = {}) => {
    const id = ++requestRef.current
    setStatus(current => ({ ...current, loading: !silent, refreshing: silent, error: silent ? current.error : '' }))
    try {
      // A facet list (technologies, levels) is read without its own filter, so
      // choosing one leaves the rest on screen to switch to.
      const [summary, previous, techFacet, levelFacet] = await Promise.all([
        loadSummary(range, filters),
        comparison ? loadSummary(comparison.range, filters) : null,
        filters.technology ? loadSummary(range, { ...filters, technology: '' }) : null,
        filters.round ? loadSummary(range, { ...filters, round: '' }) : null,
      ])
      if (id !== requestRef.current) return
      setData({ summary, previous, techFacet, levelFacet })
      setStatus({ loading: false, refreshing: false, error: '', updatedAt: clockNow() })
    } catch (error) {
      if (id !== requestRef.current) return
      setStatus(current => ({ ...current, loading: false, refreshing: false, error: error.message || 'Could not load the bookings summary' }))
    }
  }, [range, comparison, filters])

  useEffect(() => { load() }, [load])

  const loadRef = useRef(load)
  useEffect(() => { loadRef.current = load }, [load])
  useEffect(() => {
    const timer = setInterval(() => { if (!document.hidden) loadRef.current({ silent: true }) }, POLL_MS)
    const onVisible = () => { if (!document.hidden) loadRef.current({ silent: true }) }
    document.addEventListener('visibilitychange', onVisible)
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [])

  const { summary, previous, techFacet, levelFacet } = data
  const interviews = summary?.interviews || {}
  const total = Number(interviews.count) || 0
  const loading = status.loading && !summary

  const candidates = useMemo(() => candidateRows(summary), [summary])
  const candidateTotal = candidates.reduce((sum, row) => sum + row.count, 0)
  const segments = useMemo(
    () => topWithOthers(candidates, candidateTotal, TOP_LIMIT).map(item => ({ ...item, color: item.others ? OTHERS_COLOR : rankColor(item.rank) })),
    [candidates, candidateTotal],
  )
  const levelSource = (levelFacet || summary)?.booking_overview
  const levels = useMemo(() => orderedLevels(levelSource?.by_level, levelSource?.total), [levelSource])
  const techSource = (techFacet || summary)?.booking_overview
  const technologies = useMemo(() => ranked(techSource?.by_technology, techSource?.total), [techSource])
  const topTechnologies = useMemo(() => {
    const head = technologies.slice(0, TECH_LIMIT)
    const chosen = technologies.find(item => item.name === filters.technology)
    return chosen && !head.includes(chosen) ? [...head.slice(0, TECH_LIMIT - 1), chosen] : head
  }, [technologies, filters.technology])

  const topRows = useMemo(() => {
    return matchCandidates(candidates, search)
  }, [candidates, search])
  const selectedCount = candidates.filter(row => row.selected).length
  const chosenSelected = Boolean(filters.candidate && candidates.find(row => row.name === filters.candidate)?.selected)

  const option = (all, names, current) => {
    const list = [...new Set(names.filter(Boolean))]
    if (current && !list.includes(current)) list.push(current)
    return [{ value: '', label: all }, ...list.sort((a, b) => a.localeCompare(b)).map(name => ({ value: name, label: name }))]
  }
  const candidateOptions = useMemo(() => option('All candidates', candidates.map(row => row.name), filters.candidate), [candidates, filters.candidate])
  const technologyOptions = useMemo(() => option('All technologies', technologies.map(item => item.name), filters.technology), [technologies, filters.technology])
  const levelOptions = useMemo(() => [{ value: '', label: 'All levels' }, ...levels.map(item => ({ value: item.name, label: item.name })), ...(filters.round && !levels.some(item => item.name === filters.round) ? [{ value: filters.round, label: filters.round }] : [])], [levels, filters.round])

  const toggle = (key, value) => setFilters(current => ({ ...current, [key]: current[key] === value ? '' : value }))
  const setFilter = (key, value) => setFilters(current => ({ ...current, [key]: value }))
  const activeFilters = [filters.candidate, filters.technology, filters.round].filter(Boolean).length
  const period = formatRange(range)

  const openInterviews = (statusValue = '', overrides = {}) => setView({ kind: 'interviews', status: statusValue, filters: { ...filters, ...overrides } })
  const closeView = useCallback(() => setView(null), [])

  function exportReport() {
    if (!summary) return
    const csv = buildReport({ range, comparison, filters, summary, previous, candidates, levels, technologies })
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = reportFilename(range)
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }

  const previousCounts = comparison && previous ? previous.interviews || {} : null

  return (
    <div className="ba-page">
      <div className="ba-toolbar" role="group" aria-label="Bookings filters">
        <DateRangeControl presetId={presetId} range={range} onPreset={id => setPresetId(id)} onCustom={next => { setCustomRange(next); setPresetId('custom') }} />
        <FilterSelect icon="user" label="Candidate" value={filters.candidate} options={candidateOptions} onChange={value => setFilter('candidate', value)} />
        <FilterSelect icon="code" label="Technology" value={filters.technology} options={technologyOptions} onChange={value => setFilter('technology', value)} />
        <FilterSelect icon="filter" label="Interview level" value={filters.round} options={levelOptions} onChange={value => setFilter('round', value)} />
        {activeFilters > 0 && (
          <button type="button" className="ba-button ba-button--ghost" onClick={() => setFilters(EMPTY_FILTERS)}>
            <Icon name="x" size={14} />Clear filters<span className="ba-count">{activeFilters}</span>
          </button>
        )}
        <div className="ba-toolbar__end">
          <span className="ba-updated" aria-live="polite">{status.refreshing ? 'Updating…' : status.updatedAt ? `Updated ${status.updatedAt}` : ''}</span>
          <button type="button" className="ba-button" onClick={() => openInterviews()} disabled={!total}>
            <Icon name="list" size={15} />Interviews<span className="ba-count">{loading ? '…' : total}</span>
          </button>
          <button type="button" className="ba-button ba-button--icon-text" onClick={() => load({ silent: Boolean(summary) })} disabled={status.loading || status.refreshing} aria-label="Refresh" title={status.updatedAt ? `Refresh (updated ${status.updatedAt})` : 'Refresh'}>
            <Icon name="refresh" size={15} /><span>Refresh</span>
          </button>
          <button type="button" className="ba-button ba-button--icon-text" onClick={exportReport} disabled={!summary || status.loading} aria-label="Export report" title="Export the report (CSV)">
            <Icon name="download" size={15} /><span>Export</span>
          </button>
        </div>
      </div>

      {status.error && (
        <div className="ba-error" role="alert">
          <span>{status.error}</span>
          <button type="button" className="ba-link" onClick={() => load()}>Try again</button>
        </div>
      )}

      <div className="ba-kpis" aria-label="Booking counters" role="group">
        <KpiCard
          label="Total bookings"
          tone="blue"
          note={filters.candidate
            ? `${filters.candidate}${chosenSelected ? ` · ★ ${SELECTED_LABEL}` : ''}`
            : `${candidates.length} candidate${candidates.length === 1 ? '' : 's'}${selectedCount ? ` · ★ ${selectedCount} selected` : ''}`}
          value={total}
          total={total}
          previous={previousCounts ? Number(previousCounts.count) || 0 : undefined}
          comparisonLabel={comparison?.label}
          risingIsGood
          loading={loading}
          onOpen={() => openInterviews()}
        />
        {kpiStatuses().map(entry => (
          <KpiCard
            key={entry.filterValue}
            label={entry.label}
            tone={STATUS_TONE[entry.value]}
            value={Number(interviews[entry.countKey]) || 0}
            total={total}
            previous={previousCounts ? Number(previousCounts[entry.countKey]) || 0 : undefined}
            comparisonLabel={comparison?.label}
            risingIsGood={entry.value === 'attended'}
            loading={loading}
            onOpen={() => openInterviews(entry.filterValue)}
          />
        ))}
        <OtherOutcomesCard interviews={interviews} loading={loading} onOpen={statusValue => openInterviews(statusValue)} />
      </div>

      <div className="ba-grid">
        <section className="ba-card ba-card--donut" aria-labelledby="ba-donut-title">
          <header className="ba-card__head">
            <h2 id="ba-donut-title">Bookings by candidate</h2>
            <span className="ba-muted">{candidates.length} candidates</span>
          </header>
          <div className="ba-donut-layout">
            <Donut segments={segments} total={candidateTotal} activeName={filters.candidate} onPick={segment => (segment.others ? setView({ kind: 'candidates' }) : toggle('candidate', segment.name))} />
            <ul className="ba-legend" aria-label="Candidates in the chart">
              {segments.map(segment => {
                const isActive = filters.candidate === segment.name
                return (
                  <li key={segment.name}>
                    <button type="button" className={`${isActive ? 'is-active' : ''}${filters.candidate && !isActive ? ' is-dim' : ''}`} aria-pressed={segment.others ? undefined : isActive} onClick={() => (segment.others ? setView({ kind: 'candidates' }) : toggle('candidate', segment.name))} title={segment.others ? 'See every candidate' : isActive ? 'Show every candidate again' : `Show only ${segment.name}`}>
                      <i style={{ background: segment.color }} aria-hidden="true" />
                      <span>{segment.name}{segment.selected && <SelectedBadge />}</span>
                      <strong>{segment.count}</strong>
                      <em>{formatPercent(segment.percent)}</em>
                    </button>
                  </li>
                )
              })}
              {!segments.length && !loading && <li className="ba-empty">No bookings in this period.</li>}
            </ul>
          </div>
        </section>

        <section className="ba-card ba-card--top" aria-labelledby="ba-top-title">
          <header className="ba-card__head">
            <h2 id="ba-top-title">Top {TOP_LIMIT} candidates</h2>
            <SearchBox value={search} onChange={setSearch} placeholder="Search candidates" label="Search candidates" />
          </header>
          <CandidateTable
            rows={topRows}
            limit={TOP_LIMIT}
            selected={filters.candidate}
            onPick={name => toggle('candidate', name)}
            onInterviews={name => openInterviews('', { candidate: name })}
            label={`Top ${TOP_LIMIT} candidates by bookings`}
            emptyText={search ? 'No candidate matches that search.' : loading ? 'Loading…' : 'No bookings in this period.'}
          />
          <footer className="ba-card__foot">
            <span className="ba-muted">
              {search ? `${Math.min(TOP_LIMIT, topRows.length)} of ${topRows.length} matching` : `Showing ${Math.min(TOP_LIMIT, candidates.length)} of ${candidates.length} candidates`}
            </span>
            <button type="button" className="ba-link" onClick={() => setView({ kind: 'candidates' })} disabled={!candidates.length}>View all candidates<Icon name="arrow-right" size={14} /></button>
          </footer>
        </section>

        <div className="ba-stack">
          <section className="ba-card ba-card--levels" aria-labelledby="ba-levels-title">
            <header className="ba-card__head">
              <h2 id="ba-levels-title">Interview levels</h2>
              {filters.round && <button type="button" className="ba-link" onClick={() => setFilter('round', '')}>All levels</button>}
            </header>
            <BarList items={levels} active={filters.round} onPick={item => toggle('round', item.name)} colorFor={() => '#6366f1'} label="Interview levels" />
            {!levels.length && !loading && <p className="ba-empty">No bookings in this period.</p>}
          </section>

          <section className="ba-card ba-card--tech" aria-labelledby="ba-tech-title">
            <header className="ba-card__head">
              <h2 id="ba-tech-title">Tech stack</h2>
              <button type="button" className="ba-link" onClick={() => setView({ kind: 'technologies' })} disabled={!technologies.length}>
                View all{technologies.length ? ` ${technologies.length}` : ''}<Icon name="arrow-right" size={14} />
              </button>
            </header>
            <BarList items={topTechnologies} active={filters.technology} onPick={item => toggle('technology', item.name)} colorFor={() => '#14b8a6'} label="Technologies" />
            {!technologies.length && !loading && <p className="ba-empty">No bookings in this period.</p>}
          </section>
        </div>
      </div>

      {view?.kind === 'candidates' && (
        <AllCandidatesView
          rows={candidates}
          selected={filters.candidate}
          period={period}
          onPick={name => { toggle('candidate', name); setView(null) }}
          onInterviews={name => openInterviews('', { candidate: name })}
          onClose={closeView}
        />
      )}
      {view?.kind === 'technologies' && (
        <AllTechnologiesView items={technologies} total={techSource?.total || 0} active={filters.technology} period={period} onPick={item => { toggle('technology', item.name); setView(null) }} onClose={closeView} />
      )}
      {view?.kind === 'interviews' && (
        <InterviewsView range={range} filters={view.filters} initialStatus={view.status} period={period} onClose={closeView} />
      )}
    </div>
  )
}

export default BookingsAnalyticsPanel
