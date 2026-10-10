/**
 * More of the invite read into the booking form: end time, time zone, platform and meeting link.
 *
 * Only values the reader confirmed are filled (a known platform, a complete link on a meeting service, a
 * time zone the invite writes); the raw values it saw are not enough. Everything stays editable, what the
 * candidate types is never replaced by a reading, and a malformed end time or link is asked about at the
 * field, one at a time, like every other field.
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

const CONFIRMED = {
  end_time: '11:45 AM', confirmed_timezone: 'IST', confirmed_platform: 'Microsoft Teams',
  confirmed_meeting_link: 'https://teams.microsoft.com/l/meetup-join/19%3ameeting_test%40thread.v2/0',
}

function stubFetch() {
  const calls = { confirms: [], invite: { ...CONFIRMED } }
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const target = String(url)
    const reply = body => Promise.resolve({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: () => Promise.resolve(body) })
    if (target.includes('/public/slots/payment-requirement')) return reply({ status: 'ok', service_type: 'profile_service', amount_due: 0, payment_required: false })
    if (target.includes('/extract-invite-ai')) {
      return reply({ status: 'ok', success: true, data: {
        interview_date: upcomingDate(), start_time: '11:00 AM', company: 'Contoso', interview_round: 'L1', confidence_score: 95, ...calls.invite,
      } })
    }
    if (target.includes('/bookings/confirm')) {
      calls.confirms.push(options?.body)
      return reply({ status: 'ok', candidate: { name: 'Aniket' } })
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: [{ id: 'c1', name: 'Aniket', needs_payment_proof: false, balance_due: 0 }] })
  }))
  vi.stubGlobal('URL', Object.assign(function (...args) { return new globalThis.__RealURL(...args) }, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }))
  return calls
}
globalThis.__RealURL = globalThis.__RealURL || URL

const inviteInput = () => [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)
const field = label => screen.getByLabelText(label)
const hintOf = label => field(label).closest('label').querySelector('.sbs-hint')

async function attachInvite() {
  const invite = inviteInput()
  Object.defineProperty(invite, 'files', { value: [new File(['x'], 'invite.jpg', { type: 'image/jpeg' })], configurable: true })
  fireEvent.change(invite)
  await waitFor(() => expect(screen.queryByText(/reading invite/i)).toBeNull())
  await waitFor(() => expect(document.querySelector('.sbs-detected-compact')).not.toBeNull())
}

async function readyForm(invite) {
  const calls = stubFetch()
  if (invite) calls.invite = invite
  render(<SubmitSlotPage />)
  const confirm = await screen.findByRole('button', { name: /Confirm booking/i })
  fireEvent.change(document.querySelector('.sbs-field input'), { target: { value: 'Aniket' } })
  await attachInvite()
  return { confirm, calls }
}

describe('what the invite confirms is filled in', () => {
  it('fills end time, time zone, platform and meeting link and says where they came from', async () => {
    await readyForm()
    expect(field('End time').value).toBe('11:45 AM')
    expect(field('Time zone').value).toBe('IST')
    expect(field('Platform').value).toBe('Microsoft Teams')
    expect(field('Meeting link').value).toBe(CONFIRMED.confirmed_meeting_link)
    for (const label of ['End time', 'Time zone', 'Platform', 'Meeting link']) {
      expect(hintOf(label).textContent).toBe('Read from the invite. Check it.')
    }
  })

  it('sends them with the booking, the end time as 24-hour like the start', async () => {
    const { confirm, calls } = await readyForm()
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    const body = calls.confirms[0]
    expect(body.get('time_end')).toBe('11:45')
    expect(body.get('meeting_platform')).toBe('Microsoft Teams')
    expect(body.get('meeting_link')).toBe(CONFIRMED.confirmed_meeting_link)
    expect(body.get('timezone')).toBe('IST')
    expect(body.get('company')).toBe('Contoso')
  })

  it('uses only confirmed values: what the reader merely saw is not filled', async () => {
    await readyForm({ meeting_platform: 'Teams', meeting_link: 'https://teams.microsoft.com/l/meetup-join/19%3a...', timezone: 'Asia/Kolkata',
                      confirmed_platform: '', confirmed_meeting_link: '', confirmed_timezone: '' })
    expect(field('Platform').value).toBe('')
    expect(field('Meeting link').value).toBe('')
    expect(field('Time zone').value).toBe('')     // the reader's default time zone is not something the invite said
    expect(hintOf('Platform').textContent).toBe('Optional.')
  })

  it('sends nothing for details left empty', async () => {
    const { confirm, calls } = await readyForm({ confirmed_platform: '', confirmed_meeting_link: '', confirmed_timezone: '', end_time: '' })
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    for (const key of ['meeting_platform', 'meeting_link', 'timezone', 'time_end']) expect(calls.confirms[0].has(key)).toBe(false)
  })
})

describe('the candidate stays in charge', () => {
  it('a value read from the invite can be corrected, and the correction is what is booked', async () => {
    const { confirm, calls } = await readyForm()
    fireEvent.change(field('Platform'), { target: { value: 'Zoom' } })
    expect(hintOf('Platform').textContent).toBe('Optional.')
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('meeting_platform')).toBe('Zoom')
  })

  it('a typed value is never replaced by the next invite; a read one is replaced, or cleared when that invite has none', async () => {
    const { calls } = await readyForm()
    fireEvent.change(field('Platform'), { target: { value: 'Zoom' } })
    calls.invite = { confirmed_platform: 'Google Meet', confirmed_meeting_link: '', confirmed_timezone: 'EST', end_time: '' }
    await attachInvite()
    expect(field('Platform').value).toBe('Zoom')        // typed: kept
    expect(field('Time zone').value).toBe('EST')         // read: replaced
    expect(field('Meeting link').value).toBe('')         // read before, none now: cleared
    expect(field('End time').value).toBe('')
  })

  it('removing the invite clears what was read from it', async () => {
    await readyForm()
    fireEvent.click(screen.getByRole('button', { name: /remove invite screenshot/i }))
    await waitFor(() => expect(screen.queryByLabelText('Platform')).toBeNull())   // the group closes with the invite
  })

  it('a time zone other than IST is pointed out, plainly', async () => {
    await readyForm({ ...CONFIRMED, confirmed_timezone: 'EST' })
    expect(hintOf('Time zone').textContent).toBe('Not IST: check the time you enter.')
    expect(hintOf('Time zone').className).not.toContain('sbs-hint--warn')
  })
})

describe('the new fields are checked like the others, one at a time, at the field', () => {
  it('a malformed meeting link stops the booking and says so beside the link', async () => {
    const { confirm, calls } = await readyForm()
    fireEvent.change(field('Meeting link'), { target: { value: 'teams meeting' } })
    fireEvent.click(confirm)
    const warning = await screen.findByText('Enter the full link starting with https://, or leave it empty.')
    expect(warning.closest('label')).toBe(field('Meeting link').closest('label'))
    expect(document.querySelectorAll('.sbs-hint--warn')).toHaveLength(1)
    expect(calls.confirms).toHaveLength(0)
    fireEvent.change(field('Meeting link'), { target: { value: '' } })
    await waitFor(() => expect(screen.queryByText('Enter the full link starting with https://, or leave it empty.')).toBeNull())
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
  })

  it('an end time before the start stops the booking at the end time', async () => {
    const { confirm, calls } = await readyForm()
    fireEvent.change(field('End time'), { target: { value: '10:30 AM' } })
    fireEvent.click(confirm)
    expect(await screen.findByText('Enter a time after the start, like 03:00 PM, or leave it empty.')).toBeInTheDocument()
    expect(calls.confirms).toHaveLength(0)
  })

  it('an end time that is not a time stops the booking too', async () => {
    const { confirm, calls } = await readyForm()
    fireEvent.change(field('End time'), { target: { value: 'soon' } })
    fireEvent.click(confirm)
    expect(await screen.findByText('Enter a time after the start, like 03:00 PM, or leave it empty.')).toBeInTheDocument()
    expect(calls.confirms).toHaveLength(0)
  })
})

describe('where the details sit', () => {
  const before = (a, b) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

  it('appear only once an invite is attached, between the invite and Company', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByLabelText('Company')
    expect(screen.queryByLabelText('Platform')).toBeNull()
    fireEvent.change(document.querySelector('.sbs-field input'), { target: { value: 'Aniket' } })
    await attachInvite()
    expect(before(inviteInput(), field('End time'))).toBe(true)
    expect(before(field('Meeting link'), field('Company'))).toBe(true)
  })

  it('use the two-column rows the company and round already use', async () => {
    await readyForm()
    expect(field('End time').closest('.sbs-field-row')).toBe(field('Time zone').closest('.sbs-field-row'))
    expect(field('Platform').closest('.sbs-field-row')).toBe(field('Meeting link').closest('.sbs-field-row'))
  })
})
