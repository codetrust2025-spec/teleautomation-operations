/**
 * Tracked recruitment mail: which classifications matter, and what the desktop
 * notification says.
 *
 * The sounds moved out. Selections and interview bookings used to share one
 * klaxon generator, distinguishable only by sweep count, and are now two
 * unrelated sounds — a fanfare and a bell — defined in
 * src/notifications/sounds/. Dispatch and dedupe live in
 * src/notifications/notificationEvents.js. The classification lists below are
 * business rules and are unchanged.
 */

/**
 * Candidate got picked — offer/selection/joining mails.
 *
 * This must stay in step with SELECTION_RELATED_CLASSIFICATIONS in
 * core/recruitment_mail_store.py. `final_round_cleared` and `hr_confirmation`
 * were missing from both lists here, so isTrackedMailAlert rejected them and
 * they arrived on the Selection Related tab in silence — visible only to
 * someone already looking at the screen.
 */
export const SELECTION_CLASSIFICATIONS = [
  'job_selection_confirmed', 'final_round_cleared', 'offer_received',
  'offer_accepted', 'offer_declined', 'offer_revoked', 'joining_confirmed',
  'joining_date_updated', 'onboarding_started', 'background_verification',
  'document_verification', 'compensation_confirmation', 'hr_confirmation',
]

/** Interview slot movement — booked, moved or dropped. */
export const INTERVIEW_BOOKING_CLASSIFICATIONS = [
  'interview_shortlisted', 'interview_confirmed', 'interview_rescheduled',
  // An interview mail the reader gave up on. It sounds like the rest: nobody
  // is coming back to it, so the only thing that reaches a person is this.
  'interview_cancelled', 'interview_needs_reading',
]

const ALERT_CLASSIFICATIONS = new Set([
  ...SELECTION_CLASSIFICATIONS,
  ...INTERVIEW_BOOKING_CLASSIFICATIONS,
])

export function isTrackedMailAlert(classification) {
  return ALERT_CLASSIFICATIONS.has(String(classification || ''))
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** '2026-08-04' -> '4 Aug 2026'. Empty when there is no usable day. */
export function notificationDate(value) {
  const parts = String(value || '').trim().slice(0, 10).split('-')
  if (parts.length !== 3 || !parts.every((p) => /^\d+$/.test(p))) return ''
  const [year, month, day] = parts.map(Number)
  if (month < 1 || month > 12) return ''
  return `${day} ${MONTHS[month - 1]} ${year}`
}

/** '17:00' -> '5:00 PM'. Accepts the 12-hour form the model emits too. */
export function notificationTime(value) {
  let clock = String(value || '').trim().toUpperCase()
  let suffix = ''
  for (const meridiem of ['AM', 'PM']) {
    if (clock.endsWith(meridiem)) {
      suffix = meridiem
      clock = clock.slice(0, -2).trim()
      break
    }
  }
  const [rawHour, rawMinute] = clock.split(':')
  if (!/^\d+$/.test(rawHour || '') || !/^\d+$/.test(rawMinute || '')) return ''
  const hour = Number(rawHour)
  const minute = Number(rawMinute)
  if (minute > 59) return ''
  if (suffix) {
    return hour >= 1 && hour <= 12 ? `${hour}:${String(minute).padStart(2, '0')} ${suffix}` : ''
  }
  if (hour > 23) return ''
  return `${hour % 12 || 12}:${String(minute).padStart(2, '0')} ${hour < 12 ? 'AM' : 'PM'}`
}

/**
 * One booking, one notification. Keyed on the booking so a retried delivery
 * replaces its predecessor instead of stacking another copy of the same news.
 */
function bookingTag(payload) {
  const key = payload.booking_id
    || payload.booking_audit_id
    || payload.notification_id
    || payload.event_id
    || payload.candidate_name
  return `interview-booking-${key}`
}

/**
 * What a booking notification should say.
 *
 * Each line is omitted rather than shown half-empty: "undefined · undefined"
 * is worse than a line that only names the candidate. Separate from delivery
 * so the wording can be tested without a Notification implementation.
 */
export function buildBookingNotification(payload = {}) {
  const who = String(payload.candidate_name || '').trim()
  if (!who) return null
  const company = String(payload.company_name || '').trim()
  const day = notificationDate(payload.interview_date)
  const start = notificationTime(payload.start_time || payload.interview_time)
  const end = notificationTime(payload.end_time)
  const round = String(payload.interview_round || '').trim()
  const status = String(payload.status || '').toLowerCase()
  const event = String(payload.event || '')

  const span = start && end ? `${start}–${end}` : start
  const when = [day, span].filter(Boolean).join(' · ')
  const lines = [[who, company].filter(Boolean).join(' · ')]
  const tag = bookingTag(payload)

  if (event === 'slot_booking_blocked' || status === 'blocked') {
    // The blocked case names the time that was refused, then why.
    const reason = String(payload.block_reason || '').trim()
    return {
      title: 'Automatic Booking Blocked',
      body: [[who, day, start].filter(Boolean).join(' · '),
             reason ? `Reason: ${reason}` : null].filter(Boolean).join('\n'),
      tag,
    }
  }
  if (event === 'interview_rescheduled' || status === 'rescheduled') {
    if (when) lines.push(`Changed to ${when}`)
    return { title: 'Interview Rescheduled', body: lines.join('\n'), tag }
  }
  if (when) lines.push(when)
  if (round) lines.push(`Round: ${round}`)
  return { title: 'Interview Auto-Booked', body: lines.join('\n'), tag }
}

const BOOKING_EVENTS = new Set([
  'slot_auto_booked', 'slot_manually_booked', 'interview_rescheduled', 'slot_booking_blocked',
])

/**
 * Desktop notification alongside the sound, so it survives a backgrounded tab.
 *
 * `silent: true` throughout: the TeleAutomation sound is the identity we want
 * recognised, and the operating system's own chime layered on top turns two
 * different alerts into the same noise.
 */
export function showMailAlertNotification(payload = {}) {
  if (typeof Notification === 'undefined') return
  if (Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {})
    return
  }
  if (Notification.permission !== 'granted') return

  // A booking has a confirmed schedule behind it, so it says what and when
  // rather than the generic wording that made every booking look identical.
  const booking = BOOKING_EVENTS.has(String(payload.event || ''))
    ? buildBookingNotification(payload)
    : null
  if (booking) {
    try {
      const note = new Notification(`📅 ${booking.title}`, {
        body: booking.body,
        icon: '/favicon.svg',
        tag: booking.tag,
        requireInteraction: true,
        silent: true,
        data: { url: payload.booking_url || '', candidateId: payload.candidate_id || '' },
      })
      note.onclick = () => {
        // Raising the window is a courtesy and some environments refuse it;
        // that must not stop the click from opening the booking.
        try {
          window.focus?.()
        } catch {
          /* not permitted here */
        }
        try {
          window.dispatchEvent(new CustomEvent('teleautomation:navigate', {
            detail: payload.booking_id
              ? { view: 'daily-ops', bookingId: payload.booking_id, candidateId: payload.candidate_id }
              : { view: 'candidates', candidateId: payload.candidate_id },
          }))
          note.close?.()
        } catch {
          /* informational notification; navigation is best effort */
        }
      }
    } catch {
      /* ignore */
    }
    return
  }

  const title = payload.status
    || String(payload.classification || 'Mail alert').replaceAll('_', ' ')
  const who = payload.candidate_name || 'Candidate'
  const where = payload.company_name ? ` · ${payload.company_name}` : ''
  try {
    new Notification(`📬 ${title}`, {
      body: `${who}${where}`,
      icon: '/favicon.svg',
      tag: `mail-alert-${payload.notification_id || payload.event_id || who}`,
      requireInteraction: true,
      silent: true,
    })
  } catch {
    /* ignore */
  }
}
