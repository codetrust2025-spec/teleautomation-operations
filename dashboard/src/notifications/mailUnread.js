/**
 * The unread mail count, shared between the Mail Alerts page and the sidebar.
 *
 * The sidebar cannot poll for this on its own without lagging behind the page:
 * marking a mail read is a local action whose result the server only reports
 * on the next fetch. So the page publishes the count it already knows, and the
 * sidebar listens. A fetch on mount and on each mail event that can change the
 * count covers the cases the page is not open for — a mail arriving, or another tab.
 */
import { useEffect, useState } from 'react'
import { subscribeMailEvents } from './mailEventStream.js'

const EVENT = 'teleautomation:mail-unread'
const API = import.meta.env?.VITE_API_BASE || ''

/** Announce a freshly known unread count to anything showing it. */
export function publishMailUnread(count) {
  const value = Math.max(0, Number(count) || 0)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: { unread: value } }))
}

/** Events after which the counts can differ; the Mail Alerts page reloads on the same set. */
const SUMMARY_EVENTS = new Set([
  'notification_created', 'notification_updated', 'important_mail_detected', 'mail_retry_pending', 'connected',
])

export function changesMailSummary(payload) {
  return SUMMARY_EVENTS.has(payload?.event)
}

let inflight = null

/**
 * The Mail Alerts summary, one request at a time for the whole tab.
 *
 * The sidebar, the page and the bell all react to the same mail event and
 * used to send a request each. A request already on its way is shared, but only
 * one that started at or after `since`: a read that left before the change it
 * is meant to show (a mail marked read, an event that just arrived) would bring
 * back the old count.
 */
export function fetchMailSummary({ since = Date.now() } = {}) {
  if (inflight && inflight.startedAt >= since) return inflight.promise
  const entry = { startedAt: Date.now(), promise: null }
  entry.promise = fetch(`${API}/api/mail-monitoring/summary`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  }).then(async response => {
    const body = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(body?.detail || 'summary unavailable')
    return body
  })
  inflight = entry
  entry.promise.then(
    () => { if (inflight === entry) inflight = null },
    () => { if (inflight === entry) inflight = null },
  )
  return entry.promise
}

export async function fetchMailUnread(options) {
  const body = await fetchMailSummary(options)
  return Math.max(0, Number(body?.summary?.unread) || 0)
}

/**
 * Current unread count, 0 until something says otherwise.
 *
 * Never throws and never renders an error: a sidebar badge is not worth
 * breaking the shell over, so a failed read simply leaves the last known
 * count in place.
 */
export function useMailUnreadCount() {
  const [unread, setUnread] = useState(0)

  useEffect(() => {
    let live = true
    const refresh = () => {
      fetchMailUnread()
        .then(count => { if (live) setUnread(count) })
        .catch(() => { /* keep the last known count */ })
    }
    refresh()

    // The page publishes after every read/unread toggle, so the badge moves
    // with the click rather than on the next poll.
    const onPublished = event => {
      if (!live) return
      const value = Number(event?.detail?.unread)
      if (Number.isFinite(value)) setUnread(Math.max(0, value))
    }
    window.addEventListener(EVENT, onPublished)
    // Only what can change the count: keep-alives, sync progress and the AI
    // pipeline's own steps never do, and each one used to cost a request.
    const unsubscribe = subscribeMailEvents(payload => {
      if (!changesMailSummary(payload)) return
      if (payload.event === 'connected' && payload.summary) {
        if (live) setUnread(Math.max(0, Number(payload.summary.unread) || 0))
        return
      }
      refresh()
    })

    return () => {
      live = false
      window.removeEventListener(EVENT, onPublished)
      unsubscribe?.()
    }
  }, [])

  return unread
}
