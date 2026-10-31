/**
 * When an interview starts, as one IST instant, and the order that follows
 * from it: earliest first.
 *
 * Daily Ops and the public Confirmed slots list both import this, so the two
 * pages cannot disagree about which interview comes first. Each row carries
 * its slot as a wall-clock IST date ("2026-10-29") and start time ("15:00",
 * "3:00 PM", "3 pm"); the instant is built from those two fields, never from
 * a label on screen, and nothing is requested or written to sort.
 *
 * The clock reading mirrors the backend's `_interview_time_sort_key`, and a
 * tie on one start is broken by name as `_slot_chronological_sort_key` breaks
 * it, so the server's own earliest-first lists keep the order they left in.
 */

/** India has kept one offset all year since 1945: UTC+05:30, no daylight saving. */
const IST_OFFSET_MS = 330 * 60_000

/**
 * "15:00", "9:05", "3:00 PM", "11:30am", "3 pm", "3.00 p.m." -> minutes after
 * midnight; null when unreadable.
 */
export function interviewClockMinutes(value) {
  const text = String(value ?? '').trim().toLowerCase()
    .replace(/a\.m\.?/g, 'am').replace(/p\.m\.?/g, 'pm').replace(/\./g, ':').replace(/\s+/g, ' ')
  const m = /^(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(am|pm)?$/.exec(text)
  // A bare hour ("15") is not a time; "3 pm" is.
  if (!m || (m[2] === undefined && !m[3])) return null
  let hours = Number(m[1])
  const minutes = Number(m[2] || 0)
  if (minutes > 59 || hours > 23 || (m[3] && (hours < 1 || hours > 12))) return null
  if (m[3] === 'pm' && hours !== 12) hours += 12
  if (m[3] === 'am' && hours === 12) hours = 0
  return hours * 60 + minutes
}

/** The slot's IST calendar day as `YYYY-MM-DD` ("2026-9-5" padded), or '' when it is not a real day. */
export function interviewDayIso(value) {
  const m = /^(\d{4})-(\d{1,2})-(\d{1,2})/.exec(String(value ?? '').trim())
  if (!m) return ''
  const [year, month, day] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const probe = new Date(Date.UTC(year, month - 1, day))
  if (probe.getUTCFullYear() !== year || probe.getUTCMonth() !== month - 1 || probe.getUTCDate() !== day) return ''
  return `${m[1]}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

/** The interview's start as epoch ms, read as IST; null without a real date and a readable time. */
export function interviewStartMs(row) {
  const day = interviewDayIso(row?.date)
  const minutes = interviewClockMinutes(row?.time)
  if (!day || minutes === null) return null
  const [year, month, date] = day.split('-').map(Number)
  return Date.UTC(year, month - 1, date, 0, minutes) - IST_OFFSET_MS
}

/** `direction` 1 ascending / -1 descending; a missing value ('' or null) last either way. */
function presentFirst(a, b, direction) {
  const missingA = a === null || a === ''
  const missingB = b === null || b === ''
  if (missingA || missingB) return missingA === missingB ? 0 : missingA ? 1 : -1
  return a === b ? 0 : (a < b ? -1 : 1) * direction
}

/**
 * Comparator for `Array.prototype.sort`: by IST start, `direction` 1 earliest
 * first (the default) or -1 latest first. Whatever the direction, an undated
 * row goes last, and on its day a slot with no readable start goes after the
 * ones that have one. One start is broken by name, as the server breaks it.
 */
export function compareInterviewStart(a, b, direction = 1) {
  const startA = interviewStartMs(a)
  const startB = interviewStartMs(b)
  const byStart = startA !== null && startB !== null
    ? presentFirst(startA, startB, direction)
    : presentFirst(interviewDayIso(a?.date), interviewDayIso(b?.date), direction) || presentFirst(startA, startB, direction)
  if (byStart) return byStart
  const nameA = String(a?.name || '').toLowerCase()
  const nameB = String(b?.name || '').toLowerCase()
  return nameA === nameB ? 0 : nameA < nameB ? -1 : 1
}

/** A new array, earliest interview first; the input is left as it was. */
export function sortInterviewsEarliestFirst(rows) {
  return [...(rows || [])].sort((a, b) => compareInterviewStart(a, b, 1))
}
