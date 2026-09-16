/**
 * A closed candidate's mailbox is not active work.
 *
 * Ram Charan M S was `completed`, yet his Gmail still counted towards Reconnect
 * Required — creating reconnect work for a candidate who had left the pipeline.
 * Both mailboxes showing Reconnect Required in production turned out to be
 * terminal: one `completed`, one `dropped`.
 *
 * The backend derives `monitoring_excluded` from candidate stage on every read.
 * Everything here inherits that one flag rather than re-deriving it, so the
 * counts, the worklist and the badge cannot disagree.
 */
import { describe, it, expect } from 'vitest'
import {
  needsReconnect,
  mailboxUiStatus,
  expiringSoon,
  reconnectWorklist,
} from './mailboxStatus.js'

const NOW = Date.parse('2026-09-09T12:00:00Z')
const daysAgo = (d) => new Date(NOW - d * 86400000).toISOString()

const closed = (over = {}) => ({
  id: 'mb-closed',
  email_address: 'msverma2' + '@' + 'example.com',
  connection_status: 'ERROR',
  last_error_message: 'Gmail authorization expired or was revoked.',
  monitoring_excluded: true,
  authorized_at: daysAgo(14.6),
  ...over,
})

const active = (over = {}) => ({
  id: 'mb-active',
  email_address: 'live' + '@' + 'example.com',
  connection_status: 'ERROR',
  last_error_message: 'Gmail authorization expired or was revoked.',
  monitoring_excluded: false,
  authorized_at: daysAgo(14.6),
  ...over,
})

describe('a terminal candidate is not reconnect work', () => {
  it('does not need reconnecting even when the token is dead', () => {
    expect(needsReconnect(closed())).toBe(false)
  })

  it('while an active candidate in the same state still does', () => {
    expect(needsReconnect(active())).toBe(true)
  })

  it('gets its own status rather than counting as monitoring or broken', () => {
    expect(mailboxUiStatus(closed(), '')).toBe('MONITORING_ENDED')
  })

  it('is not counted as Monitoring Active', () => {
    expect(mailboxUiStatus(closed({ connection_status: 'CONNECTED', last_error_message: '' }), ''))
      .not.toBe('CONNECTED')
  })

  it('outranks a running sync, so it cannot show as Syncing', () => {
    expect(mailboxUiStatus(closed(), 'RUNNING')).toBe('MONITORING_ENDED')
  })

  it('never appears as expiring soon', () => {
    expect(expiringSoon(closed({
      connection_status: 'CONNECTED', last_error_message: '', authorized_at: daysAgo(6.5),
    }), { now: NOW })).toBe(false)
  })
})

describe('the reconnect worklist', () => {
  it('leaves terminal mailboxes out of both groups', () => {
    const list = reconnectWorklist([
      closed(),
      closed({ id: 'mb-dropped', monitoring_excluded: true }),
      active(),
    ], { now: NOW })
    expect(list.expired.map(m => m.id)).toEqual(['mb-active'])
    expect(list.expiring).toEqual([])
    expect(list.total).toBe(1)
  })

  it('is empty when every broken mailbox belongs to a closed candidate', () => {
    // Exactly production: both Reconnect Required rows were terminal.
    expect(reconnectWorklist([closed(), closed({ id: 'mb-2' })], { now: NOW }).total).toBe(0)
  })
})

describe('active monitoring is untouched', () => {
  it('an in-progress candidate still monitors normally', () => {
    expect(mailboxUiStatus({
      connection_status: 'CONNECTED', last_error_message: '',
      monitoring_enabled: true, monitoring_excluded: false,
    }, '')).toBe('CONNECTED')
  })

  it('a mailbox with no flag at all is treated as active', () => {
    // Fail-safe: an unresolved candidate must keep being monitored.
    expect(mailboxUiStatus(
      { connection_status: 'CONNECTED', last_error_message: '', monitoring_enabled: true }, '',
    )).toBe('CONNECTED')
    expect(needsReconnect({ connection_status: 'ERROR', last_error_message: 'expired' }))
      .toBe(true)
  })

  it('still reports sync states for an active mailbox', () => {
    expect(mailboxUiStatus(
      { connection_status: 'CONNECTED', last_error_message: '', monitoring_enabled: true }, 'RUNNING',
    )).toBe('SYNCING')
  })
})
