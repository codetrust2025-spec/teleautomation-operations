import { SELECTION_CLASSIFICATIONS, isTrackedMailAlert, showMailAlertNotification } from '../utils/mailAlertSound.js'
import { describeReconnectTargets, needsReconnect } from '../utils/mailboxStatus.js'
import { playNotification } from './notificationSounds.js'
import { isGenuineSelection, raiseSelectionAlarm } from './selectionAlarm.js'
import { isInterviewBellMuted } from './interviewBell.js'

const recentMailEvents = new Map()
const MAIL_DEDUPE_LIMIT = 500
const GMAIL_ALERTED_KEY = 'teleautomation:operations:gmail-reconnect-alerted'

function firstSightOfMailEvent(eventId) {
  if (eventId == null) return true
  const key = String(eventId)
  if (recentMailEvents.has(key)) return false
  recentMailEvents.set(key, true)
  if (recentMailEvents.size > MAIL_DEDUPE_LIMIT) {
    recentMailEvents.delete(recentMailEvents.keys().next().value)
  }
  return true
}

export function notifyTrackedMail(payload) {
  if (payload?.event !== 'notification_created') return false
  if (!isTrackedMailAlert(payload?.classification)) return false
  // A notification row is the logical alert. Booking can update that row and
  // transport retries can use a different event ID, but neither is a new alert
  // and neither may produce another sound.
  if (!firstSightOfMailEvent(payload.notification_id || payload.event_id)) return false
  // A genuine selection gets the long repeating alarm instead of the short
  // fanfare, never both. raiseSelectionAlarm refuses an id this browser has
  // already alarmed for or acknowledged, so a second tab or a reload cannot
  // restart it; such an alert stays silent rather than falling back.
  if (isGenuineSelection(payload.classification)) {
    const raised = raiseSelectionAlarm(payload)
    if (raised) showMailAlertNotification(payload)
    return raised
  }
  const selection = SELECTION_CLASSIFICATIONS.includes(payload.classification)
  // Interview Related has its own mute (the Interview Related page); the
  // alert and its desktop notification still arrive.
  const played = !selection && isInterviewBellMuted()
    ? false
    : playNotification(selection ? 'mail_selection' : 'mail_interview_booking')
  showMailAlertNotification(payload)
  return played
}

function readAlertedMailboxIds() {
  try { return new Set(JSON.parse(sessionStorage.getItem(GMAIL_ALERTED_KEY) || '[]').map(String)) } catch { return new Set() }
}

/**
 * Alert for mailboxes whose Gmail authorization needs the operator.
 *
 * `current` is the mailbox snapshot the list was derived from. Each row is
 * re-checked against it immediately before firing, so a mailbox that has
 * recovered between deriving the list and firing does not raise an expiry
 * alert for a connection that is working again.
 *
 * The re-check reads `connection_status` and `last_error_message` -- the
 * persisted connection state -- and deliberately not the sync-job label. A
 * failed sync leaves a retry QUEUED, so an expired mailbox reads as "Sync
 * Queued" on the page while its credentials are dead; suppressing on that
 * label would silence the true alert for exactly the mailbox that needs it.
 *
 * It fails open in both directions that matter. No snapshot, or a row missing
 * from the snapshot, alerts as before: absence of evidence that a mailbox
 * recovered is not evidence that it did, and a missed expiry is worse than a
 * repeated one.
 */
export function notifyGmailReconnect(disconnected = [], current = null) {
  const live = Array.isArray(current)
    ? new Map(current.map(row => [String(row?.id), row]))
    : null
  const stillBroken = live === null
    ? disconnected
    : disconnected.filter(row => {
      const latest = live.get(String(row?.id))
      return latest === undefined ? true : needsReconnect(latest)
    })

  const alerted = readAlertedMailboxIds()
  const newly = stillBroken.filter(row => !alerted.has(String(row.id)))
  if (newly.length) {
    playNotification('gmail_reconnect')
    showMailAlertNotification({ status: 'Gmail connection expired', candidate_name: describeReconnectTargets(newly), notification_id: `gmail-reconnect-${newly.map(row => row.id).join('-')}` })
  }
  // Only mailboxes still broken are remembered, so one that recovers is
  // dropped and a later, genuinely new failure alerts again.
  try { sessionStorage.setItem(GMAIL_ALERTED_KEY, JSON.stringify(stillBroken.map(row => String(row.id)))) } catch {}
  return newly.length > 0
}

export function notifyInterviewReminder() {
  return playNotification('interview_reminder')
}

export function __resetNotificationEvents() {
  recentMailEvents.clear()
}
