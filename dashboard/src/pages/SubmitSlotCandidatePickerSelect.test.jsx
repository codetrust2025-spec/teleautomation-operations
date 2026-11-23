/**
 * Profile Service candidate dropdown (owner, 24 Nov): choosing a candidate
 * closes the list and shows their name in the box; opening the list again
 * keeps that candidate highlighted. Tapping the list never blurs the box first,
 * which on a phone closed the keyboard and moved the list under the finger.
 * Names are invented.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, createEvent, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

const ASHA = { id: 'c-asha', name: 'Asha Fakename', technology: 'React JS', balance_due: 0, needs_payment_proof: false }
const RAVI = { id: 'c-ravi', name: 'Ravi Testcase', technology: 'ServiceNow', balance_due: 0, needs_payment_proof: false }
const MEERA = { id: 'c-meera', name: 'Meera Sample', technology: 'Java', balance_due: 0, needs_payment_proof: false }

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function stubFetch() {
  vi.stubGlobal('fetch', vi.fn(async url => {
    const target = String(url)
    const body = target.includes('/public/slots/booked') ? { status: 'ok', slots: [] }
      : target.includes('/public/slots/candidates') ? { status: 'ok', candidates: [ASHA, RAVI, MEERA] }
      : { status: 'ok' }
    return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
  }))
}

const box = () => screen.getByRole('textbox', { name: 'Profile Service candidate' })
const list = () => screen.queryByRole('listbox', { name: 'Subscribed Profile Service candidates' })
const option = name => within(list()).getByRole('option', { name: new RegExp(`^${name}`) })

async function openPage() {
  stubFetch()
  render(<SubmitSlotPage />)
  await screen.findByRole('button', { name: /Confirm booking/i })
  await waitFor(() => expect(box().getAttribute('placeholder')).toBe('Search candidates'))
}

describe('choosing a Profile Service candidate', () => {
  it('closes the list and shows the chosen name in the box', async () => {
    await openPage()
    fireEvent.click(box())
    expect(list()).not.toBeNull()
    fireEvent.click(option('Ravi Testcase'))
    expect(list()).toBeNull()
    expect(box().value).toBe('Ravi Testcase')
    expect(box()).toHaveAttribute('aria-expanded', 'false')
  })

  it('lets go of focus once chosen, so a phone keyboard closes', async () => {
    await openPage()
    box().focus()
    fireEvent.click(box())
    fireEvent.click(option('Meera Sample'))
    expect(document.activeElement).not.toBe(box())
  })

  it('keeps the chosen candidate highlighted, and named in the box, when the list opens again', async () => {
    await openPage()
    fireEvent.click(box())
    fireEvent.click(option('Meera Sample'))
    fireEvent.click(box())
    const chosen = option('Meera Sample')
    expect(chosen).toHaveAttribute('aria-selected', 'true')
    expect(chosen.className).toContain('is-selected')
    expect(chosen.className).toContain('is-active')
    expect(option('Asha Fakename').className).not.toContain('is-selected')
    expect(box().getAttribute('placeholder')).toBe('Meera Sample')
  })

  it('does not take focus from the box when the list is pressed', async () => {
    await openPage()
    fireEvent.click(box())
    const press = createEvent.mouseDown(option('Asha Fakename'))
    fireEvent(option('Asha Fakename'), press)
    expect(press.defaultPrevented).toBe(true)
  })
})
