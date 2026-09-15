/**
 * One mail-monitoring socket for the whole tab.
 *
 * Previously the notifications page and the header bell each opened their own
 * WebSocket, which is why the alert sound needed an 8-second dedupe window to
 * stop the same event sounding twice. Now there is a single connection with
 * many subscribers: the global sound manager is one of them, the page and the
 * bell are others, and a component mounting or unmounting cannot change whether
 * an event is heard.
 *
 * Connect/retry/replay semantics are carried over unchanged, including the
 * `last_event_id` replay cursor and the cross-tab BroadcastChannel mirror.
 */

import { API } from '../config.js'

const CHANNEL_NAME = 'teleautomation-mail-monitoring'
const LAST_EVENT_KEY = 'teleautomation-mail-last-event-id'
const SEEN_LIMIT = 500
const TRACE_LIMIT = 200

const subscribers = new Set()
const statusSubscribers = new Set()
// What each AI node is serving, pushed by the server as it changes. It shares
// the socket but not the mail pipeline: it is live state rather than an alert,
// so it is never deduped, replayed, traced or handed to the mail subscribers,
// any of which would refetch or sound on it.
const activitySubscribers = new Set()

let socket = null
let channel = null
let retry = 0
let reconnectTimer = null
let heartbeat = null
let stopped = true
let status = 'Offline'
const seen = new Set()
const trace = []

/**
 * Bounded, non-sensitive correlation data for production verification.
 * The buffer deliberately excludes message bodies, credentials and tokens.
 */
export function traceMailAlert(stage, payload = {}, extra = {}) {
  const record = {
    stage,
    event: payload?.event || null,
    notification_id: payload?.notification_id || null,
    event_id: payload?.event_id || null,
    provider_message_id: payload?.provider_message_id || null,
    classification: payload?.classification || null,
    delivery_source: payload?.delivery_source || 'live',
    at: new Date().toISOString(),
    ...extra,
  }
  trace.push(record)
  if (trace.length > TRACE_LIMIT) trace.shift()
  console.info('[MailAlertTrace]', JSON.stringify(record))
  try {
    window.dispatchEvent(new CustomEvent('teleautomation:mail-alert-trace', { detail: record }))
  } catch {
    /* tracing must never affect alert delivery */
  }
  return record
}

export function getMailAlertTrace() {
  return [...trace]
}

function setStatus(next) {
  if (status === next) return
  status = next
  for (const fn of statusSubscribers) {
    try {
      fn(status)
    } catch {
      /* a broken status listener must not kill the socket */
    }
  }
}

/**
 * Fan an event out to every subscriber.
 *
 * `fromSocket` distinguishes an event this tab received from the server from
 * one mirrored out of another tab. Only the former may make a noise — the same
 * rule the old per-component socket followed.
 */
function emit(payload, fromSocket) {
  for (const fn of subscribers) {
    try {
      fn(payload, { fromSocket })
    } catch {
      /* one bad subscriber must not stop the others */
    }
  }
}

function receive(payload, fromSocket = true) {
  if (payload?.event === 'ai_node_activity') {
    for (const fn of activitySubscribers) {
      try {
        fn(payload)
      } catch {
        /* one bad subscriber must not stop the others */
      }
    }
    return
  }
  const id = payload?.event_id
  if (id && seen.has(id)) return
  if (id) {
    seen.add(id)
    if (seen.size > SEEN_LIMIT) seen.delete(seen.values().next().value)
    try {
      localStorage.setItem(LAST_EVENT_KEY, id)
    } catch {
      /* storage is optional */
    }
  }

  traceMailAlert('browser_received', payload, { transport: fromSocket ? 'websocket' : 'broadcast_channel' })
  emit(payload, fromSocket)

  if (['slot_auto_booked', 'interview_rescheduled', 'interview_cancelled'].includes(payload?.event)) {
    window.dispatchEvent(new CustomEvent('teleautomation:slot-booking-updated', { detail: payload }))
  }
  if (fromSocket) channel?.postMessage(payload)
}

function connect() {
  if (stopped) return
  setStatus(retry ? 'Reconnecting' : 'Offline')
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  let last = ''
  try {
    last = localStorage.getItem(LAST_EVENT_KEY) || ''
  } catch {
    last = ''
  }
  socket = new WebSocket(
    `${scheme}://${window.location.host}/ws/mail-monitoring?last_event_id=${encodeURIComponent(last)}`,
  )
  socket.onopen = () => {
    retry = 0
    setStatus('Live')
    heartbeat = window.setInterval(
      () => socket?.readyState === WebSocket.OPEN && socket.send(JSON.stringify({ type: 'ping' })),
      20000,
    )
  }
  socket.onmessage = (event) => {
    try {
      receive(JSON.parse(event.data), true)
    } catch {
      /* ignore malformed transport frames */
    }
  }
  socket.onclose = () => {
    window.clearInterval(heartbeat)
    if (stopped) return
    retry += 1
    setStatus(retry > 1 ? 'Offline' : 'Reconnecting')
    reconnectTimer = window.setTimeout(
      connect,
      Math.min(30000, 1000 * 2 ** Math.min(retry, 5)) + Math.random() * 500,
    )
  }
  socket.onerror = () => socket.close()
}

function start() {
  if (!stopped) return
  stopped = false
  channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel(CHANNEL_NAME) : null
  // Mirrored events go through the same ID dedupe and cursor handling as
  // socket frames. Previously they bypassed both, so every open tab could
  // refetch the table for the same event and later process its own socket copy.
  if (channel) channel.onmessage = (event) => receive(event.data, false)

  fetch(`${API}/api/ai-recruitment/config`, { credentials: 'include' })
    .then((response) => (response.ok ? response.json() : null))
    .then((data) => {
      if (data?.enabled) connect()
      else setStatus('Offline')
    })
    .catch(() => setStatus('Offline'))
}

function stop() {
  stopped = true
  window.clearTimeout(reconnectTimer)
  window.clearInterval(heartbeat)
  try {
    channel?.close()
  } catch {
    /* ignore */
  }
  channel = null
  try {
    socket?.close()
  } catch {
    /* ignore */
  }
  socket = null
  setStatus('Offline')
}

/**
 * Subscribe to mail events. Returns an unsubscribe function.
 *
 * The connection opens on the first subscriber and closes when the last one
 * leaves, so nothing is held open on the login screen.
 */
export function subscribeMailEvents(handler) {
  subscribers.add(handler)
  if (subscribers.size + activitySubscribers.size === 1) start()
  return () => {
    subscribers.delete(handler)
    if (subscribers.size + activitySubscribers.size === 0) stop()
  }
}

/**
 * Subscribe to AI node activity: `{event, version, nodes: {node_id: [...]}}`,
 * the whole snapshot each time. Returns an unsubscribe function.
 *
 * Pushes sent while the socket was down are not replayed, so a subscriber
 * should read the current snapshot itself whenever the status turns Live.
 */
export function subscribeNodeActivity(handler) {
  activitySubscribers.add(handler)
  if (subscribers.size + activitySubscribers.size === 1) start()
  return () => {
    activitySubscribers.delete(handler)
    if (subscribers.size + activitySubscribers.size === 0) stop()
  }
}

export function subscribeMailStatus(handler) {
  statusSubscribers.add(handler)
  handler(status)
  return () => statusSubscribers.delete(handler)
}

export function getMailStatus() {
  return status
}

/** Test seam: forget connection state between cases. */
export function __resetMailEventStream() {
  subscribers.clear()
  statusSubscribers.clear()
  activitySubscribers.clear()
  seen.clear()
  trace.length = 0
  retry = 0
  stop()
}
