/**
 * Client name from the invite.
 *
 * The invite names the candidate. On profile service that name is matched against the existing clients, and a
 * client is picked only when the invite gives a full name that matches exactly one of them; the booking then
 * carries that client's id, exactly as if it had been picked by hand. Anything less reliable (one word, initials,
 * two clients with the same words, no client at all) leaves the box empty and offers the read name for
 * confirmation. Round-wise names are typed and are the payment identity, so they are never filled. A name the
 * candidate typed or picked is never replaced.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SubmitSlotPage, matchClientFromInvite } from './SubmitSlotPage.jsx'

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
  { id: 'c-ira', name: 'Ira Testwala', needs_payment_proof: false, balance_due: 0 },
  { id: 'c-nova', name: 'Nova Q Fakename', needs_payment_proof: false, balance_due: 0 },
  { id: 'c-ravi-1', name: 'Ravi K Sample', needs_payment_proof: false, balance_due: 0 },
  { id: 'c-ravi-2', name: 'Ravi P Sample', needs_payment_proof: false, balance_due: 0 },
]

function stubFetch(candidateName) {
  const calls = { confirms: [], invite: { candidate_name: candidateName } }
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    const target = String(url)
    const reply = body => Promise.resolve({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: () => Promise.resolve(body) })
    if (target.includes('/payment-requirement')) return reply({ status: 'ok', service_type: 'profile_service', amount_due: 0, payment_required: false, re_service: true })
    if (target.includes('/extract-invite-ai')) {
      return reply({ status: 'ok', success: true, data: {
        interview_date: upcomingDate(), start_time: '11:00 AM', company: 'Contoso Ltd', interview_round: 'L1', confidence_score: 95, ...calls.invite,
      } })
    }
    if (target.includes('/bookings/confirm')) {
      calls.confirms.push(options?.body)
      return reply({ status: 'ok', candidate: { name: 'x' } })
    }
    if (target.includes('/public/slots/booked')) return reply({ status: 'ok', slots: [] })
    return reply({ status: 'ok', candidates: CLIENTS })
  }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() })
  return calls
}

const nameBox = () => document.querySelector('.sbs-field input')
const nameHint = () => nameBox().closest('label').querySelector('.sbs-hint')
const inviteInput = () => [...document.querySelectorAll('input[type="file"]')].find(i => !i.multiple)

async function readInvite() {
  Object.defineProperty(inviteInput(), 'files', { value: [new File(['x'], 'invite.jpg', { type: 'image/jpeg' })], configurable: true })
  fireEvent.change(inviteInput())
  await waitFor(() => expect(document.querySelector('.ai-node-progress')?.textContent || '').toMatch(/^✓/))
}

async function openForm(candidateName, { typed } = {}) {
  const calls = stubFetch(candidateName)
  render(<SubmitSlotPage />)
  const confirm = await screen.findByRole('button', { name: /Confirm booking/i })
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled())
  if (typed !== undefined) fireEvent.change(nameBox(), { target: { value: typed } })
  await readInvite()
  return { confirm, calls }
}

describe('which client an invite name matches', () => {
  it.each([
    ['Ira Testwala', 'Ira Testwala'],
    ['IRA  TESTWALA', 'Ira Testwala'],          // case and spacing
    ['Testwala Ira', 'Ira Testwala'],           // word order
    ['Nova Fakename', 'Nova Q Fakename'],       // a middle initial
    ['Nova Q. Fakename', 'Nova Q Fakename'],
  ])('%s -> %s', (read, client) => {
    expect(matchClientFromInvite(read, CLIENTS).name).toBe(client)
  })

  it.each([
    ['Ira', 'one_word'],                         // one word is not a full name
    ['I. Testwala', 'one_word'],                 // initials aside, one word
    ['Ravi Sample', 'ambiguous'],                // two clients have these words
    ['Ira Testwala Kumar', 'no_match'],
    ['Someone Else', 'no_match'],
    ['', 'none'],
  ])('%s picks nobody (%s)', (read, reason) => {
    const match = matchClientFromInvite(read, CLIENTS)
    expect(match.name).toBe('')
    expect(match.reason).toBe(reason)
  })
})

describe('profile service: a reliable match is picked', () => {
  it('fills the client, says so, and books under that client exactly as if picked by hand', async () => {
    const { confirm, calls } = await openForm('Ira Testwala')
    expect(nameBox().value).toBe('Ira Testwala')
    expect(nameHint().textContent).toBe('Matched from the invite. Check it.')
    await waitFor(() => expect(confirm.disabled).toBe(false))
    fireEvent.click(confirm)
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].get('name')).toBe('Ira Testwala')
    expect(calls.confirms[0].get('candidate_id')).toBe('c-ira')
  })

  it('matches across case, word order and a middle initial, and uses the client\'s own spelling', async () => {
    await openForm('fakename nova')
    expect(nameBox().value).toBe('Nova Q Fakename')
  })
})

describe('anything less reliable is left for the candidate to confirm', () => {
  it.each([['no client has the name', 'Someone Else'], ['two clients have those words', 'Ravi Sample'], ['one word only', 'Ira']])(
    '%s: nothing is picked, the read name is offered', async (_, read) => {
      const { confirm, calls } = await openForm(read)
      expect(nameBox().value).toBe('')
      expect(nameHint().textContent).toBe(`The invite names “${read}”. Pick the client to confirm.`)
      fireEvent.click(confirm)
      expect(await screen.findByText('Enter the client name for this round.')).toBeInTheDocument()
      expect(calls.confirms).toHaveLength(0)
    })

  it('an invite that names nobody leaves the usual hint', async () => {
    await openForm('')
    expect(nameBox().value).toBe('')
    expect(nameHint().textContent).toBe('Pick from the list or type a new client name.')
  })

  it('once the candidate types a name, the offer goes away', async () => {
    await openForm('Someone Else')
    fireEvent.change(nameBox(), { target: { value: 'Ira Testwala' } })
    expect(nameHint().textContent).toBe('Pick from the list or type a new client name.')
  })
})

describe('the candidate stays in charge', () => {
  it('a name typed or picked before the invite is read is never replaced', async () => {
    await openForm('Ira Testwala', { typed: 'Nova Q Fakename' })
    expect(nameBox().value).toBe('Nova Q Fakename')
    expect(nameHint().textContent).toBe('Pick from the list or type a new client name.')
  })

  it('a matched name the candidate then changes is theirs: the next invite does not touch it', async () => {
    const { calls } = await openForm('Ira Testwala')
    fireEvent.change(nameBox(), { target: { value: 'Nova Q Fakename' } })
    calls.invite = { candidate_name: 'Ira Testwala' }
    await readInvite()
    expect(nameBox().value).toBe('Nova Q Fakename')
  })

  it('a name already matched is not replaced by a later invite naming someone else (a payment may be filed under it)', async () => {
    const { calls } = await openForm('Ira Testwala')
    calls.invite = { candidate_name: 'Nova Fakename' }
    await readInvite()
    expect(nameBox().value).toBe('Ira Testwala')
  })
})

describe('round-wise', () => {
  it('never fills the client name; it offers the read name to type', async () => {
    const calls = stubFetch('Ira Testwala')
    render(<SubmitSlotPage />)
    await screen.findByRole('button', { name: /Confirm booking/i })
    fireEvent.click(screen.getByRole('button', { name: /Profile service/i }))
    fireEvent.click(screen.getByText('Round-wise'))
    await readInvite()
    const typed = screen.getByPlaceholderText('Type client name')
    expect(typed.value).toBe('')
    expect(typed.closest('label').querySelector('.sbs-hint').textContent)
      .toBe('The invite names “Ira Testwala”. Type the client name to confirm.')
    expect(calls.confirms).toHaveLength(0)
  })
})
