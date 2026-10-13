/**
 * Calendar arithmetic for the Daily Ops date picker.
 *
 * Every helper takes and returns a bare `YYYY-MM-DD` day and does its work at
 * noon UTC. A day built that way survives every arithmetic step here — adding
 * a day, stepping a month, laying out a grid — without a local-time offset
 * ever pulling it onto the neighbouring date, which is what `new Date(iso)`
 * followed by `getDate()` does for anyone west of Greenwich.
 *
 * "Today" is asked of the IST clock through the shared timezone utility, so
 * the calendar's Today, the Today period preset and the roster all name the
 * same day at every hour, including between midnight and 05:30 IST.
 */
import { istDayIso } from '../utils/istTime.js'

/** Monday-first, matching the week the `thisWeek` preset already spans. */
export const WEEKDAY_LABELS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']

export const MONTH_LABELS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})$/
const ISO_MONTH = /^(\d{4})-(\d{2})$/

/** Today in IST. The one definition of "today" this panel uses. */
export function todayIso() {
  return istDayIso()
}

function pad(value, size) {
  return String(value).padStart(size, '0')
}

/** A UTC-noon date for `YYYY-MM-DD`, or null when the day is not a real one. */
function noonUtc(iso) {
  const match = ISO_DAY.exec(String(iso || '').trim())
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const date = new Date(Date.UTC(year, month - 1, day, 12))
  if (Number.isNaN(date.getTime())) return null
  // Date.UTC rolls 2026-02-30 forward to 2 March rather than rejecting it, so
  // a day only counts as valid when it reads back exactly as it was written.
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) {
    return null
  }
  return date
}

/** Is this a real calendar day? `2026-02-30` and `2027-13-40` are not. */
export function isValidIsoDay(value) {
  return noonUtc(value) !== null
}

/** `YYYY-MM-DD` for a year / zero-based month / day-of-month triple. */
export function toIsoDay(year, monthIndex, day) {
  const date = new Date(Date.UTC(year, monthIndex, day, 12))
  if (Number.isNaN(date.getTime())) return ''
  return `${pad(date.getUTCFullYear(), 4)}-${pad(date.getUTCMonth() + 1, 2)}-${pad(date.getUTCDate(), 2)}`
}

/** The day `days` after `iso` (negative steps backwards). */
export function addDaysIso(iso, days) {
  const date = noonUtc(iso)
  if (!date) return ''
  date.setUTCDate(date.getUTCDate() + days)
  return toIsoDay(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate())
}

/**
 * The same day-of-month `months` later, clamped to the target month's length
 * so stepping from 31 January lands on 28 February rather than 3 March.
 */
export function addMonthsIso(iso, months) {
  const date = noonUtc(iso)
  if (!date) return ''
  const year = date.getUTCFullYear()
  const monthIndex = date.getUTCMonth() + months
  const lastDay = daysInMonth(year, monthIndex)
  return toIsoDay(year, monthIndex, Math.min(date.getUTCDate(), lastDay))
}

/** How many days the given year / zero-based month holds. */
export function daysInMonth(year, monthIndex) {
  return new Date(Date.UTC(year, monthIndex + 1, 0, 12)).getUTCDate()
}

/** `YYYY-MM` for a day, or '' when the day is not a real one. */
export function monthKeyOf(iso) {
  const date = noonUtc(iso)
  if (!date) return ''
  return `${pad(date.getUTCFullYear(), 4)}-${pad(date.getUTCMonth() + 1, 2)}`
}

/** `{ year, monthIndex }` for a `YYYY-MM` key, or null. */
export function parseMonthKey(value) {
  const match = ISO_MONTH.exec(String(value || '').trim())
  if (!match) return null
  const monthIndex = Number(match[2]) - 1
  if (monthIndex < 0 || monthIndex > 11) return null
  return { year: Number(match[1]), monthIndex }
}

/** First and last day of a month, as the inclusive range the API takes. */
export function monthRangeIso(year, monthIndex) {
  return {
    from: toIsoDay(year, monthIndex, 1),
    to: toIsoDay(year, monthIndex, daysInMonth(year, monthIndex)),
  }
}

/**
 * Six Monday-first weeks covering the month, leading and trailing days
 * included. Always 42 cells, so the popover keeps one height and the grid
 * never jumps as the operator pages through months.
 */
export function monthGrid(year, monthIndex) {
  const first = new Date(Date.UTC(year, monthIndex, 1, 12))
  // An out-of-range monthIndex (December's "next") has already rolled the year
  // over here, so the month a cell belongs to is read back off the anchor.
  const shownYear = first.getUTCFullYear()
  const shownMonth = first.getUTCMonth()
  // getUTCDay() is Sunday-based; shift so Monday starts the row.
  const lead = (first.getUTCDay() + 6) % 7
  const weeks = []
  for (let week = 0; week < 6; week += 1) {
    const days = []
    for (let column = 0; column < 7; column += 1) {
      const date = new Date(Date.UTC(shownYear, shownMonth, 1 + week * 7 + column - lead, 12))
      days.push({
        iso: toIsoDay(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()),
        day: date.getUTCDate(),
        inMonth: date.getUTCFullYear() === shownYear && date.getUTCMonth() === shownMonth,
      })
    }
    weeks.push(days)
  }
  return weeks
}

/** Start of week (Monday) in IST for an ISO date. */
export function startOfWeekIso(iso = todayIso()) {
  const date = noonUtc(iso)
  if (!date) return ''
  const day = date.getUTCDay()
  return addDaysIso(iso, day === 0 ? -6 : 1 - day)
}

/** End of week (Sunday) in IST for an ISO date. */
export function endOfWeekIso(iso = todayIso()) {
  return addDaysIso(startOfWeekIso(iso), 6)
}

/** Start of month (1st) for an ISO date. */
export function startOfMonthIso(iso = todayIso()) {
  const match = ISO_DAY.exec(String(iso || '').trim())
  if (!match) return ''
  return `${match[1]}-${match[2]}-01`
}

/** End of month for an ISO date. */
export function endOfMonthIso(iso = todayIso()) {
  const match = ISO_DAY.exec(String(iso || '').trim())
  if (!match) return ''
  const year = Number(match[1])
  const monthIndex = Number(match[2]) - 1
  return toIsoDay(year, monthIndex, daysInMonth(year, monthIndex))
}

/** Start of year (1 Jan) for an ISO date. */
export function startOfYearIso(iso = todayIso()) {
  const match = ISO_DAY.exec(String(iso || '').trim())
  if (!match) return ''
  return `${match[1]}-01-01`
}

/** End of year (31 Dec) for an ISO date. */
export function endOfYearIso(iso = todayIso()) {
  const match = ISO_DAY.exec(String(iso || '').trim())
  if (!match) return ''
  return `${match[1]}-12-31`
}

export const CALENDAR_PRESETS = [
  { id: 'thisWeek', label: 'This Week' },
  { id: 'thisMonth', label: 'This Month' },
  { id: 'thisYear', label: 'This Year' },
]

export function resolveCalendarPreset(presetId, today = todayIso()) {
  switch (presetId) {
    case 'thisWeek':
      return {
        id: 'thisWeek',
        label: 'This Week',
        from: startOfWeekIso(today),
        to: endOfWeekIso(today),
      }
    case 'thisMonth':
      return {
        id: 'thisMonth',
        label: 'This Month',
        from: startOfMonthIso(today),
        to: endOfMonthIso(today),
      }
    case 'thisYear':
      return {
        id: 'thisYear',
        label: 'This Year',
        from: startOfYearIso(today),
        to: endOfYearIso(today),
      }
    default:
      return null
  }
}
