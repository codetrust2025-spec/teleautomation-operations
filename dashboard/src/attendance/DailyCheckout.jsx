import React, { useCallback, useEffect, useRef, useState } from 'react'
import { API } from '../config.js'

/**
 * Closing the working day, and what is still open.
 *
 * Attendance said when someone arrived; nothing said when they left, so the end
 * of a day was whatever the last request of a session happened to be. This
 * records it — once per day, and only when the day is actually finished.
 *
 * The server decides that. This screen's job is to say what is in the way in
 * words the person can act on, and to notice by itself when the last of it is
 * cleared: the checklist re-reads on a timer and whenever the tab is looked at
 * again, so finishing an interview on another screen unlocks the button here
 * without a reload.
 */
const POLL_MS = 60_000

const REASON_ACTIONS = {
  INTERVIEW_NOT_FINISHED: 'Wait until the slot has ended.',
  INTERVIEW_OUTCOME_PENDING: 'Set the outcome on the Daily Ops roster.',
  DAILY_TASKS_PENDING: 'Clear these on the Pending Works tab.',
  ATTENDANCE_NOT_MARKED: 'Mark attendance first.',
  OFFICE_NETWORK_REQUIRED: 'Reconnect to the office network.',
}

function timeOfDay(value) {
  if (!value) return ''
  return new Date(value).toLocaleTimeString('en-IN', {
    hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Kolkata',
  })
}

function itemLabel(item) {
  const name = item.name || item.label || 'Unnamed'
  const when = item.time ? ` · ${item.time}` : ''
  const company = item.company ? ` · ${item.company}` : ''
  return `${name}${company}${when}`
}

export function DailyCheckout() {
  const [state, setState] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const alive = useRef(true)

  const read = useCallback(async () => {
    try {
      const response = await fetch(`${API}/attendance/checkout`, { credentials: 'include' })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(payload.detail?.message || payload.detail || 'Check-out status unavailable')
      if (alive.current) {
        setState(payload)
        setError('')
      }
    } catch (requestError) {
      if (alive.current) setError(requestError.message)
    }
  }, [])

  useEffect(() => {
    alive.current = true
    read()
    const timer = window.setInterval(read, POLL_MS)
    const wake = () => { if (document.visibilityState === 'visible') read() }
    window.addEventListener('focus', read)
    document.addEventListener('visibilitychange', wake)
    return () => {
      alive.current = false
      window.clearInterval(timer)
      window.removeEventListener('focus', read)
      document.removeEventListener('visibilitychange', wake)
    }
  }, [read])

  const checkOut = async () => {
    setBusy(true)
    setError('')
    try {
      const response = await fetch(`${API}/attendance/checkout`, {
        method: 'POST', credentials: 'include',
      })
      const payload = await response.json().catch(() => ({}))
      if (response.status === 409) {
        // The day moved on between reading and pressing. The server's list is
        // the current one, so it replaces what is on screen.
        setState(previous => ({
          ...(previous || {}),
          can_check_out: false,
          blockers: payload.detail?.blockers || [],
        }))
        setError(payload.detail?.message || 'The working day is not finished yet.')
        read()
        return
      }
      if (!response.ok) throw new Error(payload.detail || 'Check-out failed')
      setState(previous => ({
        ...(previous || {}),
        checked_out: true,
        checked_out_at: payload.checkout?.checked_out_at,
        can_check_out: false,
        blockers: [],
      }))
      read()
    } catch (requestError) {
      // Deliberately not re-reading here. A refusal changed nothing, and a
      // second request that also fails would replace the reason with its own
      // less useful one -- which is how "only available to handler accounts"
      // became "Failed to fetch" on screen.
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  if (!state) return null

  const blockers = state.blockers || []

  return (
    <section className="attendance-section attendance-checkout">
      <div className="attendance-section__head">
        <div>
          <h3>Daily check-out</h3>
          <p>
            {state.checked_out
              ? "Today's working day is closed."
              : `Available from ${state.window_opens_at || '6:30 PM'} IST, once today's work is closed.`}
          </p>
        </div>
        {!state.checked_out && (
          <button
            type="button"
            className="attendance-primary-button"
            disabled={busy || !state.can_check_out}
            onClick={checkOut}
          >
            {busy ? 'Checking out…' : 'Check out'}
          </button>
        )}
      </div>

      {error && <p className="attendance-error" role="alert">{error}</p>}

      {state.checked_out && (
        <p className="attendance-checkout__done" role="status">
          ✓ Checked out at {timeOfDay(state.checked_out_at)}
        </p>
      )}

      {!state.checked_out && blockers.length === 0 && (
        <p className="attendance-checkout__ready" role="status">
          Everything for today is closed. You can check out.
        </p>
      )}

      {!state.checked_out && blockers.length > 0 && (
        <ul className="attendance-checkout__list">
          {blockers.map(blocker => (
            <li key={blocker.kind} className={`attendance-checkout__item is-${blocker.kind.toLowerCase()}`}>
              <p className="attendance-checkout__summary">
                <span aria-hidden>○</span> {blocker.summary}
                {blocker.count > 0 && <span className="attendance-count">{blocker.count}</span>}
              </p>
              {REASON_ACTIONS[blocker.kind] && (
                <small className="attendance-checkout__action">{REASON_ACTIONS[blocker.kind]}</small>
              )}
              {(blocker.items || []).length > 0 && (
                <ul className="attendance-checkout__items">
                  {blocker.items.map((item, index) => (
                    <li key={`${blocker.kind}-${item.candidate_id || index}`}>{itemLabel(item)}</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default DailyCheckout
