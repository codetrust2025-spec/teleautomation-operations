/**
 * Sort By for the Daily Ops interview roster.
 *
 * Latest to Oldest (the default) or Oldest to Latest by interview date. On one
 * date the interviews read as that day's schedule, earliest first, whichever
 * way the dates run. A row with no date, or no readable time, goes after the
 * dated ones. Only the order on screen changes: nothing is sent or saved
 * except the choice itself, in this browser, so it survives a reload.
 */
export const ROSTER_SORT_OPTIONS = [
  { value: 'date_desc', label: 'Latest to Oldest' },
  { value: 'date_asc', label: 'Oldest to Latest' },
]
export const DEFAULT_ROSTER_SORT = 'date_desc'
export const ROSTER_SORT_STORAGE_KEY = 'ta.dailyOps.rosterSort'

const VALID = new Set(ROSTER_SORT_OPTIONS.map(option => option.value))

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

/** A new array in the chosen order; the input is left as it was. */
export function sortRosterRows(rows, order = DEFAULT_ROSTER_SORT) {
  const direction = order === 'date_asc' ? 1 : -1
  return [...(rows || [])].sort((a, b) => {
    const da = dateKey(a)
    const db = dateKey(b)
    if (da !== db) {
      if (!da) return 1
      if (!db) return -1
      return da < db ? -direction : direction
    }
    const ta = minutesOfDay(a?.time)
    const tb = minutesOfDay(b?.time)
    if (ta === tb) return 0
    if (ta === null) return 1
    if (tb === null) return -1
    return ta - tb
  })
}
