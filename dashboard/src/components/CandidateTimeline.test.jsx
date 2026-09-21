/**
 * The Candidate history card shows one person's whole history -- bookings,
 * moves, attendance marks, payments and mail -- as the backend assembles it at
 * /api/candidates/{id}/timeline. It used to list only the recruitment events
 * read from Gmail, so a booking, a cancellation or a payment never appeared.
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'

import { CandidateOutcomes } from './RecruitmentMailPanelRedesign.jsx'

const ENTRIES = [
  { at: '2099-09-18T11:33:59Z', kind: 'attendance', title: 'Marked attended',
    detail: '2099-09-18 16:30-17:30 · L1 · went well · by operations_admin', source: 'Daily Ops',
    booking_id: 'slot-a', mail_id: null, event_id: null },
  { at: '2099-09-14T07:00:00Z', kind: 'alert', title: 'Cancellation not applied',
    detail: 'We found more than one booking for this candidate. · Cancelled: interview', source: 'Gmail',
    booking_id: null, mail_id: 'gm-8', event_id: null },
  { at: '2099-09-01T10:00:00Z', kind: 'mail', title: 'Shortlisted',
    detail: 'Example Corp · Engineer', source: 'Candidate Gmail',
    booking_id: null, mail_id: 'mm-9', event_id: 'ev-2' },
]

afterEach(() => cleanup())

function renderCard(props = {}) {
  const onEvidence = vi.fn()
  render(<CandidateOutcomes offers={[]} selectedId="p1" timeline={ENTRIES} onEvidence={onEvidence} {...props} />)
  return { onEvidence }
}

describe('Candidate history', () => {
  it('lists every kind of entry with what happened, the detail and where it came from', () => {
    renderCard()
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(within(items[0]).getByText('Marked attended')).toBeTruthy()
    expect(within(items[0]).getByText(/went well · by operations_admin/)).toBeTruthy()
    expect(within(items[0]).getByText('Daily Ops')).toBeTruthy()
    expect(within(items[1]).getByText('Cancellation not applied')).toBeTruthy()
    expect(within(items[2]).getByText('Candidate Gmail')).toBeTruthy()
  })

  it('offers evidence only where there is a recruitment event behind the entry', () => {
    const { onEvidence } = renderCard()
    const items = screen.getAllByRole('listitem')
    expect(within(items[0]).queryByText('View evidence')).toBeNull()
    expect(within(items[1]).queryByText('View evidence')).toBeNull()
    fireEvent.click(within(items[2]).getByText('View evidence'))
    expect(onEvidence).toHaveBeenCalledWith('ev-2')
  })

  it('opens the booking in Daily Ops from an entry that names one', () => {
    const seen = []
    const listen = (event) => seen.push(event.detail)
    window.addEventListener('teleautomation:navigate', listen)
    try {
      renderCard()
      fireEvent.click(within(screen.getAllByRole('listitem')[0]).getByText('View booking'))
    } finally {
      window.removeEventListener('teleautomation:navigate', listen)
    }
    expect(seen).toEqual([{ view: 'daily-ops', bookingId: 'slot-a', candidateId: 'p1' }])
  })

  it('says so plainly when nothing is recorded', () => {
    renderCard({ timeline: [] })
    expect(screen.getByText('Nothing recorded for this candidate yet.')).toBeTruthy()
  })
})
