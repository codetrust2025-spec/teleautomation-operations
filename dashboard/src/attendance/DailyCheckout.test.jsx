/**
 * The daily check-out panel.
 *
 * The button is the small part. What matters is that the screen says what is
 * still open in words someone can act on, that it notices by itself when the
 * last of it is cleared — an interview outcome is set on a different screen —
 * and that a second press cannot record a second end to the same day.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import { DailyCheckout } from './DailyCheckout.jsx'

const OPEN = {
  status: 'ok',
  window_opens_at: '6:30 PM',
  checked_out: false,
  checked_out_at: null,
  attendance_marked: true,
  can_check_out: true,
  blockers: [],
}

const BLOCKED = {
  ...OPEN,
  can_check_out: false,
  blockers: [
    {
      kind: 'INTERVIEW_OUTCOME_PENDING',
      summary: 'An interview has ended without its outcome being recorded.',
      count: 1,
      items: [{ candidate_id: 'c1', name: 'Sample Candidate', company: 'Example Corp', time: '4:00 PM' }],
    },
    {
      kind: 'DAILY_TASKS_PENDING',
      summary: 'Operations tasks are still open.',
      count: 1,
      items: [{ kind: 'missing_resume', label: 'Resume missing', name: 'Another Candidate' }],
    },
  ],
}

function respond(payload, { status = 200 } = {}) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  })
}

let calls

beforeEach(() => {
  calls = []
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/**
 * Answers GET with each reading in turn — the last one repeats — and POST with
 * `post`. The readings are the server's own answers, so a reading after a
 * successful check-out has to say the day is closed: the component re-reads
 * after every press, and the server is the authority over what it drew.
 */
function api(readings, post) {
  const queue = [...readings]
  vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
    calls.push({ url: String(url), method: options.method || 'GET' })
    if ((options.method || 'GET') === 'POST') return post()
    const next = queue.length > 1 ? queue.shift() : queue[0]
    return typeof next === 'function' ? next() : respond(next)
  }))
}

const CHECKED_OUT = {
  ...OPEN,
  checked_out: true,
  checked_out_at: '2026-09-17T13:35:00Z',
  can_check_out: false,
}

describe('when the day is finished', () => {
  it('offers the button and says nothing is in the way', async () => {
    api([OPEN])
    render(<DailyCheckout />)

    expect(await screen.findByText(/Everything for today is closed/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Check out' })).toBeEnabled()
  })

  it('records the check-out and shows the time it kept', async () => {
    api([OPEN, CHECKED_OUT],
      () => respond({ status: 'checked_out', checkout: { checked_out_at: '2026-09-17T13:35:00Z' } }))
    render(<DailyCheckout />)
    fireEvent.click(await screen.findByRole('button', { name: 'Check out' }))

    // Not asserting the meridiem: ICU writes it after a narrow no-break
    // space, which no regexp written by hand contains.
    expect(await screen.findByText(/Checked out at 7:05/i)).toBeInTheDocument()
  })

  it('offers no second check-out once the day is closed', async () => {
    api([{ ...OPEN, checked_out: true, checked_out_at: '2026-09-17T13:35:00Z', can_check_out: false }])
    render(<DailyCheckout />)

    await screen.findByText(/Checked out at/)
    expect(screen.getByText("Today's working day is closed.")).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Check out' })).toBeNull()
  })
})

describe('when something is still open', () => {
  it('names each thing and what to do about it', async () => {
    api([BLOCKED])
    render(<DailyCheckout />)

    expect(await screen.findByText(/An interview has ended without its outcome/)).toBeInTheDocument()
    expect(screen.getByText('Set the outcome on the Daily Ops roster.')).toBeInTheDocument()
    expect(screen.getByText(/Sample Candidate · Example Corp · 4:00 PM/)).toBeInTheDocument()
    expect(screen.getByText('Operations tasks are still open.')).toBeInTheDocument()
    expect(screen.getByText('Clear these on the Pending Works tab.')).toBeInTheDocument()
  })

  it('will not let the day be closed', async () => {
    api([BLOCKED])
    render(<DailyCheckout />)

    await screen.findByText(/An interview has ended/)
    expect(screen.getByRole('button', { name: 'Check out' })).toBeDisabled()
  })

  it('says when check-out opens, before the evening', async () => {
    api([{
      ...OPEN,
      can_check_out: false,
      blockers: [{ kind: 'BEFORE_CHECKOUT_WINDOW', summary: 'Check-out opens at 6:30 PM IST.', count: 0, items: [] }],
    }])
    render(<DailyCheckout />)

    expect(await screen.findByText('Check-out opens at 6:30 PM IST.')).toBeInTheDocument()
  })
})

describe('noticing on its own', () => {
  it('unlocks when the last blocker is cleared elsewhere', async () => {
    // The outcome is set on the Daily Ops roster, in another tab. Nobody
    // reloads this page; the next reading is enough.
    api([BLOCKED, OPEN])
    render(<DailyCheckout />)
    await screen.findByText(/An interview has ended/)

    await act(async () => {
      vi.advanceTimersByTime(60_000)
    })

    await waitFor(() => expect(screen.getByRole('button', { name: 'Check out' })).toBeEnabled())
    expect(screen.queryByText(/An interview has ended/)).toBeNull()
  })

  it('re-reads when the tab is looked at again', async () => {
    api([BLOCKED, OPEN])
    render(<DailyCheckout />)
    await screen.findByText(/An interview has ended/)

    await act(async () => {
      window.dispatchEvent(new Event('focus'))
    })

    await waitFor(() => expect(screen.getByRole('button', { name: 'Check out' })).toBeEnabled())
  })

  it('stops reading once it is off the screen', async () => {
    api([OPEN])
    const { unmount } = render(<DailyCheckout />)
    await screen.findByText(/Everything for today is closed/)
    const before = calls.length

    unmount()
    await act(async () => {
      vi.advanceTimersByTime(180_000)
    })

    expect(calls.length).toBe(before)
  })
})

describe('when the server disagrees at the last moment', () => {
  it('shows the blockers the refusal came back with', async () => {
    // An interview was booked for this evening between the page reading and
    // the button being pressed. The follow-up read fails here on purpose, so
    // what stays on screen can only have come from the refusal itself.
    api([OPEN, () => Promise.reject(new Error('offline'))], () => respond({
      detail: {
        message: 'The working day is not finished yet.',
        blockers: [{
          kind: 'INTERVIEW_NOT_FINISHED',
          summary: 'An interview today has not finished yet.',
          count: 1,
          items: [{ candidate_id: 'c9', name: 'Late Booking', time: '8:00 PM' }],
        }],
      },
    }, { status: 409 }))
    render(<DailyCheckout />)
    fireEvent.click(await screen.findByRole('button', { name: 'Check out' }))

    expect(await screen.findByText('An interview today has not finished yet.')).toBeInTheDocument()
    expect(screen.getByText(/Late Booking · 8:00 PM/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Check out' })).toBeDisabled()
  })

  it('reports a failure without claiming the day was closed', async () => {
    api([OPEN, () => Promise.reject(new Error('offline'))],
      () => respond({ detail: 'Check-out is only available to handler accounts.' }, { status: 403 }))
    render(<DailyCheckout />)
    fireEvent.click(await screen.findByRole('button', { name: 'Check out' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/only available to handler accounts/)
    expect(screen.queryByText(/Checked out at/)).toBeNull()
  })
})

describe('what it never does', () => {
  it('reads with GET and only writes when the button is pressed', async () => {
    api([OPEN], () => respond({ status: 'checked_out', checkout: { checked_out_at: '2026-09-17T13:35:00Z' } }))
    render(<DailyCheckout />)
    await screen.findByText(/Everything for today is closed/)

    await act(async () => {
      vi.advanceTimersByTime(120_000)
    })

    // Two minutes of polling and no write: a page left open in the evening
    // must not check someone out because it refreshed.
    expect(calls.length).toBeGreaterThan(1)
    expect(calls.every(call => call.method === 'GET')).toBe(true)
  })

  it('renders nothing at all until it has an answer', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
    const { container } = render(<DailyCheckout />)
    expect(container.textContent).toBe('')
  })
})
