/**
 * Confirmed upcoming slots, for the Slot Booking badge in the sidebar.
 *
 * The number comes from `/public/slots/booked` — the same request the Slot
 * Booking page makes, counted the same way it counts: every confirmed slot still
 * awaiting an outcome, whichever of its two sections ("Needs status update" or
 * "Upcoming slots") the page places it in. There is no second calculation and
 * no filtering here, so the badge and the page cannot disagree.
 *
 * Keeping it current takes three signals, because slots change in two places:
 *
 *  - The roster already announces every change it makes — attendance,
 *    reschedule, removal — on `teleautomation:pending-work-changed`, so this
 *    listens to that rather than asking Daily Ops to publish a second event.
 *  - Booking and cancelling happen on /submit-slot, which the sidebar opens in
 *    a new tab. Nothing in this document can observe that, so returning to the
 *    shell is the signal: focus and visibilitychange both re-read.
 *  - A slow poll underneath, for a change made somewhere else entirely.
 */
import { useEffect, useState } from 'react'

const EVENT = 'teleautomation:slot-booking-changed'
const ROSTER_CHANGED = 'teleautomation:pending-work-changed'
const API = import.meta.env?.VITE_API_BASE || ''

/** How often to re-read when nothing has signalled a change. */
export const SLOT_BOOKING_POLL_MS = 60000

/** Announce a freshly known confirmed-slot count to anything showing it. */
export function publishSlotBookingChanged(count) {
  const detail = Number.isFinite(Number(count))
    ? { confirmed: Math.max(0, Number(count)) }
    : {}
  window.dispatchEvent(new CustomEvent(EVENT, { detail }))
}

export async function fetchConfirmedSlotCount() {
  // no-store for the same reason the page uses it: this is read straight after
  // a booking, and a cached body would show the count before it.
  const response = await fetch(`${API}/public/slots/booked`, { cache: 'no-store' })
  if (!response.ok) throw new Error('confirmed slots unavailable')
  const body = await response.json().catch(() => ({}))
  if (body?.status !== 'ok') throw new Error('confirmed slots unavailable')
  return Array.isArray(body?.slots) ? body.slots.length : 0
}

/**
 * Current confirmed-slot count, 0 until something says otherwise.
 *
 * Never throws and never renders an error: a sidebar badge is not worth
 * breaking the shell over, so a failed read leaves the last known count.
 */
export function useConfirmedSlotCount() {
  const [confirmed, setConfirmed] = useState(0)

  useEffect(() => {
    let live = true
    const refresh = () => {
      fetchConfirmedSlotCount()
        .then(count => { if (live) setConfirmed(count) })
        .catch(() => { /* keep the last known count */ })
    }
    refresh()

    // A count published directly wins immediately; anything else re-reads.
    const onPublished = event => {
      if (!live) return
      const value = Number(event?.detail?.confirmed)
      if (Number.isFinite(value)) setConfirmed(Math.max(0, value))
      else refresh()
    }
    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh()
    }

    window.addEventListener(EVENT, onPublished)
    window.addEventListener(ROSTER_CHANGED, refresh)
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', onVisible)
    const timer = window.setInterval(refresh, SLOT_BOOKING_POLL_MS)

    return () => {
      live = false
      window.removeEventListener(EVENT, onPublished)
      window.removeEventListener(ROSTER_CHANGED, refresh)
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', onVisible)
      window.clearInterval(timer)
    }
  }, [])

  return confirmed
}
