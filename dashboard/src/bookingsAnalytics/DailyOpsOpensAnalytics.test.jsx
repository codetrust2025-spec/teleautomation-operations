/**
 * Daily Ops' booking card leads to the Bookings Analytics page, and opens no
 * popup of its own any more.
 *
 * Until 21 Oct the arrow beside the top-bar donut opened an "Interview booking
 * analytics" modal over Daily Ops. That detail is a sidebar page now, directly
 * below Daily Ops, so the same arrow navigates the shell there. Driven through
 * the real shell (App), the way the sidebar itself is.
 */
import React from 'react'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', async (importOriginal) => ({
  ...(await importOriginal()),
  useAuth: () => ({
    enabled: false,
    authenticated: true,
    role: 'admin',
    username: 'admin',
    displayName: 'Operations Admin',
    logout: () => {},
  }),
}))
vi.mock('../notifications/GlobalNotificationSounds.jsx', () => ({ default: () => null }))
vi.mock('../notifications/mailEventStream.js', async (importOriginal) => ({
  ...(await importOriginal()),
  subscribeMailEvents: () => () => {},
  subscribeNodeActivity: () => () => {},
  subscribeMailStatus: () => () => {},
  getMailStatus: () => 'live',
}))

import App from '../App.jsx'
import { ConfirmProvider } from '../context/ConfirmContext.jsx'
import { AttendanceProvider } from '../attendance/AttendanceContext.jsx'

function respond(url) {
  const path = String(url)
  let body = {}
  if (path.includes('/auth/')) body = { enabled: false }
  else if (path.includes('/candidates/interviews/global')) {
    body = {
      status: 'ok',
      available_months: [],
      interviews: { count: 3, attended_count: 2, pending_count: 1, by_candidate: [{ name: 'Asha Fakename', scheduled: 3, attended: 2, pending: 1 }] },
      booking_overview: { total: 3, by_candidate: [{ name: 'Asha Fakename', count: 3 }], by_level: [{ name: 'L1', count: 3 }], by_technology: [{ name: 'React JS', count: 3 }] },
    }
  }
  return {
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
    text: async () => JSON.stringify(body),
    blob: async () => new Blob([]),
  }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (url) => respond(url)))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Daily Ops → Bookings Analytics', () => {
  it('the arrow beside the booking donut opens the page, not a popup', async () => {
    await act(async () => {
      render(<ConfirmProvider><AttendanceProvider><App /></AttendanceProvider></ConfirmProvider>)
    })
    const header = document.querySelector('header.business-header')
    expect(within(header).getByRole('heading', { level: 1 }).textContent).toBe('Daily Ops')

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Open Bookings Analytics' })) })

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(within(header).getByRole('heading', { level: 1 }).textContent).toBe('Bookings Analytics')
    const nav = screen.getByRole('navigation', { name: 'Operations navigation' })
    const current = within(nav).getAllByRole('button').find(b => b.getAttribute('aria-current') === 'page')
    expect(current.textContent).toContain('Bookings Analytics')
    expect(await screen.findByRole('group', { name: 'Booking counters' })).toBeTruthy()
  })

  it('sits directly below Daily Ops in the sidebar', async () => {
    await act(async () => {
      render(<ConfirmProvider><AttendanceProvider><App /></AttendanceProvider></ConfirmProvider>)
    })
    const nav = screen.getByRole('navigation', { name: 'Operations navigation' })
    const labels = within(nav).getAllByRole('button').map(b => b.textContent.trim())
    const daily = labels.findIndex(text => text.startsWith('Daily Ops'))
    expect(labels[daily + 1]).toMatch(/^Bookings Analytics/)
  })
})
