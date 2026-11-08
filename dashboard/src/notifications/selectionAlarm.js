/**
 * The loud, repeating alarm for genuine Selection Related mail.
 *
 * A genuine selection (shortlisted after interview, selected, offer, BGV,
 * onboarding, joining) starts a 30-second alarm that repeats every 60 seconds
 * until someone acknowledges it. Acknowledging is "Stop Sound" on the banner,
 * or opening, reading or dismissing that alert in Mail Alerts.
 *
 * Rules this file enforces, each covered by selectionAlarm.test.js:
 * - Only the classifications in GENUINE_SELECTION_CLASSIFICATIONS can start
 *   it. Interview Related classifications are rejected explicitly, even if a
 *   future list edit put one in the selection set by mistake.
 * - One alert id alarms once: already pending, or already acknowledged, it is
 *   ignored. Several pending alerts share one alarm; they never overlap.
 * - Pending alerts survive a refresh (localStorage) and are shared by every
 *   tab, but only one tab sounds at a time (Web Locks), and an acknowledgement
 *   in any tab silences all of them.
 * - Mute silences without acknowledging: the banner stays until Stop Sound.
 */

import { INTERVIEW_BOOKING_CLASSIFICATIONS } from '../utils/mailAlertSound.js'
import { ALARM_SECONDS, setSelectionAlarmVolume, startSelectionAlarm, stopSelectionAlarm } from './sounds/selectionAlarm.js'

export const REPEAT_MS = 60000
export const PENDING_KEY = 'teleautomation:selection-alarm:pending'
export const ACKED_KEY = 'teleautomation:selection-alarm:acknowledged'
export const SETTINGS_KEY = 'teleautomation:selection-alarm:settings'
const LOCK_NAME = 'teleautomation-selection-alarm'
const PENDING_LIMIT = 50
const ACKED_LIMIT = 1000
const PENDING_MAX_AGE_MS = 24 * 60 * 60 * 1000

/**
 * Good news that needs a person. Narrower than SELECTION_CLASSIFICATIONS:
 * offer_declined and offer_revoked are Selection Related but are not good
 * news, so they keep the short fanfare and never start the alarm.
 */
export const GENUINE_SELECTION_CLASSIFICATIONS = new Set([
  'post_interview_shortlisted', 'job_selection_confirmed', 'final_round_cleared',
  'offer_received', 'offer_accepted', 'joining_confirmed', 'joining_date_updated',
  'onboarding_started', 'background_verification', 'document_verification',
  'compensation_confirmation', 'hr_confirmation',
])

const INTERVIEW = new Set([...INTERVIEW_BOOKING_CLASSIFICATIONS, 'interview_shortlisted'])

export function isGenuineSelection(classification) {
  const value = String(classification || '')
  if (INTERVIEW.has(value)) return false
  return GENUINE_SELECTION_CLASSIFICATIONS.has(value)
}

// ---- storage -------------------------------------------------------------

function read(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : fallback
  } catch {
    return fallback
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* storage is optional; the alarm still works for this page load */
  }
}

function readPending() {
  const now = Date.now()
  const list = read(PENDING_KEY, [])
  return Array.isArray(list) ? list.filter(a => a?.id && now - Number(a.at || 0) < PENDING_MAX_AGE_MS) : []
}

function readAcked() {
  const list = read(ACKED_KEY, [])
  return new Set(Array.isArray(list) ? list.map(String) : [])
}

export function readSettings() {
  const s = read(SETTINGS_KEY, {})
  const volume = Number(s?.volume)
  return { volume: Number.isFinite(volume) ? Math.min(1, Math.max(0, volume)) : 1, muted: Boolean(s?.muted) }
}

// ---- state ---------------------------------------------------------------

const listeners = new Set()
let timer = null
let releaseLock = null
let lockRequested = false
let storageWired = false

function snapshot() {
  return { pending: readPending(), ...readSettings() }
}

function notify() {
  const state = snapshot()
  for (const fn of listeners) {
    try {
      fn(state)
    } catch {
      /* a broken listener must not stop the alarm */
    }
  }
}

function playOnce() {
  const { volume, muted } = readSettings()
  if (muted || !readPending().length) return false
  return startSelectionAlarm(volume, ALARM_SECONDS)
}

function stopCycle() {
  if (timer) window.clearInterval(timer)
  timer = null
  stopSelectionAlarm()
  if (releaseLock) {
    releaseLock()
    releaseLock = null
  }
  lockRequested = false
}

function beginSounding() {
  if (timer) return
  playOnce()
  timer = window.setInterval(() => {
    if (!readPending().length) {
      stopCycle()
      return
    }
    playOnce()
  }, REPEAT_MS)
}

/** Make sure exactly one tab is sounding while anything is pending. */
function ensureCycle() {
  if (!readPending().length) {
    stopCycle()
    return
  }
  if (timer || lockRequested) return
  const locks = typeof navigator !== 'undefined' ? navigator.locks : null
  if (!locks?.request) {
    beginSounding()
    return
  }
  lockRequested = true
  // Queued, not ifAvailable: a second tab waits here and takes over if the
  // sounding tab is closed while the alert is still unacknowledged.
  locks.request(LOCK_NAME, () => new Promise(resolve => {
    if (!readPending().length) {
      lockRequested = false
      resolve()
      return
    }
    releaseLock = resolve
    beginSounding()
  })).catch(() => {
    lockRequested = false
  })
}

function wireStorage() {
  if (storageWired || typeof window === 'undefined') return
  storageWired = true
  // Another tab acknowledged, raised, muted or changed volume.
  window.addEventListener('storage', event => {
    if (![PENDING_KEY, ACKED_KEY, SETTINGS_KEY].includes(event.key)) return
    const { volume, muted } = readSettings()
    if (muted) stopSelectionAlarm()
    else setSelectionAlarmVolume(volume)
    ensureCycle()
    notify()
  })
}

// ---- public API ----------------------------------------------------------

/**
 * Start (or join) the alarm for a newly created alert. Returns true only when
 * this alert was added; duplicates, acknowledged ids and anything that is not
 * a genuine selection return false and change nothing.
 */
export function raiseSelectionAlarm(payload = {}) {
  if (!isGenuineSelection(payload.classification)) return false
  const id = String(payload.notification_id || payload.event_id || '')
  if (!id) return false
  if (readAcked().has(id)) return false
  const pending = readPending()
  if (pending.some(a => a.id === id)) return false
  pending.push({
    id,
    classification: String(payload.classification),
    status: String(payload.status || ''),
    candidate_name: String(payload.candidate_name || ''),
    company_name: String(payload.company_name || ''),
    at: Date.now(),
  })
  write(PENDING_KEY, pending.slice(-PENDING_LIMIT))
  wireStorage()
  ensureCycle()
  notify()
  return true
}

/** Acknowledge one alert (opened, read or dismissed in Mail Alerts). */
export function acknowledgeSelectionAlarm(id) {
  const key = String(id || '')
  if (!key) return false
  const pending = readPending()
  const acked = [...readAcked()]
  if (!acked.includes(key)) write(ACKED_KEY, [...acked, key].slice(-ACKED_LIMIT))
  const rest = pending.filter(a => a.id !== key)
  if (rest.length === pending.length) return false
  write(PENDING_KEY, rest)
  ensureCycle()
  notify()
  return true
}

/** Stop Sound: acknowledge everything pending and silence now. */
export function stopSelectionAlarmSound() {
  const pending = readPending()
  const acked = [...readAcked()]
  for (const a of pending) if (!acked.includes(a.id)) acked.push(a.id)
  write(ACKED_KEY, acked.slice(-ACKED_LIMIT))
  write(PENDING_KEY, [])
  stopCycle()
  notify()
}

export function setSelectionAlarmMuted(muted) {
  write(SETTINGS_KEY, { ...readSettings(), muted: Boolean(muted) })
  if (muted) stopSelectionAlarm()
  notify()
}

export function setSelectionAlarmVolumeLevel(volume) {
  const v = Math.min(1, Math.max(0, Number(volume) || 0))
  write(SETTINGS_KEY, { ...readSettings(), volume: v })
  setSelectionAlarmVolume(v)
  notify()
}

/** Play one 30-second burst now at the current volume (the Test button). */
export function testSelectionAlarm() {
  const { volume } = readSettings()
  return startSelectionAlarm(volume, ALARM_SECONDS)
}

export function stopSelectionAlarmTest() {
  if (!readPending().length) stopSelectionAlarm()
}

/**
 * Subscribe to alarm state. Also resumes an alarm that was still pending when
 * the page was refreshed. Returns an unsubscribe function.
 */
export function subscribeSelectionAlarm(fn) {
  listeners.add(fn)
  wireStorage()
  fn(snapshot())
  ensureCycle()
  return () => listeners.delete(fn)
}

export function getSelectionAlarmState() {
  return snapshot()
}

/** Test seam. */
export function __resetSelectionAlarm() {
  stopCycle()
  listeners.clear()
  try {
    localStorage.removeItem(PENDING_KEY)
    localStorage.removeItem(ACKED_KEY)
    localStorage.removeItem(SETTINGS_KEY)
  } catch {
    /* ignore */
  }
}
