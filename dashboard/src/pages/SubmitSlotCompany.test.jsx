/**
 * Company on the booking form and on Confirmed slots.
 *
 * The form takes the company the interview is with (optional -- an invite
 * does not always name it) and sends it with the booking; Confirmed slots
 * shows it beside the technology on the card's second line, with the round
 * beside the name and the status and source as two small chips, so a card is
 * two lines rather than a stack of three chips.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, '..', 'index.css'), 'utf8').replace(/\r\n/g, '\n')

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function upcomingDate() {
  const date = new Date()
  date.setDate(date.getDate() + 7)
  return date.toISOString().slice(0, 10)
}

const SLOTS = [
  { name: 'Asha Rao', technology: 'Automation Testing', company: 'Capgemini', interview_round: 'L2',
    date: '2026-10-20', time: '17:00', time_end: '18:00', interview_booking_source: 'ai_auto_booked' },
  { name: 'Vikram Devi', technology: 'React JS', company: '', interview_round: 'L1',
    date: '2026-10-20', time: '12:00', time_end: '12:30', interview_booking_source: 'candidate_booked' },
]

function stubFetch() {
  const calls = { confirms: [] }
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const target = String(url)
    const reply = body => Promise.resolve({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: () => Promise.resolve(body) })
    if (target.includes('/public/slots/payment-requirement')) return reply({ status: 'ok', service_type: 'profile_service', amount_due: 0, payment_required: false })
    if (target.includes('/extract-invite-ai')) return reply({ status: 'ok', success: true, data: { interview_date: upcomingDate(), start_time: '04:00 PM', confidence_score: 95 } })
    if (target.includes('/bookings/confirm')) {
      calls.confirms.push(options?.body)
      return reply({ status: 'ok', candidate: { name: 'Aniket' } })
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: SLOTS })
    return reply({ status: 'ok', candidates: [{ id: 'c1', name: 'Aniket', needs_payment_proof: false, balance_due: 0 }] })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  return calls
}

async function completedForm({ company } = {}) {
  render(<SubmitSlotPage />)
  const confirm = await screen.findByRole('button', { name: /Confirm booking/i })
  fireEvent.change(document.querySelector('.sbs-field input'), { target: { value: 'Aniket' } })
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L1' } })
  if (company !== undefined) fireEvent.change(screen.getByLabelText('Company'), { target: { value: company } })
  const invite = [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)
  Object.defineProperty(invite, 'files', { value: [new File(['x'], 'invite.jpg', { type: 'image/jpeg' })], configurable: true })
  fireEvent.change(invite)
  await waitFor(() => expect(screen.queryByText(/reading invite/i)).toBeNull())
  return confirm
}

describe('the booking form', () => {
  it('asks for the company, optionally, before the round and in its row', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    const field = await screen.findByLabelText('Company')
    expect(field.tagName).toBe('INPUT')
    expect(field).not.toBeRequired()
    expect(field.closest('label').textContent).toMatch(/optional/i)
    const row = field.closest('.sbs-field-row')
    const labels = [...row.querySelectorAll(':scope > .sbs-field .sbs-label')].map(l => l.textContent)
    expect(labels[0]).toMatch(/^Company/)
    expect(labels[1]).toMatch(/^Interview round/)
  })

  it('sends the company with the booking', async () => {
    const calls = stubFetch()
    fireEvent.click(await completedForm({ company: '  Capgemini  ' }))
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('company')).toBe('Capgemini')
  })

  it('sends no company when none was entered', async () => {
    const calls = stubFetch()
    fireEvent.click(await completedForm())
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].has('company')).toBe(false)
  })
})

describe('a Confirmed slots card', () => {
  async function openConfirmed() {
    stubFetch()
    render(<SubmitSlotPage />)
    fireEvent.click(await screen.findByRole('tab', { name: /confirmed slots/i }))
    await screen.findByText('Asha Rao')
  }
  const cardFor = name => screen.getByText(name).closest('.sbs-confirmed-card')

  it('shows company and technology on the line under the name', async () => {
    await openConfirmed()
    const card = cardFor('Asha Rao')
    expect(card.querySelector('.sbs-slot-card__company').textContent).toBe('Capgemini')
    expect(card.querySelector('.sbs-slot-card__tech').textContent).toBe('Automation Testing')
    expect(card.querySelector('.sbs-slot-card__line .sbs-slot-card__time')).not.toBeNull()
  })

  it('shows "—" for the company when none was recorded', async () => {
    await openConfirmed()
    const card = cardFor('Vikram Devi')
    const none = card.querySelector('.sbs-slot-card__company--none')
    expect(none.textContent).toBe('\u2014')
    expect(none).toHaveAttribute('aria-label', 'No company recorded')
    expect(card.querySelector('.sbs-slot-card__tech').textContent).toBe('React JS')
  })

  it('puts the round beside the name and the two chips side by side', async () => {
    await openConfirmed()
    const card = cardFor('Asha Rao')
    expect(card.querySelector('.sbs-slot-card__name-row .sbs-slot-card__round').textContent).toBe('L2')
    const right = card.querySelector('.sbs-confirmed-card__right')
    // No per-card "Booked" chip: only the source (and "Awaiting status" when due).
    expect([...right.children].map(c => c.className.split(' ')[0])).toEqual(['sbs-source-badge'])
    expect(within(card).getByLabelText('AI Auto-booked').textContent).toBe('AI')
    expect(within(cardFor('Vikram Devi')).getByLabelText('Candidate booked').textContent).toBe('Candidate')
  })

  it('lays the chips in a row and lets the details line wrap on a phone', () => {
    const rule = sel => { const at = CSS.indexOf(`${sel} {`); return at < 0 ? '' : CSS.slice(at, CSS.indexOf('}', at)) }
    expect(rule('.sbs-confirmed-card__right')).toMatch(/flex-direction:\s*row/)
    expect(rule('.sbs-slot-card__line')).toMatch(/flex-wrap:\s*wrap/)
  })

  it('gives the company input and the round select one height in their row', () => {
    expect(CSS).toMatch(/\.sbs-field-row \.sbs-input,\n\.sbs-field-row \.sbs-select \{ height: 44px; min-height: 44px;/)
  })

  it('on a phone puts the time on its own line, so no line starts with a dot', () => {
    const at = CSS.indexOf('/* On a phone: time on its own line')
    expect(at).toBeGreaterThan(-1)
    const block = CSS.slice(at, CSS.indexOf('\n}\n', at))
    expect(block).toMatch(/\.sbs-slot-card__time \{ flex-basis: 100%; \}/)
    expect(block).toMatch(/\.sbs-slot-card__company::before \{ content: none; \}/)
    // A long company shortens so the technology stays on the same line.
    expect(block).toMatch(/\.sbs-slot-card__company \{ margin-left: 0; max-width: 60%; \}/)
  })
})
