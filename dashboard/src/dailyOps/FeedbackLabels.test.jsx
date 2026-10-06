/**
 * Daily Ops Notes column: interview feedback says what was recorded.
 *
 * It printed "Positive" for the legacy "positive" and "Negative" for every
 * other value, so rows the operator marked Good (two on 6 Oct, notes "went
 * well") read "Negative". Each stored value now shows its own label and tone,
 * only "negative" ever says Negative, and the stored value and the note are
 * shown as they are -- nothing is rewritten.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))
vi.mock('./PendingWorksStrip.jsx', () => ({ PendingWorksStrip: () => null }))

import { DailyOpsPanel } from './DailyOpsPanel.jsx'
import { feedbackMeta } from './InterviewRoster.jsx'

const here = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(here, '..', 'dailyOps.css'), 'utf-8').replace(/\r\n/g, '\n')

const TODAY = '2026-10-06'
const FEEDBACK = ['excellent', 'good', 'positive', 'average', 'needs_improvement', 'negative', '']
const ROWS = FEEDBACK.map((feedback, i) => ({
  id: `r${i}`, name: `Person ${i}`, phone: `90000009${String(i).padStart(2, '0')}`, date: TODAY,
  time: `${10 + i}:00`, time_end: `${10 + i}:30`, technology: 'ServiceNow', interview_round: 'L1',
  interview_attendee: 'Bhavana', interview_attendance_status: feedback ? 'attended' : '',
  interview_feedback: feedback, interview_attendance_remark: feedback ? 'went well' : '',
}))

function jsonResponse(body) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => body }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${TODAY}T06:00:00Z`))
  vi.stubGlobal('fetch', vi.fn(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/interviews/global')) {
      return jsonResponse({
        status: 'ok',
        interviews: { count: ROWS.length, by_candidate: ROWS.map(r => ({ name: r.name, scheduled: 1 })), by_technology: [] },
        available_months: [], booking_overview: { total: ROWS.length, by_candidate: [], by_level: [], by_technology: [] },
      })
    }
    if (/\/interviews\/(daily|monitor)$/.test(url.pathname)) {
      return jsonResponse({ status: 'ok', interviews: ROWS, count: ROWS.length, awaiting_interviews: [] })
    }
    return jsonResponse({ status: 'ok' })
  }))
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('feedbackMeta', () => {
  it.each([
    ['excellent', 'Excellent', 'positive'],
    ['good', 'Good', 'positive'],
    ['positive', 'Positive', 'positive'],
    ['average', 'Average', 'neutral'],
    ['needs_improvement', 'Needs Improvement', 'caution'],
    ['negative', 'Negative', 'negative'],
    ['mixed_signals', 'Mixed signals', 'neutral'],
  ])('labels %s as %s (%s)', (value, label, tone) => {
    expect(feedbackMeta(value)).toEqual({ value, label, tone })
  })

  it('shows nothing when no feedback was recorded', () => {
    expect(feedbackMeta('')).toBeNull()
    expect(feedbackMeta(null)).toBeNull()
    expect(feedbackMeta(undefined)).toBeNull()
  })

  it('never calls anything but "negative" Negative', () => {
    for (const value of ['excellent', 'good', 'positive', 'average', 'needs_improvement', 'other']) {
      expect(feedbackMeta(value).label).not.toBe('Negative')
      expect(feedbackMeta(value).tone).not.toBe('negative')
    }
  })
})

describe('the Notes column', () => {
  async function renderPanel() {
    render(<DailyOpsPanel />)
    await waitFor(() => expect(screen.getByText('Person 0')).toBeInTheDocument())
    await act(async () => { await Promise.resolve() })
  }
  const notesOf = name => screen.getByText(name).closest('tr').querySelector('td[data-label="Notes"]')

  it('shows each row the feedback that was stored, with the note beside it', async () => {
    await renderPanel()
    const expected = ['Excellent', 'Good', 'Positive', 'Average', 'Needs Improvement', 'Negative']
    FEEDBACK.slice(0, 6).forEach((value, i) => {
      const pill = notesOf(`Person ${i}`).querySelector('.ops-feedback-pill')
      expect(pill.textContent).toBe(expected[i])
      expect(pill.getAttribute('data-feedback')).toBe(value)
      expect(notesOf(`Person ${i}`).querySelector('.ops-interview-notes-text').textContent).toBe('went well')
    })
    expect(notesOf('Person 6').querySelector('.ops-feedback-pill')).toBeNull()
    expect(notesOf('Person 6').textContent).toBe('—')
  })

  it('says Negative on the negative row only', async () => {
    await renderPanel()
    const negatives = [...document.querySelectorAll('.ops-feedback-pill')].filter(p => p.textContent === 'Negative')
    expect(negatives.map(p => p.getAttribute('data-feedback'))).toEqual(['negative'])
    expect(notesOf('Person 1').querySelector('.ops-feedback-pill').className).toContain('ops-feedback-pill--positive')
    expect(notesOf('Person 5').querySelector('.ops-feedback-pill').className).toContain('ops-feedback-pill--negative')
  })

  it('styles each tone, red for negative only', () => {
    expect(CSS).toMatch(/\.ops-feedback-pill--positive\{[^}]*color:#6ee7b7/)
    expect(CSS).toMatch(/\.ops-feedback-pill--caution\{/)
    expect(CSS).toMatch(/\.ops-feedback-pill--negative\{[^}]*color:#fca5a5/)
  })
})
