/**
 * The interview invite screenshot is the first field (19 Oct).
 *
 * Reading it fills what it can -- client, technology, company, round, date and
 * time -- so it sits above Service type and Client name, and every other field
 * keeps its order. Because the invite is now usually read before the service
 * type is chosen, changing the service type afterwards must not throw away what
 * the invite filled: the client name is matched (profile service) or offered
 * (round-wise) again, and the technology the invite named is put back.
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

const CLIENTS = [
  { id: 'c-ira', name: 'Ira Testwala', technology: 'Java', needs_payment_proof: false, balance_due: 0 },
]

function stubFetch() {
  vi.stubGlobal('fetch', vi.fn((url) => {
    const target = String(url)
    const reply = body => Promise.resolve({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: () => Promise.resolve(body) })
    if (target.includes('/payment-requirement')) return reply({ status: 'ok', service_type: 'round_wise', amount_due: 0, payment_required: false, re_service: true })
    if (target.includes('/extract-invite-ai')) {
      return reply({ status: 'ok', success: true, data: {
        interview_date: upcomingDate(), start_time: '11:00 AM', company: 'Contoso Ltd', interview_round: 'L1',
        technology: 'Python', candidate_name: 'Ira Testwala', confidence_score: 95,
      } })
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: CLIENTS })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
}

const inviteInput = () => [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)
const nameBox = () => document.querySelector('.sbs-field--client input')
const labels = () => [...document.querySelectorAll('.sbs-form > .sbs-field > .sbs-label, .sbs-form > .sbs-field-row > .sbs-field > .sbs-label')].map(l => l.textContent)

async function readInvite() {
  Object.defineProperty(inviteInput(), 'files', { value: [new File(['x'], 'invite.jpg', { type: 'image/jpeg' })], configurable: true })
  fireEvent.change(inviteInput())
  await waitFor(() => expect(document.querySelector('.ai-node-progress')?.textContent || '').toMatch(/^✓/))
}

function chooseService(label) {
  fireEvent.click(screen.getByRole('button', { name: /^(Profile service|Round-wise)$/ }))
  fireEvent.click(screen.getByText(label, { selector: 'li' }))
}

describe('the invite is the first field', () => {
  it('sits above Service type and Client name, and the rest keep their order', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByLabelText('Company')
    expect(labels()).toEqual(['Interview invite screenshot', 'Service type', 'Client name', 'Company *', 'Interview round *'])
    const first = document.querySelector('.sbs-form > .sbs-field')
    expect(first.contains(inviteInput())).toBe(true)
  })

  it('round-wise keeps the order below it: client, phone, technology, company, round', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByLabelText('Company')
    chooseService('Round-wise')
    expect(labels().map(l => l.replace(/ \*$/, ''))).toEqual([
      'Interview invite screenshot', 'Service type', 'Client name', 'Candidate phone', 'Technology', 'Company', 'Interview round',
    ])
  })

  it('reading it fills client, company and round', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByLabelText('Company')
    await readInvite()
    await waitFor(() => expect(nameBox().value).toBe('Ira Testwala'))
    expect(screen.getByLabelText('Company').value).toBe('Contoso Ltd')
    expect(screen.getByRole('combobox').value).toBe('L1')
  })
})

describe('choosing the service type after the invite is read keeps what it filled', () => {
  it('round-wise: the read technology is put back and the read name is offered', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByLabelText('Company')
    await readInvite()
    chooseService('Round-wise')
    await waitFor(() => expect(screen.getByPlaceholderText(/technology/i).value).toBe('Python'))
    // Round-wise names are the payment identity: offered, never entered.
    expect(nameBox().value).toBe('')
    expect(screen.getByText(/The invite names “Ira Testwala”/)).toBeInTheDocument()
    expect(screen.getByLabelText('Company').value).toBe('Contoso Ltd')
  })

  it('back to profile service: the client is matched again', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByLabelText('Company')
    await readInvite()
    chooseService('Round-wise')
    chooseService('Profile service')
    await waitFor(() => expect(nameBox().value).toBe('Ira Testwala'))
    expect(screen.getByText('Matched from the invite. Check it.')).toBeInTheDocument()
  })

  it('without an invite, changing the service type still starts from an empty name', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByLabelText('Company')
    chooseService('Round-wise')
    expect(nameBox().value).toBe('')
    expect(screen.queryByText(/The invite names/)).toBeNull()
  })
})
