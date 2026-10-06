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

  it('puts the interview on the right: company, then technology with the round', async () => {
    await openConfirmed()
    const right = cardFor('Asha Rao').querySelector('.sbs-confirmed-card__right.sbs-slot-card__data')
    expect([...right.children].map(c => c.className)).toEqual(['sbs-slot-card__company', 'sbs-slot-card__data-line'])
    expect(right.querySelector('.sbs-slot-card__company').textContent).toBe('Capgemini')
    const line = right.querySelector('.sbs-slot-card__data-line')
    expect([...line.children].map(c => c.textContent)).toEqual(['Automation Testing', 'L2'])
    expect(line.lastElementChild.className).toContain('sbs-slot-card__round--l2')
  })

  it('keeps who and when on the left, with the round no longer beside the name', async () => {
    await openConfirmed()
    const body = cardFor('Asha Rao').querySelector('.sbs-slot-card__body')
    expect(body.querySelector('.sbs-slot-card__name').textContent).toBe('Asha Rao')
    expect(body.querySelector('.sbs-slot-card__line .sbs-slot-card__time')).not.toBeNull()
    expect(body.querySelector('.sbs-slot-card__round')).toBeNull()
    expect(body.querySelector('.sbs-slot-card__company, .sbs-slot-card__tech')).toBeNull()
  })

  it('shows technology and round alone when no company was recorded: no "—", no empty slot', async () => {
    await openConfirmed()
    const card = cardFor('Vikram Devi')
    expect(card.querySelector('.sbs-slot-card__company')).toBeNull()
    const right = card.querySelector('.sbs-slot-card__data')
    expect(right.textContent).toBe('React JSL1')
    expect(card.textContent).not.toContain('\u2014')
  })

  it('keeps a long company whole in the DOM and on hover, for the CSS to shorten', async () => {
    SLOTS[0].company = 'Capgemini Technology Services India Private Limited, Bengaluru Delivery Centre'
    try {
      await openConfirmed()
      const company = cardFor('Asha Rao').querySelector('.sbs-slot-card__company')
      expect(company.textContent).toBe(SLOTS[0].company)
      expect(company).toHaveAttribute('title', SLOTS[0].company)
    } finally {
      SLOTS[0].company = 'Capgemini'
    }
  })

  it('shows the booking source as a small icon beside the name, not a text chip', async () => {
    await openConfirmed()
    const ai = within(cardFor('Asha Rao')).getByLabelText('AI Auto-booked')
    expect(ai.className).toBe('sbs-source-icon sbs-source-icon--auto')
    expect(ai.textContent).toBe('')
    expect(ai.querySelector('svg.ta-icon--sparkles')).not.toBeNull()
    expect(ai.getAttribute('title')).toMatch(/^AI Auto-booked: /)
    expect(ai.parentElement.className).toBe('sbs-slot-card__name-row')
    const candidate = within(cardFor('Vikram Devi')).getByLabelText('Candidate booked')
    expect(candidate.textContent).toBe('')
    expect(candidate.querySelector('svg.ta-icon--user-check')).not.toBeNull()
    expect(document.querySelector('.sbs-source-badge')).toBeNull()
    expect(screen.queryByText('Candidate')).toBeNull()
    expect(screen.queryByText('AI')).toBeNull()
  })

  it('says "Awaiting status" beside the time when an ended interview has no outcome', async () => {
    SLOTS.push({ name: 'Late Person', technology: 'Java', company: 'Infosys', interview_round: 'HR',
      date: '2026-09-30', time: '16:00', time_end: '16:30', interview_booking_source: 'candidate_booked',
      slot_phase: 'needs_status_update' })
    try {
      await openConfirmed()
      const card = cardFor('Late Person')
      const awaiting = card.querySelector('.sbs-confirmed-card__status--awaiting')
      expect(awaiting.textContent).toBe('Awaiting status')
      expect(awaiting.parentElement.className).toBe('sbs-slot-card__line')
      expect(card.querySelector('.sbs-slot-card__data').textContent).toBe('InfosysJavaHR')
      expect(cardFor('Asha Rao').querySelector('.sbs-confirmed-card__status--awaiting')).toBeNull()
    } finally {
      SLOTS.pop()
    }
  })

  it('stacks the right side and lets the time line wrap on a phone', () => {
    const rule = sel => { const at = CSS.indexOf(`${sel} {`); return at < 0 ? '' : CSS.slice(at, CSS.indexOf('}', at)) }
    expect(rule('.sbs-confirmed-card__right.sbs-slot-card__data')).toMatch(/flex-direction: column; align-items: flex-end;/)
    expect(rule('.sbs-slot-card__line')).toMatch(/flex-wrap:\s*wrap/)
    expect(rule('.sbs-slot-card__company')).toMatch(/text-overflow: ellipsis; white-space: nowrap;/)
  })

  it('gives the company input and the round select one height in their row', () => {
    expect(CSS).toMatch(/\.sbs-field-row \.sbs-input,\n\.sbs-field-row \.sbs-select \{ height: 44px; min-height: 44px;/)
  })

  it('gives the name the larger share on a phone and lets a long round label drop under', () => {
    const at = CSS.indexOf('/* On a phone the name keeps the larger share')
    expect(at).toBeGreaterThan(-1)
    const block = CSS.slice(at, CSS.indexOf('\n}\n', at))
    expect(block).toMatch(/\.sbs-confirmed-card__right\.sbs-slot-card__data \{ max-width: 45%; \}/)
    expect(block).toMatch(/\.sbs-slot-card__data-line \{ flex-wrap: wrap;/)
  })
})
