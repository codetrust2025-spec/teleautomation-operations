/**
 * Bookings Analytics: the date ranges, comparisons and groupings the page
 * draws from the interview summary (`GET /candidates/interviews/global`).
 *
 * Nothing here computes a booking count of its own. Every number comes from
 * the summary the server builds over the stored interview records -- the same
 * one Daily Ops reads -- and this module only arranges it: which period to ask
 * for, which period to compare it with, the share each item holds, and the
 * report a person downloads.
 */
import { ALL_TIME_RANGE } from '../dailyOps/dateRangePresets.js'
import { todayIso } from '../dailyOps/calendarDates.js'
import { INTERVIEW_STATUSES, statusEntry } from '../dailyOps/interviewStatuses.js'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function parts(iso) {
  const [year, month, day] = String(iso).slice(0, 10).split('-').map(Number)
  return { year, month, day }
}

function iso(year, month, day) {
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

function daysInMonth(year, month) {
  return new Date(Date.UTC(year, month, 0)).getUTCDate()
}

/** The calendar month `offset` months from the one holding `day`. */
export function monthRange(day, offset = 0) {
  const { year, month } = parts(day)
  const index = year * 12 + (month - 1) + offset
  const y = Math.floor(index / 12)
  const m = (index % 12) + 1
  return { from: iso(y, m, 1), to: iso(y, m, daysInMonth(y, m)) }
}

function dayNumber(day) {
  const { year, month, day: d } = parts(day)
  return Date.UTC(year, month - 1, d) / 86400000
}

function fromDayNumber(n) {
  const date = new Date(n * 86400000)
  return iso(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate())
}

export const RANGE_PRESETS = [
  { id: 'all_time', label: 'All time' },
  { id: 'this_month', label: 'This month' },
  { id: 'last_month', label: 'Last month' },
  { id: 'last_3_months', label: 'Last 3 months' },
  { id: 'this_year', label: 'This year' },
]

// The page opens on every booking there is: an analytics page that opened on
// one month would greet the reader with a fraction of the record and no sign
// of the rest.
export const DEFAULT_PRESET = 'all_time'

/** The span a preset covers on `today` (IST), or null for an unknown id. */
export function presetRange(presetId, today = todayIso()) {
  const { year } = parts(today)
  switch (presetId) {
    case 'this_month': return monthRange(today, 0)
    case 'last_month': return monthRange(today, -1)
    case 'last_3_months': return { from: monthRange(today, -2).from, to: monthRange(today, 0).to }
    case 'this_year': return { from: iso(year, 1, 1), to: iso(year, 12, 31) }
    case 'all_time': return { ...ALL_TIME_RANGE }
    default: return null
  }
}

/**
 * The period a range is compared with, and how to say so. Calendar presets
 * compare with the calendar period before them; a custom range with the same
 * number of days just before it. All time has nothing before it.
 */
export function comparisonFor(presetId, range) {
  if (!range || presetId === 'all_time') return null
  if (presetId === 'this_month' || presetId === 'last_month') {
    return { range: monthRange(range.from, -1), label: 'vs last month' }
  }
  if (presetId === 'last_3_months') {
    return { range: { from: monthRange(range.from, -3).from, to: monthRange(range.from, -1).to }, label: 'vs previous 3 months' }
  }
  if (presetId === 'this_year') {
    const { year } = parts(range.from)
    return { range: { from: iso(year - 1, 1, 1), to: iso(year - 1, 12, 31) }, label: 'vs last year' }
  }
  const start = dayNumber(range.from)
  const end = dayNumber(range.to)
  if (!(end >= start)) return null
  const length = end - start + 1
  return {
    range: { from: fromDayNumber(start - length), to: fromDayNumber(start - 1) },
    label: length === 1 ? 'vs previous day' : `vs previous ${length} days`,
  }
}

export function isAllTime(range) {
  return Boolean(range) && range.from === ALL_TIME_RANGE.from && range.to === ALL_TIME_RANGE.to
}

/** "01 Oct 2026 - 31 Oct 2026", or "All time". */
export function formatRange(range) {
  if (!range) return ''
  if (isAllTime(range)) return 'All time'
  const label = day => { const { year, month, day: d } = parts(day); return `${String(d).padStart(2, '0')} ${MONTHS[month - 1]} ${year}` }
  return range.from === range.to ? label(range.from) : `${label(range.from)} - ${label(range.to)}`
}

/**
 * The change from `previous` to `current`, in percent, one decimal. Null when
 * there is no comparison to make: no earlier period, or nothing in it to
 * compare against (a rise from zero has no percentage).
 */
export function percentChange(current, previous) {
  if (previous === null || previous === undefined) return null
  const now = Number(current) || 0
  const before = Number(previous) || 0
  if (before === 0) return now === 0 ? 0 : null
  return Math.round(((now - before) / before) * 1000) / 10
}

/** Share of `total`, one decimal, as a number. */
export function share(count, total) {
  const t = Number(total) || 0
  return t ? Math.round(((Number(count) || 0) / t) * 1000) / 10 : 0
}

export function formatPercent(value) {
  return `${(Number(value) || 0).toFixed(1)}%`
}

/** Items by count, largest first, with their rank and share of `total`. */
export function ranked(items, total) {
  return [...(items || [])]
    .map(item => ({ ...item, count: Number(item.count) || 0 }))
    .sort((a, b) => b.count - a.count || String(a.name).localeCompare(String(b.name)))
    .map((item, index) => ({ ...item, rank: index + 1, percent: share(item.count, total) }))
}

export function sumCounts(items) {
  return (items || []).reduce((sum, item) => sum + (Number(item.count) || 0), 0)
}

/**
 * The first `limit` items, and the rest folded into one "Others (n)" entry
 * when there is more than one of them (a single leftover is shown by name).
 */
export function topWithOthers(items, total, limit) {
  const all = ranked(items, total)
  if (all.length <= limit + 1) return all
  const head = all.slice(0, limit)
  const rest = all.slice(limit)
  const count = sumCounts(rest)
  return [...head, { name: `Others (${rest.length})`, count, percent: share(count, total), others: rest.length }]
}

/** The interview levels in the order a round runs, then any others. */
const LEVEL_ORDER = ['Screening', 'L1', 'L2', 'L3', 'Final', 'HR']

export function orderedLevels(levels, total) {
  const position = name => (name === 'Unspecified' ? 99 : (LEVEL_ORDER.indexOf(name) + 1 || 50))
  return ranked(levels, total).sort((a, b) => position(a.name) - position(b.name) || b.count - a.count)
}

/**
 * Every candidate in the selection, with their booking count and outcome
 * counts. Read from `interviews.by_candidate`, which the server takes before a
 * candidate selection narrows the rows: the candidate list on the page keeps
 * everyone while one of them is chosen, and the chosen one is highlighted
 * rather than left alone in it.
 */
export function candidateRows(summary) {
  const entries = summary?.interviews?.by_candidate || []
  const rows = entries.map(entry => ({ ...entry, name: entry.name, count: Number(entry.scheduled) || 0 }))
  return ranked(rows, sumCounts(rows))
}

/** The statuses shown as cards of their own, in order. */
export const KPI_STATUS_VALUES = ['attended', '', 'not_attended', 'cancelled', 'rescheduled']

/** The rest of the stored statuses, gathered on one card. */
export function otherStatuses() {
  return INTERVIEW_STATUSES.filter(status => !KPI_STATUS_VALUES.includes(status.value))
}

export function kpiStatuses() {
  return KPI_STATUS_VALUES.map(value => INTERVIEW_STATUSES.find(status => status.value === value))
}

/** A roster row's status, the way Daily Ops reads it. */
export function rowStatus(row) {
  return statusEntry(row?.interview_attendance_status_resolved ?? row?.interview_attendance_status)
}

/** Interview rows, latest first: date, then start time. */
export function latestFirst(rows) {
  return [...(rows || [])].sort((a, b) => {
    const day = String(b.date || '').localeCompare(String(a.date || ''))
    return day || String(b.time || '').localeCompare(String(a.time || ''))
  })
}

function csvCell(value) {
  const text = String(value ?? '')
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function csvRows(rows) {
  return rows.map(row => row.map(csvCell).join(',')).join('\r\n')
}

/**
 * The report behind Export: the period and filters, the headline figures with
 * their comparison, every candidate with their outcome counts, and the level
 * and technology breakdowns -- all from the summaries on screen.
 */
export function buildReport({ range, comparison, filters, summary, previous, candidates, levels, technologies }) {
  const interviews = summary?.interviews || {}
  const before = previous?.interviews || null
  const statuses = INTERVIEW_STATUSES
  const rows = [
    ['Bookings Analytics report'],
    ['Period', formatRange(range)],
    ['Candidate', filters?.candidate || 'All candidates'],
    ['Technology', filters?.technology || 'All technologies'],
    ['Interview level', filters?.round || 'All levels'],
    [],
    ['Metric', 'Count', comparison ? `Previous period (${formatRange(comparison.range)})` : 'Previous period', 'Change %'],
    ...[{ label: 'Total bookings', countKey: 'count' }, ...statuses].map(({ label, countKey }) => {
      const now = Number(interviews[countKey]) || 0
      const prior = before ? Number(before[countKey]) || 0 : ''
      const change = before ? percentChange(now, prior) : null
      return [label, now, prior, change === null ? '' : change]
    }),
    [],
    ['Candidate', 'Bookings', 'Share %', ...statuses.map(status => status.label)],
  ]
  const shown = filters?.candidate ? candidates.filter(item => item.name === filters.candidate) : candidates
  for (const item of shown) {
    rows.push([item.name, item.count, item.percent, ...statuses.map(status => Number(item[status.value || 'pending']) || 0)])
  }
  rows.push([], ['Interview level', 'Bookings', 'Share %'])
  for (const item of levels) rows.push([item.name, item.count, item.percent])
  rows.push([], ['Technology', 'Bookings', 'Share %'])
  for (const item of technologies) rows.push([item.name, item.count, item.percent])
  return `﻿${csvRows(rows)}\r\n`
}

export function reportFilename(range) {
  const span = isAllTime(range) ? 'all-time' : `${range.from}_${range.to}`
  return `bookings-analytics_${span}.csv`
}
