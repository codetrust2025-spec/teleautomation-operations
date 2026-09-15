/**
 * Confirm booking reports the first missing field, in form order.
 *
 * It used to be one OR across every rule: it flagged all of them at once, said
 * nothing about any of them, and left the browser to choose where to send you.
 * The browser chose Interview round, because `required` sat on that select and
 * on nothing else, so an empty form jumped past Client name, Candidate phone
 * and Technology to say "Please select an item in the list." -- which names
 * neither the field nor what to do about it.
 *
 * The rules are unchanged. They are ordered, and reported one at a time.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = dirname(fileURLToPath(import.meta.url))
const page = readFileSync(join(SRC, 'SubmitSlotPage.jsx'), 'utf8')
const css = readFileSync(join(SRC, '..', 'index.css'), 'utf8')

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function upcomingDate() {
  const date = new Date()
  date.setDate(date.getDate() + 7)
  return date.toISOString().slice(0, 10)
}

function stubFetch({ paymentRequired = true, upload } = {}) {
  vi.stubGlobal('fetch', vi.fn((url) => {
    const target = String(url)
    const reply = body => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
    if (target.includes('/public/slots/payment-requirement')) {
      return reply({
        status: 'ok', service_type: 'round_wise', amount_due: paymentRequired ? 5000 : 0,
        payment_required: paymentRequired, re_service: !paymentRequired,
      })
    }
    if (target.includes('/extract-invite-ai')) {
      return reply({ status: 'ok', success: true, data: { interview_date: upcomingDate(), start_time: '03:00 PM', confidence_score: 92 } })
    }
    if (target.includes('/public/slots/payment-proof')) {
      return upload ? reply(upload) : new Promise(() => {})
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: [] })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
}

function attach(input, files) {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  fireEvent.change(input)
}

const inviteInput = () => [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)

/** Read an invite, as a candidate would before Confirm opens. */
async function readInvite() {
  attach(inviteInput(), [new File(['invite'], 'invite.jpg', { type: 'image/jpeg' })])
  await waitFor(() => expect(document.querySelector('.ai-node-progress--active')).toBeNull())
  await waitFor(() => expect(document.querySelector('.sbs-detected-compact')).not.toBeNull())
}

/**
 * The form with Confirm open, so the sequence can be asked for its first
 * missing field.
 *
 * Confirm now opens only once the invite is read and any payment owed is
 * verified. These tests are about the fields the sequence still reports on
 * click, so they read an invite and, for round-wise, use a candidate whose
 * payment is waived -- a round-wise payment cannot be uploaded before the name
 * and phone it is filed under, so an owed one would stop the form earlier.
 */
async function openForm({ roundWise = false } = {}) {
  stubFetch({ paymentRequired: false })
  render(<SubmitSlotPage />)
  const confirm = await screen.findByRole('button', { name: /Confirm booking/i })
  if (roundWise) {
    // The page opens on Profile service, where Client name is a picker and
    // phone and technology do not apply. Round-wise is the form with every
    // step in the sequence on it.
    fireEvent.click(screen.getByRole('button', { name: /Profile service/i }))
    fireEvent.click(screen.getByText('Round-wise'))
  }
  await readInvite()
  return confirm
}

/** Click Confirm once it is open; the waiver is re-asked as fields change. */
async function clickConfirm(confirm) {
  await waitFor(() => expect(confirm.disabled).toBe(false))
  fireEvent.click(confirm)
}

/** The ordered checks, read out of the component. */
function steps() {
  const start = page.indexOf('const steps = [')
  const end = page.indexOf(']', page.indexOf('inviteRef', start))
  expect(start).toBeGreaterThan(-1)
  const block = page.slice(start, end)
  return [...block.matchAll(/\{ key: '(\w+)'/g)].map(m => m[1])
}

describe('the browser no longer decides', () => {
  it('the form opts out of native validation', () => {
    expect(page).toMatch(/<form className="sbs-form" noValidate/)
  })

  it('no control carries a bare required attribute any more', () => {
    // One `required` on a mid-form select is what sent an empty form to
    // Interview round.
    expect(page).not.toMatch(/disabled=\{busy \|\| parsing\} required>/)
  })
})

describe('validation runs top to bottom', () => {
  it('checks the fields in the order they appear on the form', () => {
    expect(steps()).toEqual([
      'name', 'phone', 'technology', 'round', 'payment', 'invite',
    ])
  })

  it('payment is validated after the candidate and interview details', () => {
    // What is owed depends on the service type, the round and the candidate, so
    // checking it earlier would judge an amount that is not settled yet.
    const order = steps()
    expect(order.indexOf('payment')).toBeGreaterThan(order.indexOf('round'))
    expect(order.indexOf('payment')).toBeGreaterThan(order.indexOf('technology'))
  })

  it('the invite screenshot is checked after payment', () => {
    const order = steps()
    expect(order.indexOf('invite')).toBeGreaterThan(order.indexOf('payment'))
  })

  it('stops at the first missing field rather than reporting all of them', () => {
    expect(page).toContain('const firstMissing = steps.find(step => !step.ok)')
  })

  it('says which field, in the application\'s own words', () => {
    for (const message of [
      'Enter the client name for this round.',
      'Enter the candidate phone number.',
      'Choose the technology for this interview.',
      'Choose the interview round.',
      'Attach a payment screenshot that covers the amount due.',
      'Attach the payment screenshot.',
      'Attach the interview invite screenshot.',
    ]) {
      expect(page).toContain(message)
    }
  })

  it('states no price, minimum or pricing explanation for round-wise', () => {
    expect(page).not.toMatch(/finali[sz]ed based on|minimum charge|referrer and client|client discussion|more to reach/i)
  })

  it('brings that field into view', () => {
    expect(page).toContain('focusField(firstMissing.ref')
    expect(page).toMatch(/scrollIntoView\(\{ behavior: 'smooth', block: 'center' \}\)/)
  })
})

describe('the rules themselves are unchanged', () => {
  it('phone and technology stay round-wise only', () => {
    expect(page).toContain("ok: serviceType !== 'round_wise' || !!roundWisePhone.trim()")
    expect(page).toContain("ok: serviceType !== 'round_wise' || !!effectiveTechnology")
  })

  it('payment still gates on the same needsPaymentProof flag', () => {
    expect(page).toContain('ok: !needsPaymentProof')
  })

  it('the past-date rule still runs after the sequence', () => {
    const sequenceAt = page.indexOf('const firstMissing')
    const pastDateAt = page.indexOf('Interview date is in the past')
    expect(pastDateAt).toBeGreaterThan(sequenceAt)
  })
})

describe('every field in the sequence can actually be reached', () => {
  it('each ref is attached to something in the markup', () => {
    const refFor = { name: 'nameRef', phone: 'phoneRef', technology: 'technologyRef',
                     round: 'roundRef', payment: 'paymentRef', invite: 'inviteRef' }
    for (const key of steps()) {
      expect(page).toContain(`ref={${refFor[key]}}`)
    }
  })

  it('client name is reachable under both service types', () => {
    // It renders as a text input for round-wise and a picker for profile
    // service, so the wrapper carries the ref the sequence scrolls to.
    expect(page).toContain('ref={nameFieldRef} className="sbs-field"')
    expect(page).toContain('fallbackRef: nameFieldRef')
  })

  it('scrolling and focusing are both optional, so a submit cannot throw', () => {
    // jsdom implements neither, and a container element is not focusable.
    expect(page).toContain("typeof node.scrollIntoView === 'function'")
    expect(page).toContain("typeof node.focus === 'function'")
  })
})


describe("an empty form reports the first field, not the browser's choice", () => {
  it('asks for the client name, not the interview round', async () => {
    // The symptom: an empty form used to jump to Interview round and say
    // "Please select an item in the list."
    const confirm = await openForm()
    await clickConfirm(confirm)

    await waitFor(() =>
      expect(screen.getByText('Enter the client name for this round.')).toBeInTheDocument(),
    )
    expect(screen.queryByText('Choose the interview round.')).not.toBeInTheDocument()
  })

  it('moves to the phone only once the name is filled', async () => {
    const confirm = await openForm({ roundWise: true })
    fireEvent.change(screen.getByPlaceholderText('Type client name'), {
      target: { value: 'Code Trust' },
    })
    await clickConfirm(confirm)

    await waitFor(() =>
      expect(screen.getByText('Enter the candidate phone number.')).toBeInTheDocument(),
    )
    expect(screen.queryByText('Enter the client name for this round.')).not.toBeInTheDocument()
  })

  it('reaches the interview round only after name, phone and technology', async () => {
    const confirm = await openForm({ roundWise: true })
    fireEvent.change(screen.getByPlaceholderText('Type client name'), {
      target: { value: 'Code Trust' },
    })
    const phone = document.querySelector('input[type="tel"], input[inputMode="numeric"]')
      || [...document.querySelectorAll('input')].find(i => i !== screen.getByPlaceholderText('Type client name'))
    fireEvent.change(phone, { target: { value: '1234567890' } })
    await clickConfirm(confirm)

    await waitFor(() =>
      expect(screen.getByText('Choose the technology for this interview.')).toBeInTheDocument(),
    )
  })
})


describe('one error, in one place', () => {
  const warnings = () => [...document.querySelectorAll('.sbs-hint--warn')]

  it('shows exactly one warning, not one per empty field', async () => {
    // An empty form used to answer a single click with a warning under every
    // field at once.
    const confirm = await openForm({ roundWise: true })
    await clickConfirm(confirm)

    await waitFor(() => expect(warnings()).toHaveLength(1))
    expect(warnings()[0].textContent).toBe('Enter the client name for this round.')
  })

  it('does not repeat the same message in the page-level alert', async () => {
    const confirm = await openForm({ roundWise: true })
    await clickConfirm(confirm)

    await waitFor(() =>
      expect(screen.getByText('Enter the client name for this round.')).toBeInTheDocument(),
    )
    // One occurrence on the whole screen: beside the field, and nowhere else.
    expect(screen.getAllByText('Enter the client name for this round.')).toHaveLength(1)
    expect(document.querySelector('.sbs-alert--error')).toBeNull()
  })

  it('clears the message as soon as that field is filled', async () => {
    const confirm = await openForm({ roundWise: true })
    await clickConfirm(confirm)
    await waitFor(() => expect(warnings()).toHaveLength(1))

    fireEvent.change(screen.getByPlaceholderText('Type client name'), {
      target: { value: 'Code Trust' },
    })

    // Without waiting for another Confirm: the form should stop asking for
    // something that is now filled in.
    await waitFor(() => expect(warnings()).toHaveLength(0))
  })

  it('moves the single warning down the form as each field is filled', async () => {
    const confirm = await openForm({ roundWise: true })
    fireEvent.change(screen.getByPlaceholderText('Type client name'), {
      target: { value: 'Code Trust' },
    })
    await clickConfirm(confirm)

    await waitFor(() => expect(warnings()).toHaveLength(1))
    expect(warnings()[0].textContent).toBe('Enter the candidate phone number.')
  })
})


describe('the payment card is compact, without shrinking what you tap', () => {
  // Newline-anchored: ".submit-slot-drop {" also occurs as the tail of
  // ".sbs-pay-card ...--compact .submit-slot-drop {", and matching that by
  // accident would have this assert the override against itself.
  const rule = (selector) => {
    const at = css.indexOf(`\n${selector} {`)
    expect(at).toBeGreaterThan(-1)
    return css.slice(at, css.indexOf('}', at))
  }

  it('tightens its own spacing rather than the shared drop styles', () => {
    // Scoped under .sbs-pay-card so the invite field below keeps its sizing.
    for (const selector of [
      '.sbs-pay-card .submit-slot-drop-wrap',
      '.sbs-pay-card .submit-slot-drop-list',
      '.sbs-pay-card .submit-slot-drop-list__item',
    ]) {
      expect(css).toContain(selector)
    }
  })

  it('keeps the drop zone at a 44px tap target', () => {
    // The "compact" variant had raised it to 3rem; this returns it to the
    // 2.75rem base, which is 44px, and no lower.
    expect(rule('.sbs-pay-card .submit-slot-drop-wrap--compact .submit-slot-drop'))
      .toContain('min-height: 2.75rem')
  })

  it('does not shrink the base drop zone used by the invite field', () => {
    expect(rule('.submit-slot-drop')).toContain('min-height: 2.75rem')
  })

  it('fits the split-payment hint on one line', () => {
    // Two lines of hint inside a card that already stacks seven things is most
    // of the difference in height against the invite field.
    expect(page).toContain('Paid in parts? Attach each screenshot — they are added up.')
    expect(page).not.toContain('they are added up for this booking.')
  })

  it('keeps the drop component and multi-file upload, and analyses on attach', () => {
    // The same drop component and multiple flag. There is no save handler any
    // more: attaching the screenshots is what starts their analysis.
    expect(page).toContain('multiple')
    expect(page).toContain('onFiles={analysePaymentScreenshots}')
    expect(page).not.toContain('uploadPaymentProof')
    expect(page).not.toContain('sbs-pay-save')
  })
})


describe('the payment card looks like a form field, not a warning', () => {
  const rule = (selector) => {
    const at = css.indexOf(`
${selector} {`)
    expect(at).toBeGreaterThan(-1)
    return css.slice(at, css.indexOf('}', at))
  }

  it('uses the same surface as a normal input', () => {
    // .sbs-input is the reference: same background, border and radius.
    const card = rule('.sbs-pay-card')
    const input = rule('.sbs-input')
    for (const property of ['background', 'border-radius']) {
      // Whitespace-insensitive: "rgba(255,255,255,0.04)" and
      // "rgba(255, 255, 255, 0.04)" are the same colour.
      const from = (text) =>
        text.match(new RegExp(`${property}: ([^;]+);`))?.[1].replace(/\s+/g, '')
      expect(from(card)).toBe(from(input))
    }
    expect(card).toContain('border: 1px solid rgba(148, 163, 184, 0.15)')
  })

  it('carries no amber in its normal state', () => {
    // The panel used to be amber bordered and amber washed, permanently, so it
    // read as a caution card sitting inside the form.
    const card = rule('.sbs-pay-card')
    expect(card).not.toMatch(/251,\s*191,\s*36/)
  })

  it('keeps amber for the state that earns it', () => {
    expect(rule('.sbs-pay-card--warn')).toMatch(/251,\s*191,\s*36/)
  })

  it('applies the warning class only on a refusal or a missing proof', () => {
    expect(page).toContain(
      "`sbs-pay-card${paymentRejected.length || missingField === 'payment' ? ' sbs-pay-card--warn' : ''}`",
    )
  })

  it('there is exactly one card rule, so nothing later overrides it', () => {
    // The compaction shipped before this had a second .sbs-pay-card rule
    // earlier in the file; the original amber one came later and won on every
    // property they shared, which is why the box never got smaller.
    const matches = css.match(/\n\.sbs-pay-card \{/g) || []
    expect(matches).toHaveLength(1)
  })

  it('states the amount on one compact header row', () => {
    expect(rule('.sbs-pay-head')).toContain('justify-content: space-between')
    expect(css).toContain('.sbs-pay-head strong')
  })
})


describe('Confirm opens only once both uploads have been analysed', () => {
  const confirmButton = () => screen.getByRole('button', { name: /Confirm booking/i })

  it('stays disabled on an empty form', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    expect((await screen.findByRole('button', { name: /Confirm booking/i })).disabled).toBe(true)
  })

  it('opens once the invite is read when nothing is owed', async () => {
    stubFetch({ paymentRequired: false })
    render(<SubmitSlotPage />)
    await screen.findByRole('button', { name: /Confirm booking/i })
    expect(confirmButton().disabled).toBe(true)
    await readInvite()
    expect(confirmButton().disabled).toBe(false)
  })

  it('stays disabled while an owed payment is unverified, and opens once it is', async () => {
    stubFetch({
      upload: {
        status: 'ok', proof_ids: ['proof-1'], verified_total: 5000, remaining_due: 0, amount_due: 5000,
        payment_complete: true, rejected: [], ai_extractions: [{ is_payment_screenshot: true, amount: 5000, verified: true }],
      },
    })
    render(<SubmitSlotPage />)
    await screen.findByRole('button', { name: /Confirm booking/i })
    fireEvent.click(screen.getByRole('button', { name: /Profile service/i }))
    fireEvent.click(screen.getByText('Round-wise'))
    fireEvent.change(screen.getByPlaceholderText('Type client name'), { target: { value: 'Code Trust' } })
    fireEvent.change(screen.getByPlaceholderText(/10-digit phone number/i), { target: { value: '9000066350' } })

    await readInvite()
    // The invite is read; the payment is still owed.
    expect(confirmButton().disabled).toBe(true)
    expect(document.querySelector('.sbs-hint--warn')).toBeNull()

    attach([...document.querySelectorAll('input[type="file"]')].find(i => i.multiple),
           [new File(['pay'], 'pay.jpg', { type: 'image/jpeg' })])
    await screen.findByText(/payment verified/i)
    await waitFor(() => expect(confirmButton().disabled).toBe(false))
  })

  it('stays disabled while a payment is being analysed', async () => {
    stubFetch()
    render(<SubmitSlotPage />)
    await screen.findByRole('button', { name: /Confirm booking/i })
    fireEvent.click(screen.getByRole('button', { name: /Profile service/i }))
    fireEvent.click(screen.getByText('Round-wise'))
    fireEvent.change(screen.getByPlaceholderText('Type client name'), { target: { value: 'Code Trust' } })
    fireEvent.change(screen.getByPlaceholderText(/10-digit phone number/i), { target: { value: '9000066350' } })
    await readInvite()

    attach([...document.querySelectorAll('input[type="file"]')].find(i => i.multiple),
           [new File(['pay'], 'pay.jpg', { type: 'image/jpeg' })])
    await waitFor(() => expect(document.querySelector('.sbs-pay-card .ai-node-progress--active')).not.toBeNull())
    // While busy the button shows a spinner rather than its label, so it is
    // found as the form's submit button.
    expect(document.querySelector('form.sbs-form button[type="submit"]').disabled).toBe(true)
  })
})
