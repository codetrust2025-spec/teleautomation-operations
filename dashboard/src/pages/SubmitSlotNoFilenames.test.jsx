/**
 * Uploaded file names never reach the screen, and an autofilled field stays
 * dark.
 *
 * The invite field showed "5cc08d62-1afe-4058-b855-2a751b1ce006.jpg" and a
 * refused receipt was reported as "pay-2.jpg: …". A phone names a screenshot
 * after a hash or a timestamp: it tells the person nothing, and whatever the
 * name holds was put on screen. Attachments are now described, and refusals
 * are numbered by their place in the upload.
 *
 * Client name, autofilled by the browser, turned into a white box in the dark
 * form; the browser's own autofill colours are overridden for form fields.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'index.css'), 'utf8')

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const PHONE_NAME = '5cc08d62-1afe-4058-b855-2a751b1ce006.jpg'

function attach(input, files) {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  fireEvent.change(input)
}

const image = name => new File(['x'], name, { type: 'image/jpeg' })

/** Every place a name could hide: text, and the attributes read aloud or shown on hover. */
function exposedAnywhere(fragment) {
  if (document.body.textContent.includes(fragment)) return true
  return [...document.querySelectorAll('[aria-label], [title], [alt]')].some(el =>
    ['aria-label', 'title', 'alt'].some(attr => (el.getAttribute(attr) || '').includes(fragment)))
}

function stubFetch({ upload } = {}) {
  vi.stubGlobal('fetch', vi.fn(url => {
    const target = String(url)
    const reply = (body, status = 200) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })
    if (target.includes('/public/slots/payment-requirement')) {
      return reply({ status: 'ok', service_type: 'round_wise', amount_due: 5000, payment_required: true, re_service: false })
    }
    if (target.includes('/public/slots/payment-proof')) return upload ? reply(...upload) : new Promise(() => {})
    if (target.includes('/extract-invite-ai')) return new Promise(() => {})
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: [] })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
}

async function roundWise() {
  render(<SubmitSlotPage />)
  fireEvent.click(await screen.findByRole('button', { name: /profile service/i }))
  fireEvent.click(await screen.findByText('Round-wise'))
  fireEvent.change(screen.getByPlaceholderText(/type client name/i), { target: { value: 'Code Trust' } })
  fireEvent.change(screen.getByPlaceholderText(/10-digit phone number/i), { target: { value: '8889990001' } })
}

const paymentInput = () => [...document.querySelectorAll('input[type="file"]')].find(i => i.multiple)
const inviteInput = () => [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)

describe('uploaded file names are never shown', () => {
  it('describes an attached invite instead of naming it', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByRole('button', { name: /confirm booking/i })

    attach(inviteInput(), [image(PHONE_NAME)])

    expect(await screen.findByText('Invite screenshot attached')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove invite screenshot' })).toBeTruthy()
    expect(exposedAnywhere(PHONE_NAME)).toBe(false)
    expect(exposedAnywhere('5cc08d62')).toBe(false)
  })

  it('gives a single refused receipt its reason alone', async () => {
    stubFetch({ upload: [{ status: 'error', message: 'Receiver is not registered.',
      rejected: [{ filename: PHONE_NAME, message: 'Receiver is not registered.' }] }, 400] })
    await roundWise()
    await waitFor(() => expect(paymentInput().disabled).toBe(false))

    attach(paymentInput(), [image(PHONE_NAME)])

    expect(await screen.findByText('Receiver is not registered.')).toBeTruthy()
    expect(exposedAnywhere(PHONE_NAME)).toBe(false)
  })

  it('numbers refused receipts by their place in the upload, even when a phone repeats a name', async () => {
    stubFetch({ upload: [{ status: 'error', message: 'Two screenshots could not be verified.',
      rejected: [
        { filename: 'image.jpg', message: 'Receiver is not registered.' },
        { filename: 'image.jpg', message: 'The amount could not be read.' },
      ] }, 400] })
    await roundWise()
    await waitFor(() => expect(paymentInput().disabled).toBe(false))

    // The first and third share a name; the second was accepted.
    attach(paymentInput(), [image('image.jpg'), image('IMG_2041.jpg'), image('image.jpg')])

    expect(await screen.findByText('Screenshot 1: Receiver is not registered.')).toBeTruthy()
    expect(screen.getByText('Screenshot 3: The amount could not be read.')).toBeTruthy()
    expect(exposedAnywhere('image.jpg')).toBe(false)
    expect(exposedAnywhere('IMG_2041')).toBe(false)
  })
})

describe('an autofilled field keeps the dark form surface', () => {
  const autofill = () => {
    const at = css.indexOf('.sbs-input:-webkit-autofill,')
    expect(at).toBeGreaterThan(-1)
    return css.slice(at, css.indexOf('}', at))
  }

  it('covers every booking field in each autofill state', () => {
    const rule = autofill()
    for (const selector of ['.sbs-input:-webkit-autofill:hover', '.sbs-input:-webkit-autofill:focus', '.sbs-input:autofill']) {
      expect(rule).toContain(selector)
    }
  })

  it('keeps the form text colour rather than the browser dark text', () => {
    expect(autofill()).toContain('-webkit-text-fill-color: var(--text,#eef2f7)')
  })

  it("paints the field's own resting colour over the browser's light background", () => {
    // .sbs-input is a 4% white wash over the #0f1117 card: 15 + 240 * 0.04 ≈ 25,
    // 17 + 238 * 0.04 ≈ 27, 23 + 232 * 0.04 ≈ 32.
    const input = css.slice(css.indexOf('\n.sbs-input {'), css.indexOf('}', css.indexOf('\n.sbs-input {')))
    expect(input).toContain('background: rgba(255,255,255,0.04)')
    const rule = autofill()
    expect(rule).toContain('-webkit-box-shadow: 0 0 0 1000px rgb(25,27,32) inset')
    expect(rule).toContain('box-shadow: 0 0 0 1000px rgb(25,27,32) inset')
  })
})
