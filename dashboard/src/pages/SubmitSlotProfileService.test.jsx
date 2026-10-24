/**
 * Book Interview Slot, Profile Service: the client is a subscribed candidate
 * chosen from the list -- never a typed name.
 *
 * The list is the server's (`/public/slots/candidates`: on Candidates, profile
 * service, In progress), the one `/bookings/confirm` holds a Profile Service
 * booking to (tests/test_profile_service_books_subscribed_candidates.py). Here:
 * typing searches and never sets a client; a client not on the list cannot be
 * booked; the chosen candidate's profile is shown; an empty list says why; the
 * list is read again when opened, so someone subscribed since appears; and a
 * refusal from the server is shown as it is. Round-wise is untouched.
 * Names are invented.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SubmitSlotPage, NO_SUBSCRIBED_CANDIDATES } from './SubmitSlotPage.jsx'
import { captureLayout } from '../test/captureLayout.js'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function upcomingDate() {
  const date = new Date()
  date.setDate(date.getDate() + 7)
  return date.toISOString().slice(0, 10)
}

const ASHA = { id: 'c-asha', name: 'Asha Fakename', technology: 'React JS', balance_due: 0, needs_payment_proof: false }
const RAVI = { id: 'c-ravi', name: 'Ravi Testcase', technology: 'ServiceNow', balance_due: 5000, needs_payment_proof: false, payment_message: 'Rs 5,000 is due; it can be paid after this interview.' }

function stubFetch({ lists = [[ASHA, RAVI]], confirm } = {}) {
  const calls = { confirms: [], candidateReads: 0 }
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const target = String(url)
    const reply = (body, status = 200) => Promise.resolve({ ok: status < 400, status, headers: { get: () => 'application/json' }, json: () => Promise.resolve(body) })
    if (target.includes('/payment-requirement')) return reply({ status: 'ok', service_type: 'profile_service', amount_due: 0, payment_required: false })
    if (target.includes('/extract-invite-ai')) {
      return reply({ status: 'ok', success: true, data: { interview_date: upcomingDate(), start_time: '11:00 AM', company: 'Contoso Ltd', interview_round: 'L1', confidence_score: 95, candidate_name: '' } })
    }
    if (target.includes('/bookings/confirm')) {
      calls.confirms.push(options?.body)
      return confirm ? reply(...confirm) : reply({ status: 'ok', candidate: { name: 'x' } })
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    if (target.includes('/public/slots/candidates')) {
      const list = lists[Math.min(calls.candidateReads, lists.length - 1)]
      calls.candidateReads += 1
      return reply({ status: 'ok', candidates: list })
    }
    return reply({ status: 'ok' })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  return calls
}

const box = () => screen.getByRole('textbox', { name: 'Profile Service candidate' })
const field = () => box().closest('label')
const list = () => screen.getByRole('listbox', { name: 'Subscribed Profile Service candidates' })
const options = () => within(list()).queryAllByRole('option')
const listMessage = () => within(list()).getByRole('status').textContent

async function openPage(setup) {
  const calls = stubFetch(setup)
  render(<SubmitSlotPage />)
  const confirm = await screen.findByRole('button', { name: /Confirm booking/i })
  await waitFor(() => expect(calls.candidateReads).toBeGreaterThan(0))
  return { calls, confirm }
}

async function attachInvite() {
  const input = [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)
  Object.defineProperty(input, 'files', { value: [new File(['x'], 'invite.jpg', { type: 'image/jpeg' })], configurable: true })
  fireEvent.change(input)
  await waitFor(() => expect(document.querySelector('.ai-node-progress')?.textContent || '').toMatch(/^✓/))
}

describe('the client is chosen from the subscribed list', () => {
  it('lists the subscribed candidates, with their technology', async () => {
    await openPage()
    expect(field().querySelector('.sbs-label').textContent).toBe('Subscribed candidate')
    fireEvent.focus(box())
    expect(options().map(o => o.textContent)).toEqual(['Asha FakenameReact JS', 'Ravi TestcaseServiceNow'])
    captureLayout('submit-slot-profile-open', document.querySelector('.sbs-screen'))
  })

  it('searches as you type, and a name that is not on the list sets nothing', async () => {
    const { calls, confirm } = await openPage()
    await attachInvite()
    fireEvent.focus(box())
    fireEvent.change(box(), { target: { value: 'ravi' } })
    expect(options().map(o => o.querySelector('.sbs-profile-option__name').textContent)).toEqual(['Ravi Testcase'])
    fireEvent.change(box(), { target: { value: 'Someone New' } })
    expect(options()).toHaveLength(0)
    expect(listMessage()).toBe('No subscribed candidate matches “Someone New”.')
    // Leaving the box does not make the typed text a client.
    fireEvent.pointerDown(document.body)
    expect(box().value).toBe('')
    fireEvent.click(confirm)
    expect(await screen.findByText('Choose the candidate from the list of subscribed Profile Service candidates.')).toBeInTheDocument()
    expect(calls.confirms).toHaveLength(0)
  })

  it('shows the chosen candidate’s profile and books them by id', async () => {
    const { calls, confirm } = await openPage()
    await attachInvite()
    fireEvent.focus(box())
    fireEvent.click(screen.getByRole('option', { name: /^Ravi Testcase/ }))
    expect(box().value).toBe('Ravi Testcase')
    const details = field().querySelector('.sbs-profile-details')
    expect(details.textContent).toContain('ServiceNow')
    expect(details.textContent).toContain('₹5,000 due')
    expect(details.textContent).toContain('it can be paid after this interview')
    captureLayout('submit-slot-profile-chosen', document.querySelector('.sbs-screen'))
    await waitFor(() => expect(confirm.disabled).toBe(false))
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('name')).toBe('Ravi Testcase')
    expect(calls.confirms[0].get('candidate_id')).toBe('c-ravi')
    expect(calls.confirms[0].get('service_type')).toBe('profile_service')
  })

  it('chooses a listed candidate whose whole name is typed out', async () => {
    await openPage()
    fireEvent.focus(box())
    fireEvent.change(box(), { target: { value: 'asha fakename' } })
    fireEvent.pointerDown(document.body)
    expect(box().value).toBe('Asha Fakename')
    expect(field().querySelector('.sbs-profile-details').textContent).toContain('React JS')
  })

  it('can be chosen from the keyboard', async () => {
    await openPage()
    fireEvent.focus(box())
    fireEvent.keyDown(box(), { key: 'ArrowDown' })
    fireEvent.keyDown(box(), { key: 'Enter' })
    expect(box().value).toBe('Ravi Testcase')
  })
})

describe('when nobody is subscribed', () => {
  it('says why the list is empty, and nothing can be booked', async () => {
    const { calls, confirm } = await openPage({ lists: [[]] })
    await attachInvite()
    expect(field().querySelector('.sbs-hint').textContent).toBe(NO_SUBSCRIBED_CANDIDATES)
    fireEvent.focus(box())
    expect(listMessage()).toBe(NO_SUBSCRIBED_CANDIDATES)
    captureLayout('submit-slot-profile-empty', document.querySelector('.sbs-screen'))
    fireEvent.pointerDown(document.body)
    fireEvent.click(confirm)
    await new Promise(resolve => setTimeout(resolve, 50))
    expect(calls.confirms).toHaveLength(0)
  })
})

describe('a newly subscribed candidate', () => {
  it('appears when the list is opened again, without a reload', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { calls } = await openPage({ lists: [[ASHA], [ASHA, RAVI]] })
    fireEvent.focus(box())
    expect(options()).toHaveLength(1)
    fireEvent.pointerDown(document.body)
    await act(async () => { vi.advanceTimersByTime(25000) })
    fireEvent.focus(box())
    await waitFor(() => expect(options()).toHaveLength(2))
    expect(calls.candidateReads).toBe(2)
  })
})

describe('the server holds the same rule', () => {
  it('shows its refusal as it is', async () => {
    const message = 'Choose the candidate from the Profile Service list. “Asha Fakename” is not a subscribed Profile Service candidate (on Candidates, In progress).'
    const { confirm } = await openPage({ confirm: [{ status: 'error', message, not_subscribed: true }, 400] })
    await attachInvite()
    fireEvent.focus(box())
    fireEvent.click(screen.getByRole('option', { name: /^Asha Fakename/ }))
    await waitFor(() => expect(confirm.disabled).toBe(false))
    fireEvent.click(confirm)
    expect(await screen.findByText(message)).toBeInTheDocument()
  })
})

describe('round-wise is unchanged', () => {
  it('still takes a typed client name and phone', async () => {
    await openPage()
    fireEvent.click(screen.getByRole('button', { name: /Profile service/i }))
    fireEvent.click(screen.getByText('Round-wise'))
    const typed = screen.getByPlaceholderText('Type client name')
    fireEvent.change(typed, { target: { value: 'Someone New' } })
    expect(typed.value).toBe('Someone New')
    expect(typed.closest('label').querySelector('.sbs-label').textContent).toBe('Client name')
    expect(screen.queryByRole('textbox', { name: 'Profile Service candidate' })).toBeNull()
  })
})
