/**
 * Sort By for the Daily Ops interview roster.
 *
 * Every order is read from fields the roster rows already carry -- the ISO
 * date and the time, never the label shown -- nothing is requested for it and
 * nothing is written except the choice itself, kept in this browser so it
 * survives a reload. Oldest to Latest (the default) runs by each interview's
 * IST start, earliest first, through the helper the public Confirmed slots
 * list sorts with too; Latest to Oldest is its reverse. Time orders settle
 * ties earliest first, the other orders latest first. A missing value (no
 * date, no readable time, no round...) goes last.
 * Alphabetical orders by candidate, company, technology or attendee name are
 * deliberately not offered.
 */
import { INTERVIEW_STATUSES, canonicalStatus } from './interviewStatuses.js'
import { rowCompanyName } from './companyName.js'
import { compareInterviewStart, interviewClockMinutes } from '../utils/interviewChronology.js'

export const ROSTER_SORT_OPTIONS = [
  { value: 'date_asc', label: 'Oldest to Latest' },
  { value: 'date_desc', label: 'Latest to Oldest' },
  { value: 'time_asc', label: 'Time Earliest' },
  { value: 'time_desc', label: 'Time Latest' },
  { value: 'round', label: 'Interview Round' },
  { value: 'status', label: 'Attendance Status' },
  { value: 'updated_desc', label: 'Recently Updated' },
  { value: 'created_desc', label: 'Recently Created' },
  { value: 'screenshot_first', label: 'Screenshot Available First' },
  { value: 'missing_company_first', label: 'Missing Company First' },
  { value: 'missing_round_first', label: 'Missing Round First' },
]
export const DEFAULT_ROSTER_SORT = 'date_asc'
export const ROSTER_SORT_STORAGE_KEY = 'ta.dailyOps.rosterSort.v3'

const VALID = new Set(ROSTER_SORT_OPTIONS.map(option => option.value))

/** Earliest stage first; the same rounds the roster's Level filter offers, plus L3. */
const ROUND_ORDER = ['screening', 'l1', 'l2', 'l3', 'final', 'hr']
/** The order of the status tabs above the table: Pending, Attended, Not attended, ... */
const STATUS_ORDER = INTERVIEW_STATUSES.map(entry => entry.value)

export function rosterSortLabel(value) {
  return (ROSTER_SORT_OPTIONS.find(option => option.value === value) || ROSTER_SORT_OPTIONS[0]).label
}

/** The saved choice, or the default when there is none, it is unknown, or storage is unavailable. */
export function readRosterSort() {
  try {
    const saved = window.localStorage.getItem(ROSTER_SORT_STORAGE_KEY)
    return VALID.has(saved) ? saved : DEFAULT_ROSTER_SORT
  } catch {
    return DEFAULT_ROSTER_SORT
  }
}

export function saveRosterSort(value) {
  if (!VALID.has(value)) return
  try {
    window.localStorage.setItem(ROSTER_SORT_STORAGE_KEY, value)
  } catch {
    // Private windows and blocked storage: the choice simply lasts until reload.
  }
}

/** "15:00", "9:05", "3:00 PM", "11:30am", "3 pm" -> minutes after midnight; null when unreadable. */
export const minutesOfDay = interviewClockMinutes

function stamp(value) {
  const ms = Date.parse(String(value || ''))
  return Number.isFinite(ms) ? ms : null
}

function roundRank(row) {
  const round = String(row?.interview_round || '').trim().toLowerCase()
  if (!round) return null
  const at = ROUND_ORDER.indexOf(round)
  return at === -1 ? ROUND_ORDER.length : at
}

function statusRank(row) {
  const status = canonicalStatus(row?.interview_attendance_status_resolved || row?.interview_attendance_status)
  const at = STATUS_ORDER.indexOf(status)
  return at === -1 ? STATUS_ORDER.length : at
}

const has = value => String(value || '').trim() !== ''

/** Compare two values, missing (null/undefined) last, `direction` 1 ascending / -1 descending. */
function by(a, b, direction = 1) {
  const missingA = a === null || a === undefined || a === ''
  const missingB = b === null || b === undefined || b === ''
  if (missingA || missingB) return missingA === missingB ? 0 : missingA ? 1 : -1
  if (a === b) return 0
  return (a < b ? -1 : 1) * direction
}

/** The earliest IST start first: the order the server and Confirmed slots use. */
function earliestFirst(a, b) {
  return compareInterviewStart(a, b, 1)
}

/** The latest IST start first (tie-breaker for attribute sorts). */
function latestFirst(a, b) {
  return compareInterviewStart(a, b, -1)
}

const COMPARE = {
  date_asc: earliestFirst,
  date_desc: latestFirst,
  time_asc: (a, b) => by(minutesOfDay(a?.time), minutesOfDay(b?.time), 1) || earliestFirst(a, b),
  time_desc: (a, b) => by(minutesOfDay(a?.time), minutesOfDay(b?.time), -1) || latestFirst(a, b),
  round: (a, b) => by(roundRank(a), roundRank(b), 1) || latestFirst(a, b),
  status: (a, b) => by(statusRank(a), statusRank(b), 1) || latestFirst(a, b),
  updated_desc: (a, b) => by(stamp(a?.updated_at), stamp(b?.updated_at), -1) || latestFirst(a, b),
  created_desc: (a, b) => by(stamp(a?.created_at), stamp(b?.created_at), -1) || latestFirst(a, b),
  screenshot_first: (a, b) => by(has(a?.slot_screenshot_proof?.url) ? 0 : 1, has(b?.slot_screenshot_proof?.url) ? 0 : 1) || latestFirst(a, b),
  missing_company_first: (a, b) => by(has(rowCompanyName(a)) ? 1 : 0, has(rowCompanyName(b)) ? 1 : 0) || latestFirst(a, b),
  missing_round_first: (a, b) => by(has(a?.interview_round) ? 1 : 0, has(b?.interview_round) ? 1 : 0) || latestFirst(a, b),
}

/** A new array in the chosen order; the input is left as it was. */
export function sortRosterRows(rows, order = DEFAULT_ROSTER_SORT) {
  const compare = COMPARE[order] || COMPARE[DEFAULT_ROSTER_SORT]
  return [...(rows || [])].sort(compare)
}
