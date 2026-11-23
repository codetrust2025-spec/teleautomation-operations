/**
 * Profile + Round (owner's mockup, 15 Nov).
 *
 * - Profile-wise and Round-wise are independent checkboxes for the booking
 *   type (profile service / round-wise service). Both ticked: every booking.
 *   One ticked: that type only, sent as `channel`. The last one stays ticked.
 * - Interview Round: Screening, L1, L2, L3, HR, Final, several at once.
 * - No technology list any more.
 * - Every click applies at once to the table and the counters; booking type
 *   and rounds must both match. Clear All and the page Reset put both back.
 */
import React from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'
import { ALL_BOOKING_TYPES, ProfileRoundFilter, channelFor } from './ProfileRoundFilter.jsx'
import { applyFilters, openFilters, toggleFilterValue } from './filterMenuTestHelpers.js'

const TODAY = '2026-10-06'
let calls

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

const ROWS = [
  ['Asha Varma', 'HR', 'profile_service', 'Bhavana'], ['Bala Kumar', 'L2', 'round_wise', 'Nikhila'],
  ['Chitra Rao', 'L2', 'profile_service', 'Bhavana'], ['Dev Prasad', 'L1', 'round_wise', 'Tool'],
  ['Esha Pillai', 'L1', 'profile_service', 'Tool'],
].map(([name, round, service_type, attendee], i) => ({
  id: `r${i}`, name, phone: `90000004${String(i).padStart(2, '0')}`, date: TODAY, time: `${10 + i}:00`,
  time_end: `${10 + i}:30`, technology: 'Java', interview_round: round, service_type,
  interview_attendee: attendee, interview_attendance_status: '',
}))

/** Stands in for the server: channel picks a booking type, rounds OR, fields AND. */
function serverRows(url) {
  const channel = url.searchParams.get('channel')
  const rounds = url.searchParams.getAll('round')
  const attendees = url.searchParams.getAll('attendee')
  return ROWS
    .filter(r => !channel || (channel === 'round_wise' ? r.service_type === 'round_wise' : r.service_type !== 'round_wise'))
    .filter(r => !rounds.length || rounds.includes(r.interview_round))
    .filter(r => !attendees.length || attendees.includes(r.interview_attendee))
}

function mockFetch() {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    const rows = serverRows(url)
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok',
        interviews: { by_candidate: ROWS.map(r => ({ name: r.name, scheduled: 1 })), by_technology: [{ name: 'Java' }] },
        booking_overview: { total: rows.length, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (/\/interviews\/(daily|monitor)$/.test(url.pathname)) {
      return jsonResponse({ status: 'ok', interviews: rows, count: rows.length, awaiting_interviews: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
}

const rosterCalls = () => calls.filter(u => /\/interviews\/(daily|monitor)$/.test(u.pathname))
const globalCalls = () => calls.filter(u => u.pathname.endsWith('/interviews/global'))
const shown = () => [...document.querySelectorAll('.ops-interview-table tbody tr')]
  .map(tr => ROWS.find(r => tr.textContent.includes(r.name))?.name).filter(Boolean)
const openMenu = () => fireEvent.click(screen.getByRole('button', { name: /^Profile and round/ }))
const box = (group, value) => within(screen.getByRole('group', { name: group })).getByRole('checkbox', { name: value })
const tick = (group, value) => fireEvent.click(box(group, value))
const ticked = group => within(screen.getByRole('group', { name: group })).getAllByRole('checkbox')
  .filter(b => b.checked).map(b => b.getAttribute('aria-label'))
const scheduled = () => screen.getByRole('button', { name: /^Scheduled/ }).textContent.replace(/\D+/g, '')
const lastRoster = () => rosterCalls().at(-1).searchParams
const lastGlobal = () => globalCalls().at(-1).searchParams

async function renderPanel() {
  const view = render(<DailyOpsPanel />)
  await waitFor(() => expect(shown()).toContain('Asha Varma'))
  await act(async () => { await Promise.resolve() })
  return view
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  mockFetch()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the Profile + Round control', () => {
  it('matches the mockup: two booking-type boxes, six rounds then Others, no technology list, no Apply', () => {
    render(<ProfileRoundFilter />)
    openMenu()
    expect(within(screen.getByRole('group', { name: 'Booking type' })).getAllByRole('checkbox').map(b => b.getAttribute('aria-label')))
      .toEqual(['Profile-wise', 'Round-wise'])
    expect(within(screen.getByRole('group', { name: 'Interview rounds' })).getAllByRole('checkbox').map(b => b.getAttribute('aria-label')))
      .toEqual(['Screening', 'L1', 'L2', 'L3', 'HR', 'Final', 'Others'])
    expect(screen.queryByRole('group', { name: 'Profiles' })).toBeNull()
    expect(screen.queryByRole('radio')).toBeNull()
    expect(screen.queryByRole('button', { name: /^Apply/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'Clear all profile and round filters' })).toHaveTextContent('Clear All')
    expect(screen.getByRole('button', { name: 'All rounds' })).toHaveTextContent('All Rounds')
    expect(screen.getByText('Select at least one filter (Profile-wise or Round-wise).')).toBeInTheDocument()
    // Both booking types start ticked: nothing is narrowed.
    expect(ticked('Booking type')).toEqual(['Profile-wise', 'Round-wise'])
    expect(screen.getByRole('button', { name: 'Profile and round' })).toBeInTheDocument()
  })

  it('ticks the booking types independently, but the last one stays ticked', () => {
    const onChange = vi.fn()
    const { rerender } = render(<ProfileRoundFilter onChange={onChange} />)
    openMenu()
    tick('Booking type', 'Round-wise')
    expect(onChange).toHaveBeenLastCalledWith({ bookingTypes: ['profile'], round: [] })
    rerender(<ProfileRoundFilter bookingTypes={['profile']} onChange={onChange} />)
    onChange.mockClear()
    tick('Booking type', 'Profile-wise')
    expect(onChange).not.toHaveBeenCalled()
    expect(box('Booking type', 'Profile-wise')).toHaveAttribute('aria-disabled', 'true')
    tick('Booking type', 'Round-wise')
    expect(onChange).toHaveBeenLastCalledWith({ bookingTypes: ['profile', 'round_wise'], round: [] })
  })

  it('takes several rounds, in interview order, and All Rounds unticks them', () => {
    const onChange = vi.fn()
    const { rerender } = render(<ProfileRoundFilter onChange={onChange} />)
    openMenu()
    tick('Interview rounds', 'L2')
    expect(onChange).toHaveBeenLastCalledWith({ bookingTypes: ALL_BOOKING_TYPES, round: ['L2'] })
    rerender(<ProfileRoundFilter rounds={['L2']} onChange={onChange} />)
    tick('Interview rounds', 'L1')
    expect(onChange).toHaveBeenLastCalledWith({ bookingTypes: ALL_BOOKING_TYPES, round: ['L1', 'L2'] })
    rerender(<ProfileRoundFilter rounds={['L1', 'L2']} bookingTypes={['round_wise']} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'All rounds' }))
    expect(onChange).toHaveBeenLastCalledWith({ bookingTypes: ['round_wise'], round: [] })
  })

  it('counts and names what narrows on the button, and Clear All puts both types back', () => {
    const onChange = vi.fn()
    render(<ProfileRoundFilter bookingTypes={['profile']} rounds={['L1', 'L2']} onChange={onChange} />)
    const trigger = screen.getByRole('button', { name: 'Profile and round (3 selected)' })
    expect(trigger.getAttribute('title')).toBe('Bookings: Profile-wise\nRounds: L1, L2')
    openMenu()
    fireEvent.click(screen.getByRole('button', { name: 'Clear all profile and round filters' }))
    expect(onChange).toHaveBeenLastCalledWith({ bookingTypes: ['profile', 'round_wise'], round: [] })
  })

  it('sends one booking type as the channel, and none for both', () => {
    expect(channelFor(['profile'])).toBe('profile')
    expect(channelFor(['round_wise'])).toBe('round_wise')
    expect(channelFor(['profile', 'round_wise'])).toBe('')
    expect(channelFor([])).toBe('')
  })

  it('closes on Escape and on a click outside', () => {
    render(<div><span data-testid="out">x</span><ProfileRoundFilter /></div>)
    openMenu()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('group', { name: 'Booking type' })).toBeNull()
    openMenu()
    fireEvent.mouseDown(screen.getByTestId('out'))
    expect(screen.queryByRole('group', { name: 'Booking type' })).toBeNull()
  })
})

describe('Others (owner, 15 Nov)', () => {
  it('says what it covers: no round, Assessment, anything off the six', () => {
    render(<ProfileRoundFilter />)
    openMenu()
    const others = box('Interview rounds', 'Others').closest('label')
    expect(others.getAttribute('title')).toMatch(/Needs Review \(no round\), Assessment, and any round other than Screening, L1, L2, L3, HR or Final/)
  })

  it('emits the server token, alone or with rounds, in menu order', () => {
    const onChange = vi.fn()
    const { rerender } = render(<ProfileRoundFilter rounds={[]} onChange={onChange} />)
    openMenu()
    tick('Interview rounds', 'Others')
    expect(onChange).toHaveBeenLastCalledWith({ bookingTypes: ALL_BOOKING_TYPES, round: ['other'] })
    rerender(<ProfileRoundFilter rounds={['other']} onChange={onChange} />)
    tick('Interview rounds', 'L2')
    expect(onChange).toHaveBeenLastCalledWith({ bookingTypes: ALL_BOOKING_TYPES, round: ['L2', 'other'] })
    rerender(<ProfileRoundFilter rounds={['L2', 'other']} onChange={onChange} />)
    expect(ticked('Interview rounds')).toEqual(['L2', 'Others'])
    expect(screen.getByRole('button', { name: 'Profile and round (2 selected)' }).getAttribute('title')).toContain('Rounds: L2, Others')
  })

  it('goes to the server at once on Daily Ops, and names itself on an empty table', async () => {
    await renderPanel()
    openMenu()
    tick('Interview rounds', 'Others')
    await waitFor(() => expect(lastRoster().getAll('round')).toEqual(['other']))
    await waitFor(() => expect(lastGlobal().getAll('round')).toEqual(['other']))
    // The stand-in server knows only the six, so nothing comes back.
    await waitFor(() => expect(screen.getByText(/round Others/)).toBeInTheDocument())
    tick('Interview rounds', 'HR')
    await waitFor(() => expect(lastRoster().getAll('round')).toEqual(['HR', 'other']))
    await waitFor(() => expect(shown()).toEqual(['Asha Varma']))
  })
})

describe('on Daily Ops', () => {
  it('applies every click to the table and the counters at once', async () => {
    await renderPanel()
    await waitFor(() => expect(scheduled()).toBe('5'))
    openMenu()

    // Profile-wise only.
    tick('Booking type', 'Round-wise')
    await waitFor(() => expect(lastRoster().get('channel')).toBe('profile'))
    await waitFor(() => expect(lastGlobal().get('channel')).toBe('profile'))
    await waitFor(() => expect(shown()).toEqual(['Asha Varma', 'Chitra Rao', 'Esha Pillai']))
    await waitFor(() => expect(scheduled()).toBe('3'))

    // + rounds L1 or L2: profile bookings in L1/L2.
    tick('Interview rounds', 'L1')
    tick('Interview rounds', 'L2')
    await waitFor(() => expect(lastRoster().getAll('round')).toEqual(['L1', 'L2']))
    await waitFor(() => expect(shown()).toEqual(['Chitra Rao', 'Esha Pillai']))
    await waitFor(() => expect(scheduled()).toBe('2'))

    // Round-wise only (tick it, then untick Profile-wise).
    tick('Booking type', 'Round-wise')
    await waitFor(() => expect(lastRoster().get('channel')).toBeNull())
    await waitFor(() => expect(scheduled()).toBe('4'))
    tick('Booking type', 'Profile-wise')
    await waitFor(() => expect(lastRoster().get('channel')).toBe('round_wise'))
    await waitFor(() => expect(shown()).toEqual(['Bala Kumar', 'Dev Prasad']))
    await waitFor(() => expect(scheduled()).toBe('2'))

    // Clear All: everything again.
    fireEvent.click(screen.getByRole('button', { name: 'Clear all profile and round filters' }))
    await waitFor(() => {
      expect(lastRoster().get('channel')).toBeNull()
      expect(lastRoster().getAll('round')).toEqual([])
    })
    await waitFor(() => expect(scheduled()).toBe('5'))
  })

  it('never sends a technology filter', async () => {
    await renderPanel()
    openMenu()
    tick('Interview rounds', 'HR')
    await waitFor(() => expect(lastRoster().getAll('round')).toEqual(['HR']))
    expect(calls.every(u => !u.searchParams.has('technology'))).toBe(true)
  })

  it('combines with the Filters panel, and keeps its ticks through a panel Apply or Reset', async () => {
    await renderPanel()
    openMenu()
    tick('Booking type', 'Round-wise')
    tick('Interview rounds', 'L1')
    fireEvent.keyDown(document, { key: 'Escape' })

    openFilters()
    toggleFilterValue('Attendee filter', 'Tool')
    applyFilters()
    await waitFor(() => {
      const last = lastRoster()
      expect(last.getAll('attendee')).toEqual(['Tool'])
      expect(last.get('channel')).toBe('profile')
      expect(last.getAll('round')).toEqual(['L1'])
    })
    await waitFor(() => expect(shown()).toEqual(['Esha Pillai']))

    openFilters()
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }))
    await waitFor(() => expect(lastRoster().getAll('attendee')).toEqual([]))
    expect(lastRoster().get('channel')).toBe('profile')
    expect(screen.getByRole('button', { name: 'Profile and round (2 selected)' })).toBeInTheDocument()
  })

  it('is put back by the page Reset, which counts it', async () => {
    await renderPanel()
    openMenu()
    tick('Booking type', 'Profile-wise')
    tick('Interview rounds', 'Final')
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reset all filters (2 set)' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /^Reset all filters/ }))
    await waitFor(() => {
      expect(lastRoster().get('channel')).toBeNull()
      expect(lastRoster().getAll('round')).toEqual([])
    })
    openMenu()
    expect(ticked('Booking type')).toEqual(['Profile-wise', 'Round-wise'])
  })

  it('sits right after the date, then Status, then Filters, and Today stays beside Filters', async () => {
    await renderPanel()
    const order = [...document.querySelector('.ops-roster-controls__filters').children]
      .map(el => el.classList.contains('ops-profile-round-filter') ? 'profile-round'
        : el.classList.contains('ops-feedback-filter') ? 'status' : el.className.split(' ')[0])
    const at = name => order.indexOf(name)
    expect(at('profile-round')).toBe(at('ops-roster-control') + 1)
    expect(at('status')).toBe(at('profile-round') + 1)
    expect(at('ops-filter-dropdown-wrap')).toBe(at('status') + 1)
    expect(at('ops-roster-today')).toBe(at('ops-filter-dropdown-wrap') + 1)
  })
})
