/**
 * The expired-Gmail count, on the mailbox summary and in the sidebar.
 *
 * Two Gmail accounts sit in "Reconnect Required" and nothing outside the
 * mailbox table said so, so an expired connection was invisible unless someone
 * opened that page — which is exactly when nobody is looking at it.
 *
 * The count is never recomputed here. `reconnectRequiredMailboxes` is the same
 * selector the mailbox rows and the desktop fault alert already use, so the
 * badge cannot drift from the rows it counts. These tests pin that: the shared
 * selector decides, and a badge that disagrees with a row is a bug.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'

import {
  fetchGmailExpiredCount,
  publishGmailExpired,
  useGmailExpiredCount,
} from './gmailExpired.js'
import { reconnectRequiredMailboxes } from '../utils/mailboxStatus.js'

const HERE = dirname(fileURLToPath(import.meta.url))

/** The two real production mailboxes, in the shape the health endpoint returns. */
const EXPIRED = [
  {
    id: 'mb-1',
    email_address: 'msverma2' + '@' + 'example.com',
    connection_status: 'ERROR',
    last_error_message: 'Gmail authorization expired or was revoked. Reconnect Gmail.',
  },
  {
    id: 'mb-2',
    email_address: 'anjali.adhikari' + '@' + 'example.com',
    connection_status: 'ERROR',
    last_error_message: 'Gmail authorization expired or was revoked. Reconnect Gmail.',
  },
]

const HEALTHY = [
  { id: 'mb-3', email_address: 'ok' + '@' + 'example.com', connection_status: 'ACTIVE', last_error_message: '' },
  { id: 'mb-4', email_address: 'fine' + '@' + 'example.com', connection_status: 'ACTIVE', last_error_message: null },
]

function stubHealth(mailboxes, { ok = true } = {}) {
  const spy = vi.fn(() => Promise.resolve({
    ok,
    status: ok ? 200 : 503,
    json: () => Promise.resolve({ mailboxes }),
  }))
  vi.stubGlobal('fetch', spy)
  return spy
}

function Probe() {
  const expired = useGmailExpiredCount()
  return <span data-testid="count">{expired}</span>
}

beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }) })
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('the count itself', () => {
  it('counts the two production accounts that need reconnecting', async () => {
    stubHealth([...EXPIRED, ...HEALTHY])
    await expect(fetchGmailExpiredCount()).resolves.toBe(2)
  })

  it('is zero when every mailbox is healthy', async () => {
    stubHealth(HEALTHY)
    await expect(fetchGmailExpiredCount()).resolves.toBe(0)
  })

  it('agrees with the selector the mailbox rows use', async () => {
    const mailboxes = [...EXPIRED, ...HEALTHY]
    stubHealth(mailboxes)
    const fetched = await fetchGmailExpiredCount()
    expect(fetched).toBe(reconnectRequiredMailboxes(mailboxes).length)
  })

  it('counts a revoked token, not only an ERROR status', async () => {
    stubHealth([{ id: 'x', connection_status: 'ACTIVE', last_error_message: 'token was revoked' }])
    await expect(fetchGmailExpiredCount()).resolves.toBe(1)
  })

  it('survives a payload with no mailboxes at all', async () => {
    stubHealth(undefined)
    await expect(fetchGmailExpiredCount()).resolves.toBe(0)
  })

  it('rejects rather than inventing a count when health is unavailable', async () => {
    stubHealth([], { ok: false })
    await expect(fetchGmailExpiredCount()).rejects.toThrow()
  })
})

describe('what the badge shows', () => {
  it('reads the real count on mount', async () => {
    stubHealth([...EXPIRED, ...HEALTHY])
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('2'))
  })

  it('starts at zero so nothing flashes before the first read', () => {
    stubHealth([...EXPIRED])
    render(<Probe />)
    expect(screen.getByTestId('count')).toHaveTextContent('0')
  })

  it('keeps the last known count when a read fails', async () => {
    stubHealth([...EXPIRED])
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('2'))
    stubHealth([], { ok: false })
    await act(async () => { publishGmailExpired(2) })
    expect(screen.getByTestId('count')).toHaveTextContent('2')
  })

  it('drops to zero the moment the page says both were reconnected', async () => {
    stubHealth([...EXPIRED])
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('2'))
    await act(async () => { publishGmailExpired(0) })
    expect(screen.getByTestId('count')).toHaveTextContent('0')
  })

  it('follows a single reconnect down to one', async () => {
    stubHealth([...EXPIRED])
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('2'))
    await act(async () => { publishGmailExpired(1) })
    expect(screen.getByTestId('count')).toHaveTextContent('1')
  })

  it('ignores a published value that is not a number', async () => {
    stubHealth([...EXPIRED])
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('2'))
    await act(async () => {
      window.dispatchEvent(new CustomEvent('teleautomation:gmail-expired', {
        detail: { expired: 'lots' },
      }))
    })
    expect(screen.getByTestId('count')).toHaveTextContent('2')
  })

  it('never shows a negative count', async () => {
    stubHealth(HEALTHY)
    render(<Probe />)
    await act(async () => { publishGmailExpired(-4) })
    expect(screen.getByTestId('count')).toHaveTextContent('0')
  })
})

describe('how it is wired into the shell', () => {
  const app = readFileSync(resolve(HERE, '..', 'App.jsx'), 'utf8')

  it('hangs the badge on AI Mail Review', () => {
    expect(app).toMatch(/id: 'ai-recruitment'[^}]*badge: 'gmail-expired'/)
  })

  it('keeps the section icon, unlike the alert-icon items', () => {
    const item = app.match(/\{ id: 'ai-recruitment'[^}]*\}/)[0]
    expect(item).not.toContain('alertIcon')
  })

  it('hides the badge at zero, like every other sidebar badge', () => {
    expect(app).toContain('badgeValue > 0 &&')
  })

  it('says what the number means rather than calling it pending', () => {
    expect(app).toMatch(/accounts need'\s*\}\s*reconnecting/)
    expect(app).toContain("account needs")
  })

  it('is styled as a fault, not as a queue', () => {
    const css = readFileSync(resolve(HERE, '..', 'businessShell.css'), 'utf8')
    expect(css).toMatch(/\.desktop-sidebar__badge--fault \{\s*background: #ef4444;\s*\}/)
  })
})

describe('the mailbox summary card', () => {
  const panel = readFileSync(
    resolve(HERE, '..', 'components', 'RecruitmentMailPanelRedesign.jsx'), 'utf8',
  )

  it('counts the rows the table renders rather than refetching', () => {
    expect(panel).toMatch(
      /reconnectRequiredCount = rows\.filter\(\s*\(row\) => row\.uiStatus === "RECONNECT_REQUIRED",\s*\)\.length/,
    )
  })

  it('sits with the other mailbox metrics', () => {
    const metrics = panel.slice(panel.indexOf('sot-mailbox-metrics'))
    expect(metrics).toContain('label="Reconnect Required"')
    expect(metrics.indexOf('label="Pending Gmail"'))
      .toBeLessThan(metrics.indexOf('label="Reconnect Required"'))
  })

  it('is hidden entirely when nothing is broken', () => {
    expect(panel).toContain('{reconnectRequiredCount > 0 && (')
  })

  it('tells the sidebar about every mailbox, not just the one candidate', () => {
    expect(panel).toMatch(/globalReconnectRequired = allRows\.filter/)
    expect(panel).toMatch(/publishGmailExpired\(globalReconnectRequired\)/)
  })

  it('reuses the shared status rather than re-deriving it', () => {
    // The import now also pulls in reconnectWorklist for the Reconnect tab,
    // which is the same module and the same point: one derivation, not two.
    expect(panel).toMatch(
      /import \{ mailboxUiStatus[^}]*\} from "\.\.\/utils\/mailboxStatus\.js"/,
    )
  })
})
