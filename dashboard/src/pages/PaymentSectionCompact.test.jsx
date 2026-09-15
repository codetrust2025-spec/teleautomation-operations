/**
 * The payment section is a field on this form, not a panel bolted onto it.
 *
 * Measured on the live page before this change: the box was 164px tall against
 * 85px for the interview-invite field directly below it, and read as a
 * separate card. Two things carried that height without earning it -- a
 * full-width "Save payment proof" button that was rendered and disabled
 * whenever there was nothing to save, which is most of the time, and a second
 * caption inside the drop zone repeating what the header already said.
 *
 * Folding Save into the header row and dropping the caption took it to 102px
 * idle, within 17px of the invite field, and the remainder is the header that
 * states the amount -- which is the one thing this section has to say that the
 * invite field does not.
 */
import React from 'react'
import fs from 'node:fs'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

const css = fs.readFileSync(path.join(__dirname, '..', 'index.css'), 'utf8')

/** The body of the last rule with this exact selector.
 *
 * The selector arrives already regex-escaped (`\\.sbs-pay-card`); escaping it
 * again here is what made every one of these assertions read an empty string
 * and pass vacuously on the first run.
 */
function rule(selector) {
  const matches = rules(selector)
  return matches.length ? matches[matches.length - 1] : ''
}

/** Every rule body for this selector. `.sbs-input` is declared more than once
 *  -- the later ones adjust width and padding only -- so a token check has to
 *  look across all of them rather than at whichever happens to come last. */
function rules(selector) {
  return [...css.matchAll(new RegExp(`(?:^|\\n)${selector}\\s*\\{([^}]*)\\}`, 'g'))].map(m => m[1])
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function screenshot(label) {
  return new File([label], `${label}.jpg`, { type: 'image/jpeg' })
}

function attach(input, files) {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  fireEvent.change(input)
}

function stubFetch({ upload } = {}) {
  vi.stubGlobal('fetch', vi.fn((url) => {
    const target = String(url)
    const reply = (body, status = 200) => Promise.resolve({
      ok: status < 400, status, headers: { get: () => 'application/json' },
      json: () => Promise.resolve(body),
    })
    if (target.includes('/public/slots/payment-requirement')) {
      return reply({ status: 'ok', service_type: 'round_wise', amount_due: 5000, payment_required: true })
    }
    if (target.includes('/public/slots/payment-proof')) {
      return upload ? upload(reply) : new Promise(() => {})
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: [] })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
}

/** Reach the round-wise form, which is the one that asks for payment. */
async function roundWiseForm(options) {
  stubFetch(options)
  render(<SubmitSlotPage />)
  await screen.findByRole('button', { name: /Confirm booking/i })

  const serviceButton = document.querySelector('.sbs-select--custom')
  fireEvent.click(serviceButton)
  const roundWise = [...document.querySelectorAll('li')].find(li => /Round-wise/i.test(li.textContent))
  fireEvent.click(roundWise)

  const nameBox = document.querySelector('.sbs-field input')
  fireEvent.change(nameBox, { target: { value: 'Sowmya' } })
  await waitFor(() => expect(document.querySelector('.sbs-pay-card')).not.toBeNull())
  return document.querySelector('.sbs-pay-card')
}

const paymentInput = () => document.querySelector('.sbs-pay-card input[type="file"]')

const enterPhone = () => fireEvent.change(screen.getByPlaceholderText(/10-digit phone number/i),
                                          { target: { value: '9000066350' } })

describe('upload is the action: nothing waits to be saved', () => {
  it('never shows a Save button, before or after a screenshot is attached', async () => {
    await roundWiseForm()
    enterPhone()
    expect(screen.queryByRole('button', { name: /save/i })).toBeNull()
    attach(paymentInput(), [screenshot('pay-1')])
    await waitFor(() => expect(document.querySelector('.sbs-pay-card .sbs-status--loading')).not.toBeNull())
    expect(screen.queryByRole('button', { name: /save/i })).toBeNull()
    expect(document.querySelector('.sbs-pay-head button')).toBeNull()
  })

  it('starts analysing the moment a screenshot is attached, as the invite does', async () => {
    await roundWiseForm()
    enterPhone()
    attach(paymentInput(), [screenshot('pay-1')])
    await waitFor(() =>
      expect(fetch.mock.calls.some(([url]) => String(url).includes('/public/slots/payment-proof'))).toBe(true),
    )
    const status = document.querySelector('.sbs-pay-card .sbs-status--loading')
    expect(status.textContent).toContain('Waiting for AI node…')
  })

  it('waits for the client name and phone before taking a screenshot', async () => {
    // A round-wise proof is filed under the phone, and changing it afterwards
    // discards the proof -- so the upload is not offered before it is known.
    await roundWiseForm()
    expect(paymentInput().disabled).toBe(true)
    expect(document.querySelector('.sbs-pay-card').textContent).toContain('Enter the client name and phone number first.')

    enterPhone()
    await waitFor(() => expect(paymentInput().disabled).toBe(false))
    expect(document.querySelector('.sbs-pay-card').textContent).not.toContain('Enter the client name and phone number first.')
  })

  it('does not repeat the section caption inside the drop zone', async () => {
    const card = await roundWiseForm()
    // The header already says what this is; a second caption was pure height.
    expect(card.querySelector('.submit-slot-field-label')).toBeNull()
  })
})

describe('the surface is the form surface', () => {
  it('uses the same background, border and radius as an ordinary input', () => {
    // Compared with whitespace normalised: the two rules were authored years
    // apart and write the same colour differently -- rgba(255,255,255,0.04)
    // against rgba(255, 255, 255, 0.04). Confirmed on the live page as well,
    // where both compute to the identical value at an 8px radius.
    const flat = text => text.replace(/\s+/g, '')
    const card = flat(rule('\\.sbs-pay-card'))
    const input = flat(rules('\\.sbs-input').join('\n'))
    for (const token of ['rgba(255,255,255,0.04)', 'rgba(148,163,184,0.15)']) {
      expect(card).toContain(token)
      expect(input).toContain(token)
    }
    expect(card).toContain('border-radius:0.5rem')
    expect(input).toContain('border-radius:0.5rem')
  })

  it('is defined exactly once, so the compaction is not overridden later', () => {
    // A previous attempt added a second .sbs-pay-card rule above the original.
    // Equal specificity, so the later one won and nothing visibly changed.
    expect((css.match(/\n\.sbs-pay-card \{/g) || []).length).toBe(1)
  })

  it('carries no amber until something is actually wrong', async () => {
    const card = await roundWiseForm()
    expect(card.className).not.toContain('sbs-pay-card--warn')
  })

  it('turns amber when a screenshot is refused', async () => {
    await roundWiseForm({
      upload: reply => reply({
        status: 'error', message: 'Receiver is not registered.',
        rejected: [{ filename: 'pay-1.jpg', message: 'Receiver is not registered.' }],
      }, 400),
    })
    enterPhone()
    attach(paymentInput(), [screenshot('pay-1')])
    await waitFor(() =>
      expect(document.querySelector('.sbs-pay-card').className).toContain('sbs-pay-card--warn'),
    )
    expect(document.querySelector('.sbs-pay-result')).toBeNull()
  })

  it('shows a verified payment in the green result card the invite uses', async () => {
    await roundWiseForm({
      upload: reply => reply({
        status: 'ok', proof_ids: ['proof-1'], verified_total: 5000, remaining_due: 0, amount_due: 5000,
        payment_complete: true, rejected: [],
        ai_extractions: [{ is_payment_screenshot: true, amount: 5000, verified: true, utr_number: '629529860169' }],
        analysis: { state: 'done', node: 'RTX 4060', analysed_by: ['RTX 4060'] },
      }),
    })
    enterPhone()
    attach(paymentInput(), [screenshot('pay-1')])
    await waitFor(() => expect(document.querySelector('.sbs-pay-result')).not.toBeNull())
    const result = document.querySelector('.sbs-pay-result')
    expect(result.className).toContain('sbs-detected-compact')
    expect(result.querySelector('.sbs-detected-compact__text').textContent).toBe('Payment verified · Analysed by RTX 4060')
    expect(document.querySelector('.sbs-pay-card').className).not.toContain('sbs-pay-card--warn')
  })

  it('keeps amber reserved for the warn variant alone', () => {
    expect(rule('\\.sbs-pay-card')).not.toMatch(/251,\s*191,\s*36/)
    expect(rule('\\.sbs-pay-card--warn')).toMatch(/251,\s*191,\s*36/)
  })
})

describe('the header stays one line', () => {
  it('lays the amount out against the label', () => {
    expect(rule('\\.sbs-pay-head')).toContain('justify-content: space-between')
  })

  it('groups the amount at the end of that line', () => {
    expect(rule('\\.sbs-pay-head__end')).toContain('display: flex')
  })

  it('carries no Save styling, because there is no Save', () => {
    expect(css).not.toContain('.sbs-pay-save')
  })

  it('keeps the drop zone at a full tap target on mobile', () => {
    // Compacting spacing must not shrink what a thumb has to hit.
    expect(rule('\\.sbs-pay-card \\.submit-slot-drop-wrap--compact \\.submit-slot-drop'))
      .toContain('min-height: 2.75rem')
  })
})
