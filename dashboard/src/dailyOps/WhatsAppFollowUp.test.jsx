/**
 * WhatsApp on every Daily Ops booking (owner, 24 Nov): the icon opens a chat
 * with a short message built from the card -- the candidate's name, company and
 * round -- asking for attendance while Pending and for the result once Attended.
 */
import React from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { whatsappLink, whatsappMessage, whatsappNumber } from './whatsappMessage.js'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'

describe('the message', () => {
  const card = { name: 'Tummala Sankara Sai', company: 'Accenture', round: 'L1' }

  it('asks a Pending candidate to confirm attendance', () => {
    expect(whatsappMessage({ ...card, status: '' }))
      .toBe('Hi Tummala Sankara Sai, please confirm your attendance for your L1 interview with Accenture.')
    expect(whatsappMessage({ ...card, status: 'pending' })).toContain('please confirm your attendance')
  })

  it('asks an Attended candidate for the result', () => {
    expect(whatsappMessage({ ...card, status: 'attended' }))
      .toBe('Hi Tummala Sankara Sai, how did your L1 interview with Accenture go? Please share the result when you hear back.')
  })

  it('follows up plainly on any other status', () => {
    expect(whatsappMessage({ ...card, status: 'not_attended' }))
      .toBe('Hi Tummala Sankara Sai, following up on your L1 interview with Accenture.')
  })

  it('leaves out what the card does not have', () => {
    expect(whatsappMessage({ name: 'Asha', company: '', round: '', status: '' }))
      .toBe('Hi Asha, please confirm your attendance for your interview.')
    expect(whatsappMessage({ name: 'Asha', company: 'Mphasis', round: 'Assessment', status: '' }))
      .toBe('Hi Asha, please confirm your attendance for your assessment with Mphasis.')
  })
})

describe('the number', () => {
  it('adds India\'s code to a 10-digit number and keeps a full one', () => {
    expect(whatsappNumber('7036580775')).toBe('917036580775')
    expect(whatsappNumber('+91 70365 80775')).toBe('917036580775')
    expect(whatsappNumber('07036580775')).toBe('917036580775')
    expect(whatsappNumber('+44 7700 900123')).toBe('447700900123')
  })

  it('gives no link without a usable number', () => {
    expect(whatsappNumber('')).toBe('')
    expect(whatsappNumber('12345')).toBe('')
    expect(whatsappLink({ name: 'Asha', phone: '' })).toBe('')
  })

  it('puts the message in the wa.me link', () => {
    const href = whatsappLink({ name: 'Asha', phone: '9000000001', company: 'Omhra', round: 'L2', status: '' })
    expect(href.startsWith('https://wa.me/919000000001?text=')).toBe(true)
    expect(decodeURIComponent(href.split('?text=')[1])).toBe('Hi Asha, please confirm your attendance for your L2 interview with Omhra.')
  })
})

describe('on the roster', () => {
  const TODAY = '2026-09-28'
  const json = body => ({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body })
  const row = overrides => ({
    id: 'r1', name: 'Asha Rao', phone: '9000000001', date: TODAY, time: '10:00', time_end: '11:00',
    technology: 'Python', interview_round: 'L1', company: 'Omhra', interview_attendee: 'Bhavana',
    interview_attendance_status: '', ...overrides,
  })
  let rows

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
    rows = [row({}), row({ id: 'r2', name: 'Ravi Kumar', phone: '9000000002', interview_attendance_status: 'attended' }), row({ id: 'r3', name: 'No Phone', phone: '' })]
    vi.stubGlobal('fetch', vi.fn(async input => {
      const url = new URL(String(input), 'http://localhost')
      if (url.pathname.endsWith('/interviews/global')) {
        return json({ status: 'ok', interviews: {}, available_months: [], booking_overview: { total: 0, by_candidate: [], by_level: [], by_technology: [] } })
      }
      if (/\/interviews\/(daily|monitor)$/.test(url.pathname)) return json({ status: 'ok', interviews: rows, count: rows.length, awaiting_interviews: [] })
      return json({ status: 'ok' })
    }))
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('shows a WhatsApp link on every booking with a number, worded for its status', async () => {
    render(<DailyOpsPanel />)
    await waitFor(() => expect(screen.getByText('Ravi Kumar')).toBeInTheDocument())
    await act(async () => { await Promise.resolve() })

    const pending = screen.getByRole('link', { name: 'WhatsApp Asha Rao' })
    expect(pending).toHaveAttribute('target', '_blank')
    expect(pending).toHaveAttribute('rel', expect.stringContaining('noopener'))
    expect(decodeURIComponent(pending.getAttribute('href').split('?text=')[1])).toContain('please confirm your attendance for your L1 interview')

    const attended = screen.getByRole('link', { name: 'WhatsApp Ravi Kumar' })
    expect(decodeURIComponent(attended.getAttribute('href').split('?text=')[1])).toContain('Please share the result')

    expect(screen.queryByRole('link', { name: 'WhatsApp No Phone' })).toBeNull()
  })

  it('does not open the row\'s Edit dialog when clicked', async () => {
    render(<DailyOpsPanel />)
    await waitFor(() => expect(screen.getByText('Asha Rao')).toBeInTheDocument())
    const link = screen.getByRole('link', { name: 'WhatsApp Asha Rao' })
    link.addEventListener('click', event => event.preventDefault())
    fireEvent.click(link)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
