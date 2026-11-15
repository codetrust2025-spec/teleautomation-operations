/**
 * Daily Ops Status column (Notes until 15 Nov): the feedback badge; its note opens from it.
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
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../context/AuthContext.jsx', () => ({
  useAuth: () => ({ role: 'admin', reference: '', enabled: false }),
}))
vi.mock('../context/ConfirmContext.jsx', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

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

describe('the Status column (was Notes)', () => {
  async function renderPanel() {
    render(<DailyOpsPanel />)
    await waitFor(() => expect(screen.getByText('Person 0')).toBeInTheDocument())
    await act(async () => { await Promise.resolve() })
  }
  const statusOf = name => screen.getByText(name).closest('tr').querySelector('td[data-label="Status"]')

  it('is headed Status, and Notes is gone', async () => {
    await renderPanel()
    const heads = [...document.querySelectorAll('.ops-interview-table thead th')].map(th => th.textContent)
    expect(heads).toContain('Status')
    expect(heads).not.toContain('Notes')
    expect(document.querySelector('td[data-label="Notes"]')).toBeNull()
  })

  it('shows each row only the feedback badge that was stored; the note stays out of the row', async () => {
    await renderPanel()
    const expected = ['Excellent', 'Good', 'Positive', 'Average', 'Needs Improvement', 'Negative']
    FEEDBACK.slice(0, 6).forEach((value, i) => {
      const cell = statusOf(`Person ${i}`)
      const pill = cell.querySelector('.ops-feedback-pill')
      expect(pill.textContent).toBe(expected[i])
      expect(pill.getAttribute('data-feedback')).toBe(value)
      expect(cell.textContent).toBe(expected[i])
    })
    expect(screen.queryByText('went well')).toBeNull()
    expect(statusOf('Person 6').querySelector('.ops-feedback-pill')).toBeNull()
    expect(statusOf('Person 6').textContent).toBe('—')
  })

  it('opens the full note on hover and closes it when the pointer leaves', async () => {
    await renderPanel()
    const badge = within(statusOf('Person 1')).getByRole('button', { name: 'Status: Good. Show notes' })
    fireEvent.mouseEnter(badge)
    const card = screen.getByRole('tooltip')
    expect(card.textContent).toBe('went well')
    expect(badge.getAttribute('aria-describedby')).toBe(card.id)
    // Portalled out of the table, so its scroll box cannot clip it.
    expect(card.parentElement).toBe(document.body)
    fireEvent.mouseLeave(badge)
    expect(screen.queryByRole('tooltip')).toBeNull()
  })

  it('opens on a tap and stays until tapped again, tapped elsewhere or Escape, and never opens Edit', async () => {
    await renderPanel()
    const badge = within(statusOf('Person 0')).getByRole('button', { name: 'Status: Excellent. Show notes' })
    fireEvent.focus(badge)
    fireEvent.click(badge)
    expect(screen.getByRole('tooltip').textContent).toBe('went well')
    expect(screen.queryByText('Edit interview slot')).toBeNull()
    fireEvent.click(badge)
    expect(screen.queryByRole('tooltip')).toBeNull()

    fireEvent.click(badge)
    expect(screen.getByRole('tooltip')).toBeTruthy()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('tooltip')).toBeNull()

    fireEvent.click(badge)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).toBeNull()
    expect(screen.queryByText('Edit interview slot')).toBeNull()
  })

  it('still opens Edit from the rest of the row', async () => {
    await renderPanel()
    fireEvent.click(screen.getByText('Person 2').closest('tr').querySelector('td[data-label="Technology"]'))
    expect(await screen.findByText('Edit interview slot')).toBeTruthy()
  })

  it('says Negative on the negative row only', async () => {
    await renderPanel()
    const negatives = [...document.querySelectorAll('.ops-feedback-pill')].filter(p => p.textContent === 'Negative')
    expect(negatives.map(p => p.getAttribute('data-feedback'))).toEqual(['negative'])
    expect(statusOf('Person 1').querySelector('.ops-feedback-pill').className).toContain('ops-feedback-pill--positive')
    expect(statusOf('Person 5').querySelector('.ops-feedback-pill').className).toContain('ops-feedback-pill--negative')
  })

  it('styles each tone, red for negative only', () => {
    expect(CSS).toMatch(/\.ops-feedback-pill--positive\{[^}]*color:#6ee7b7/)
    expect(CSS).toMatch(/\.ops-feedback-pill--caution\{/)
    expect(CSS).toMatch(/\.ops-feedback-pill--negative\{[^}]*color:#fca5a5/)
  })
})
