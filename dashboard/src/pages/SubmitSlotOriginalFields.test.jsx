/**
 * The Book Interview Slot form shows its original fields only: Service type, Client name, Interview invite
 * screenshot, Company, Interview round, Confirm booking.
 *
 * The invite fills Company and Interview round when it names them clearly, and the date and times it read
 * confidently, straight into editable Interview date, Start and End (IST) fields that follow the round. There is
 * no separate summary of what was read and no separate date box (19 Oct). No other detail fields (time zone,
 * platform, meeting link) appear.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage, roundFromInvite } from './SubmitSlotPage.jsx'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function upcomingDate(days = 7) {
  const date = new Date()
  date.setDate(date.getDate() + days)
  return date.toISOString().slice(0, 10)
}

const READ = {
  interview_date: upcomingDate(), start_time: '11:00 AM', end_time: '11:45 AM', company: 'Contoso Ltd', interview_round: 'L1 Technical',
  meeting_platform: 'Microsoft Teams', confirmed_platform: 'Microsoft Teams', confidence_score: 95,
  meeting_link: 'https://teams.microsoft.com/l/meetup-join/19%3ameeting_test%40thread.v2/0',
  confirmed_meeting_link: 'https://teams.microsoft.com/l/meetup-join/19%3ameeting_test%40thread.v2/0', confirmed_timezone: 'IST',
}

function stubFetch(read = READ) {
  const calls = { confirms: [], read }
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const target = String(url)
    const reply = body => Promise.resolve({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: () => Promise.resolve(body) })
    if (target.includes('/public/slots/payment-requirement')) return reply({ status: 'ok', service_type: 'profile_service', amount_due: 0, payment_required: false })
    if (target.includes('/extract-invite-ai')) return reply({ status: 'ok', success: true, data: { ...calls.read } })
    if (target.includes('/bookings/confirm')) {
      calls.confirms.push(options?.body)
      return reply({ status: 'ok', candidate: { name: 'Aniket' } })
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: [{ id: 'c1', name: 'Aniket', needs_payment_proof: false, balance_due: 0 }] })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  return calls
}

const inviteInput = () => [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)
const roundSelect = () => document.querySelector('select.sbs-select')
const labels = () => [...document.querySelectorAll('form.sbs-form .sbs-label')].map(l => l.textContent.replace(/\s+/g, ' ').trim())
const readDone = () => waitFor(() => expect(document.querySelector('.ai-node-progress')?.textContent || '').toMatch(/^✓/))

async function readyForm(read) {
  const calls = stubFetch(read)
  render(<SubmitSlotPage />)
  const confirm = await screen.findByRole('button', { name: /Confirm booking/i })
  fireEvent.change(document.querySelector('.sbs-field--client input'), { target: { value: 'Aniket' } })
  Object.defineProperty(inviteInput(), 'files', { value: [new File(['x'], 'invite.jpg', { type: 'image/jpeg' })], configurable: true })
  fireEvent.change(inviteInput())
  await readDone()
  return { confirm, calls }
}

describe('the form shows its original fields only', () => {
  // The invite moved to the top on 19 Oct; the other fields keep their order.
  const ORIGINAL = [
    'Interview invite screenshot', 'Service type', 'Client name', 'Company *', 'Interview round *',
    'Interview date', 'Start time (IST)', 'End time (IST)',
  ]

  it('before an invite is attached', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByLabelText('Company')
    expect(labels()).toEqual(ORIGINAL)
    expect(screen.getByRole('button', { name: /Confirm booking/i })).toBeInTheDocument()
    // The date and times wait for the invite: a later upload must never wipe what was typed.
    for (const label of ['Interview date', 'Start time (IST)', 'End time (IST)']) expect(screen.getByLabelText(label).disabled).toBe(true)
  })

  it('and after the invite has been read: the same fields, filled and open, and nothing else', async () => {
    await readyForm()
    expect(labels()).toEqual(ORIGINAL)
    for (const label of ['Interview date', 'Start time (IST)', 'End time (IST)']) expect(screen.getByLabelText(label).disabled).toBe(false)
    for (const gone of ['Time zone', 'Platform', 'Meeting link']) expect(screen.queryByLabelText(gone)).toBeNull()
    expect(document.querySelector('.sbs-detected-compact')).toBeNull()
    expect(document.querySelector('.sbs-details')).toBeNull()
    // No "Read from invite" summary and no separate green date box: the fields are the result.
    expect(screen.queryByText('Read from invite')).toBeNull()
    expect(document.querySelector('.sbs-read, .sbs-manual')).toBeNull()
  })

  it('still says the invite was read, and by which node, as its status line', async () => {
    await readyForm()
    expect(document.querySelector('.ai-node-progress').textContent).toMatch(/^✓ (Invite read|Analysed by .+) in \d+\.\ds$/)
  })
})

describe('the invite still works in the background', () => {
  it('fills Company and the Interview round it clearly names', async () => {
    await readyForm()
    expect(screen.getByLabelText('Company').value).toBe('Contoso Ltd')
    expect(roundSelect().value).toBe('L1')
  })

  it('books the date and times it read, end time included, and nothing it did not show', async () => {
    const { confirm, calls } = await readyForm()
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    const body = calls.confirms[0]
    expect(body.get('date')).toBe(READ.interview_date)
    expect(body.get('time')).toBe('11:00')
    expect(body.get('time_end')).toBe('11:45')
    expect(body.get('company')).toBe('Contoso Ltd')
    expect(body.get('interview_round')).toBe('L1')
    for (const key of ['meeting_platform', 'meeting_link', 'timezone']) expect(body.has(key)).toBe(false)
  })

  it('a company or round the candidate corrects is what is booked', async () => {
    const { confirm, calls } = await readyForm()
    fireEvent.change(screen.getByLabelText('Company'), { target: { value: 'Contoso India' } })
    fireEvent.change(roundSelect(), { target: { value: 'L2' } })
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('company')).toBe('Contoso India')
    expect(calls.confirms[0].get('interview_round')).toBe('L2')
  })

  it('leaves the round to the candidate when the invite is not clear, and asks for it', async () => {
    const { confirm, calls } = await readyForm({ ...READ, interview_round: 'L1 / Final' })
    expect(roundSelect().value).toBe('')
    fireEvent.click(confirm)
    expect(await screen.findByText('Choose the interview round.')).toBeInTheDocument()
    expect(calls.confirms).toHaveLength(0)
  })
})

describe('the manual fallback is unchanged', () => {
  it('says why when the reader could not give a date, and the typed date and time are booked', async () => {
    const { confirm, calls } = await readyForm({
      ...READ, interview_date: '', start_time: '', end_time: '', confidence_score: 40, manual_fields_required: true,
      warnings: ['The AI could not read the interview date. Enter it to continue.'],
    })
    const hints = document.querySelector('.sbs-slot-hints')
    expect(hints.textContent).toContain('The AI could not read the interview date. Enter it to continue.')
    expect(hints.textContent).toContain('No date or time found on the invite. Enter them (IST).')
    fireEvent.change(screen.getByLabelText('Interview date'), { target: { value: upcomingDate(9) } })
    fireEvent.change(screen.getByLabelText('Start time (IST)'), { target: { value: '03:30 PM' } })
    await waitFor(() => expect(confirm.disabled).toBe(false))
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('date')).toBe(upcomingDate(9))
    expect(calls.confirms[0].get('time')).toBe('15:30')
  })

  it('asks for the date and time after an uncertain read, and leaves them empty', async () => {
    await readyForm({ ...READ, confidence_score: 50 })
    expect(screen.getByLabelText('Interview date').value).toBe('')
    expect(screen.getByLabelText('Start time (IST)').value).toBe('')
    expect(document.querySelector('.sbs-slot-hints').textContent)
      .toContain('The invite could not be read with confidence. Enter the date and time (IST).')
  })
})

describe('the interview round the invite names', () => {
  it.each([
    ['L1', 'L1'], ['L1 Technical', 'L1'], ['Technical Round 1', 'L1'], ['Round 2', 'L2'], ['L2 - Managerial', 'L2'],
    ['Screening call', 'Screening'], ['Final round', 'Final'], ['HR discussion', 'HR'],
    // 19 Oct: how real invites write it.
    ['Interview 2', 'L2'], ['2nd Round', 'L2'], ['2nd Interview Round', 'L2'], ['Level I', 'L1'],
    ['Level II', 'L2'], ['First round', 'L1'], ['Second technical round', 'L2'], ['Round I', 'L1'],
  ])('%s -> %s', (text, option) => {
    expect(roundFromInvite(text)).toBe(option)
  })

  it.each(['', 'Technical', 'L3', 'L1 / Final', 'Round 1 and Round 2', 'Candidate ID 123', 'hrs',
    'Interview 3', '3rd Round', 'Manager Round', 'Level I and Level II', 'Interview'])('names none of the options, or more than one: %s', text => {
    expect(roundFromInvite(text)).toBe('')
  })
})
