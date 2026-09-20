/**
 * The interview attendance statuses, in one place.
 *
 * These mirror `INTERVIEW_ATTENDANCE_STATUSES` in features/candidate_store.py,
 * which is the source of truth: the API normalises every write through it and
 * derives one counter per member. Nothing here may invent a status the backend
 * would not store.
 *
 * They were previously declared twice — the row dropdown listed all six, while
 * the KPI tabs above the table named four of them inline. Cancelled,
 * Rescheduled and Re-Service therefore had no tab, and with the tabs being the
 * only way to filter, three of the six statuses could be set on a row and then
 * never filtered for.
 *
 * Pending is the absence of a stored status, not a stored value: the row
 * dropdown submits `''` for it, and the backend normaliser maps both `''` and
 * `'pending'` to `''`. The dashboard filter needs a token that is distinct
 * from "no filter at all", so it uses `'pending'` — hence `filterValue`
 * differing from `value` for that one entry, and only that one.
 */

/** Booking state, not attendance: every row in the roster has a confirmed
 *  slot, so this counts them all and clears the attendance filter. It is kept
 *  separate from the six below and is never a value any row can be set to. */
export const SCHEDULED_TAB = {
  id: 'scheduled',
  label: 'Scheduled',
  countKey: 'count',
  kpiTone: 'blue',
  filterValue: '',
}

export const INTERVIEW_STATUSES = [
  { value: '', filterValue: 'pending', label: 'Pending', tone: 'pending', countKey: 'pending_count', kpiTone: 'amber' },
  { value: 'attended', filterValue: 'attended', label: 'Attended', tone: 'done', countKey: 'attended_count', kpiTone: 'green' },
  { value: 'not_attended', filterValue: 'not_attended', label: 'Not attended', tone: 'missed', countKey: 'not_attended_count', kpiTone: 'red' },
  { value: 'cancelled', filterValue: 'cancelled', label: 'Cancelled', tone: 'cancelled', countKey: 'cancelled_count', kpiTone: 'slate' },
  { value: 'rescheduled', filterValue: 'rescheduled', label: 'Rescheduled', tone: 'rescheduled', countKey: 'rescheduled_count', kpiTone: 'purple' },
  // The sitting is off and the hour is free again, with no replacement booked
  // yet. Rescheduled cannot say that: a booking moved to its new time in place
  // keeps that marker and is still the live interview.
  { value: 'released_for_reschedule', filterValue: 'released_for_reschedule', label: 'Awaiting new slot', tone: 'rescheduled', countKey: 'released_for_reschedule_count', kpiTone: 'purple' },
  // Admin-only: grants one free repeat interview. Never shown to candidates,
  // but it is a stored status and so it counts and filters like the rest.
  { value: 're_service', filterValue: 're_service', label: 'Re-Service', tone: 'reservice', countKey: 're_service_count', kpiTone: 'yellow' },
]

/** What the row dropdown offers — every status, Pending first. */
export const STATUS_OPTIONS = INTERVIEW_STATUSES.map(({ value, label, tone }) => ({ value, label, tone }))

/** The tabs above the table: Scheduled, then one per status. */
export const STATUS_TABS = [SCHEDULED_TAB, ...INTERVIEW_STATUSES]

const byValue = new Map(INTERVIEW_STATUSES.map(entry => [entry.value, entry]))

/** Normalise the stored spelling before looking anything up. */
export function canonicalStatus(status) {
  const key = String(status || '').trim().toLowerCase()
  if (key === 'canceled') return 'cancelled'
  if (key === 'reschedule') return 'rescheduled'
  if (key === 'pending') return ''
  return key
}

export function statusEntry(status) {
  return byValue.get(canonicalStatus(status)) || byValue.get('')
}

export function statusTone(status) {
  return statusEntry(status).tone
}

export function statusLabel(status) {
  return statusEntry(status).label
}

/** Does this row's status belong under the given dashboard filter token? */
export function matchesStatusFilter(status, filterValue) {
  if (!filterValue) return true
  return statusEntry(status).filterValue === filterValue
}

/** Every counter the KPI tabs index into, zeroed.
 *
 *  The roster used to seed and rebuild its counts from three keys spelled out
 *  by hand. Those counts are what the dashboard falls back to until the global
 *  summary arrives, so the tabs for the statuses it omitted read 0 in that
 *  window — and permanently if that request failed. */
export function emptyStatusCounts() {
  const zeroed = { count: 0, scheduled_count: 0 }
  for (const status of INTERVIEW_STATUSES) zeroed[status.countKey] = 0
  return zeroed
}

/** Tally the rows themselves.
 *
 *  The authoritative Pending number, because it is the one the reader can
 *  count on screen. The payload's totals are computed server-side over the
 *  same query, but only the rows survive the client-side status filter, and a
 *  payload that disagrees with its own rows is the payload being wrong. */
export function countStatusRows(rows, statusOf) {
  const counts = emptyStatusCounts()
  const list = Array.isArray(rows) ? rows : []
  counts.count = list.length
  counts.scheduled_count = list.length
  for (const row of list) {
    counts[statusEntry(statusOf(row)).countKey] += 1
  }
  return counts
}

/** Pull those same counters out of an API payload. */
export function readStatusCounts(payload) {
  const source = payload || {}
  const counts = emptyStatusCounts()
  for (const key of Object.keys(counts)) counts[key] = source[key] || 0
  return counts
}
