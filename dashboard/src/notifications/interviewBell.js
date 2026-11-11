/**
 * The Interview Related alert sound: the interview bell, with its own mute.
 *
 * Selection Related mail has its alarm and its controls (selectionAlarm.js);
 * this is the Interview Related counterpart, so each Mail Alerts page controls
 * its own sound. Muting silences the bell only: the alert, its count and the
 * desktop notification still arrive. The setting is per browser and shared by
 * every tab.
 */
import { previewNotification } from './notificationSounds.js'

export const MUTED_KEY = 'teleautomation:interview-bell:muted'
const listeners = new Set()

export function isInterviewBellMuted() {
  try {
    return localStorage.getItem(MUTED_KEY) === 'true'
  } catch {
    return false
  }
}

export function setInterviewBellMuted(muted) {
  try {
    localStorage.setItem(MUTED_KEY, muted ? 'true' : 'false')
  } catch {
    /* storage is optional; the choice then lasts for this page only */
  }
  for (const listener of listeners) listener(Boolean(muted))
}

export function subscribeInterviewBell(listener) {
  listeners.add(listener)
  const onStorage = event => { if (event.key === MUTED_KEY) listener(isInterviewBellMuted()) }
  window.addEventListener('storage', onStorage)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', onStorage)
  }
}

/** Play the bell once, muted or not: the Test button. */
export function testInterviewBell() {
  return previewNotification('mail_interview_booking')
}
