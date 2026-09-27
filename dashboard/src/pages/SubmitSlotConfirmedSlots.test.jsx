/**
 * Confirmed slots must say HOW each slot was booked.
 *
 * The split carried this page across without the booking-source badge or its
 * stylesheet, so every confirmed slot rendered as a bare "Booked" and the
 * operator lost the AI-vs-candidate distinction. Nothing failed: the backend
 * kept returning `interview_booking_source`, the resolver kept its unit tests,
 * and only the rendering was gone — which no existing test looked at, in either
 * repository.
 *
 * These render the real page against a stubbed `/public/slots/booked` payload
 * so the badge is asserted where the operator actually sees it, not where the
 * helper is defined.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SubmitSlotPage } from './SubmitSlotPage.jsx'

const SLOTS = [
  {
    name: 'Aniket', technology: 'Java', interview_round: 'L1',
    date: '2026-08-15', time: '12:00', time_end: '12:30',
    interview_booking_source: 'ai_auto_booked',
  },
  {
    name: 'Manu', technology: 'Python', interview_round: 'L1',
    date: '2026-08-17', time: '12:00', time_end: '12:30',
    interview_booking_source: 'candidate_booked',
  },
  {
    // A legacy row written before the field existed. The historical behaviour
    // is a muted grey badge reading "Booked", never a guessed source.
    name: 'Ashok uppuluri', technology: 'DevOps', interview_round: 'Screening',
    date: '2026-08-18', time: '14:00', time_end: '14:30',
    interview_booking_source: '',
  },
  {
    name: 'Poojitha', technology: 'Java', interview_round: 'L2',
    date: '2026-08-15', time: '14:00', time_end: '14:30',
    interview_booking_source: 'ai_auto_booked',
  },
]

function stubFetch(slots = SLOTS) {
  return vi.fn((url) => {
    const u = String(url)
    const body = u.includes('/public/slots/booked')
      ? { status: 'ok', slots }
      : { status: 'ok', candidates: [] }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
  })
}

async function openConfirmed(slots) {
  vi.stubGlobal('fetch', stubFetch(slots))
  render(<SubmitSlotPage />)
  const tab = await screen.findByRole('tab', { name: /confirmed slots/i })
  fireEvent.click(tab)
  return tab
}

/** The card element containing a candidate's name. */
function cardFor(name) {
  const el = screen.queryByText(name) || screen.getByText(new RegExp(`^${name}$`, 'i'))
  return el.closest('.sbs-confirmed-card')
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('confirmed slot booking source', () => {
  it('labels an AI auto-booked slot, alongside its stage and Booked status', async () => {
    await openConfirmed()
    await waitFor(() => expect(cardFor('Aniket')).toBeTruthy())
    const card = within(cardFor('Aniket'))
    expect(card.getByText('L1')).toBeTruthy()
    expect(card.getByText('Booked')).toBeTruthy()
    expect(card.getByText('AI Auto-booked')).toBeTruthy()
  })

  it('labels a candidate-booked slot, alongside its stage and Booked status', async () => {
    await openConfirmed()
    await waitFor(() => expect(cardFor('Manu')).toBeTruthy())
    const card = within(cardFor('Manu'))
    expect(card.getByText('L1')).toBeTruthy()
    expect(card.getByText('Booked')).toBeTruthy()
    expect(card.getByText('Candidate booked')).toBeTruthy()
  })

  it('falls back to the historical muted Booked badge when no source was recorded', async () => {
    await openConfirmed()
    await waitFor(() => expect(cardFor('Ashok uppuluri')).toBeTruthy())
    const card = cardFor('Ashok uppuluri')
    const badge = card.querySelector('.sbs-source-badge')
    expect(badge).toBeTruthy()
    expect(badge.textContent).toBe('Booked')
    expect(badge.className).toContain('sbs-source-badge--unknown')
    // Never guessed into one of the two real sources.
    expect(card.textContent).not.toContain('AI Auto-booked')
    expect(card.textContent).not.toContain('Candidate booked')
  })

  it('tones the two real sources apart from each other and from unknown', async () => {
    await openConfirmed()
    await waitFor(() => expect(cardFor('Aniket')).toBeTruthy())
    expect(cardFor('Aniket').querySelector('.sbs-source-badge').className)
      .toContain('sbs-source-badge--auto')
    expect(cardFor('Manu').querySelector('.sbs-source-badge').className)
      .toContain('sbs-source-badge--candidate')
  })

  it('keeps the stage badge a separate element from the source badge', async () => {
    await openConfirmed()
    await waitFor(() => expect(cardFor('Aniket')).toBeTruthy())
    const card = cardFor('Aniket')
    const round = card.querySelector('.sbs-slot-card__round')
    const source = card.querySelector('.sbs-source-badge')
    expect(round).toBeTruthy()
    expect(source).toBeTruthy()
    expect(round).not.toBe(source)
    expect(round.textContent).toBe('L1')
    expect(source.textContent).toBe('AI Auto-booked')
  })
})

describe('the rest of the confirmed slots view is unchanged', () => {
  it('keeps stage badges for every round', async () => {
    await openConfirmed()
    await waitFor(() => expect(cardFor('Aniket')).toBeTruthy())
    expect(within(cardFor('Poojitha')).getByText('L2')).toBeTruthy()
    expect(within(cardFor('Ashok uppuluri')).getByText('Screening')).toBeTruthy()
  })

  it('keeps date grouping and per-day slot counts', async () => {
    await openConfirmed()
    await waitFor(() => expect(cardFor('Aniket')).toBeTruthy())
    const groups = document.querySelectorAll('.sbs-date-group')
    // 15th (2 slots), 17th (1), 18th (1)
    expect(groups.length).toBe(3)
    const counts = [...document.querySelectorAll('.sbs-date-group__count')].map((n) => n.textContent)
    expect(counts).toContain('2 slots')
    expect(counts).toContain('1 slot')
  })

  it('keeps candidate name and time on the card', async () => {
    await openConfirmed()
    await waitFor(() => expect(cardFor('Aniket')).toBeTruthy())
    const card = cardFor('Aniket')
    expect(card.querySelector('.sbs-slot-card__name').textContent).toBe('Aniket')
    expect(card.querySelector('.sbs-slot-card__time').textContent).toMatch(/12:00\s*pm.*12:30\s*pm/i)
  })

  it('renders every returned slot, one card each', async () => {
    await openConfirmed()
    await waitFor(() => expect(cardFor('Aniket')).toBeTruthy())
    expect(document.querySelectorAll('.sbs-confirmed-card').length).toBe(SLOTS.length)
  })
})

describe('the badge ships with its stylesheet', () => {
  // The split lost the markup and the CSS together, so asserting the class is
  // rendered proves only half of it.
  it('index.css defines all three source tones', async () => {
    const { readFileSync } = await import('node:fs')
    const { join, dirname } = await import('node:path')
    const { fileURLToPath } = await import('node:url')
    const css = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), '..', 'index.css'), 'utf8')
    expect(css).toMatch(/\.sbs-source-badge\s*\{/)
    for (const tone of ['auto', 'candidate', 'unknown']) {
      expect(css).toMatch(new RegExp(`\\.sbs-source-badge--${tone}\\s*\\{`))
    }
  })
})

/**
 * An assessment is a confirmed slot too.
 *
 * It is booked by the same automation, into the same roster, and it appears in
 * this list -- but it has no interview round, so a card labelled only by round
 * showed nothing at all, and the heading counted it as an interview. The
 * operator could not tell a one-hour test from an interview.
 */
describe('assessments in the confirmed list', () => {
  const MIXED = [
    {
      name: 'Dinesh', technology: 'Automation', interview_round: '',
      date: '2026-09-15', time: '19:05', time_end: '20:05',
      interview_booking_source: 'ai_auto_booked', booking_type: 'Assessment',
    },
    {
      name: 'Lavanya', technology: 'Java', interview_round: 'L1',
      date: '2026-09-16', time: '14:00', time_end: '14:30',
      interview_booking_source: 'ai_auto_booked', booking_type: 'Interview',
    },
  ]

  it('shows the assessment beside the interview', async () => {
    await openConfirmed(MIXED)

    await waitFor(() => expect(cardFor('Dinesh')).toBeTruthy())
    expect(cardFor('Lavanya')).toBeTruthy()
  })

  it('labels it Assessment where the round would be', async () => {
    await openConfirmed(MIXED)

    await waitFor(() => expect(within(cardFor('Dinesh')).getByText('Assessment')).toBeTruthy())
    expect(within(cardFor('Lavanya')).getByText('L1')).toBeTruthy()
    expect(within(cardFor('Lavanya')).queryByText('Assessment')).toBeNull()
  })

  it('keeps the AI Auto-booked badge on both', async () => {
    await openConfirmed(MIXED)

    await waitFor(() => expect(within(cardFor('Dinesh')).getByText(/auto-booked/i)).toBeTruthy())
    expect(within(cardFor('Lavanya')).getByText(/auto-booked/i)).toBeTruthy()
  })

  it('counts slots rather than interviews', async () => {
    await openConfirmed(MIXED)

    await waitFor(() => expect(screen.getByText('2 slots scheduled')).toBeTruthy())
    expect(screen.queryByText(/interviews scheduled/i)).toBeNull()
  })

  it('still says one slot for a single booking', async () => {
    await openConfirmed([MIXED[0]])

    await waitFor(() => expect(screen.getByText('1 slot scheduled')).toBeTruthy())
  })

  it('treats a row booked before the type existed as an interview', async () => {
    await openConfirmed([{ ...MIXED[1], booking_type: undefined }])

    await waitFor(() => expect(within(cardFor('Lavanya')).getByText('L1')).toBeTruthy())
    expect(within(cardFor('Lavanya')).queryByText('Assessment')).toBeNull()
  })
})

describe('candidate name capitalization and round badge presentation', () => {
  it('formats lowercase and uppercase candidate names into Title Case', async () => {
    const slots = [
      { name: 'candidate alpha', technology: 'QA', interview_round: 'Screening', date: '2026-09-24', time: '09:30' },
      { name: 'candidate beta', technology: 'Java', interview_round: 'L1', date: '2026-09-24', time: '14:00' },
      { name: 'CANDIDATE GAMMA', technology: 'ServiceNow', interview_round: 'L1', date: '2026-09-24', time: '15:00' },
    ]
    await openConfirmed(slots)
    await waitFor(() => expect(screen.getByText('Candidate Alpha')).toBeTruthy())
    expect(screen.getByText('Candidate Beta')).toBeTruthy()
    expect(screen.getByText('Candidate Gamma')).toBeTruthy()
  })

  it('renders "Round not specified" badge when interview has no round or generic technical round', async () => {
    const slots = [
      { name: 'Candidate Delta', technology: 'Data', interview_round: '', date: '2026-09-24', time: '16:00', booking_type: 'Interview' },
    ]
    await openConfirmed(slots)
    await waitFor(() => expect(screen.getByText('Candidate Delta')).toBeTruthy())
    const badge = screen.getByText('Round not specified')
    expect(badge).toBeTruthy()
    expect(badge.className).toContain('sbs-slot-card__round--unspecified')
    expect(screen.queryByText('Technical')).toBeNull()
  })

  it('renders L1 for explicit Technical Round 1 and L2 for Round 2', async () => {
    const slots = [
      { name: 'Candidate Delta', technology: 'Data', interview_round: 'L1', date: '2026-09-24', time: '14:30', booking_type: 'Interview' },
      { name: 'Candidate Epsilon', technology: 'ServiceNow', interview_round: 'L2', date: '2026-09-25', time: '15:30', booking_type: 'Interview' },
      { name: 'Candidate Zeta', technology: 'Java', interview_round: 'L3', date: '2026-09-26', time: '10:00', booking_type: 'Interview' },
    ]
    await openConfirmed(slots)
    await waitFor(() => expect(screen.getByText('Candidate Delta')).toBeTruthy())
    expect(screen.getByText('L1')).toBeTruthy()
    expect(screen.getByText('L2')).toBeTruthy()
    expect(screen.getByText('L3')).toBeTruthy()
  })

  it('keeps HR, Final, and Screening badges unchanged', async () => {
    const slots = [
      { name: 'Candidate A', technology: 'Tech', interview_round: 'Screening', date: '2026-09-24', time: '10:00', booking_type: 'Interview' },
      { name: 'Candidate B', technology: 'Tech', interview_round: 'Final', date: '2026-09-24', time: '11:00', booking_type: 'Interview' },
      { name: 'Candidate C', technology: 'Tech', interview_round: 'HR', date: '2026-09-24', time: '12:00', booking_type: 'Interview' },
    ]
    await openConfirmed(slots)
    await waitFor(() => expect(screen.getByText('Candidate A')).toBeTruthy())
    expect(screen.getByText('Screening')).toBeTruthy()
    expect(screen.getByText('Final')).toBeTruthy()
    expect(screen.getByText('HR')).toBeTruthy()
  })

  it('formats date headers with 3-letter month abbreviations (SEP, OCT, etc.)', async () => {
    const { formatDayHeader } = await import('./SubmitSlotPage.jsx')
    const sepHeader = formatDayHeader('2026-09-24')
    expect(sepHeader).toMatch(/24 SEP 2026/i)
    expect(sepHeader).not.toMatch(/SEPT/i)

    const octHeader = formatDayHeader('2026-10-05')
    expect(octHeader).toMatch(/5 OCT 2026/i)
  })
})

