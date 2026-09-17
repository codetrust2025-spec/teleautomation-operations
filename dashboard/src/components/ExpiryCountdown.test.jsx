/**
 * The Gmail grant countdown.
 *
 * Nineteen mailboxes whose refresh tokens die seven days after consent means an
 * operator is always deciding "is this one worth reconnecting now?". "expires
 * today" answered that badly at both ends of the day, and the page had to be
 * reloaded for even that to change.
 *
 * The deadline is the server's (`grant_expires_at`); only the clock is local.
 * These pin the four things the label has to get right: the unit it counts in,
 * that it never promises more time than remains, that it moves on its own, and
 * that it comes back correct after the tab has been hidden.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'

import { ExpiryCountdown } from './ExpiryCountdown.jsx'
import { expiryTickMs, formatExpiry, grantExpiresAt } from '../utils/mailboxStatus.js'

const NOW = Date.parse('2026-09-09T12:00:00Z')
const SECOND = 1000
const MINUTE = 60 * SECOND
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('the unit it counts in', () => {
  it.each([
    [2 * DAY + 3 * HOUR, 'Expires in 2 days'],
    [DAY + HOUR, 'Expires in 1 day'],
    [18 * HOUR + 42 * MINUTE, 'Expires in 18h 42m'],
    [42 * MINUTE + 18 * SECOND, 'Expires in 42m 18s'],
    [0, 'Expired'],
    [-5 * DAY, 'Expired'],
  ])('%dms reads as %s', (remaining, expected) => {
    expect(formatExpiry(remaining)).toBe(expected)
  })

  it('switches unit exactly at a day and at an hour, not near them', () => {
    expect(formatExpiry(DAY)).toBe('Expires in 1 day')
    expect(formatExpiry(DAY - SECOND)).toBe('Expires in 23h 59m')
    expect(formatExpiry(HOUR)).toBe('Expires in 1h 0m')
    expect(formatExpiry(HOUR - SECOND)).toBe('Expires in 59m 59s')
  })

  it('never promises time that is not there', () => {
    // 47 hours is not two days, and 59 minutes is not an hour.
    expect(formatExpiry(47 * HOUR)).toBe('Expires in 1 day')
    expect(formatExpiry(59 * MINUTE + 59 * SECOND)).toBe('Expires in 59m 59s')
  })

  it('says nothing it cannot know', () => {
    expect(formatExpiry(NaN)).toBe('Expired')
    expect(formatExpiry(undefined)).toBe('Expired')
  })
})

describe('where the deadline comes from', () => {
  it('uses the timestamp the server sent', () => {
    const expires = new Date(NOW + 3 * DAY).toISOString()
    expect(grantExpiresAt({ grant_expires_at: expires })).toBe(NOW + 3 * DAY)
  })

  it('falls back to the grant length over the authorisation, for an older payload', () => {
    // A page loaded before the field existed, or served by the old release
    // mid-deploy, keeps counting instead of blanking.
    const authorised = new Date(NOW - 2 * DAY).toISOString()
    expect(grantExpiresAt({ authorized_at: authorised })).toBe(NOW + 5 * DAY)
  })

  it('prefers the server even when both are present and disagree', () => {
    const value = grantExpiresAt({
      grant_expires_at: new Date(NOW + HOUR).toISOString(),
      authorized_at: new Date(NOW).toISOString(),
    })
    expect(value).toBe(NOW + HOUR)
  })

  it('is null when the mailbox was never authorised through a recorded route', () => {
    expect(grantExpiresAt({})).toBeNull()
    expect(grantExpiresAt(null)).toBeNull()
  })
})

describe('moving on its own', () => {
  it('counts down without the page being reloaded', () => {
    render(<ExpiryCountdown expiresAt={NOW + 42 * MINUTE + 18 * SECOND} />)
    expect(screen.getByText('Expires in 42m 18s')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(3 * SECOND)
    })

    expect(screen.getByText('Expires in 42m 15s')).toBeInTheDocument()
  })

  it('crosses from minutes into seconds by itself', () => {
    render(<ExpiryCountdown expiresAt={NOW + HOUR + 2 * SECOND} />)
    expect(screen.getByText('Expires in 1h 0m')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(3 * SECOND)
    })

    expect(screen.getByText('Expires in 59m 59s')).toBeInTheDocument()
  })

  it('reaches Expired on its own and then stops', () => {
    render(<ExpiryCountdown expiresAt={NOW + 2 * SECOND} />)

    act(() => {
      vi.advanceTimersByTime(3 * SECOND)
    })
    expect(screen.getByText('Expired')).toBeInTheDocument()

    // Nothing is left scheduled, so an expired row costs nothing for the rest
    // of the day.
    expect(vi.getTimerCount()).toBe(0)
  })

  it('redraws a second apart near the end and five minutes apart far from it', () => {
    expect(expiryTickMs(30 * MINUTE)).toBe(SECOND)
    expect(expiryTickMs(6 * HOUR)).toBe(30 * SECOND)
    expect(expiryTickMs(3 * DAY)).toBe(5 * MINUTE)
    expect(expiryTickMs(-1)).toBeNull()
  })

  it('waits only as far as the next unit boundary', () => {
    // Otherwise the label sits on "1h 0m" for another half minute after it has
    // stopped being true.
    expect(expiryTickMs(HOUR + 2 * SECOND)).toBe(2 * SECOND + 1)
    expect(expiryTickMs(DAY + 3 * SECOND)).toBe(3 * SECOND + 1)
  })
})

describe('after the tab has been away', () => {
  it('is correct the moment the tab is shown again', () => {
    // A hidden tab gets about one timer a minute and a sleeping laptop none,
    // so the last minute of a grant is exactly when the chain cannot be
    // trusted. Being shown again recomputes immediately.
    render(<ExpiryCountdown expiresAt={NOW + 20 * MINUTE} />)
    expect(screen.getByText('Expires in 20m 0s')).toBeInTheDocument()

    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    act(() => {
      vi.setSystemTime(NOW + 19 * MINUTE)
    })
    hidden.mockReturnValue(false)
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(screen.getByText('Expires in 1m 0s')).toBeInTheDocument()
    hidden.mockRestore()
  })

  it('ignores a visibility event that hid the tab', () => {
    render(<ExpiryCountdown expiresAt={NOW + 20 * MINUTE} />)
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)

    act(() => {
      vi.setSystemTime(NOW + 19 * MINUTE)
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(screen.getByText('Expires in 20m 0s')).toBeInTheDocument()
    hidden.mockRestore()
  })
})

describe('what it will not claim', () => {
  it('says unknown rather than guessing when there is no deadline', () => {
    render(<ExpiryCountdown expiresAt={null} />)
    expect(screen.getByText('unknown')).toBeInTheDocument()
  })

  it('lets the caller say something truer than a countdown', () => {
    render(<ExpiryCountdown expiresAt={NOW + DAY} override="authorisation revoked" />)
    expect(screen.getByText('authorisation revoked')).toBeInTheDocument()
    expect(screen.queryByText(/Expires in/)).toBeNull()
  })

  it('leaves the exact deadline readable in the markup', () => {
    const { container } = render(<ExpiryCountdown expiresAt={NOW + DAY} />)
    expect(container.querySelector('time').getAttribute('dateTime'))
      .toBe(new Date(NOW + DAY).toISOString())
  })
})
