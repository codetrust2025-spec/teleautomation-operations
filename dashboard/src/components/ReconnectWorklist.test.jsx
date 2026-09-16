/**
 * The reconnect worklist.
 *
 * The OAuth app is in Testing mode, so Google expires refresh tokens seven days
 * after consent — measured across 54 consecutive reconnects, median gap 7.0
 * days, 34 of them in the 6–8 day window. With nineteen connected mailboxes
 * that is roughly three reconnects a day, and no code change prevents them
 * until the app is published to Production. What this screen changes is that an
 * operator sees them together rather than a banner at a time.
 *
 * The two groups are kept apart on purpose: already broken is work that must
 * happen, about to break is work worth batching with it. Both come from one
 * derivation over the same rows the mailbox table renders, so the list cannot
 * disagree with the badges next to those accounts.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import { ReconnectWorklist } from './ReconnectWorklist.jsx'
import {
  GMAIL_GRANT_DAYS,
  grantDaysRemaining,
  expiringSoon,
  reconnectWorklist,
} from '../utils/mailboxStatus.js'

const NOW = Date.parse('2026-09-09T12:00:00Z')
const daysAgo = (days) => new Date(NOW - days * 86400000).toISOString()

/** A row shaped like the mailbox table's, which is what the tab passes in. */
const row = (name, email, authorisedDaysAgo, { broken = false } = {}) => ({
  candidate: { id: `c-${name}`, name },
  mailbox: {
    id: `mb-${name}`,
    email_address: email,
    authorized_at: authorisedDaysAgo === null ? null : daysAgo(authorisedDaysAgo),
    connection_status: broken ? 'ERROR' : 'CONNECTED',
    last_error_message: broken ? 'Gmail authorization expired or was revoked.' : '',
  },
})

// The fixtures are anchored to NOW, but the component asks the real clock:
// `grantDaysRemaining(mailbox, Date.now())`. On 2026-09-09 the two agreed and
// on 2026-09-10 they were a day apart, so "expires in 2 days" became "expires
// in 1 day" and a deploy failed on a test that had nothing to do with the
// change. Freezing Date -- and only Date, so React's own timers still run --
// makes these assertions mean the same thing on any day.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
})

afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

describe('how long a grant has left', () => {
  it('lasts the seven days Google actually gives it', () => {
    expect(GMAIL_GRANT_DAYS).toBe(7)
  })

  it('counts down from the day it was authorised', () => {
    const mailbox = { authorized_at: daysAgo(4.2) }
    expect(grantDaysRemaining(mailbox, NOW)).toBeCloseTo(2.8, 1)
  })

  it('goes negative once the week is up', () => {
    expect(grantDaysRemaining({ authorized_at: daysAgo(9) }, NOW)).toBeCloseTo(-2, 1)
  })

  it('says nothing when the mailbox was never authorised through a recorded route', () => {
    expect(grantDaysRemaining({ authorized_at: null }, NOW)).toBeNull()
    expect(grantDaysRemaining({}, NOW)).toBeNull()
    expect(grantDaysRemaining({ authorized_at: 'not a date' }, NOW)).toBeNull()
  })
})

describe('who counts as expiring soon', () => {
  const soon = (mailbox) => expiringSoon(mailbox, { now: NOW })

  it('catches a grant with under two days left', () => {
    expect(soon({ authorized_at: daysAgo(5.5), connection_status: 'CONNECTED' })).toBe(true)
  })

  it('leaves a fresh grant alone', () => {
    expect(soon({ authorized_at: daysAgo(1), connection_status: 'CONNECTED' })).toBe(false)
  })

  it('does not double-count one that has already broken', () => {
    // It belongs on the other list; counting it twice overstates the work.
    expect(soon({
      authorized_at: daysAgo(6.5),
      connection_status: 'ERROR',
      last_error_message: 'Gmail authorization expired or was revoked.',
    })).toBe(false)
  })

  it('says nothing about a mailbox with no authorisation on record', () => {
    expect(soon({ authorized_at: null, connection_status: 'CONNECTED' })).toBe(false)
  })
})

describe('the worklist', () => {
  const mailboxes = [
    { id: 'a', authorized_at: daysAgo(6.3), connection_status: 'ERROR', last_error_message: 'expired' },
    { id: 'b', authorized_at: daysAgo(14.6), connection_status: 'ERROR', last_error_message: 'expired' },
    { id: 'c', authorized_at: daysAgo(4.2), connection_status: 'CONNECTED', last_error_message: '' },
    { id: 'd', authorized_at: daysAgo(6.1), connection_status: 'CONNECTED', last_error_message: '' },
    { id: 'e', authorized_at: daysAgo(0.2), connection_status: 'CONNECTED', last_error_message: '' },
  ]

  it('splits broken from nearly-broken', () => {
    const { expired, expiring, total } = reconnectWorklist(mailboxes, { now: NOW })
    expect(expired.map(m => m.id)).toEqual(['b', 'a'])
    expect(expiring.map(m => m.id)).toEqual(['d'])
    expect(total).toBe(3)
  })

  it('puts the longest expired first', () => {
    const { expired } = reconnectWorklist(mailboxes, { now: NOW })
    expect(expired[0].id).toBe('b')
  })

  it('leaves healthy mailboxes off it entirely', () => {
    const ids = reconnectWorklist(mailboxes, { now: NOW })
    expect([...ids.expired, ...ids.expiring].map(m => m.id)).not.toContain('e')
  })

  it('widens with the window', () => {
    const { expiring } = reconnectWorklist(mailboxes, { now: NOW, withinDays: 3 })
    expect(expiring.map(m => m.id)).toEqual(['d', 'c'])
  })

  it('survives a payload that is not a list', () => {
    expect(reconnectWorklist(undefined).total).toBe(0)
    expect(reconnectWorklist(null).total).toBe(0)
  })
})

describe('what the screen shows', () => {
  const rows = [
    row('Ram Charan', 'msverma2' + '@' + 'example.com', 14.6, { broken: true }),
    row('Anjali', 'anjali.adhikari' + '@' + 'example.com', 6.3, { broken: true }),
    row('Deepa', 'deepa.shetty' + '@' + 'example.com', 6.1),
    row('Aniket', 'aniket.rane2' + '@' + 'example.com', 0.2),
  ]

  it('lists both groups with their counts', () => {
    render(<ReconnectWorklist rows={rows} busy={false} onAction={() => {}} />)
    expect(screen.getByText('Reconnect required')).toBeInTheDocument()
    expect(screen.getByText('Expiring soon')).toBeInTheDocument()
  })

  it('names the accounts and the people behind them', () => {
    render(<ReconnectWorklist rows={rows} busy={false} onAction={() => {}} />)
    expect(screen.getByText('msverma2' + '@' + 'example.com')).toBeInTheDocument()
    expect(screen.getByText('Ram Charan')).toBeInTheDocument()
  })

  it('says how long each has been broken', () => {
    render(<ReconnectWorklist rows={rows} busy={false} onAction={() => {}} />)
    expect(screen.getByText(/expired 8 days ago/)).toBeInTheDocument()
  })

  it('says today rather than in 1 day for a grant with hours left', () => {
    // Deepa was authorised 6.1 days ago, so 0.9 days remain. "in 1 day"
    // would read as tomorrow and buy an operator a day that does not exist.
    render(<ReconnectWorklist rows={rows} busy={false} onAction={() => {}} />)
    expect(screen.getByText('expires today')).toBeInTheDocument()
  })

  it('counts the days when there is more than one left', () => {
    render(
      <ReconnectWorklist
        rows={[row('Arun', 'arunkumar.pillai' + '@' + 'example.com', 5.2)]}
        busy={false}
        onAction={() => {}}
        withinDays={3}
      />,
    )
    expect(screen.getByText('expires in 2 days')).toBeInTheDocument()
  })

  it('leaves a healthy account off the list', () => {
    render(<ReconnectWorklist rows={rows} busy={false} onAction={() => {}} />)
    expect(screen.queryByText('aniket.rane2' + '@' + 'example.com')).toBeNull()
  })

  it('reconnects through the action the table already uses', () => {
    const onAction = vi.fn()
    render(<ReconnectWorklist rows={rows} busy={false} onAction={onAction} />)
    fireEvent.click(screen.getAllByText('Reconnect Gmail')[0])
    expect(onAction).toHaveBeenCalledWith('reconnect', expect.objectContaining({
      candidate: expect.objectContaining({ name: 'Ram Charan' }),
    }))
  })

  it('cannot be clicked while the page is busy', () => {
    render(<ReconnectWorklist rows={rows} busy onAction={() => {}} />)
    for (const button of screen.getAllByText('Reconnect Gmail')) {
      expect(button).toBeDisabled()
    }
  })

  it('says so when there is nothing to do', () => {
    render(<ReconnectWorklist rows={[row('Fine', 'ok' + '@' + 'example.com', 0.5)]}
      busy={false} onAction={() => {}} />)
    expect(screen.getByText('No Gmail account needs reconnecting.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('shows only the broken group when nothing is near expiry', () => {
    render(<ReconnectWorklist rows={[rows[0]]} busy={false} onAction={() => {}} />)
    expect(screen.getByText('Reconnect required')).toBeInTheDocument()
    expect(screen.queryByText('Expiring soon')).toBeNull()
  })
})

describe('how it is wired into the page', () => {
  const panel = require('node:fs').readFileSync(
    require('node:path').join(__dirname, 'RecruitmentMailPanelRedesign.jsx'), 'utf8',
  )

  it('is a third tab beside Linked and Pending Gmail', () => {
    expect(panel).toContain('setMailboxListMode("reconnect")')
    expect(panel.indexOf('Pending Gmail <span>'))
      .toBeLessThan(panel.indexOf('Reconnect <span>'))
  })

  it('counts what the list actually holds', () => {
    expect(panel).toContain('reconnectWorklistTotal')
    expect(panel).toContain('reconnectWorklist(')
  })

  it('sees every mailbox, not just the filtered view', () => {
    expect(panel).toMatch(/<ReconnectWorklist\s+rows=\{allRows\}/)
  })

  it('reuses the existing reconnect action', () => {
    const block = panel.slice(panel.indexOf('<ReconnectWorklist'))
    expect(block.slice(0, 200)).toContain('onAction={mailboxAction}')
  })
})
