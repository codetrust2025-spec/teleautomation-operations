/**
 * Day arithmetic for the Daily Ops calendar.
 *
 * Every assertion here is about the same failure: a calendar day that quietly
 * becomes the day before or after because a UTC instant was sliced, or a
 * local-time Date was read back through `toISOString()`. The picker names a
 * day, and the roster has to be filtered by that same day.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  addDaysIso,
  addMonthsIso,
  daysInMonth,
  endOfMonthIso,
  endOfWeekIso,
  endOfYearIso,
  isValidIsoDay,
  monthGrid,
  monthKeyOf,
  monthRangeIso,
  parseMonthKey,
  resolveCalendarPreset,
  startOfMonthIso,
  startOfWeekIso,
  startOfYearIso,
  todayIso,
  toIsoDay,
} from './calendarDates.js'

afterEach(() => {
  vi.useRealTimers()
})

/** Freeze the wall clock at a real instant. */
function at(utcIso) {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(utcIso))
}

describe('todayIso — the IST day, not the UTC day', () => {
  it('is the IST date in the hours when UTC is still on yesterday', () => {
    // 01:30 IST on 9 September is 20:00 UTC on the 8th. Slicing the UTC
    // instant names the 8th; an operator looking at the clock sees the 9th.
    at('2026-09-08T20:00:00Z')
    expect(new Date().toISOString().slice(0, 10)).toBe('2026-09-08')
    expect(todayIso()).toBe('2026-09-09')
  })

  it('agrees with UTC during the working day', () => {
    at('2026-09-08T06:00:00Z')
    expect(todayIso()).toBe('2026-09-08')
  })

  it('does not run ahead just before IST midnight', () => {
    at('2026-09-08T18:29:00Z')
    expect(todayIso()).toBe('2026-09-08')
  })
})

describe('addDaysIso', () => {
  it('steps back to yesterday — the case the picker exists for', () => {
    expect(addDaysIso('2026-09-08', -1)).toBe('2026-09-07')
  })

  it('crosses month and year boundaries', () => {
    expect(addDaysIso('2026-09-01', -1)).toBe('2026-08-31')
    expect(addDaysIso('2026-01-01', -1)).toBe('2025-12-31')
    expect(addDaysIso('2026-12-31', 1)).toBe('2027-01-01')
  })

  it('handles the leap day', () => {
    expect(addDaysIso('2028-02-28', 1)).toBe('2028-02-29')
    expect(addDaysIso('2026-02-28', 1)).toBe('2026-03-01')
  })

  it('returns nothing for a day that never existed', () => {
    expect(addDaysIso('2026-02-30', 1)).toBe('')
    expect(addDaysIso('', 1)).toBe('')
  })
})

describe('addMonthsIso', () => {
  it('clamps to the shorter month instead of spilling into the next one', () => {
    expect(addMonthsIso('2026-01-31', 1)).toBe('2026-02-28')
    expect(addMonthsIso('2028-01-31', 1)).toBe('2028-02-29')
  })

  it('steps backwards across a year boundary', () => {
    expect(addMonthsIso('2026-01-15', -1)).toBe('2025-12-15')
  })
})

describe('isValidIsoDay', () => {
  it('accepts real days', () => {
    expect(isValidIsoDay('2026-09-07')).toBe(true)
    expect(isValidIsoDay('2028-02-29')).toBe(true)
  })

  it('rejects days no one can be interviewed on', () => {
    // The same shapes the month filter had to learn to refuse server-side.
    for (const bad of ['2026-02-30', '2027-13-40', '2026-1-4', '20260804', '', 'not-a-date', null]) {
      expect(isValidIsoDay(bad)).toBe(false)
    }
  })
})

describe('month helpers', () => {
  it('reads and writes YYYY-MM keys', () => {
    expect(monthKeyOf('2026-09-07')).toBe('2026-09')
    expect(parseMonthKey('2026-09')).toEqual({ year: 2026, monthIndex: 8 })
    expect(parseMonthKey('2026-13')).toBeNull()
    expect(parseMonthKey('rubbish')).toBeNull()
  })

  it('spans a whole month, first day to last', () => {
    expect(monthRangeIso(2026, 8)).toEqual({ from: '2026-09-01', to: '2026-09-30' })
    expect(monthRangeIso(2026, 1)).toEqual({ from: '2026-02-01', to: '2026-02-28' })
    expect(monthRangeIso(2028, 1)).toEqual({ from: '2028-02-01', to: '2028-02-29' })
  })

  it('counts the days in a month', () => {
    expect(daysInMonth(2026, 8)).toBe(30)
    expect(daysInMonth(2026, 0)).toBe(31)
  })

  it('builds a day from its parts', () => {
    expect(toIsoDay(2026, 8, 7)).toBe('2026-09-07')
  })
})

describe('monthGrid', () => {
  it('is six Monday-first weeks, so the popover keeps one height', () => {
    const weeks = monthGrid(2026, 8)
    expect(weeks).toHaveLength(6)
    for (const week of weeks) expect(week).toHaveLength(7)
    // 1 September 2026 is a Tuesday, so the grid opens on Monday 31 August.
    expect(weeks[0][0].iso).toBe('2026-08-31')
    expect(weeks[0][0].inMonth).toBe(false)
    expect(weeks[0][1]).toMatchObject({ iso: '2026-09-01', day: 1, inMonth: true })
  })

  it('marks only the shown month as in-month', () => {
    const days = monthGrid(2026, 8).flat()
    expect(days.filter(cell => cell.inMonth)).toHaveLength(30)
    expect(days.find(cell => cell.iso === '2026-10-01').inMonth).toBe(false)
  })

  it('rolls the year over when asked for the month after December', () => {
    const weeks = monthGrid(2026, 12)
    expect(weeks.flat().find(cell => cell.inMonth).iso.slice(0, 7)).toBe('2027-01')
  })
})

describe('calendar presets arithmetic in IST', () => {
  it('calculates This Week from Monday to Sunday', () => {
    // 2026-10-13 is Tuesday -> Mon 2026-10-12 to Sun 2026-10-18
    expect(startOfWeekIso('2026-10-13')).toBe('2026-10-12')
    expect(endOfWeekIso('2026-10-13')).toBe('2026-10-18')

    // Sunday 2026-10-18 belongs to the same week ending on Sunday
    expect(startOfWeekIso('2026-10-18')).toBe('2026-10-12')
    expect(endOfWeekIso('2026-10-18')).toBe('2026-10-18')

    // Monday 2026-10-12 starts the week
    expect(startOfWeekIso('2026-10-12')).toBe('2026-10-12')
    expect(endOfWeekIso('2026-10-12')).toBe('2026-10-18')
  })

  it('calculates This Month from 1st to last day of month', () => {
    expect(startOfMonthIso('2026-10-13')).toBe('2026-10-01')
    expect(endOfMonthIso('2026-10-13')).toBe('2026-10-31')

    expect(startOfMonthIso('2026-02-15')).toBe('2026-02-01')
    expect(endOfMonthIso('2026-02-15')).toBe('2026-02-28')

    // Leap year
    expect(endOfMonthIso('2028-02-15')).toBe('2028-02-29')
  })

  it('calculates This Year from 1 Jan to 31 Dec', () => {
    expect(startOfYearIso('2026-10-13')).toBe('2026-01-01')
    expect(endOfYearIso('2026-10-13')).toBe('2026-12-31')
  })

  it('resolves presets via resolveCalendarPreset', () => {
    const today = '2026-10-13'
    const upcoming = resolveCalendarPreset('upcoming', today)
    expect(upcoming).toEqual({
      id: 'upcoming',
      label: 'Upcoming',
      from: '2026-10-13',
      to: '2026-11-12',
    })

    const todayPreset = resolveCalendarPreset('today', today)
    expect(todayPreset).toEqual({
      id: 'today',
      label: 'Today',
      from: '2026-10-13',
      to: '2026-10-13',
    })

    const week = resolveCalendarPreset('thisWeek', today)
    expect(week).toEqual({
      id: 'thisWeek',
      label: 'This Week',
      from: '2026-10-12',
      to: '2026-10-18',
    })

    const month = resolveCalendarPreset('thisMonth', today)
    expect(month).toEqual({
      id: 'thisMonth',
      label: 'This Month',
      from: '2026-10-01',
      to: '2026-10-31',
    })

    const year = resolveCalendarPreset('thisYear', today)
    expect(year).toEqual({
      id: 'thisYear',
      label: 'This Year',
      from: '2026-01-01',
      to: '2026-12-31',
    })

    expect(resolveCalendarPreset('unknown', today)).toBeNull()
  })
})
