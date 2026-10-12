import { useEffect, useRef } from 'react'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'
import { reconnectRequiredMailboxes } from '../utils/mailboxStatus.js'
import { mailboxHealthReadAt, onMailboxHealth, readMailboxHealth } from './mailboxHealth.js'
import { subscribeMailEvents, traceMailAlert } from './mailEventStream.js'
import { notifyGmailReconnect, notifyTrackedMail } from './notificationEvents.js'
import { useInterviewReminders } from './useInterviewReminders.js'
import { installSoundPreview } from './soundPreview.js'

const GMAIL_HEALTH_POLL_MS = 120000

export function GlobalNotificationSounds() {
  const { authenticated } = useAuth()
  const gmailEnabledRef = useRef(null)

  useEffect(() => { if (import.meta.env?.DEV) installSoundPreview() }, [])
  useInterviewReminders(authenticated)

  useEffect(() => {
    if (!authenticated) return undefined
    return subscribeMailEvents((payload, meta) => {
      if (!meta?.fromSocket) return
      if (payload?.event === 'notification_created' && payload?.delivery_source === 'replay') {
        traceMailAlert('sound_suppressed_replay', payload)
        return
      }
      if (notifyTrackedMail(payload)) traceMailAlert('sound', payload)
    })
  }, [authenticated])

  useEffect(() => {
    if (!authenticated) return undefined
    let cancelled = false
    // Every health snapshot this tab reads arrives here, whoever asked for it
    // (the sidebar badge reads every minute). Reads are shared one at a time,
    // so snapshots arrive in order and an older one cannot undo a newer one.
    let latest = null
    const check = body => {
      // The snapshot is passed alongside the derived list so the alert is
      // re-checked against the same mailbox state it came from.
      if (body && !cancelled && gmailEnabledRef.current) {
        notifyGmailReconnect(reconnectRequiredMailboxes(body.mailboxes), body.mailboxes)
      }
    }
    const offHealth = onMailboxHealth(body => { latest = body; check(body) })
    const readMailboxes = async () => {
      if (gmailEnabledRef.current == null) {
        const config = await fetch(`${API}/api/ai-recruitment/config`, { credentials: 'include' }).then(r => r.ok ? r.json() : null).catch(() => null)
        gmailEnabledRef.current = Boolean(config?.enabled)
        // A snapshot that arrived while the config was being read was skipped.
        if (latest && Date.now() - mailboxHealthReadAt() < GMAIL_HEALTH_POLL_MS) check(latest)
      }
      if (!gmailEnabledRef.current || cancelled) return
      if (Date.now() - mailboxHealthReadAt() < GMAIL_HEALTH_POLL_MS / 2) return
      await readMailboxHealth().catch(() => null)
    }
    readMailboxes()
    const timer = window.setInterval(readMailboxes, GMAIL_HEALTH_POLL_MS)
    return () => { cancelled = true; offHealth(); window.clearInterval(timer) }
  }, [authenticated])

  return null
}

export default GlobalNotificationSounds
