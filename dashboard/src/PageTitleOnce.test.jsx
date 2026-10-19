/**
 * Every page is named once: in the shell header.
 *
 * The shell's header shows the open page's name ("Daily Ops", "Data Room",
 * ...), and each page then opened on its own title as well -- "Daily ops",
 * "Mail Monitoring Notifications", "Candidate Mailboxes", "Candidates",
 * "Data room", "Attendance & earnings eligibility" -- usually with an eyebrow
 * and a strapline, so the same name sat twice, one under the other, and the
 * content began a header's height lower than it needed to.
 *
 * This drives the real shell (App, with every page it mounts) through the
 * sidebar, page by page, and checks two things on each: the name is a heading
 * exactly once, in the shell header, and the page still has the controls that
 * shared its old title row -- tabs, filters, counters, actions -- since those
 * are what the page is for.
 *
 * Slot Booking is not here on purpose: it opens /submit-slot in a new tab,
 * outside the shell, so its own "Book Interview Slot" header is the only one.
 */
import React from 'react'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./context/AuthContext.jsx', async (importOriginal) => ({
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
// Sounds and the live mail socket have suites of their own; here they would
// only need an AudioContext and a WebSocket that jsdom does not have.
vi.mock('./notifications/GlobalNotificationSounds.jsx', () => ({ default: () => null }))
vi.mock('./notifications/mailEventStream.js', async (importOriginal) => ({
  ...(await importOriginal()),
  subscribeMailEvents: () => () => {},
  subscribeNodeActivity: () => () => {},
  subscribeMailStatus: () => () => {},
  getMailStatus: () => 'live',
}))

import App from './App.jsx'
import { ConfirmProvider } from './context/ConfirmContext.jsx'
import { AttendanceProvider } from './attendance/AttendanceContext.jsx'
import { captureLayout } from './test/captureLayout.js'

/** Each sidebar page, the title it used to repeat, and what it must keep. */
const PAGES = [
  {
    label: 'Daily Ops',
    oldTitle: /^daily ops$/i,
    keeps: () => [
      screen.getByRole('textbox', { name: 'Candidate search' }),
      screen.getByLabelText('Roster controls'),
    ],
  },
  {
    label: 'Mail Alerts',
    oldTitle: /^mail monitoring notifications$/i,
    keeps: () => [
      screen.getByRole('tablist', { name: 'Mail alerts filter tabs' }),
      screen.getByRole('button', { name: 'Clear all notifications' }),
      screen.getByText('Live'),
    ],
  },
  {
    label: 'AI Mail Review',
    oldTitle: /^candidate mailboxes$/i,
    keeps: () => [
      screen.getByRole('combobox', { name: 'Global candidate filter' }),
      screen.getByRole('button', { name: /refresh/i }),
    ],
  },
  {
    label: 'Candidates',
    oldTitle: /^candidates$/i,
    keeps: () => [
      screen.getByRole('tablist', { name: 'Candidates sections' }),
      screen.getByRole('region', { name: 'Candidate filters' }),
    ],
  },
  {
    label: 'Data Room',
    oldTitle: /^data room$/i,
    keeps: () => [screen.getByRole('navigation', { name: 'Data room sections' })],
  },
  {
    label: 'Attendance',
    oldTitle: /^attendance( & earnings eligibility)?$/i,
    keeps: () => [screen.getByRole('button', { name: 'Refresh' })],
  },
]

function respond(url) {
  const body = String(url).includes('/auth/') ? { enabled: false } : {}
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

async function openShell() {
  await act(async () => {
    render(
      <ConfirmProvider>
        <AttendanceProvider>
          <App />
        </AttendanceProvider>
      </ConfirmProvider>,
    )
  })
}

async function open(label) {
  const nav = screen.getByRole('navigation', { name: 'Operations navigation' })
  const link = within(nav).getAllByRole('button').find(b => b.textContent.trim().startsWith(label))
  await act(async () => { fireEvent.click(link) })
}

describe('each page is named once, by the shell header', () => {
  for (const page of PAGES) {
    it(`${page.label}: one title, and the page's own controls are still there`, async () => {
      await openShell()
      await open(page.label)

      const shellHeader = document.querySelector('header.business-header')
      const main = document.querySelector('main.business-body')
      expect(within(shellHeader).getByRole('heading', { level: 1 }).textContent).toBe(page.label)

      // The name is a heading once on the whole screen, and it is the shell's.
      const named = screen.getAllByRole('heading')
        .filter(h => h.textContent.trim().toLowerCase() === page.label.toLowerCase())
      expect(named).toHaveLength(1)
      expect(shellHeader.contains(named[0])).toBe(true)

      // The page opens on its controls, not on a title of its own -- neither
      // the one it used to repeat nor any other page-level heading.
      const repeated = within(main).queryAllByRole('heading')
        .filter(h => page.oldTitle.test(h.textContent.trim()))
      expect(repeated.map(h => h.textContent)).toEqual([])
      expect(main.querySelector('h1')).toBeNull()

      for (const control of page.keeps()) expect(main.contains(control)).toBe(true)

      // The whole shell, header included, for scripts/layout_harness.py.
      captureLayout(`page-title-once-${page.label.toLowerCase().replace(/\W+/g, '-')}`, document.querySelector('.desktop-app'))
    })
  }
})
