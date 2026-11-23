/**
 * Book Interview Slot, Round-wise and Profile Service (owner, 24 Nov): 20px
 * phone padding, 24px between sections, 52px controls, a 56px Confirm that is
 * always enabled, no helper text, and dropdowns that are never cut short.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, '..', 'index.css'), 'utf-8').replace(/\r\n/g, '\n')
const PAGE = readFileSync(join(here, 'SubmitSlotPage.jsx'), 'utf-8')
const DROP = readFileSync(join(here, 'SubmitSlotFileDrop.jsx'), 'utf-8')
const BLOCK = CSS.slice(CSS.indexOf('/* ── Book Interview Slot: Round-wise and Profile Service forms (owner, 24 Nov)'))
  .split('/* ── Edit Candidate modal')[0]

function stubFetch() {
  vi.stubGlobal('fetch', vi.fn(async input => {
    const target = String(input)
    const body = target.includes('/public/slots/booked') ? { status: 'ok', slots: [] } : { status: 'ok', candidates: [] }
    return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
  }))
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('the measurements', () => {
  it('uses 52px controls and a 56px Confirm', () => {
    expect(BLOCK).toMatch(/height: 52px; min-height: 52px;/)
    expect(BLOCK).toMatch(/\.sbs-screen \.sbs-cta \{ min-height: 56px; height: 56px;/)
  })

  it('puts 24px between sections and 20px around the form on a phone', () => {
    expect(BLOCK).toContain('.sbs-screen .sbs-form { gap: 24px; }')
    expect(BLOCK).toContain('.sbs-screen .sbs-body { padding: 24px; gap: 24px; }')
    const phone = BLOCK.slice(BLOCK.indexOf('@media (max-width: 599px)'))
    expect(phone).toContain('.sbs-screen .sbs-body { padding: 20px 20px 32px; }')
    expect(phone).toContain('.sbs-screen .sbs-header { padding: 20px 20px 0; }')
  })

  it('never cuts a dropdown short: Company and round stack on a phone, long values end in an ellipsis', () => {
    expect(BLOCK).toContain('.sbs-screen .sbs-field-row { grid-template-columns: minmax(0, 1fr); gap: 24px; }')
    expect(BLOCK).toMatch(/\.sbs-form \.sbs-select \{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; \}/)
    expect(PAGE).toContain('<option value="">Select round</option>')
  })
})

describe('no helper text', () => {
  it.each([
    'Pick the right slot, upload invite, and confirm.',
    'Teams, Gmail, Calendar, or Zoom',
    'Identifies the candidate across rounds.',
    'Read from the invite when it says',
    'Not on the invite. Type it.',
    'Matched from the invite. Check it.',
    'Paid in parts? Attach each screenshot',
    'You can book this first interview now.',
    'Secure &amp; Private',
    'TeleAutomation · secure slot booking',
    'Attach interview invite screenshot to continue',
    'Type the technology to continue.',
  ])('drops "%s"', text => {
    expect(PAGE).not.toContain(text)
  })

  it('drops the upload instructions under the drop zones', () => {
    for (const text of ['or tap to browse', 'one per payment', 'Tap to replace', 'Tap to add another']) {
      expect(DROP).not.toContain(text)
    }
  })
})

describe('Confirm booking', () => {
  it('is enabled on a blank form and a click marks the first missing field', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    const confirm = await screen.findByRole('button', { name: /Confirm booking/i })
    expect(confirm.disabled).toBe(false)
    fireEvent.click(confirm)
    expect(await screen.findByRole('alert')).toHaveTextContent('Attach the interview invite screenshot.')
  })

  it('is enabled on the Round-wise form too', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByRole('button', { name: /Confirm booking/i })
    fireEvent.click(screen.getByRole('button', { name: /Profile service/i }))
    fireEvent.click(screen.getByText('Round-wise'))
    expect(screen.getByRole('button', { name: /Confirm booking/i }).disabled).toBe(false)
    expect(screen.getByPlaceholderText(/10-digit phone number/i)).toBeInTheDocument()
  })
})
