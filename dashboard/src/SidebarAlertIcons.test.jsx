/**
 * Sidebar icons and counts.
 *
 * Every item draws its own line icon, always. Daily Ops and Mail Alerts used to
 * drop their glyph whenever their count was zero (`alertIcon`), which left those
 * two rows unaligned and textless beside icons that never moved -- the sidebar
 * looked inconsistent precisely when nothing was wrong. The count is the
 * signal: the badge appears with it and goes when it reaches zero.
 *
 * The count behind Daily Ops also has to move when attendance changes. Its
 * provider polls every two minutes, so without a signal the badge sat on a
 * stale number for up to that long after a status was set.
 */
import React from 'react'
import fs from 'node:fs'
import path from 'node:path'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { publishPendingWorkChanged } from './dailyOps/PendingWorksProvider.jsx'
import { Icon, ICON_NAMES } from './components/ui/Icon.jsx'

const app = fs.readFileSync(path.join(__dirname, 'App.jsx'), 'utf8')
const provider = fs.readFileSync(
  path.join(__dirname, 'dailyOps', 'PendingWorksProvider.jsx'), 'utf8',
)
const roster = fs.readFileSync(
  path.join(__dirname, 'dailyOps', 'InterviewRoster.jsx'), 'utf8',
)

/** The sidebar's own rules, applied to one row. */
function SidebarRow({ item, badgeValue }) {
  return (
    <button type="button">
      <span data-testid="icon"><Icon name={item.icon} /></span>
      <span>{item.label}</span>
      {badgeValue > 0 && (
        <span data-testid="badge" aria-label={`${badgeValue} pending`}>
          {badgeValue > 99 ? '99+' : badgeValue}
        </span>
      )}
    </button>
  )
}

const DAILY_OPS = { id: 'daily-ops', label: 'Daily Ops', icon: 'clipboard' }
const CANDIDATES = { id: 'candidates', label: 'Candidates', icon: 'users' }

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Daily Ops with nothing pending', () => {
  it('keeps its icon, with no badge', () => {
    render(<SidebarRow item={DAILY_OPS} badgeValue={0} />)
    expect(screen.getByText('Daily Ops')).toBeInTheDocument()
    expect(screen.getByTestId('icon').querySelector('svg.ta-icon--clipboard')).not.toBeNull()
    expect(screen.queryByTestId('badge')).toBeNull()
  })
})

describe('Daily Ops with work pending', () => {
  it('shows the same icon and the real count', () => {
    render(<SidebarRow item={DAILY_OPS} badgeValue={5} />)
    expect(screen.getByTestId('icon').querySelector('svg.ta-icon--clipboard')).not.toBeNull()
    expect(screen.getByTestId('badge').textContent).toBe('5')
  })

  it('caps the badge at 99+', () => {
    render(<SidebarRow item={DAILY_OPS} badgeValue={120} />)
    expect(screen.getByTestId('badge').textContent).toBe('99+')
  })

  it('says what the count is', () => {
    render(<SidebarRow item={DAILY_OPS} badgeValue={2} />)
    expect(screen.getByLabelText('2 pending')).toBeInTheDocument()
  })

  it('drops the badge, not the icon, when the count reaches zero', () => {
    const { rerender } = render(<SidebarRow item={DAILY_OPS} badgeValue={2} />)
    rerender(<SidebarRow item={DAILY_OPS} badgeValue={0} />)
    expect(screen.getByTestId('icon').querySelector('svg')).not.toBeNull()
    expect(screen.queryByTestId('badge')).toBeNull()
  })
})

describe('items that are not counters', () => {
  it('draw their icon the same way', () => {
    render(<SidebarRow item={CANDIDATES} badgeValue={0} />)
    expect(screen.getByTestId('icon').querySelector('svg.ta-icon--users')).not.toBeNull()
  })
})

describe('the shell applies this rule', () => {
  it("draws every item's icon unconditionally, from the one icon set", () => {
    expect(app).not.toContain('alertIcon')
    expect(app).not.toContain('showIcon')
    expect(app).toContain('<span className="desktop-sidebar__link-icon" aria-hidden><Icon name={item.icon}')
    const icons = [...app.matchAll(/\bicon:\s*'([^']+)'/g)].map((m) => m[1])
    expect(icons.length).toBe(7)
    for (const icon of icons) expect(ICON_NAMES, `${icon} is not in the icon set`).toContain(icon)
    expect(new Set(icons).size, 'two sections share an icon').toBe(icons.length)
  })

  it('shows a badge only when there is a count', () => {
    expect(app).toMatch(/\{badgeValue > 0 && \(/)
  })

  it('still feeds Daily Ops the real pending interview count', () => {
    expect(app).toMatch(/item\.badge === 'interviews'\s*\?\s*pending\?\.pendingInterviewCount/)
  })

  it('counts candidates on the Candidates badge, not tasks between them', () => {
    // Was pending?.count -- the number of to-do items -- which made the badge
    // disagree with its own label as soon as one candidate had two gaps.
    expect(app).toContain('pending?.candidateCount || 0')
    expect(app).not.toContain('pending?.count || 0')
  })
})

describe('the count refreshes on a status change', () => {
  it('reloads when the roster announces one', async () => {
    // The provider listens for the signal; without it the badge waits for the
    // two-minute poll.
    expect(provider).toContain("const PENDING_CHANGED = 'teleautomation:pending-work-changed'")
    expect(provider).toMatch(/window\.addEventListener\(PENDING_CHANGED, onChanged\)/)
    // It takes a published count when there is one and reloads otherwise, so
    // the handler is a block rather than the one-liner it used to be.
    expect(provider).toMatch(/const onChanged = \(event\) => \{/)
    expect(provider).toMatch(/reload\(\{ silent: true \}\)/)
  })

  it('announces from every roster mutation path', () => {
    // Four of them: attendance, attendee, slot and the row actions. Each
    // changes what is pending.
    expect(roster).toContain('import { publishPendingWorkChanged }')
    // Four call sites plus the wrapper's own definition.
    expect((roster.match(/notifyRosterChanged\(\)/g) || []).length).toBe(5)
    // And exactly one direct call to the prop — the one inside the wrapper.
    // A second would be a path that mutates without telling the sidebar.
    expect((roster.match(/onRosterMutate\?\.\(\)/g) || []).length).toBe(1)
  })

  it('refreshes silently, so the sidebar does not flash', () => {
    expect(provider).toMatch(/reload\(\{ silent: true \}\)/)
  })

  it('removes its listener on unmount', () => {
    expect(provider).toMatch(/removeEventListener\(PENDING_CHANGED, onChanged\)/)
  })

  it('publishes without throwing when nothing is listening', () => {
    expect(() => act(() => publishPendingWorkChanged())).not.toThrow()
  })

  it('reaches a listener', async () => {
    const seen = vi.fn()
    window.addEventListener('teleautomation:pending-work-changed', seen)
    act(() => publishPendingWorkChanged())
    await waitFor(() => expect(seen).toHaveBeenCalled())
    window.removeEventListener('teleautomation:pending-work-changed', seen)
  })
})
