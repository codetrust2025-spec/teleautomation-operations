/**
 * The booking page keeps a past invite date in the past (11 Oct audit).
 *
 * fixPastYear moved any date more than a week past into next year: a 16 Sep
 * 2026 invite uploaded on 11 Oct was booked for 16 Sep 2027. Only an old year
 * from the reader (2023 for 2026) is corrected now, and next year is used only
 * for the first weeks of January read in late December. It also returned the
 * day before in IST, through toISOString on a local midnight.
 */
import { describe, it, expect } from 'vitest'
import { fixPastYear } from './SubmitSlotPage.jsx'

const on = (iso) => new Date(`${iso}T10:00:00`)

describe('fixPastYear', () => {
  it.each([
    ['2026-09-16', '2026-10-11', '2026-09-16'],
    ['2026-06-19', '2026-10-09', '2026-06-19'],
    ['2023-09-16', '2026-10-11', '2026-09-16'],
    ['2023-10-20', '2026-10-11', '2026-10-20'],
    ['2026-10-08', '2026-10-11', '2026-10-08'],
    ['2026-10-20', '2026-10-11', '2026-10-20'],
    ['2026-01-05', '2026-12-28', '2027-01-05'],
    ['2026-03-15', '2026-12-28', '2026-03-15'],
    ['2024-02-29', '2026-10-11', '2024-02-29'],
    ['', '2026-10-11', ''],
  ])('%s read on %s -> %s', (read, today, expected) => {
    expect(fixPastYear(read, on(today))).toBe(expected)
  })
})
