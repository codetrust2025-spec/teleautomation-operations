/**
 * When a candidate mailbox needs the operator to reconnect Gmail.
 *
 * Extracted verbatim from RecruitmentMailPanelRedesign so the page badge and
 * the global fault alert cannot drift apart: one predicate, two callers. The
 * condition itself is unchanged.
 */
export function needsReconnect(mailbox) {
  // A closed, rejected or dropped candidate's Gmail is history. Reconnecting it
  // achieves nothing, so it is not reconnect work and must not be counted as
  // any. The backend derives this from candidate stage on every read.
  if (mailbox?.monitoring_excluded) return false
  const error = String(mailbox?.last_error_message || '').toLowerCase()
  return (
    mailbox?.connection_status === 'ERROR'
    || error.includes('expired')
    || error.includes('revoked')
  )
}

/**
 * The status the Mailboxes page shows for one mailbox.
 *
 * Connection state is decided BEFORE sync-job state, and that order is the
 * whole point of this function.
 *
 * A failed sync leaves a retry queued, so a mailbox whose Gmail authorization
 * has expired has a job row sitting in QUEUED while its credentials are dead.
 * The page used to test the job first, so that mailbox rendered "Sync Queued /
 * Waiting to start…", never reached RECONNECT_REQUIRED, and never showed the
 * expiry banner or the Reconnect button. The page reported a mailbox as busy
 * working when nothing it queued could succeed.
 *
 * shalini.rao@example.com on 2026-09-02 is the case this came from: the sync
 * created at 07:38:36Z failed on expired Gmail authorization, the retry stayed
 * QUEUED, and the page showed "Sync Queued" while the desktop alert correctly
 * reported the connection as expired. The alert was right; this was wrong.
 */
export function mailboxUiStatus(mailbox, latestSyncStatus) {
  const sync = String(latestSyncStatus || '').toUpperCase()
  // Decided before everything else: a terminal candidate's mailbox is neither
  // monitoring nor broken, so it must not land in the active count or the
  // reconnect count. It keeps its row, its history and its linkage.
  if (mailbox?.monitoring_excluded) return 'MONITORING_ENDED'
  if (needsReconnect(mailbox)) return 'RECONNECT_REQUIRED'
  if (sync === 'RUNNING') return 'SYNCING'
  if (sync === 'QUEUED') return 'SYNC_QUEUED'
  if (!mailbox?.monitoring_enabled) return 'PAUSED'
  return 'CONNECTED'
}

/**
 * The reconnect-required mailboxes, in the one shape both callers use.
 *
 * `/api/candidate-mailboxes/health` and `/api/candidate-mailboxes/overview`
 * return identical mailbox rows — overview is built from the health rows — so
 * selecting here keeps the page badge and the fault alert on one source of
 * truth rather than two hand-rolled projections.
 *
 * Neither payload carries a candidate name (see `mailbox_health_rows`, which
 * selects no name column), so `email` is the label that is always populated.
 * A name is still read when present so a richer payload needs no change here.
 */
export function reconnectRequiredMailboxes(mailboxes) {
  return (Array.isArray(mailboxes) ? mailboxes : [])
    .filter(needsReconnect)
    .map((mailbox) => ({
      id: mailbox?.id,
      candidateId: String(
        mailbox?.canonical_candidate_id || mailbox?.candidate_id || '',
      ),
      email: String(mailbox?.email_address || ''),
      name: String(mailbox?.candidate_name || mailbox?.name || ''),
    }))
}

/**
 * Describe reconnect-required mailboxes so the wording matches what is counted.
 *
 * The unit is the *mailbox*, because each expired mailbox needs its own
 * reconnect. One candidate holding several expired mailboxes is reported as
 * several accounts against one candidate, not as several candidates.
 *
 * This is never called with an empty list — the caller only alerts when at
 * least one mailbox is newly broken — so it can never render a zero count.
 */
export function describeReconnectTargets(rows) {
  const list = Array.isArray(rows) ? rows : []
  if (!list.length) return ''

  const label = (row) => String(row?.name || row?.email || '').trim()
  if (list.length === 1) return label(list[0]) || '1 Gmail account'

  const people = new Set(
    list
      .map((row) => String(row?.candidateId || '').trim() || label(row))
      .filter(Boolean),
  )
  if (people.size > 1) {
    return `${list.length} Gmail accounts across ${people.size} candidates`
  }
  const who = label(list[0])
  return who
    ? `${list.length} Gmail accounts for ${who}`
    : `${list.length} Gmail accounts`
}

/**
 * How long a Gmail grant lasts before Google kills it.
 *
 * The OAuth app is in Testing mode, where refresh tokens expire seven days
 * after consent. That is measured, not assumed: across 54 consecutive
 * reconnects on 21 mailboxes the median gap is 7.0 days and 34 of them fall in
 * the 6-8 day window. Publishing the app to Production is the only thing that
 * changes this, at which point this constant stops mattering rather than
 * becoming wrong.
 */
export const GMAIL_GRANT_DAYS = 7

/** Days since the mailbox was last authorised, or null if that is unknown. */
export function grantAgeDays(mailbox, now = Date.now()) {
  const authorised = Date.parse(mailbox?.authorized_at || '')
  if (!Number.isFinite(authorised)) return null
  return (now - authorised) / 86400000
}

/**
 * Days left on the grant, or null when the mailbox has never been authorised
 * through a route the audit log recorded. Can go negative: a grant past its
 * seventh day is living on borrowed time until something actually uses it.
 */
export function grantDaysRemaining(mailbox, now = Date.now()) {
  const age = grantAgeDays(mailbox, now)
  return age === null ? null : GMAIL_GRANT_DAYS - age
}

/**
 * A working mailbox close enough to expiry to be worth reconnecting now.
 *
 * Deliberately excludes anything already broken: those belong on the other
 * list, and counting them twice would overstate the work.
 */
export function expiringSoon(mailbox, { withinDays = 2, now = Date.now() } = {}) {
  if (mailbox?.monitoring_excluded) return false
  if (needsReconnect(mailbox)) return false
  const remaining = grantDaysRemaining(mailbox, now)
  return remaining !== null && remaining <= withinDays
}

/**
 * The reconnect worklist: what is already broken, and what is about to be.
 *
 * One derivation for both, off the same rows the mailbox table renders, so the
 * list cannot disagree with the badges beside the accounts on it. Sorted by
 * urgency — longest expired first, then soonest to expire.
 */
export function reconnectWorklist(mailboxes, { withinDays = 2, now = Date.now() } = {}) {
  const rows = Array.isArray(mailboxes) ? mailboxes : []
  const decorate = (mailbox) => ({
    ...mailbox,
    grantDaysRemaining: grantDaysRemaining(mailbox, now),
    grantExpiresAt: grantExpiresAt(mailbox),
  })
  // needsReconnect and expiringSoon both already exclude terminal mailboxes,
  // so the worklist inherits the rule rather than restating it.
  const expired = rows.filter(needsReconnect).map(decorate).sort(
    (a, b) => (a.grantDaysRemaining ?? 0) - (b.grantDaysRemaining ?? 0),
  )
  const expiring = rows
    .filter((mailbox) => expiringSoon(mailbox, { withinDays, now }))
    .map(decorate)
    .sort((a, b) => (a.grantDaysRemaining ?? 0) - (b.grantDaysRemaining ?? 0))
  return { expired, expiring, total: expired.length + expiring.length }
}

/** One second, one minute, one hour and one day, in milliseconds. */
const SECOND = 1000
const MINUTE = 60 * SECOND
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/**
 * When this mailbox's grant dies, as a timestamp, or null when unknown.
 *
 * The server sends `grant_expires_at`; the fall back to `authorized_at` plus
 * the grant length is what keeps a page loaded before that field existed --
 * or served by an older release during a deploy -- counting down instead of
 * blanking.
 */
export function grantExpiresAt(mailbox) {
  const sent = Date.parse(mailbox?.grant_expires_at || '')
  if (Number.isFinite(sent)) return sent
  const authorised = Date.parse(mailbox?.authorized_at || '')
  return Number.isFinite(authorised) ? authorised + GMAIL_GRANT_DAYS * DAY : null
}

/**
 * How long is left, in the unit a person reading it would use.
 *
 *   more than a day   Expires in 2 days
 *   less than a day   Expires in 18h 42m
 *   less than an hour Expires in 42m 18s
 *   nothing left      Expired
 *
 * Every part rounds down, so the label never claims more time than remains:
 * 47 hours left is "1 day", not "2 days".
 */
export function formatExpiry(remainingMs) {
  if (!Number.isFinite(remainingMs) || remainingMs <= 0) return 'Expired'
  if (remainingMs < HOUR) {
    const minutes = Math.floor(remainingMs / MINUTE)
    const seconds = Math.floor((remainingMs % MINUTE) / SECOND)
    return `Expires in ${minutes}m ${seconds}s`
  }
  if (remainingMs < DAY) {
    const hours = Math.floor(remainingMs / HOUR)
    const minutes = Math.floor((remainingMs % HOUR) / MINUTE)
    return `Expires in ${hours}h ${minutes}m`
  }
  const days = Math.floor(remainingMs / DAY)
  return `Expires in ${days} day${days === 1 ? '' : 's'}`
}

/**
 * How long to wait before the label could change: a second while seconds are
 * on screen, half a minute while minutes are, five minutes while days are.
 *
 * A single one-second interval would redraw every row on the page all week to
 * change a number once a day.
 */
export function expiryTickMs(remainingMs) {
  if (!Number.isFinite(remainingMs) || remainingMs <= 0) return null
  if (remainingMs < HOUR) return SECOND
  // Near a unit boundary the slow cadence would hold a stale label: at 1h 2s
  // left, a thirty-second step still reads "1h 0m" when 48 seconds remain. So
  // the wait is shortened to land just past the boundary, where the next unit
  // takes over and sets its own pace.
  if (remainingMs < DAY) return Math.min(30 * SECOND, remainingMs - HOUR + 1)
  return Math.min(5 * MINUTE, remainingMs - DAY + 1)
}
