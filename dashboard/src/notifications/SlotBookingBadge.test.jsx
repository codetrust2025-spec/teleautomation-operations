/**
 * The Slot Booking badge, counting confirmed upcoming slots.
 *
 * The number is whatever `/public/slots/booked` returns — the same request the
 * Slot Booking page makes, counted the same way it counts, as the length of the
 * list it renders under "Confirmed upcoming slots". Nothing is filtered or
 * recomputed here, so the badge and the page cannot disagree; these tests pin
 * that, because a second calculation is exactly how the two would drift.
 *
 * Staying current takes three signals. Reschedule and removal happen in the
 * roster, which already announces every change it makes on
 * `teleautomation:pending-work-changed`. Booking and cancelling happen on
 * /submit-slot, which opens in its own tab and cannot be observed from this
 * document — returning to the shell is the signal instead.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'

import {
  fetchConfirmedSlotCount,
  publishSlotBookingChanged,
  useConfirmedSlotCount,
} from './slotBooking.js'

const HERE = dirname(fileURLToPath(import.meta.url))

/** The five slots production is actually holding. */
const FIVE = [
  { date: '2026-09-09', time: '15:30', name: 'Aniket' },
  { date: '2026-09-09', time: '17:00', name: 'Aniket' },
  { date: '2026-09-09', time: '18:30', name: 'Aniket' },
  { date: '2026-09-10', time: '12:00', name: 'Pavan Ravi' },
  { date: '2026-09-10', time: '15:00', name: 'Aniket' },
]

function stubSlots(slots, { ok = true, status = 'ok' } = {}) {
  const spy = vi.fn(() => Promise.resolve({
    ok,
    json: () => Promise.resolve({ status, slots }),
  }))
  vi.stubGlobal('fetch', spy)
  return spy
}

function Probe() {
  const confirmed = useConfirmedSlotCount()
  return <span data-testid="count">{confirmed}</span>
}

beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }) })
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('the count', () => {
  it('is the five slots production is holding', async () => {
    stubSlots(FIVE)
    await expect(fetchConfirmedSlotCount()).resolves.toBe(5)
  })

  it('asks the endpoint the Slot Booking page asks', async () => {
    const spy = stubSlots(FIVE)
    await fetchConfirmedSlotCount()
    expect(String(spy.mock.calls[0][0])).toContain('/public/slots/booked')
  })

  it('does not read a cached body, the way the page does not', async () => {
    const spy = stubSlots(FIVE)
    await fetchConfirmedSlotCount()
    expect(spy.mock.calls[0][1]).toMatchObject({ cache: 'no-store' })
  })

  it('is the length of the list, with nothing filtered out', async () => {
    // Whatever the endpoint calls confirmed is what the page shows, so the
    // badge must not second-guess it with a filter of its own.
    stubSlots([...FIVE, { date: '2026-12-01', time: '09:00', name: 'Later' }])
    await expect(fetchConfirmedSlotCount()).resolves.toBe(6)
  })

  it('is zero when nothing is booked', async () => {
    stubSlots([])
    await expect(fetchConfirmedSlotCount()).resolves.toBe(0)
  })

  it('rejects rather than inventing a count when the read fails', async () => {
    stubSlots([], { ok: false })
    await expect(fetchConfirmedSlotCount()).rejects.toThrow()
  })

  it('rejects a body that did not report ok', async () => {
    stubSlots(FIVE, { status: 'error' })
    await expect(fetchConfirmedSlotCount()).rejects.toThrow()
  })

  it('survives a payload with no slots array', async () => {
    stubSlots(undefined)
    await expect(fetchConfirmedSlotCount()).resolves.toBe(0)
  })
})

describe('what the badge shows', () => {
  it('reads the real count on mount', async () => {
    stubSlots(FIVE)
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('5'))
  })

  it('starts at zero so nothing flashes before the first read', () => {
    stubSlots(FIVE)
    render(<Probe />)
    expect(screen.getByTestId('count')).toHaveTextContent('0')
  })

  it('keeps the last known count when a read fails', async () => {
    stubSlots(FIVE)
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('5'))
    stubSlots([], { ok: false })
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    expect(screen.getByTestId('count')).toHaveTextContent('5')
  })
})

describe('it follows every way a slot changes', () => {
  it('re-reads when the roster reschedules or removes one', async () => {
    const spy = stubSlots(FIVE)
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('5'))
    stubSlots(FIVE.slice(0, 4))
    await act(async () => {
      window.dispatchEvent(new CustomEvent('teleautomation:pending-work-changed'))
    })
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('4'))
    expect(spy).toHaveBeenCalled()
  })

  it('re-reads on returning from the booking tab', async () => {
    stubSlots(FIVE)
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('5'))
    stubSlots([...FIVE, { date: '2026-09-11', time: '10:00', name: 'New' }])
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('6'))
  })

  it('re-reads when the tab becomes visible again', async () => {
    stubSlots(FIVE)
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('5'))
    stubSlots([])
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('0'))
  })

  it('takes a directly published count without a round trip', async () => {
    stubSlots(FIVE)
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('5'))
    await act(async () => { publishSlotBookingChanged(2) })
    expect(screen.getByTestId('count')).toHaveTextContent('2')
  })

  it('re-reads when a publish carries no number', async () => {
    stubSlots(FIVE)
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('5'))
    stubSlots(FIVE.slice(0, 1))
    await act(async () => { publishSlotBookingChanged() })
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('1'))
  })

  it('never shows a negative count', async () => {
    stubSlots([])
    render(<Probe />)
    await act(async () => { publishSlotBookingChanged(-3) })
    expect(screen.getByTestId('count')).toHaveTextContent('0')
  })

  it('lets go of its listeners when unmounted', async () => {
    stubSlots(FIVE)
    const view = render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('5'))
    view.unmount()
    const after = stubSlots([])
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    expect(after).not.toHaveBeenCalled()
  })
})

describe('how it is wired into the shell', () => {
  const app = readFileSync(resolve(HERE, '..', 'App.jsx'), 'utf8')

  it('hangs the badge on Slot Booking', () => {
    expect(app).toMatch(/id: 'slot-booking'[^}]*badge: 'slots'/)
  })

  it('keeps the section icon, unlike the alert-icon items', () => {
    const item = app.match(/\{ id: 'slot-booking'[^}]*\}/)[0]
    expect(item).not.toContain('alertIcon')
  })

  it('still opens the booking page in its own tab', () => {
    expect(app).toMatch(/id: 'slot-booking'[^}]*external: '\/submit-slot'/)
  })

  it('hides the badge at zero, like every other sidebar badge', () => {
    expect(app).toContain('badgeValue > 0 &&')
  })

  it('says what the number counts', () => {
    expect(app).toContain('confirmed upcoming slot')
  })

  it('uses the plain badge styling rather than the fault red', () => {
    const badge = app.slice(app.indexOf('desktop-sidebar__badge${'))
    expect(badge.slice(0, 220)).not.toContain("item.badge === 'slots'")
  })
})
