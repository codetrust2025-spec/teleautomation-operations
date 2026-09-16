/**
 * The Shalini mismatch: one mailbox, two screens, two different answers.
 *
 * On 2026-09-02 the desktop alert said "Gmail connection expired" for
 * shalini.rao@example.com while the Mailboxes page showed the same mailbox as
 * "Sync Queued / Waiting to start…". Production timestamps:
 *
 *   07:23:37Z  last successful sync
 *   07:38:36Z  sync job created; first attempt fails with
 *              "Gmail authorization expired or was revoked", and
 *              candidate_mailboxes.connection_status becomes ERROR
 *   ~07:39Z    health poll sees ERROR, desktop alert fires   <- correct
 *              the retry sits QUEUED, page renders "Sync Queued"  <- wrong
 *   08:08:39Z  fifth attempt, DEAD_LETTER
 *   08:11:54Z  a new retry is QUEUED for 09:15:54Z
 *
 * The alert was right and the page was wrong, so these tests pin the page to
 * the connection state, and pin the alert to firing whenever the connection is
 * genuinely broken -- including while a doomed retry is queued, which is
 * exactly when the page used to claim everything was fine.
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { installRecordingAudioStub } from './audioTestStub.js'
import { __resetNotificationEvents, notifyGmailReconnect } from './notificationEvents.js'
import { mailboxUiStatus, reconnectRequiredMailboxes } from '../utils/mailboxStatus.js'

const EXPIRED = 'Gmail authorization expired or was revoked. Reconnect Gmail to resume automatic monitoring and historical rescans.'
const SHALINI = '95532928-50fb-4139-acf4-8f7a541144ec'

const expiredMailbox = (overrides = {}) => ({
  id: SHALINI,
  email_address: 'shalini.rao@example.com',
  connection_status: 'ERROR',
  monitoring_enabled: true,
  last_error_code: 'RuntimeError',
  last_error_message: EXPIRED,
  ...overrides,
})

const recoveredMailbox = (overrides = {}) => ({
  id: SHALINI,
  email_address: 'shalini.rao@example.com',
  connection_status: 'CONNECTED',
  monitoring_enabled: true,
  last_error_code: null,
  last_error_message: null,
  ...overrides,
})

beforeEach(() => {
  installRecordingAudioStub()
  __resetNotificationEvents()
  sessionStorage.clear()
})

describe('the Mailboxes page reports the connection, not the retry', () => {
  it('shows RECONNECT_REQUIRED for an expired mailbox whose retry is queued', () => {
    // The exact production shape at 07:39Z, and the reason the two screens
    // disagreed: the job was QUEUED while the connection was dead.
    expect(mailboxUiStatus(expiredMailbox(), 'QUEUED')).toBe('RECONNECT_REQUIRED')
  })

  it('shows RECONNECT_REQUIRED even while a retry is actually running', () => {
    expect(mailboxUiStatus(expiredMailbox(), 'RUNNING')).toBe('RECONNECT_REQUIRED')
  })

  it('still reports a queued sync on a healthy mailbox', () => {
    // The fix must not swallow the ordinary case: a connected mailbox with a
    // queued job is genuinely "Sync Queued".
    expect(mailboxUiStatus(recoveredMailbox(), 'QUEUED')).toBe('SYNC_QUEUED')
    expect(mailboxUiStatus(recoveredMailbox(), 'RUNNING')).toBe('SYNCING')
    expect(mailboxUiStatus(recoveredMailbox(), 'COMPLETED')).toBe('CONNECTED')
    expect(mailboxUiStatus(recoveredMailbox({ monitoring_enabled: false }), 'COMPLETED')).toBe('PAUSED')
  })

  it('treats an expired error message as reconnect even if the status lags', () => {
    // needsReconnect matches on the message too, because the status and the
    // message are written by different paths.
    expect(mailboxUiStatus(
      expiredMailbox({ connection_status: 'CONNECTED' }), 'QUEUED',
    )).toBe('RECONNECT_REQUIRED')
  })
})

describe('a stale expired alert does not fire after the mailbox recovers', () => {
  it('does not alert when the snapshot shows the mailbox has recovered', () => {
    // The required behaviour: an expired mailbox is picked up, it recovers
    // before the alert is raised, and the alert must not fire for a connection
    // that is working again.
    const derived = reconnectRequiredMailboxes([expiredMailbox()])
    expect(derived).toHaveLength(1)
    expect(notifyGmailReconnect(derived, [recoveredMailbox()])).toBe(false)
  })

  it('does not alert when the recovered mailbox is queued or syncing again', () => {
    // Sync Queued, Syncing Emails and Monitoring Active all mean the
    // connection works, so none of them may carry an expiry alert.
    const derived = reconnectRequiredMailboxes([expiredMailbox()])
    for (const status of ['QUEUED', 'RUNNING', 'COMPLETED']) {
      __resetNotificationEvents()
      sessionStorage.clear()
      const recovered = recoveredMailbox()
      expect(mailboxUiStatus(recovered, status)).not.toBe('RECONNECT_REQUIRED')
      expect(notifyGmailReconnect(derived, [recovered])).toBe(false)
    }
  })

  it('alerts once, then stays quiet, then alerts again on a genuine new failure', () => {
    const broken = [expiredMailbox()]
    const derived = reconnectRequiredMailboxes(broken)
    expect(notifyGmailReconnect(derived, broken)).toBe(true)
    expect(notifyGmailReconnect(derived, broken)).toBe(false)
    // recovery clears it from the remembered set
    expect(notifyGmailReconnect([], [recoveredMailbox()])).toBe(false)
    // and a new failure is a new alert
    expect(notifyGmailReconnect(derived, broken)).toBe(true)
  })
})

describe('a genuine expiry is never suppressed', () => {
  it('alerts for an expired mailbox whose retry is queued', () => {
    // This is the Shalini case. The page called it "Sync Queued"; suppressing
    // on that label would have silenced the one alert that was correct.
    const broken = [expiredMailbox()]
    expect(notifyGmailReconnect(reconnectRequiredMailboxes(broken), broken)).toBe(true)
  })

  it('alerts when no snapshot is supplied at all', () => {
    // Fails open. No evidence of recovery is not evidence of recovery, and a
    // missed expiry is worse than a repeated one.
    expect(notifyGmailReconnect([{ id: SHALINI }])).toBe(true)
  })

  it('alerts when the mailbox has dropped out of the snapshot', () => {
    // A mailbox missing from the payload has not been shown to have
    // recovered, so it is left alone rather than silently suppressed.
    expect(notifyGmailReconnect([{ id: SHALINI }], [{ id: 'some-other-mailbox' }])).toBe(true)
  })

  it('alerts for a revoked mailbox as well as an expired one', () => {
    const revoked = [expiredMailbox({ last_error_message: 'Token was revoked by the user' })]
    expect(notifyGmailReconnect(reconnectRequiredMailboxes(revoked), revoked)).toBe(true)
  })
})
