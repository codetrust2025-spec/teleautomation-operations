/**
 * Mail Alerts in the sidebar: quiet when the inbox is clear.
 *
 * The bell is the section's icon and is always drawn, like every other item's.
 * What says something is waiting is the unread badge, which appears with a
 * count and disappears with it -- and the count has to move with the click
 * that marks a mail read, not with the next poll, which is why the page
 * publishes it rather than the sidebar polling alone.
 */
import React from 'react'
import fs from 'node:fs'
import path from 'node:path'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const subscribers = new Set()
vi.mock('./mailEventStream.js', () => ({
  subscribeMailEvents: (handler) => {
    subscribers.add(handler)
    return () => subscribers.delete(handler)
  },
}))

import { publishMailUnread, useMailUnreadCount } from './mailUnread.js'

/** Minimal stand-in for the sidebar row, with the same conditional rules. */
function SidebarRow() {
  const unread = useMailUnreadCount()
  return (
    <button type="button">
      <span data-testid="icon">bell</span>
      <span>Mail Alerts</span>
      {unread > 0 && (
        <span data-testid="badge" aria-label={`${unread} unread`}>
          {unread > 99 ? '99+' : unread}
        </span>
      )}
    </button>
  )
}

function stubSummary(unread) {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => ({ summary: { unread } }),
  })))
}

beforeEach(() => subscribers.clear())
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('nothing unread', () => {
  it('shows the bell and no badge', async () => {
    stubSummary(0)
    render(<SidebarRow />)
    await waitFor(() => expect(screen.getByText('Mail Alerts')).toBeInTheDocument())
    expect(screen.getByTestId('icon').textContent).toBe('bell')
    expect(screen.queryByTestId('badge')).toBeNull()
  })

  it('keeps the icon slot so the label stays aligned', async () => {
    // The slot is a fixed 20px in CSS; removing the element would shift this
    // row's text out of line with every other one.
    stubSummary(0)
    render(<SidebarRow />)
    await waitFor(() => expect(screen.getByTestId('icon')).toBeInTheDocument())
  })
})

describe('something unread', () => {
  it('shows the bell and the real count', async () => {
    stubSummary(4)
    render(<SidebarRow />)
    await waitFor(() => expect(screen.getByTestId('badge').textContent).toBe('4'))
    expect(screen.getByTestId('icon').textContent).toBe('bell')
  })

  it('caps the badge at 99+', async () => {
    stubSummary(140)
    render(<SidebarRow />)
    await waitFor(() => expect(screen.getByTestId('badge').textContent).toBe('99+'))
  })

  it('labels the count for screen readers', async () => {
    stubSummary(3)
    render(<SidebarRow />)
    await waitFor(() => expect(screen.getByLabelText('3 unread')).toBeInTheDocument())
  })
})

describe('the count follows the click', () => {
  it('drops as soon as the page publishes a lower count', async () => {
    stubSummary(2)
    render(<SidebarRow />)
    await waitFor(() => expect(screen.getByTestId('badge').textContent).toBe('2'))

    act(() => publishMailUnread(1))
    await waitFor(() => expect(screen.getByTestId('badge').textContent).toBe('1'))
  })

  it('clears the badge, not the bell, when the last one is read', async () => {
    stubSummary(1)
    render(<SidebarRow />)
    await waitFor(() => expect(screen.getByTestId('badge')).toBeInTheDocument())

    act(() => publishMailUnread(0))
    await waitFor(() => expect(screen.queryByTestId('badge')).toBeNull())
    expect(screen.getByTestId('icon').textContent).toBe('bell')
  })

  it('comes back when a mail is marked unread again', async () => {
    stubSummary(0)
    render(<SidebarRow />)
    await waitFor(() => expect(screen.queryByTestId('badge')).toBeNull())

    act(() => publishMailUnread(1))
    await waitFor(() => expect(screen.getByTestId('badge').textContent).toBe('1'))
    expect(screen.getByTestId('icon').textContent).toBe('bell')
  })

  it('never goes negative', async () => {
    stubSummary(0)
    render(<SidebarRow />)
    act(() => publishMailUnread(-5))
    await waitFor(() => expect(screen.queryByTestId('badge')).toBeNull())
  })
})

describe('a mail arriving while the page is closed', () => {
  it('re-reads the summary on a mail socket event', async () => {
    stubSummary(0)
    render(<SidebarRow />)
    await waitFor(() => expect(screen.queryByTestId('badge')).toBeNull())

    stubSummary(2)
    await act(async () => { subscribers.forEach(handler => handler({})) })
    await waitFor(() => expect(screen.getByTestId('badge').textContent).toBe('2'))
  })
})

describe('the real sidebar is wired to this', () => {
  // The rows above drive a stand-in, so on their own they would keep passing
  // if App.jsx stopped using the hook. These read the shell itself.
  const app = fs.readFileSync(path.join(__dirname, '..', 'App.jsx'), 'utf8')

  it('takes its count from the shared hook', () => {
    expect(app).toContain("import { useMailUnreadCount } from './notifications/mailUnread.js'")
    expect(app).toContain('const mailUnread = useMailUnreadCount()')
  })

  it('feeds that count into the Mail Alerts badge', () => {
    expect(app).toMatch(/id: 'mail-notifications'[^}]*badge: 'mail'/)
    expect(app).toMatch(/item\.badge === 'mail'\s*\?\s*mailUnread/)
  })

  it('draws the bell always and leaves the signal to the badge', () => {
    // The bell used to vanish at zero, which left the row unaligned with the
    // rest of the sidebar; every item now draws its icon unconditionally.
    expect(app).toMatch(/id: 'mail-notifications'[^}]*icon: 'bell'/)
    expect(app).not.toContain('alertIcon')
    expect(app).toContain('<Icon name={item.icon}')
  })

  it('keeps the icon slot in the markup either way', () => {
    expect(app).not.toMatch(/\{showIcon && <span className="desktop-sidebar__link-icon"/)
  })

  it('publishes from every place the mail page learns a new count', () => {
    const mail = fs.readFileSync(
      path.join(__dirname, '..', 'components', 'MailMonitoringNotifications.jsx'), 'utf8',
    )
    const writes = (mail.match(/setSummary\(/g) || []).length
    const publishes = (mail.match(/publishMailUnread\(/g) || []).length
    expect(writes).toBeGreaterThan(0)
    expect(publishes).toBe(writes)
  })

  it('gives the unread badge its own tone without touching the shared one', () => {
    const css = fs.readFileSync(path.join(__dirname, '..', 'businessShell.css'), 'utf8')
    expect(css).toMatch(/\.desktop-sidebar__badge--unread \{\s*background: #ef4444;\s*\}/)
    // The pending badges keep the colour and geometry they had.
    expect(css).toMatch(/\.desktop-sidebar__badge \{[^}]*background: #625bf6/)
    expect(css).toMatch(/\.desktop-sidebar__badge \{[^}]*min-width: 21px/)
  })
})

describe('a failing summary does not break the shell', () => {
  it('leaves the row rendered with no badge', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline') }))
    render(<SidebarRow />)
    await waitFor(() => expect(screen.getByText('Mail Alerts')).toBeInTheDocument())
    expect(screen.queryByTestId('badge')).toBeNull()
  })

  it('keeps the last known count when a later read fails', async () => {
    stubSummary(3)
    render(<SidebarRow />)
    await waitFor(() => expect(screen.getByTestId('badge').textContent).toBe('3'))

    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline') }))
    await act(async () => { subscribers.forEach(handler => handler({})) })
    expect(screen.getByTestId('badge').textContent).toBe('3')
  })
})
