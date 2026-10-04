/**
 * Confirmed slots: interviews that ended with no Daily Ops outcome are listed
 * first, under "Needs status update", and stay there until a status is recorded.
 * Current and future interviews are under "Upcoming slots".
 *
 * The server decides which is which (`slot_phase`, by the rule Daily Ops uses);
 * the page only places the cards. A slot without the field is upcoming, so an
 * older server can never make a booking look overdue. Names are invented.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SubmitSlotPage, needsStatusUpdate } from './SubmitSlotPage.jsx'

const slot = (name, date, time, phase, extra = {}) => ({
  name, technology: 'Java', interview_round: 'L1', date, time, time_end: '',
  interview_booking_source: 'candidate_booked', slot_phase: phase, ...extra,
})

const MIXED = [
  slot('Overdue Two', '2026-10-01', '16:00', 'needs_status_update'),
  slot('Overdue One', '2026-10-01', '11:00', 'needs_status_update'),
  slot('Soon Person', '2026-10-05', '13:00', 'upcoming'),
  slot('Later Person', '2026-10-07', '18:30', 'upcoming'),
]

function stubFetch(slots) {
  return vi.fn((url) => {
    const body = String(url).includes('/public/slots/booked')
      ? { status: 'ok', slots }
      : { status: 'ok', candidates: [] }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  })
}

async function openConfirmed(slots) {
  vi.stubGlobal('fetch', stubFetch(slots))
  render(<SubmitSlotPage />)
  fireEvent.click(await screen.findByRole('tab', { name: /confirmed slots/i }))
}

const card = (name) => screen.getByText(name).closest('.sbs-confirmed-card')
const sectionOf = (name) => screen.getByText(name).closest('section')

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('Needs status update', () => {
  it('comes first, above Upcoming slots', async () => {
    await openConfirmed(MIXED)
    const needs = await screen.findByRole('heading', { name: 'Needs status update' })
    const upcoming = screen.getByRole('heading', { name: 'Upcoming slots' })
    expect(needs.compareDocumentPosition(upcoming) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('holds exactly the ended-without-outcome interviews, marked Awaiting status', async () => {
    await openConfirmed(MIXED)
    await screen.findByText('Overdue One')
    const needs = screen.getByRole('heading', { name: 'Needs status update' }).closest('section')
    expect(sectionOf('Overdue One')).toBe(needs)
    expect(sectionOf('Overdue Two')).toBe(needs)
    expect(sectionOf('Soon Person')).not.toBe(needs)
    expect(within(card('Overdue One')).getByText('Awaiting status')).toBeTruthy()
    expect(within(card('Overdue One')).queryByText('Booked', { selector: '.sbs-confirmed-card__status' })).toBeNull()
    expect(card('Overdue One').className).toContain('sbs-confirmed-card--awaiting')
  })

  it('says how many need an update and where to record it', async () => {
    await openConfirmed(MIXED)
    const needs = (await screen.findByRole('heading', { name: 'Needs status update' })).closest('section')
    expect(needs.textContent).toMatch(/2 interviews ended with no outcome recorded/)
    expect(needs.textContent).toMatch(/Daily Ops/)
    expect(needs.textContent).toMatch(/they stay here until then/)
  })

  it('keeps each card intact: round and booking source still shown', async () => {
    await openConfirmed(MIXED)
    await screen.findByText('Overdue One')
    expect(within(card('Overdue One')).getByText('L1')).toBeTruthy()
    expect(within(card('Overdue One')).getByText('Candidate booked')).toBeTruthy()
  })

  it('is not shown at all when nothing needs an update', async () => {
    await openConfirmed(MIXED.filter((s) => s.slot_phase === 'upcoming'))
    await screen.findByText('Soon Person')
    expect(screen.queryByRole('heading', { name: 'Needs status update' })).toBeNull()
  })
})

describe('Upcoming slots', () => {
  it('holds the current and future interviews, marked Booked, with their count', async () => {
    await openConfirmed(MIXED)
    const upcoming = (await screen.findByRole('heading', { name: 'Upcoming slots' })).closest('section')
    expect(sectionOf('Soon Person')).toBe(upcoming)
    expect(sectionOf('Later Person')).toBe(upcoming)
    expect(upcoming.textContent).toMatch(/2 slots scheduled/)
    expect(within(card('Soon Person')).getByText('Booked', { selector: '.sbs-confirmed-card__status' })).toBeTruthy()
  })

  it('says there is nothing upcoming when every slot needs an update', async () => {
    await openConfirmed(MIXED.filter((s) => s.slot_phase === 'needs_status_update'))
    const upcoming = (await screen.findByRole('heading', { name: 'Upcoming slots' })).closest('section')
    expect(upcoming.textContent).toMatch(/No upcoming slots/)
    expect(upcoming.querySelector('.sbs-confirmed-card')).toBeNull()
  })

  it('treats a slot without a phase as upcoming, never as overdue', async () => {
    const legacy = { ...MIXED[2] }
    delete legacy.slot_phase
    await openConfirmed([legacy])
    await screen.findByText('Soon Person')
    expect(screen.queryByRole('heading', { name: 'Needs status update' })).toBeNull()
    expect(needsStatusUpdate(legacy)).toBe(false)
  })

  it('keeps the empty state and its Book button when there are no slots at all', async () => {
    await openConfirmed([])
    expect(await screen.findByText(/No confirmed slots yet\. Book your first slot\./)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Book a slot' })).toBeTruthy()
  })
})

describe('nothing is dropped', () => {
  it('every slot the server sends is rendered exactly once', async () => {
    await openConfirmed(MIXED)
    await screen.findByText('Overdue One')
    expect(document.querySelectorAll('.sbs-confirmed-card')).toHaveLength(MIXED.length)
  })
})
