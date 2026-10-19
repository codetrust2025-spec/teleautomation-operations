/**
 * AI-first booking (19 Oct).
 *
 * After the invite is read, every field it read with confidence is filled in:
 * client name, company, interview round, date, start and end time (IST), right
 * in the editable fields -- the date, start and end in the one Interview date &
 * time (IST) field -- with no separate summary or slot preview (19 Oct). Every filled
 * value can be corrected, and nothing missing or doubtful is guessed: a date or
 * time the reader was not confident about is left for the candidate to enter.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage, inviteSlotConfident, slotFieldText } from './SubmitSlotPage.jsx'

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
const plain = text => text.replace(/\u00a0/g, ' ')
const whenButton = () => document.querySelector('.sbs-when__button')
const whenText = () => plain(whenButton().querySelector('.sbs-when__value').textContent)
const openWhen = () => fireEvent.click(whenButton())

async function openAndRead(invite) {
  const calls = stubFetch(invite)
  render(<SubmitSlotPage />)
  const confirm = await screen.findByRole('button', { name: /Confirm booking/i })
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled())
  Object.defineProperty(inviteInput(), 'files', { value: [new File(['x'], 'invite.jpg', { type: 'image/jpeg' })], configurable: true })
  fireEvent.change(inviteInput())
  await waitFor(() => expect(document.querySelector('.ai-node-progress')?.textContent || '').toMatch(/^✓/))
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
    expect(whenText()).toBe(plain(slotFieldText({ date: upcomingDate(), time: '03:00 PM', time_end: '03:45 PM' })))
    expect(whenText()).toMatch(/ · 03:00 PM – 03:45 PM$/)
    // The one picker holds the same three values.
    openWhen()
    expect(field('Interview date').value).toBe(upcomingDate())
    expect(field('Start time (IST)').value).toBe('15:00')
    expect(field('End time (IST)').value).toBe('15:45')

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

  it('shows what was read in the fields themselves: no summary, no slot preview, one status line', async () => {
    await openAndRead(CONFIDENT())
    expect(screen.queryByText('Read from invite')).toBeNull()
    expect(document.querySelector('.sbs-read, .sbs-manual, .sbs-slot-preview')).toBeNull()
    expect(screen.queryByRole('status', { name: 'Detected interview slot' })).toBeNull()
    const status = document.querySelectorAll('.ai-node-progress')
    expect(status).toHaveLength(1)
    expect(status[0].textContent).toMatch(/^✓ (Invite read|Analysed by .+) in \d+\.\ds$/)
    // The date and the whole range, once, in the one field; the picker stays closed until tapped.
    expect(whenText()).toMatch(/ · 03:00 PM – 03:45 PM$/)
    expect(document.querySelector('.sbs-when__panel')).toBeNull()
    expect(whenButton().getAttribute('aria-expanded')).toBe('false')
  })

  it('opens one picker for the date, start and end, and closes it with Done', async () => {
    await openAndRead(CONFIDENT())
    openWhen()
    const panel = screen.getByRole('dialog', { name: 'Interview date and time (IST)' })
    expect([...panel.querySelectorAll('.sbs-label')].map(l => l.textContent)).toEqual(['Interview date', 'Start time (IST)', 'End time (IST)'])
    expect(whenButton().getAttribute('aria-expanded')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(document.querySelector('.sbs-when__panel')).toBeNull()
    // Escape closes it too.
    openWhen()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(document.querySelector('.sbs-when__panel')).toBeNull()
  })

  it('lets every filled value be corrected, and books the correction', async () => {
    const { confirm, calls } = await openAndRead(CONFIDENT())
    openWhen()
    fireEvent.change(field('Start time (IST)'), { target: { value: '16:00' } })
    fireEvent.change(field('End time (IST)'), { target: { value: '16:30' } })
    // What was picked is what the field shows.
    expect(whenText()).toMatch(/ · 04:00 PM – 04:30 PM$/)
    fireEvent.change(field('Company'), { target: { value: 'Contoso India' } })
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
    expect(whenText()).toMatch(/ · 03:00 PM$/)
    openWhen()
    expect(field('End time (IST)').value).toBe('')
    await waitFor(() => expect(confirm.disabled).toBe(false))
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('time_end')).toBeNull()
  })

  it('a company or round the invite does not name is left empty', async () => {
    await openAndRead({ ...CONFIDENT(), company: '', interview_round: '' })
    expect(field('Company').value).toBe('')
    expect(screen.getByRole('combobox').value).toBe('')
  })

  it('a date and time read without confidence are shown but not filled, and Confirm waits for them', async () => {
    const { confirm, calls } = await openAndRead({
      ...CONFIDENT(), confidence_score: 0, manual_fields_required: true, auto_booking_safe: false,
      warnings: ['OCR and AI did not independently agree on the booking date/time.'],
    })
    expect(whenText()).toBe('Select date & time')
    expect(document.querySelector('.sbs-slot-hints').textContent)
      .toContain('The invite could not be read with confidence. Enter the date and time (IST).')
    expect(document.querySelector('.sbs-slot-hints').textContent)
      .toContain('OCR and AI did not independently agree on the booking date/time.')
    expect(confirm.disabled).toBe(true)

    openWhen()
    fireEvent.change(field('Interview date'), { target: { value: upcomingDate(8) } })
    fireEvent.change(field('Start time (IST)'), { target: { value: '17:00' } })
    await waitFor(() => expect(confirm.disabled).toBe(false))
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('date')).toBe(upcomingDate(8))
    expect(calls.confirms[0].get('time')).toBe('17:00')
  })

  it('warns when the end time is before the start', async () => {
    await openAndRead(CONFIDENT())
    openWhen()
    fireEvent.change(field('End time (IST)'), { target: { value: '14:00' } })
    expect(screen.getByText('End time is before the start time. Check it.')).toBeInTheDocument()
  })
})

describe('what the Interview date & time field shows', () => {
  it('the date and the whole time range, as in the mockup', () => {
    expect(plain(slotFieldText({ date: '2026-10-20', time: '14:00', time_end: '14:45' }))).toBe('20 Oct 2026 · 02:00 PM – 02:45 PM')
    expect(plain(slotFieldText({ date: '2026-10-20', time: '02:00 PM', time_end: '02:45 PM' }))).toBe('20 Oct 2026 · 02:00 PM – 02:45 PM')
  })

  it('just the start when there is no end time', () => {
    expect(plain(slotFieldText({ date: '2026-10-20', time: '09:30' }))).toBe('20 Oct 2026 · 09:30 AM')
  })

  it('says what is still missing when only one of date and start is set', () => {
    expect(plain(slotFieldText({ date: '2026-10-20' }))).toBe('20 Oct 2026 · add start time')
    expect(plain(slotFieldText({ time: '14:00' }))).toBe('add date · 02:00 PM')
  })

  it('nothing until a date or a start is set', () => {
    expect(slotFieldText({})).toBe('')
    expect(slotFieldText()).toBe('')
  })

  it('wraps on a phone only at the separators, never inside a date or a time', () => {
    const text = slotFieldText({ date: '2026-10-20', time: '14:00', time_end: '14:45' })
    expect(text.split(/ /)).toEqual(['20\u00a0Oct\u00a02026', '·', '02:00\u00a0PM', '–', '02:45\u00a0PM'])
  })
})
