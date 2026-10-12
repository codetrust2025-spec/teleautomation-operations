/**
 * One reader of the Gmail mailbox health for the whole tab.
 *
 * The sidebar's reconnect badge, the reconnect alert sound and the AI Mail
 * Review page each polled `/api/candidate-mailboxes/health` on a timer of
 * their own, so one open tab asked for the same snapshot up to three times a
 * minute. Every read now goes through here: a read already on its way is
 * shared, and its result reaches every listener, so a page that reads more
 * often keeps the badge and the alert current for free.
 */

const API = import.meta.env?.VITE_API_BASE || ''

const listeners = new Set()
let inflight = null
let lastReadAt = 0

/** When a health snapshot last arrived in this tab (ms since epoch, 0 if never). */
export function mailboxHealthReadAt() {
  return lastReadAt
}

/** Hear every health snapshot this tab reads, whoever asked for it. */
export function onMailboxHealth(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Read the health snapshot now, sharing a read already in flight. */
export function readMailboxHealth() {
  if (inflight) return inflight
  // The cache-buster stays: this is read straight after an operator
  // reconnects, and a cached body would show the account still broken.
  const request = fetch(`${API}/api/candidate-mailboxes/health?_=${Date.now()}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  })
    .then(async response => {
      if (!response.ok) throw new Error('mailbox health unavailable')
      const body = await response.json().catch(() => ({}))
      lastReadAt = Date.now()
      for (const listener of listeners) {
        try {
          listener(body)
        } catch {
          /* one broken listener must not starve the others */
        }
      }
      return body
    })
    .finally(() => {
      if (inflight === request) inflight = null
    })
  inflight = request
  return request
}

export function __resetMailboxHealth() {
  listeners.clear()
  inflight = null
  lastReadAt = 0
}
