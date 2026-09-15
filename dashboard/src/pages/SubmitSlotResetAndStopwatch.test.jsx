/**
 * After a booking the whole form resets, and each analysis shows how long it
 * has taken.
 *
 * The booking used to clear the fields but not what the analyses left behind:
 * the invite's green result card -- "✓ Analysed by RTX 4060" -- stayed under an
 * empty form as though it belonged to the next booking. Now everything goes:
 * fields, both uploads, both results, the node that read them, their times and
 * every warning.
 *
 * The stopwatch ticks while an analysis runs, freezes when it ends -- beside
 * the result it produced, or on its own when there is no result card -- and
 * clears with the form. It is display only.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const pause = ms => act(() => new Promise(resolve => setTimeout(resolve, ms)))

function upcomingDate() {
  const date = new Date()
  date.setDate(date.getDate() + 7)
  return date.toISOString().slice(0, 10)
}

function screenshot(label) {
  return new File([label], `${label}.jpg`, { type: 'image/jpeg' })
}

function attach(input, files) {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  fireEvent.change(input)
}

function pending() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

const ANALYSED = { state: 'done', node: 'RTX 4060', analysed_by: ['RTX 4060'] }
const INVITE = {
  status: 'ok', success: true, analysis: ANALYSED,
  data: { interview_date: upcomingDate(), start_time: '05:00 PM', end_time: '06:00 PM', interview_round: 'L1', confidence_score: 95 },
}
const PAYMENT = {
  status: 'ok', proof_ids: ['proof-1'], verified_total: 5000, remaining_due: 0, amount_due: 5000,
  payment_complete: true, rejected: [], analysis: ANALYSED,
  ai_extractions: [{ is_payment_screenshot: true, amount: 5000, verified: true, utr_number: '629529860169' }],
}

function stubServer() {
  const server = { upload: pending(), invite: pending(), confirms: [], bookedLoads: 0 }
  const reply = (body, status = 200) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const target = String(url)
    if (target.includes('/public/slots/payment-requirement')) {
      return reply({ status: 'ok', service_type: 'round_wise', amount_due: 5000, payment_required: true, re_service: false })
    }
    if (target.includes('/public/slots/payment-proof')) return server.upload.promise
    if (target.includes('/extract-invite-ai')) return server.invite.promise
    if (target.includes('/bookings/confirm')) {
      server.confirms.push(options.body)
      return reply({ status: 'ok', candidate: { name: 'Rama krishna' } })
    }
    if (target.includes('/public/slots/booked')) {
      server.bookedLoads += 1
      return reply({ status: 'ok', slots: [] })
    }
    return reply({ status: 'ok', candidates: [] })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  server.answerUpload = (body, status = 200) =>
    act(async () => server.upload.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) }))
  server.answerInvite = body =>
    act(async () => server.invite.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) }))
  return server
}

const paymentInput = () => [...document.querySelectorAll('input[type="file"]')].find(i => i.multiple)
const inviteInput = () => [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)
const timerIn = selector => document.querySelector(`${selector} .sbs-status--loading .sbs-timer`)
const seconds = text => Number(String(text).replace(/s$/, ''))

async function roundWiseDetails() {
  render(<SubmitSlotPage />)
  fireEvent.click(await screen.findByRole('button', { name: /profile service/i }))
  fireEvent.click(await screen.findByText('Round-wise'))
  fireEvent.change(screen.getByPlaceholderText(/type client name/i), { target: { value: 'Rama krishna' } })
  fireEvent.change(screen.getByPlaceholderText(/10-digit phone number/i), { target: { value: '8897870998' } })
  fireEvent.change(screen.getByPlaceholderText(/choose or type the technology/i), { target: { value: 'Automation Testing' } })
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L1' } })
  await waitFor(() => expect(paymentInput().disabled).toBe(false))
}

describe('the analysis stopwatch', () => {
  it('ticks while the payment is analysed and freezes beside its result', async () => {
    const server = stubServer()
    await roundWiseDetails()
    attach(paymentInput(), [screenshot('4500')])

    await waitFor(() => expect(timerIn('.sbs-pay-card')).not.toBeNull())
    const first = seconds(timerIn('.sbs-pay-card').textContent)
    await pause(350)
    expect(timerIn('.sbs-pay-card').textContent).toMatch(/^\d+\.\ds$/)
    expect(seconds(timerIn('.sbs-pay-card').textContent)).toBeGreaterThan(first)

    await server.answerUpload(PAYMENT)
    await waitFor(() => expect(document.querySelector('.sbs-pay-result')).not.toBeNull())
    const result = () => document.querySelector('.sbs-pay-result .sbs-detected-compact__text').textContent
    expect(result()).toMatch(/^Payment verified · Analysed by RTX 4060 in \d+\.\ds$/)
    expect(timerIn('.sbs-pay-card')).toBeNull()

    // Frozen: it does not keep counting once the analysis is over.
    const frozen = result()
    await pause(300)
    expect(result()).toBe(frozen)
  })

  it('shows a refused payment its time on its own, with no result card', async () => {
    const server = stubServer()
    await roundWiseDetails()
    attach(paymentInput(), [screenshot('wrong')])
    await waitFor(() => expect(timerIn('.sbs-pay-card')).not.toBeNull())

    await server.answerUpload({
      status: 'error', message: 'Receiver is not registered.',
      rejected: [{ filename: 'wrong.jpg', message: 'Receiver is not registered.' }],
    }, 400)

    await waitFor(() => expect(document.querySelector('.sbs-pay-card .sbs-status--done')).not.toBeNull())
    expect(document.querySelector('.sbs-pay-card .sbs-status--done').textContent).toMatch(/^Analysed in \d+\.\ds$/)
    expect(document.querySelector('.sbs-pay-result')).toBeNull()
  })

  it('ticks while the invite is read and freezes beside its result', async () => {
    const server = stubServer()
    render(<SubmitSlotPage />)
    await screen.findByRole('button', { name: /confirm booking/i })
    attach(inviteInput(), [screenshot('invite')])

    await waitFor(() => expect(document.querySelector('.sbs-status--loading .sbs-timer')).not.toBeNull())
    const timer = () => document.querySelector('.sbs-status--loading .sbs-timer')
    const first = seconds(timer().textContent)
    await pause(350)
    expect(seconds(timer().textContent)).toBeGreaterThan(first)

    await server.answerInvite(INVITE)
    expect(await screen.findByText(/^✓ Analysed by RTX 4060 in \d+\.\ds$/)).toBeTruthy()
    expect(document.querySelector('.sbs-timer')).toBeNull()
  })

  it('never keeps a time once its invite is removed', async () => {
    const server = stubServer()
    render(<SubmitSlotPage />)
    await screen.findByRole('button', { name: /confirm booking/i })
    attach(inviteInput(), [screenshot('invite')])
    await server.answerInvite(INVITE)
    await screen.findByText(/^✓ Analysed by RTX 4060 in \d+\.\ds$/)

    fireEvent.click(screen.getByRole('button', { name: /remove invite\.jpg/i }))

    await waitFor(() => expect(screen.queryByText(/analysed by|read in/i)).toBeNull())
  })
})

describe('after a booking the whole form resets', () => {
  it('clears every field, both uploads, both results, their nodes and times', async () => {
    const server = stubServer()
    await roundWiseDetails()

    attach(paymentInput(), [screenshot('4500')])
    await server.answerUpload(PAYMENT)
    await screen.findByText(/payment verified/i)
    attach(inviteInput(), [screenshot('invite')])
    await server.answerInvite(INVITE)
    await screen.findByText(/^✓ Analysed by RTX 4060 in \d+\.\ds$/)

    const confirm = screen.getByRole('button', { name: /confirm booking/i })
    await waitFor(() => expect(confirm.disabled).toBe(false))
    const loadsBefore = server.bookedLoads
    fireEvent.click(confirm)

    expect(await screen.findByText('Slot confirmed for Rama krishna.')).toBeTruthy()
    expect(server.confirms).toHaveLength(1)

    // Back to the form a new booking starts from.
    expect(screen.getByRole('button', { name: /profile service/i })).toBeTruthy()
    expect(screen.getByPlaceholderText(/choose or type your name/i).value).toBe('')
    expect(screen.getByRole('combobox').value).toBe('')
    expect(screen.queryByPlaceholderText(/10-digit phone number/i)).toBeNull()
    expect(screen.queryByPlaceholderText(/choose or type the technology/i)).toBeNull()

    // Nothing an analysis left behind survives it.
    expect(screen.getByText('Drop invite screenshot here')).toBeTruthy()
    expect(document.querySelector('.sbs-detected-compact')).toBeNull()
    expect(document.querySelector('.sbs-pay-card')).toBeNull()
    expect(document.querySelector('.sbs-manual')).toBeNull()
    expect(document.querySelector('.sbs-timer')).toBeNull()
    expect(document.querySelector('.sbs-status')).toBeNull()
    expect(screen.queryByText(/analysed by|analysed in|read in|payment verified/i)).toBeNull()
    expect(document.querySelectorAll('.sbs-hint--warn')).toHaveLength(0)
    expect(document.querySelector('.sbs-alert--error')).toBeNull()

    // The booking itself is kept: the confirmed list is loaded again.
    await waitFor(() => expect(server.bookedLoads).toBeGreaterThan(loadsBefore))
  })
})
