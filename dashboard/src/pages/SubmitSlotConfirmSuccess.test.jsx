/**
 * A booking that the backend accepted must be reported as accepted.
 *
 * Production showed "Network error — try again" on Confirm with every field
 * valid and the invite parsed. There was no network fault: the success path
 * still called `setTriedSubmit(false)`, a setter removed when sequential
 * validation replaced that state with `missingField`. It threw a
 * ReferenceError, the bare `catch` around the whole submit turned it into
 * "Network error — try again", and the slot had already been created on the
 * server by then -- so the message invited the user to book a second time.
 *
 * The catch is the reason it was invisible: it asserts a cause it has not
 * established, and swallows every other kind of fault to do it.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function upcomingDate() {
  const date = new Date()
  date.setDate(date.getDate() + 7)
  return date.toISOString().slice(0, 10)
}

function screenshot(label) {
  return new File([label], `${label}.jpg`, { type: 'image/jpeg' })
}

function attach(input, files) {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  fireEvent.change(input)
}

/** @param confirmReply what /bookings/confirm answers, and how. */
function stubFetch(confirmReply) {
  const calls = { confirms: [] }
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const target = String(url)
    const reply = body =>
      Promise.resolve({ ok: true, status: 200, headers: { get: () => 'application/json' },
                        json: () => Promise.resolve(body) })
    if (target.includes('/public/slots/payment-requirement')) {
      return reply({ status: 'ok', service_type: 'profile_service', amount_due: 0, payment_required: false })
    }
    if (target.includes('/extract-invite-ai')) {
      return reply({ status: 'ok', success: true,
                     data: { interview_date: upcomingDate(), start_time: '04:00 PM', confidence_score: 95 } })
    }
    if (target.includes('/bookings/confirm')) {
      calls.confirms.push(options?.body)
      return confirmReply()
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: [{ id: 'c1', name: 'Aniket', needs_payment_proof: false, balance_due: 0 }] })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  return calls
}

/** Fill the profile-service form to the point where Confirm is legitimate. */
async function completedForm() {
  render(<SubmitSlotPage />)
  const confirm = await screen.findByRole('button', { name: /Confirm booking/i })

  const nameBox = document.querySelector('.sbs-field input')
  fireEvent.change(nameBox, { target: { value: 'Aniket' } })
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L1' } })

  const invite = [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)
  attach(invite, [screenshot('invite')])
  await waitFor(() => expect(screen.queryByText(/reading invite/i)).toBeNull())

  return confirm
}

const okReply = () => Promise.resolve({
  ok: true, status: 200, headers: { get: () => 'application/json' },
  json: () => Promise.resolve({ status: 'ok', candidate: { name: 'Aniket' } }),
})

describe('a booking the backend accepted', () => {
  it('is reported as confirmed, not as a network error', async () => {
    const calls = stubFetch(okReply)
    const confirm = await completedForm()
    fireEvent.click(confirm)

    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    await waitFor(() =>
      expect(screen.getByText(/Slot confirmed for Aniket/i)).toBeInTheDocument(),
    )
    expect(screen.queryByText(/Network error/i)).not.toBeInTheDocument()
  })

  it('never claims a network fault after the slot was created', async () => {
    // The damage in the original: the booking existed, and the message asked
    // for it to be made again.
    const calls = stubFetch(okReply)
    const confirm = await completedForm()
    fireEvent.click(confirm)

    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    await waitFor(() => expect(screen.queryByText(/Network error/i)).not.toBeInTheDocument())
  })
})

describe('a booking the backend refused', () => {
  it('shows the backend message rather than a generic one', async () => {
    stubFetch(() => Promise.resolve({
      ok: false, status: 409, headers: { get: () => 'application/json' },
      json: () => Promise.resolve({ status: 'error', message: 'That slot is already taken.' }),
    }))
    const confirm = await completedForm()
    fireEvent.click(confirm)

    await waitFor(() =>
      expect(screen.getByText('That slot is already taken.')).toBeInTheDocument(),
    )
  })

  it('does not call a non-JSON failure a network error', async () => {
    // A 502 from the proxy arrives as HTML. `res.json()` throws on it, and the
    // bare catch reported that as a network fault too.
    stubFetch(() => Promise.resolve({
      ok: false, status: 502, headers: { get: () => 'text/html' },
      text: () => Promise.resolve('<html><body>Bad Gateway</body></html>'),
      json: () => Promise.reject(new SyntaxError('Unexpected token <')),
    }))
    const confirm = await completedForm()
    fireEvent.click(confirm)

    await waitFor(() => expect(screen.queryByText(/Confirm booking/i)).toBeInTheDocument())
    const alert = document.querySelector('.sbs-alert--error')
    expect(alert).not.toBeNull()
    expect(alert.textContent).toMatch(/502/)
  })
})

describe('the body is read, not the label on it', () => {
  it('shows a refusal that arrived as JSON without a content-type header', async () => {
    // readApiResponse first keyed off the declared content-type and returned
    // {} for anything else, so a refusal whose reason was sitting right there
    // in the body was replaced by a bare "The server returned 409." -- the
    // same class of thing as calling a working booking a network error.
    //
    // A success is not a useful test of this: the confirmation message falls
    // back to the name already typed into the form, so it reads correctly
    // either way. The refusal path is where the lost body actually shows.
    stubFetch(() => Promise.resolve({
      ok: false, status: 409, headers: { get: () => null },
      json: () => Promise.resolve({ status: 'error', message: 'That slot is already taken.' }),
    }))
    const confirm = await completedForm()
    fireEvent.click(confirm)

    await waitFor(() =>
      expect(screen.getByText('That slot is already taken.')).toBeInTheDocument(),
    )
  })
})

describe('a genuine network fault still says so', () => {
  it('reports it when fetch itself rejects', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))
    const confirm = await completedForm()
    fireEvent.click(confirm)

    await waitFor(() =>
      expect(screen.getByText(/Network error — try again/i)).toBeInTheDocument(),
    )
  })
})
