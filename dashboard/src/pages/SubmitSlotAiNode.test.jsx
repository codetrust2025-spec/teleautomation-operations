/**
 * The booking page names the AI node reading an upload, as the server reports it.
 *
 * "Analysing…" said nothing about where the work was. The page now shows
 * "Waiting for AI node…" until the gateway puts the upload on a node, then
 * "● <node> · Analysing…" -- changing if the request moves to another node --
 * and "✓ Analysed by <node>" once the upload's response says which nodes
 * answered. Every node name in these tests arrives from the (fake) server; the
 * page has no list of nodes to fall back on.
 */
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

class FakeEventSource {
  static instances = []

  constructor(url) {
    this.url = url
    this.closed = false
    FakeEventSource.instances.push(this)
  }

  close() {
    this.closed = true
  }
}

/** The stream for the upload most recently sent. */
function stream() {
  return FakeEventSource.instances[FakeEventSource.instances.length - 1]
}

function push(status) {
  act(() => stream().onmessage({ data: JSON.stringify({ analysed_by: [], ...status }) }))
}

function screenshot(label) {
  return new File([label], `${label}.jpg`, { type: 'image/jpeg' })
}

function attach(input, files) {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  fireEvent.change(input)
}

function upcomingDate() {
  const date = new Date()
  date.setDate(date.getDate() + 7)
  return date.toISOString().slice(0, 10)
}

/** A promise the test settles, standing in for an upload still being analysed. */
function pending() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

function stubServer() {
  const server = { uploads: [], invites: [], parses: [], upload: pending(), invite: pending(), parse: pending() }
  const reply = (body, status = 200) =>
    Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const target = String(url)
    if (target.includes('/public/slots/payment-requirement')) {
      return reply({ status: 'ok', service_type: 'round_wise', amount_due: 5000, payment_required: true, re_service: false })
    }
    if (target.includes('/public/slots/payment-proof')) {
      server.uploads.push(options.body)
      return server.upload.promise
    }
    if (target.includes('/extract-invite-ai')) {
      server.invites.push(options.body)
      return server.invite.promise
    }
    if (target.includes('/public/slots/parse-screenshot')) {
      server.parses.push(options.body)
      return server.parse.promise
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: [] })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  server.finishUpload = (body, status = 200) =>
    act(async () => server.upload.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) }))
  server.finishInvite = body =>
    act(async () => server.invite.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) }))
  return server
}

async function startPaymentUpload() {
  render(<SubmitSlotPage />)
  fireEvent.click(await screen.findByRole('button', { name: /profile service/i }))
  fireEvent.click(await screen.findByText('Round-wise'))
  fireEvent.change(screen.getByPlaceholderText(/type client name/i), { target: { value: 'venkat' } })
  fireEvent.change(screen.getByPlaceholderText(/10-digit phone number/i), { target: { value: '7306994576' } })
  fireEvent.change(screen.getByPlaceholderText(/choose or type the technology/i), { target: { value: 'Java' } })
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'L1' } })
  await screen.findByText(/paid in parts\? attach each screenshot/i)
  // Attaching is the upload: the analysis starts at once, as the invite's does.
  attach(document.querySelectorAll('input[type="file"]')[0], [screenshot('receipt')])
}

/** The payment card's status row, where the node reading the receipt is named. */
const paymentStatus = () => document.querySelector('.sbs-pay-card .sbs-status--loading')

/** The payment card's green result line. */
const paymentResult = () => document.querySelector('.sbs-pay-result .sbs-detected-compact__text')

const ACCEPTED = {
  status: 'ok', proof_ids: ['proof-1'], verified_total: 5000, remaining_due: 0,
  amount_due: 5000, payment_complete: true, rejected: [],
  ai_extractions: [{ is_payment_screenshot: true, amount: 5000, verified: true, utr_number: '629529860169' }],
}

describe('payment screenshot: the node reading it', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('waits for a node, names the node, and follows the request to another node', async () => {
    const server = stubServer()
    await startPaymentUpload()

    await waitFor(() => expect(server.uploads).toHaveLength(1))
    expect(within(paymentStatus()).getByText('Waiting for AI node…')).toBeTruthy()
    expect(within(paymentStatus()).queryByText(/^Analysing…$/)).toBeNull()
    expect(screen.queryByRole('button', { name: /save/i })).toBeNull()

    // The page follows exactly the analysis it sent.
    const analysisId = server.uploads[0].get('analysis_id')
    expect(analysisId).toMatch(/^[0-9a-f]{32}$/)
    expect(stream().url).toBe(`/public/slots/analysis/${analysisId}/events`)

    push({ state: 'running', node: 'Jagadeesh' })
    expect(paymentStatus().textContent).toContain('● Jagadeesh · Analysing…')

    // Jagadeesh fails; the gateway moves the request on.
    push({ state: 'waiting', node: null })
    expect(paymentStatus().textContent).toContain('Waiting for AI node…')
    expect(paymentStatus().textContent).not.toContain('Jagadeesh')

    push({ state: 'running', node: 'Praveen' })
    expect(paymentStatus().textContent).toContain('● Praveen · Analysing…')
    expect(paymentStatus().textContent).not.toContain('Jagadeesh')
  })

  it('says which node analysed it once the upload returns', async () => {
    const server = stubServer()
    await startPaymentUpload()
    await waitFor(() => expect(server.uploads).toHaveLength(1))
    push({ state: 'running', node: 'RTX 4060' })

    await server.finishUpload({ ...ACCEPTED, analysis: { state: 'done', node: 'RTX 4060', analysed_by: ['RTX 4060'] } })

    await waitFor(() => expect(paymentResult()).not.toBeNull())
    expect(paymentResult().textContent).toMatch(/^Payment verified · Analysed by RTX 4060 in \d+\.\ds$/)
    expect(paymentStatus()).toBeNull()
    expect(stream().closed).toBe(true)
  })

  it('takes the final node from the response, not the last live update', async () => {
    const server = stubServer()
    await startPaymentUpload()
    await waitFor(() => expect(server.uploads).toHaveLength(1))
    push({ state: 'running', node: 'Jagadeesh' })

    await server.finishUpload({ ...ACCEPTED, analysis: { state: 'done', node: 'RTX 4060', analysed_by: ['RTX 4060', 'Jagadeesh'] } })

    await waitFor(() => expect(paymentResult()).not.toBeNull())
    expect(paymentResult().textContent).toMatch(/^Payment verified · Analysed by RTX 4060 \+ Jagadeesh in \d+\.\ds$/)
  })

  it('names a node it has never heard of, exactly as the server does', async () => {
    const server = stubServer()
    await startPaymentUpload()
    await waitFor(() => expect(server.uploads).toHaveLength(1))
    push({ state: 'running', node: 'RTX 5090' })
    expect(paymentStatus().textContent).toContain('● RTX 5090 · Analysing…')
  })

  it('shows no analysed-by line for a refused screenshot, only why it was refused', async () => {
    const server = stubServer()
    await startPaymentUpload()
    await waitFor(() => expect(server.uploads).toHaveLength(1))
    push({ state: 'running', node: 'RTX 4060' })

    await server.finishUpload({
      status: 'error', message: 'This receipt is not a verified payment to a registered company or referrer account.',
      rejected: [{ filename: 'receipt.jpg', message: 'This receipt is not a verified payment to a registered company or referrer account.' }],
      analysis: { state: 'done', node: 'RTX 4060', analysed_by: ['RTX 4060'] },
    }, 400)

    expect(await screen.findByText(/not a verified payment/i)).toBeTruthy()
    expect(screen.queryByText(/analysed by/i)).toBeNull()
    expect(paymentResult()).toBeNull()
    expect(document.querySelector('.sbs-pay-card').className).toContain('sbs-pay-card--warn')
  })
})

describe('interview invite: the node reading it', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  async function startInviteUpload() {
    render(<SubmitSlotPage />)
    await screen.findByPlaceholderText(/choose or type your name/i)
    const invite = [...document.querySelectorAll('input[type="file"]')].find(input => !input.multiple)
    attach(invite, [screenshot('invite')])
  }

  it('waits for a node, names it, and says which node analysed the invite', async () => {
    const server = stubServer()
    await startInviteUpload()

    await waitFor(() => expect(server.invites).toHaveLength(1))
    expect(await screen.findByText('Waiting for AI node…')).toBeTruthy()
    const analysisId = server.invites[0].get('analysis_id')
    expect(analysisId).toMatch(/^[0-9a-f]{32}$/)
    expect(stream().url).toBe(`/public/slots/analysis/${analysisId}/events`)

    push({ state: 'running', node: 'RTX 4060' })
    expect(screen.getByText(/RTX 4060 · Analysing…/).textContent).toContain('● RTX 4060 · Analysing…')

    await server.finishInvite({
      status: 'ok', success: true,
      data: { interview_date: upcomingDate(), start_time: '05:00 PM', end_time: '06:00 PM', confidence_score: 92 },
      analysis: { state: 'done', node: 'RTX 4060', analysed_by: ['RTX 4060'] },
    })

    expect(await screen.findByText(/^✓ Analysed by RTX 4060 in \d+\.\ds$/)).toBeTruthy()
    expect(screen.queryByText('Waiting for AI node…')).toBeNull()
    expect(screen.queryByText(/· Analysing…/)).toBeNull()
    expect(stream().closed).toBe(true)
  })

  it('stops claiming a node wait once the AI read has failed and the page falls back', async () => {
    // Seen live: a read on a CPU-only node outlasted the public proxy's 60s
    // limit, the proxy answered with an HTML 504, and the page fell back to
    // the plain screenshot parser -- which runs on no AI node at all.
    const server = stubServer()
    await startInviteUpload()
    await waitFor(() => expect(server.invites).toHaveLength(1))
    push({ state: 'running', node: 'Praveen' })

    await act(async () => server.invite.resolve({
      ok: false, status: 504, json: () => Promise.reject(new SyntaxError("Unexpected token '<'")),
    }))
    await waitFor(() => expect(server.parses).toHaveLength(1))

    const row = document.querySelector('.sbs-status--loading')
    expect(row.textContent).toContain('Reading invite…')
    expect(row.textContent).not.toMatch(/waiting for ai node|praveen|analysing/i)
    expect(stream().closed).toBe(true)
  })

  it('does not show a finished analysis as a wait either', async () => {
    const server = stubServer()
    await startInviteUpload()
    await waitFor(() => expect(server.invites).toHaveLength(1))

    await act(async () => server.invite.resolve({
      ok: false, status: 500,
      json: () => Promise.resolve({ status: 'error', analysis: { state: 'done', node: 'RTX 4060', analysed_by: ['RTX 4060'] } }),
    }))
    await waitFor(() => expect(server.parses).toHaveLength(1))

    const row = document.querySelector('.sbs-status--loading')
    expect(row.textContent).toContain('Reading invite…')
    expect(row.textContent).not.toMatch(/waiting for ai node/i)
  })

  it('follows a failover while the invite is being read', async () => {
    const server = stubServer()
    await startInviteUpload()
    await waitFor(() => expect(server.invites).toHaveLength(1))

    push({ state: 'running', node: 'RTX 4060' })
    expect(screen.getByText(/· Analysing…/).textContent).toContain('RTX 4060')
    push({ state: 'waiting', node: null })
    expect(screen.getByText('Waiting for AI node…')).toBeTruthy()
    push({ state: 'running', node: 'Jagadeesh' })
    expect(screen.getByText(/· Analysing…/).textContent).toContain('● Jagadeesh · Analysing…')
  })
})
