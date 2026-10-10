/**
 * The Book Interview Slot form shows its original fields only: Service type, Client name, Interview invite
 * screenshot, Company, Interview round, Confirm booking.
 *
 * The invite is still read in the background: it fills Company and Interview round when it names them clearly,
 * and the date and times it found are booked as they were read. What it read is not shown back as a panel, and
 * there are no extra detail fields. When the date or time is missing or uncertain, the manual date and time
 * appear as before (the fallback), with the reader's reason.
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
  fireEvent.change(document.querySelector('.sbs-field input'), { target: { value: 'Aniket' } })
  Object.defineProperty(inviteInput(), 'files', { value: [new File(['x'], 'invite.jpg', { type: 'image/jpeg' })], configurable: true })
  fireEvent.change(inviteInput())
  await readDone()
  return { confirm, calls }
}

describe('the form shows its original fields only', () => {
  const ORIGINAL = ['Service type', 'Client name', 'Interview invite screenshot', 'Company *', 'Interview round *']

  it('before an invite is attached', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByLabelText('Company')
    expect(labels()).toEqual(ORIGINAL)
    expect(screen.getByRole('button', { name: /Confirm booking/i })).toBeInTheDocument()
  })

  it('and after the invite has been read: no detail fields, no result panel', async () => {
    await readyForm()
    expect(labels()).toEqual(ORIGINAL)
    for (const gone of ['End time', 'Time zone', 'Platform', 'Meeting link']) expect(screen.queryByLabelText(gone)).toBeNull()
    expect(document.querySelector('.sbs-detected-compact')).toBeNull()
    expect(document.querySelector('.sbs-details')).toBeNull()
    expect(document.querySelector('.sbs-manual')).toBeNull()          // a confident read needs no manual date and time
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
  it('appears when the reader could not give a date, with its reason, and the typed date and time are booked', async () => {
    const { confirm, calls } = await readyForm({
      ...READ, interview_date: '', start_time: '', end_time: '', confidence_score: 40, manual_fields_required: true,
      warnings: ['The AI could not read the interview date. Enter it to continue.'],
    })
    const manual = document.querySelector('.sbs-manual')
    expect(manual).not.toBeNull()
    expect(manual.textContent).toContain('The AI could not read the interview date. Enter it to continue.')
    fireEvent.change(manual.querySelector('input[type="date"]'), { target: { value: upcomingDate(9) } })
    fireEvent.change(manual.querySelector('input[type="text"]'), { target: { value: '03:30 PM' } })
    await waitFor(() => expect(confirm.disabled).toBe(false))
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('date')).toBe(upcomingDate(9))
    expect(calls.confirms[0].get('time')).toBe('15:30')
  })

  it('appears for an uncertain read too, so the candidate can check the date and time', async () => {
    await readyForm({ ...READ, confidence_score: 50 })
    expect(document.querySelector('.sbs-manual')).not.toBeNull()
  })
})

describe('the interview round the invite names', () => {
  it.each([
    ['L1', 'L1'], ['L1 Technical', 'L1'], ['Technical Round 1', 'L1'], ['Round 2', 'L2'], ['L2 - Managerial', 'L2'],
    ['Screening call', 'Screening'], ['Final round', 'Final'], ['HR discussion', 'HR'],
  ])('%s -> %s', (text, option) => {
    expect(roundFromInvite(text)).toBe(option)
  })

  it.each(['', 'Technical', 'L3', 'L1 / Final', 'Round 1 and Round 2', 'Candidate ID 123', 'hrs'])('names none of the options, or more than one: %s', text => {
    expect(roundFromInvite(text)).toBe('')
  })
})
