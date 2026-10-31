/**
 * The one earliest-first rule Daily Ops and Confirmed slots share (29 Oct).
 * Names are invented.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  compareInterviewStart, interviewClockMinutes, interviewDayIso, interviewStartMs, sortInterviewsEarliestFirst,
} from './interviewChronology.js'

const slot = (id, date, time, name = `${id} Fakename`) => ({ id, name, date, time })
const ids = rows => rows.map(row => row.id).join(' ')

describe('reading a slot', () => {
  it.each([
    ['15:00', 900], ['9:05', 545], ['3:00 PM', 900], ['12:15 am', 15], ['12:00 PM', 720], ['11:30am', 690],
    ['3 pm', 900], ['3.00 p.m.', 900], ['09:30:00', 570],
    ['', null], ['15', null], ['25:00', null], ['13:00 pm', null], ['soon', null],
  ])('reads %s as %s minutes', (value, minutes) => {
    expect(interviewClockMinutes(value)).toBe(minutes)
  })

  it('pads a day and refuses one that is not on the calendar', () => {
    expect(interviewDayIso('2026-9-5')).toBe('2026-09-05')
    expect(interviewDayIso('2026-10-29T00:00:00')).toBe('2026-10-29')
    expect(interviewDayIso('2026-02-30')).toBe('')
    expect(interviewDayIso('29/10/2026')).toBe('')
  })

  it('builds the start as an IST instant, so just after midnight IST is the previous UTC evening', () => {
    expect(interviewStartMs(slot('a', '2026-10-29', '00:30'))).toBe(Date.UTC(2026, 9, 28, 19, 0))
    expect(interviewStartMs(slot('b', '2026-10-29', '3:00 PM'))).toBe(Date.UTC(2026, 9, 29, 9, 30))
    expect(interviewStartMs(slot('c', '2026-10-29', 'soon'))).toBeNull()
    expect(interviewStartMs(slot('d', '', '10:00'))).toBeNull()
  })
})

describe('earliest first', () => {
  it('orders by the real start: year, day, then the clock whichever way it was written', () => {
    const served = [
      slot('jan27', '2027-01-04', '09:00'),
      slot('pm', '2026-10-30', '2:30 PM'),
      slot('dec', '2026-12-10', '10:00'),
      slot('am', '2026-10-30', '11:00'),
      slot('night', '2026-10-29', '23:45'),
      slot('3pm', '2026-10-30', '3 pm'),
    ]
    expect(ids(sortInterviewsEarliestFirst(served))).toBe('night am pm 3pm dec jan27')
    expect(ids(served)).toBe('jan27 pm dec am night 3pm')
  })

  it('puts an undated slot last, and on a day a slot with no readable start after the timed ones, in both directions', () => {
    const rows = [slot('x', '', '08:00'), slot('t', '2026-10-30', 'soon'), slot('a', '2026-10-30', '09:00'), slot('b', '2026-10-31', '09:00')]
    expect(ids(sortInterviewsEarliestFirst(rows))).toBe('a t b x')
    expect(ids([...rows].sort((p, q) => compareInterviewStart(p, q, -1)))).toBe('b a t x')
  })

  it('breaks one start by name, as the server does', () => {
    const rows = [slot('z', '2026-10-30', '10:00', 'zara Fakename'), slot('m', '2026-10-30', '10:00', 'Mani Fakename')]
    expect(ids(sortInterviewsEarliestFirst(rows))).toBe('m z')
  })
})

describe('one rule, both pages', () => {
  const root = dirname(fileURLToPath(import.meta.url))
  it('Daily Ops Sort By and Confirmed slots both import it rather than keeping their own copy', () => {
    for (const file of ['../dailyOps/rosterSort.js', '../pages/SubmitSlotPage.jsx']) {
      const source = readFileSync(join(root, file), 'utf8')
      expect(source, file).toMatch(/from '\.\.\/utils\/interviewChronology\.js'/)
    }
  })
})
