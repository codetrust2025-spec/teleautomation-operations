/**
 * Bookings Analytics, driven through the page itself against a stand-in for
 * the two endpoints it reads.
 *
 * The stand-in is not a list of canned numbers. It holds interview rows and
 * answers `/candidates/interviews/global` and `/candidates/interviews/monitor`
 * the way the server does -- range, then technology and level, then the
 * candidate selection, with `interviews.by_candidate` taken before the
 * candidate narrows the rows -- so every figure the page shows has to have
 * come through the API. Change the rows and the page has to follow; nothing
 * on it may be a total of its own.
 *
 * Names are invented.
 */
import React from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BookingsAnalyticsPanel } from './BookingsAnalyticsPanel.jsx'
import { buildReport } from './analyticsData.js'
import { captureLayout } from '../test/captureLayout.js'

const STATUS_KEYS = ['attended', 'not_attended', 'technical_issue', 'cancelled', 'rescheduled', 'released_for_reschedule', 're_service']

// 13 candidates, so the Top 10 leaves three behind and the donut folds them.
const PEOPLE = [
  ['Asha Fakename', 9, 'React JS'], ['Ravi Testcase', 8, 'ServiceNow'], ['Meena Sample', 7, 'React JS'],
  ['Kiran Mockdata', 6, 'Power BI'], ['Divya Example', 5, 'Python'], ['Arjun Placeholder', 5, 'ServiceNow'],
  ['Lata Dummyrow', 4, 'Java Full Stack'], ['Gopal Fixture', 4, 'Salesforce'], ['Hema Stub', 3, 'React JS'],
  ['Imran Fauxname', 3, 'Azure DevOps'], ['Jaya Notreal', 2, 'Power BI'], ['Kavya Madeup', 2, 'Python'],
  ['Zubin Lastrow', 1, 'ETL'],
]
const LEVELS = ['L1', 'L1', 'L2', 'Screening', '', 'Final', 'HR']
// Meena's profile is Closed / Completed (★ Selected); Ravi's round-wise work is
// completed too, which stays Closed / Completed.
const PLACED = 'Meena Sample'
const ROUND_DONE = 'Ravi Testcase'
const OUTCOMES = ['attended', 'attended', 'attended', 'cancelled', '', 'rescheduled', 'not_attended']

function buildRows(people = PEOPLE) {
  const rows = []
  let n = 0
  for (const [name, count, technology] of people) {
    for (let i = 0; i < count; i += 1) {
      n += 1
      const month = String((n % 6) + 4).padStart(2, '0')
      rows.push({
        id: `r${n}`,
        name,
        technology,
        technology_resolved: technology,
        stage: name === PLACED || name === ROUND_DONE ? 'completed' : 'in_progress',
        service_type: name === ROUND_DONE ? 'round_wise' : 'profile_service',
        candidate_selected: name === PLACED,
        interview_round: LEVELS[n % LEVELS.length],
        interview_attendance_status: OUTCOMES[n % OUTCOMES.length],
        interview_attendance_status_resolved: OUTCOMES[n % OUTCOMES.length],
        interview_attendee: n % 2 ? 'Bhavana' : 'Nikhila',
        interview_company: `Company ${n % 5}`,
        date: `2026-${month}-${String((n % 27) + 1).padStart(2, '0')}`,
        time: '15:00',
        time_end: '16:00',
      })
    }
  }
  return rows
}

let ROWS
let requests

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

function bucketList(map, keyName = 'count') {
  return [...map.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([name, count]) => ({ name, [keyName]: count }))
}

function select(url) {
  const p = url.searchParams
  let rows = ROWS.filter(row => row.date >= p.get('from') && row.date <= p.get('to'))
  if (p.get('technology')) rows = rows.filter(row => row.technology === p.get('technology'))
  if (p.get('round')) rows = rows.filter(row => (row.interview_round || 'Unspecified') === p.get('round'))
  const optionRows = rows
  if (p.get('candidate')) rows = rows.filter(row => row.name === p.get('candidate'))
  return { rows, optionRows }
}

function summary(url) {
  const { rows, optionRows } = select(url)
  // As the server reads it: the candidate's outcome, over every stored row.
  const selected = new Set(ROWS.filter(row => row.candidate_selected).map(row => row.name))
  const counts = new Map()
  const tally = (key, map) => map.set(key, (map.get(key) || 0) + 1)
  const byCandidate = new Map()
  for (const row of optionRows) {
    const entry = byCandidate.get(row.name) || { name: row.name, scheduled: 0, pending: 0, ...Object.fromEntries(STATUS_KEYS.map(k => [k, 0])) }
    entry.scheduled += 1
    entry[row.interview_attendance_status || 'pending'] += 1
    byCandidate.set(row.name, entry)
  }
  const levels = new Map(); const techs = new Map(); const people = new Map()
  for (const row of rows) {
    tally(row.interview_attendance_status || 'pending', counts)
    tally(row.interview_round || 'Unspecified', levels)
    tally(row.technology, techs)
    tally(row.name, people)
  }
  return {
    status: 'ok',
    booking_overview: { total: rows.length, by_candidate: bucketList(people).map(entry => ({ ...entry, selected: selected.has(entry.name) })), by_level: bucketList(levels), by_technology: bucketList(techs) },
    interviews: {
      count: rows.length,
      pending_count: counts.get('pending') || 0,
      ...Object.fromEntries(STATUS_KEYS.map(k => [`${k}_count`, counts.get(k) || 0])),
      by_candidate: [...byCandidate.values()].map(entry => ({ ...entry, selected: selected.has(entry.name) })).sort((a, b) => b.scheduled - a.scheduled || a.name.localeCompare(b.name)),
    },
    available_months: [],
  }
}

beforeEach(() => {
  ROWS = buildRows()
  requests = []
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    requests.push(url)
    if (url.pathname.endsWith('/candidates/interviews/global')) return jsonResponse(summary(url))
    if (url.pathname.endsWith('/candidates/interviews/monitor')) {
      const { rows } = select(url)
      return jsonResponse({ status: 'ok', interviews: rows, count: rows.length })
    }
    return jsonResponse({ status: 'ok' })
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const counters = () => screen.getByRole('group', { name: 'Booking counters' })
const kpi = label => within(counters()).getAllByRole('button').find(b => b.querySelector('.ba-kpi__label')?.textContent === label)
const kpiValue = label => kpi(label).querySelector('.ba-kpi__value').textContent
const topTable = () => screen.getByRole('table', { name: 'Top 10 candidates by bookings' })
const bodyRows = table => within(table).getAllByRole('row').slice(1)
const globalReads = () => requests.filter(url => url.pathname.endsWith('/interviews/global'))

async function openPage() {
  render(<BookingsAnalyticsPanel />)
  await waitFor(() => expect(kpiValue('Total bookings')).not.toBe('…'))
}

describe('Bookings Analytics reads every figure from the API', () => {
  it('opens on all time, so every stored booking is counted', async () => {
    await openPage()
    const first = globalReads()[0]
    expect(first.searchParams.get('from')).toBe('2000-01-01')
    expect(first.searchParams.get('to')).toBe('2100-12-31')
    expect(kpiValue('Total bookings')).toBe(String(ROWS.length))
    const attended = ROWS.filter(row => row.interview_attendance_status === 'attended').length
    expect(kpiValue('Attended')).toBe(String(attended))
    expect(kpi('Attended').textContent).toContain(`${((attended / ROWS.length) * 100).toFixed(1)}% of total`)
    expect(screen.getByRole('img', { name: `Bookings by candidate, ${ROWS.length} in total` })).toBeTruthy()
    captureLayout('bookings-analytics-desktop', document.querySelector('.ba-page'))
  })

  it('follows the data rather than any number of its own: 243 bookings read as 243', async () => {
    // The live store held 243 interview bookings when this page shipped.
    const people = [...PEOPLE.map(([name, count, tech]) => [name, count * 2, tech])]
    const sum = people.reduce((total, [, count]) => total + count, 0)
    people.push(['Extra Bookingsrow', 243 - sum, 'React JS'])
    ROWS = buildRows(people)
    expect(ROWS).toHaveLength(243)
    await openPage()
    expect(kpiValue('Total bookings')).toBe('243')
    const legend = within(screen.getByRole('list', { name: 'Candidates in the chart' }))
    const shown = legend.getAllByRole('button').map(b => Number(b.querySelector('strong').textContent))
    expect(shown.reduce((a, b) => a + b, 0)).toBe(243)
  })
})

describe('Top 10, search and View all', () => {
  it('lists the ten with the most bookings, and says how many more there are', async () => {
    await openPage()
    const rows = bodyRows(topTable())
    expect(rows).toHaveLength(10)
    // Most bookings first; a tie reads alphabetically.
    const expected = [...PEOPLE].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 10).map(([name]) => name)
    expect(rows.map(row => row.querySelector('.ba-table__name').textContent)).toEqual(expected)
    expect(screen.getByText(`Showing 10 of ${PEOPLE.length} candidates`)).toBeTruthy()
    // The donut folds the rest into one slice that opens the full list.
    const others = within(screen.getByRole('list', { name: 'Candidates in the chart' })).getByRole('button', { name: /Others \(3\)/ })
    expect(others.querySelector('strong').textContent).toBe('5')
  })

  it('searches every candidate, not only the ten on screen', async () => {
    await openPage()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search candidates' }), { target: { value: 'lastrow' } })
    const rows = bodyRows(topTable())
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toContain('Zubin Lastrow')
    expect(rows[0].querySelector('.ba-rank').textContent).toBe('13')
  })

  it('View all opens every candidate in a view of its own, with its own search', async () => {
    await openPage()
    fireEvent.click(screen.getByRole('button', { name: /View all candidates/ }))
    const view = screen.getByRole('dialog', { name: 'All candidates' })
    const table = within(view).getByRole('table', { name: 'All candidates by bookings' })
    expect(bodyRows(table)).toHaveLength(PEOPLE.length)
    fireEvent.change(within(view).getByRole('searchbox', { name: 'Search all candidates' }), { target: { value: 'kavya' } })
    expect(bodyRows(table)).toHaveLength(1)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('choosing a candidate, a level or a technology narrows the page', () => {
  it('a candidate: the counters follow, the list keeps everyone to switch to', async () => {
    await openPage()
    const [name, count] = PEOPLE[1]
    fireEvent.click(within(topTable()).getByRole('button', { name: name }))
    await waitFor(() => expect(kpiValue('Total bookings')).toBe(String(count)))
    expect(globalReads().at(-1).searchParams.get('candidate')).toBe(name)
    expect(bodyRows(topTable())).toHaveLength(10)
    expect(within(topTable()).getByRole('button', { name }).getAttribute('aria-pressed')).toBe('true')
    // The technology card describes that one person now.
    const techs = within(screen.getByRole('list', { name: 'Technologies' })).getAllByRole('button')
    expect(techs.map(b => b.querySelector('.ba-bar__name').textContent)).toEqual([PEOPLE[1][2]])
    // Choosing them again shows everyone.
    fireEvent.click(within(topTable()).getByRole('button', { name }))
    await waitFor(() => expect(kpiValue('Total bookings')).toBe(String(ROWS.length)))
  })

  it('a technology: the others stay listed, dimmed, to switch to', async () => {
    await openPage()
    const techList = () => within(screen.getByRole('list', { name: 'Technologies' }))
    fireEvent.click(techList().getByRole('button', { name: /^React JS/ }))
    const react = ROWS.filter(row => row.technology === 'React JS').length
    await waitFor(() => expect(kpiValue('Total bookings')).toBe(String(react)))
    expect(globalReads().some(url => url.searchParams.get('technology') === 'React JS')).toBe(true)
    expect(techList().getAllByRole('button').length).toBeGreaterThan(1)
    expect(techList().getByRole('button', { name: /^React JS/ }).getAttribute('aria-pressed')).toBe('true')
    // Only React JS people are left in the candidate list.
    const names = bodyRows(topTable()).map(row => row.querySelector('.ba-table__name').textContent)
    expect(names.sort()).toEqual(PEOPLE.filter(([, , tech]) => tech === 'React JS').map(([name]) => name).sort())
    fireEvent.click(screen.getByRole('button', { name: /Clear filters/ }))
    await waitFor(() => expect(kpiValue('Total bookings')).toBe(String(ROWS.length)))
  })

  it('a level, including bookings with none recorded', async () => {
    await openPage()
    fireEvent.click(within(screen.getByRole('list', { name: 'Interview levels' })).getByRole('button', { name: /^Unspecified/ }))
    const none = ROWS.filter(row => !row.interview_round).length
    await waitFor(() => expect(kpiValue('Total bookings')).toBe(String(none)))
    expect(globalReads().some(url => url.searchParams.get('round') === 'Unspecified')).toBe(true)
    // The level list itself is read without the level, so the other levels stay listed.
    expect(within(screen.getByRole('list', { name: 'Interview levels' })).getAllByRole('button').length).toBeGreaterThan(1)
  })
})

describe('drilling into the interviews', () => {
  it('a counter opens the interviews it counts, from the roster read', async () => {
    await openPage()
    fireEvent.click(kpi('Cancelled'))
    const view = await screen.findByRole('dialog', { name: 'Interviews' })
    const cancelled = ROWS.filter(row => row.interview_attendance_status === 'cancelled')
    await waitFor(() => expect(bodyRows(within(view).getByRole('table', { name: 'Interviews' }))).toHaveLength(cancelled.length))
    expect(within(view).getByRole('tab', { name: /Cancelled/ }).getAttribute('aria-selected')).toBe('true')
    const monitor = requests.filter(url => url.pathname.endsWith('/interviews/monitor')).at(-1)
    expect(monitor.searchParams.get('from')).toBe('2000-01-01')
    // All of them, one tab away.
    fireEvent.click(within(view).getByRole('tab', { name: /^All/ }))
    expect(bodyRows(within(view).getByRole('table', { name: 'Interviews' }))).toHaveLength(ROWS.length)
    captureLayout('bookings-analytics-interviews', view)
  })

  it("a candidate's row opens that candidate's interviews", async () => {
    await openPage()
    const [name, count] = PEOPLE[2]
    fireEvent.click(within(topTable()).getByRole('button', { name: `${name}: view ${count} interviews` }))
    const view = await screen.findByRole('dialog', { name: `${name}: interviews` })
    await waitFor(() => expect(bodyRows(within(view).getByRole('table', { name: 'Interviews' }))).toHaveLength(count))
    expect(within(view).getAllByText(name)).toHaveLength(count)
  })
})

describe('the period', () => {
  it('a month compares with the month before it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date('2026-08-15T06:00:00Z'))
    await openPage()
    fireEvent.click(screen.getByRole('button', { name: 'Period: All time' }))
    fireEvent.click(screen.getByRole('button', { name: 'This month' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Period: 01 Aug 2026 - 31 Aug 2026' })).toBeTruthy())
    const august = ROWS.filter(row => row.date.startsWith('2026-08')).length
    await waitFor(() => expect(kpiValue('Total bookings')).toBe(String(august)))
    expect(globalReads().some(url => url.searchParams.get('from') === '2026-07-01' && url.searchParams.get('to') === '2026-07-31')).toBe(true)
    expect(kpi('Total bookings').querySelector('.ba-trend')?.getAttribute('title')).toBe('vs last month')
  })
})

describe('live data', () => {
  it('reads again every minute while visible, and not while hidden', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let hidden = false
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
    try {
      await openPage()
      const before = globalReads().length
      await act(async () => { await vi.advanceTimersByTimeAsync(60000) })
      expect(globalReads().length).toBeGreaterThan(before)
      hidden = true
      const whileHidden = globalReads().length
      await act(async () => { await vi.advanceTimersByTimeAsync(180000) })
      expect(globalReads().length).toBe(whileHidden)
      // A booking made meanwhile is on the page as soon as it is looked at.
      ROWS.push({ ...ROWS[0], id: 'new-one' })
      hidden = false
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
      await waitFor(() => expect(kpiValue('Total bookings')).toBe(String(ROWS.length)))
    } finally {
      delete document.hidden
    }
  })
})

describe('★ Selected', () => {
  const badgeIn = el => el.querySelector('.ta-selected-badge')

  it('marks a selected candidate in the Top 10 and the chart legend, and nobody else', async () => {
    await openPage()
    for (const row of bodyRows(topTable())) {
      const name = row.querySelector('.ba-table__name').textContent
      expect(Boolean(badgeIn(row)), name).toBe(name === PLACED)
    }
    const legend = within(screen.getByRole('list', { name: 'Candidates in the chart' }))
    const placed = legend.getAllByRole('button').find(b => b.textContent.includes(PLACED))
    expect(badgeIn(placed).textContent).toBe('★Selected')
    expect(legend.getAllByRole('button').filter(badgeIn)).toHaveLength(1)
  })

  it('counts them on the Total bookings card', async () => {
    await openPage()
    expect(kpi('Total bookings').querySelector('.ba-kpi__share').textContent).toBe(`${PEOPLE.length} candidates · ★ 1 selected`)
  })

  it('finds them by the word, in the Top 10 search and in All candidates', async () => {
    await openPage()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search candidates' }), { target: { value: 'selected' } })
    expect(bodyRows(topTable()).map(r => r.querySelector('.ba-table__name').textContent)).toEqual([PLACED])
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search candidates' }), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: /View all candidates/ }))
    const view = screen.getByRole('dialog', { name: 'All candidates' })
    const only = within(view).getByRole('button', { name: /Selected only/ })
    expect(only.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(only)
    const table = within(view).getByRole('table', { name: 'All candidates by bookings' })
    expect(bodyRows(table).map(r => r.querySelector('.ba-table__name').textContent)).toEqual([PLACED])
  })

  it('marks their interviews in the drill-down, and finds them by the word', async () => {
    await openPage()
    fireEvent.click(kpi('Total bookings'))
    const view = await screen.findByRole('dialog', { name: 'Interviews' })
    const table = () => within(view).getByRole('table', { name: 'Interviews' })
    await waitFor(() => expect(bodyRows(table())).toHaveLength(ROWS.length))
    const marked = bodyRows(table()).filter(badgeIn)
    expect(marked.length).toBe(ROWS.filter(r => r.name === PLACED).length)
    expect(marked.every(r => r.textContent.includes(PLACED))).toBe(true)
    fireEvent.change(within(view).getByRole('searchbox', { name: 'Search interviews' }), { target: { value: 'selected' } })
    expect(bodyRows(table()).every(r => r.textContent.includes(PLACED))).toBe(true)
  })

  it('names them in the exported report', () => {
    const csv = buildReport({
      range: { from: '2000-01-01', to: '2100-12-31' }, comparison: null, filters: {},
      summary: { interviews: { count: 3 } }, previous: null,
      candidates: [{ name: PLACED, count: 2, percent: 66.7, selected: true }, { name: ROUND_DONE, count: 1, percent: 33.3, selected: false }],
      levels: [], technologies: [],
    })
    const lines = csv.replace(/^\uFEFF/, '').split('\r\n')
    expect(lines).toContain('Selected candidates,1')
    expect(lines).toContain(`${PLACED},Selected,2,66.7,0,0,0,0,0,0,0,0`)
    expect(lines).toContain(`${ROUND_DONE},,1,33.3,0,0,0,0,0,0,0,0`)
    expect(lines.some(l => l.startsWith('Candidate,Status,Bookings,Share %,Pending,Attended'))).toBe(true)
  })
})
