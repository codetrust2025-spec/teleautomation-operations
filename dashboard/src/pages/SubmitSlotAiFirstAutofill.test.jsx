/**
 * AI-first booking (19 Oct).
 *
 * After the invite is read, every field it read with confidence is filled in:
 * client name, company, interview round, date, start and end time (IST). What
 * it read is shown back in a "Read from invite" summary before Confirm, every
 * filled value can be corrected, and nothing missing or doubtful is guessed:
 * a date or time the reader was not confident about is shown as read but left
 * for the candidate to enter.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage, inviteSlotConfident } from './SubmitSlotPage.jsx'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function upcomingDate(days = 7) {
  const date = new Date()
  date.setDate(date.getDate() + days)
  return date.toISOString().slice(0, 10)
}

const CLIENTS = [{ id: 'c-ira', name: 'Ira Testwala', needs_payment_proof: false, balance_due: 0 }]

const CONFIDENT = () => ({
  candidate_name: 'Ira Testwala', company: 'Contoso Ltd', interview_round: 'L2',
  interview_date: upcomingDate(), start_time: '03:00 PM', end_time: '03:45 PM',
  confidence_score: 95, manual_fields_required: false, auto_booking_safe: true,
})

function stubFetch(invite) {
  const calls = { confirms: [] }
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const target = String(url)
    const reply = body => Promise.resolve({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: () => Promise.resolve(body) })
    if (target.includes('/payment-requirement')) return reply({ status: 'ok', service_type: 'profile_service', amount_due: 0, payment_required: false, re_service: true })
    if (target.includes('/extract-invite-ai')) return reply({ status: 'ok', success: true, data: invite })
    if (target.includes('/bookings/confirm')) {
      calls.confirms.push(options?.body)
      return reply({ status: 'ok', candidate: { name: 'Ira Testwala' } })
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: CLIENTS })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  return calls
}

const inviteInput = () => [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)
const field = label => screen.getByLabelText(label)
const statusOf = key => document.querySelector(`.sbs-read__row[data-field="${key}"]`)?.dataset.status
const valueOf = key => document.querySelector(`.sbs-read__row[data-field="${key}"] .sbs-read__value`)?.textContent

async function openAndRead(invite) {
  const calls = stubFetch(invite)
  render(<SubmitSlotPage />)
  const confirm = await screen.findByRole('button', { name: /Confirm booking/i })
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled())
  Object.defineProperty(inviteInput(), 'files', { value: [new File(['x'], 'invite.jpg', { type: 'image/jpeg' })], configurable: true })
  fireEvent.change(inviteInput())
  await waitFor(() => expect(document.querySelector('.sbs-read')).not.toBeNull())
  return { confirm, calls }
}

describe('which readings count as confident', () => {
  it.each([
    [{ confidence_score: 95, manual_fields_required: false, auto_booking_safe: true }, true],
    [{ confidence_score: 92 }, true],
    [{ confidence_score: 95, manual_fields_required: true }, false],
    [{ confidence_score: 95, manual_fields_required: false, auto_booking_safe: false }, false],
    [{ confidence_score: 60, manual_fields_required: false }, false],
    [{ confidence_score: 0, manual_fields_required: true, auto_booking_safe: false }, false],
    [null, false],
  ])('%j -> %s', (ext, expected) => {
    expect(inviteSlotConfident(ext)).toBe(expected)
  })
})

describe('a confident reading fills every field it found', () => {
  it('fills client, company, round, date, start and end, and books them', async () => {
    const { confirm, calls } = await openAndRead(CONFIDENT())
    await waitFor(() => expect(document.querySelector('.sbs-field--client input').value).toBe('Ira Testwala'))
    expect(field('Company').value).toBe('Contoso Ltd')
    expect(screen.getByRole('combobox').value).toBe('L2')
    expect(field('Interview date').value).toBe(upcomingDate())
    expect(field('Start time (IST)').value).toBe('03:00 PM')
    expect(field('End time (IST)').value).toBe('03:45 PM')

    await waitFor(() => expect(confirm.disabled).toBe(false))
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    const body = calls.confirms[0]
    expect(body.get('name')).toBe('Ira Testwala')
    expect(body.get('company')).toBe('Contoso Ltd')
    expect(body.get('interview_round')).toBe('L2')
    expect(body.get('date')).toBe(upcomingDate())
    expect(body.get('time')).toBe('15:00')
    expect(body.get('time_end')).toBe('15:45')
  })

  it('shows what was read before Confirm, each field marked filled', async () => {
    await openAndRead(CONFIDENT())
    await waitFor(() => expect(statusOf('client')).toBe('filled'))
    for (const key of ['company', 'round', 'date', 'start', 'end']) expect(statusOf(key)).toBe('filled')
    expect(valueOf('company')).toBe('Contoso Ltd')
    expect(valueOf('start')).toBe('03:00 PM')
    expect(valueOf('end')).toBe('03:45 PM')
    // The slot about to be booked, with its end time, above Confirm.
    expect(screen.getByRole('status', { name: 'Detected interview slot' }).textContent).toMatch(/03:00 PM – 03:45 PM/)
  })

  it('lets every filled value be corrected, and books the correction', async () => {
    const { confirm, calls } = await openAndRead(CONFIDENT())
    fireEvent.change(field('Start time (IST)'), { target: { value: '04:00 PM' } })
    fireEvent.change(field('End time (IST)'), { target: { value: '04:30 PM' } })
    fireEvent.change(field('Company'), { target: { value: 'Contoso India' } })
    expect(statusOf('start')).toBe('changed')
    await waitFor(() => expect(confirm.disabled).toBe(false))
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('time')).toBe('16:00')
    expect(calls.confirms[0].get('time_end')).toBe('16:30')
    expect(calls.confirms[0].get('company')).toBe('Contoso India')
  })
})

describe('nothing missing or doubtful is guessed', () => {
  it('an end time the invite does not give stays empty and is not booked', async () => {
    const { confirm, calls } = await openAndRead({ ...CONFIDENT(), end_time: '' })
    expect(field('End time (IST)').value).toBe('')
    expect(statusOf('end')).toBe('missing')
    await waitFor(() => expect(confirm.disabled).toBe(false))
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('time_end')).toBeNull()
  })

  it('a company or round the invite does not name is left empty', async () => {
    await openAndRead({ ...CONFIDENT(), company: '', interview_round: '' })
    expect(field('Company').value).toBe('')
    expect(screen.getByRole('combobox').value).toBe('')
    expect(statusOf('company')).toBe('missing')
    expect(statusOf('round')).toBe('missing')
  })

  it('a date and time read without confidence are shown but not filled, and Confirm waits for them', async () => {
    const { confirm, calls } = await openAndRead({
      ...CONFIDENT(), confidence_score: 0, manual_fields_required: true, auto_booking_safe: false,
      warnings: ['OCR and AI did not independently agree on the booking date/time.'],
    })
    expect(field('Interview date').value).toBe('')
    expect(field('Start time (IST)').value).toBe('')
    expect(field('End time (IST)').value).toBe('')
    expect(statusOf('date')).toBe('unconfirmed')
    expect(statusOf('start')).toBe('unconfirmed')
    expect(valueOf('start')).toBe('03:00 PM')                    // what was read is still shown
    expect(screen.queryByRole('status', { name: 'Detected interview slot' })).toBeNull()
    expect(confirm.disabled).toBe(true)

    fireEvent.change(field('Interview date'), { target: { value: upcomingDate(8) } })
    fireEvent.change(field('Start time (IST)'), { target: { value: '05:00 PM' } })
    await waitFor(() => expect(confirm.disabled).toBe(false))
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('date')).toBe(upcomingDate(8))
    expect(calls.confirms[0].get('time')).toBe('17:00')
  })

  it('warns when the end time is before the start', async () => {
    await openAndRead(CONFIDENT())
    fireEvent.change(field('End time (IST)'), { target: { value: '02:00 PM' } })
    expect(screen.getByText('End time is before the start time. Check it.')).toBeInTheDocument()
  })
})
