/**
 * The Status badge on its own: what it shows for each combination of feedback
 * and note, and that a note-less badge is not a button (a tap on it opens the
 * row's Edit dialog, as the rest of the row does).
 */
import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { FeedbackStatusBadge } from './FeedbackStatusBadge.jsx'
import { feedbackMeta } from './InterviewRoster.jsx'

afterEach(cleanup)

describe('FeedbackStatusBadge', () => {
  it('shows a dash when nothing was recorded', () => {
    const { container } = render(<FeedbackStatusBadge feedback={null} note="" />)
    expect(container.textContent).toBe('—')
    expect(container.querySelector('.ops-status-badge')).toBeNull()
  })

  it('shows the badge alone, not a button, when there is no note', () => {
    const { container } = render(<FeedbackStatusBadge feedback={feedbackMeta('average')} note="  " />)
    expect(container.textContent).toBe('Average')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('shows a Note badge for a remark saved without feedback, so the remark stays reachable', () => {
    render(<FeedbackStatusBadge feedback={null} note="Client called it off" />)
    const badge = screen.getByRole('button', { name: 'Status: Note. Show notes' })
    expect(badge.className).toContain('ops-feedback-pill--note')
    fireEvent.mouseEnter(badge)
    expect(screen.getByRole('tooltip').textContent).toBe('Client called it off')
  })

  it('shows a long note in full', () => {
    const note = 'Joined late because of a network issue, then answered every question well and the panel asked for L2.'
    render(<FeedbackStatusBadge feedback={feedbackMeta('excellent')} note={note} />)
    fireEvent.focus(screen.getByRole('button', { name: 'Status: Excellent. Show notes' }))
    expect(screen.getByRole('tooltip').textContent).toBe(note)
  })

  it('reports whether the note is open', () => {
    render(<FeedbackStatusBadge feedback={feedbackMeta('good')} note="went well" />)
    const badge = screen.getByRole('button', { name: 'Status: Good. Show notes' })
    expect(badge.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(badge)
    expect(badge.getAttribute('aria-expanded')).toBe('true')
  })
})
