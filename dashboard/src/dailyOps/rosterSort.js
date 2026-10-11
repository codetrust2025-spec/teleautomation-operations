/**
 * Sort By for the Daily Ops interview roster.
 *
 * Every order is read from fields the roster rows already carry -- the ISO
 * date and the time, never the label shown -- nothing is requested for it and
 * nothing is written except the choice itself, kept in this browser so it
 * survives a reload. Latest to Oldest (the default) runs date then time, both
 * newest first; Oldest to Latest both oldest first. Within any other order,
 * ties fall back to the default. A missing value (no date, no readable time,
 * no round...) goes last.
 * Alphabetical orders by candidate, company, technology or attendee name are
 * deliberately not offered.
 */
import { INTERVIEW_STATUSES, canonicalStatus } from './interviewStatuses.js'

export const ROSTER_SORT_OPTIONS = [
  { value: 'date_desc', label: 'Latest to Oldest' },
  { value: 'date_asc', label: 'Oldest to Latest' },
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
export const DEFAULT_ROSTER_SORT = 'date_desc'
export const ROSTER_SORT_STORAGE_KEY = 'ta.dailyOps.rosterSort'

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

/** "15:00", "9:05", "3:00 PM", "11:30am" -> minutes after midnight; null when unreadable. */
export function minutesOfDay(value) {
  const m = /^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*([ap]\.?m\.?)?\s*$/i.exec(String(value || ''))
  if (!m) return null
  let hours = Number(m[1])
  const minutes = Number(m[2])
  const meridiem = (m[3] || '').replace(/\./g, '').toLowerCase()
  if (minutes > 59 || hours > 23 || (meridiem && (hours < 1 || hours > 12))) return null
  if (meridiem === 'pm' && hours !== 12) hours += 12
  if (meridiem === 'am' && hours === 12) hours = 0
  return hours * 60 + minutes
}

function dateKey(row) {
  const value = String(row?.date || '').slice(0, 10)
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : ''
}

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

/** The default order: newest date first and, on one date, latest time first. */
function defaultOrder(a, b) {
  return by(dateKey(a), dateKey(b), -1) || by(minutesOfDay(a?.time), minutesOfDay(b?.time), -1)
}

const COMPARE = {
  date_desc: defaultOrder,
  date_asc: (a, b) => by(dateKey(a), dateKey(b), 1) || by(minutesOfDay(a?.time), minutesOfDay(b?.time), 1),
  time_asc: (a, b) => by(minutesOfDay(a?.time), minutesOfDay(b?.time), 1) || defaultOrder(a, b),
  time_desc: (a, b) => by(minutesOfDay(a?.time), minutesOfDay(b?.time), -1) || defaultOrder(a, b),
  round: (a, b) => by(roundRank(a), roundRank(b), 1) || defaultOrder(a, b),
  status: (a, b) => by(statusRank(a), statusRank(b), 1) || defaultOrder(a, b),
  updated_desc: (a, b) => by(stamp(a?.updated_at), stamp(b?.updated_at), -1) || defaultOrder(a, b),
  created_desc: (a, b) => by(stamp(a?.created_at), stamp(b?.created_at), -1) || defaultOrder(a, b),
  screenshot_first: (a, b) => by(has(a?.slot_screenshot_proof?.url) ? 0 : 1, has(b?.slot_screenshot_proof?.url) ? 0 : 1) || defaultOrder(a, b),
  missing_company_first: (a, b) => by(has(a?.interview_company) ? 1 : 0, has(b?.interview_company) ? 1 : 0) || defaultOrder(a, b),
  missing_round_first: (a, b) => by(has(a?.interview_round) ? 1 : 0, has(b?.interview_round) ? 1 : 0) || defaultOrder(a, b),
}

/** A new array in the chosen order; the input is left as it was. */
export function sortRosterRows(rows, order = DEFAULT_ROSTER_SORT) {
  const compare = COMPARE[order] || defaultOrder
  return [...(rows || [])].sort(compare)
}
