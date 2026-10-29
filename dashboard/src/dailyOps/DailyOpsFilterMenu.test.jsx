import React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DailyOpsFilterMenu } from './DailyOpsFilterMenu.jsx'

describe('DailyOpsFilterMenu', () => {
  afterEach(() => {
    cleanup()
  })
  const candidateOptions = [
    { name: 'John Doe', scheduled: 2 },
    { name: 'Jane Smith', scheduled: 1 },
  ]
  const attendeeOptions = ['Nikhila', 'Bhavana', 'Tool']
  const roundOptions = ['L1', 'L2', 'HR', 'Final']
  const technologyOptions = ['React', 'Node.js', 'Python']

  it('renders closed by default with no active count badge when filters are empty', () => {
    render(
      <DailyOpsFilterMenu
        candidateOptions={candidateOptions}
        attendeeOptions={attendeeOptions}
        roundOptions={roundOptions}
        technologyOptions={technologyOptions}
      />
    )

    const trigger = screen.getByRole('button', { name: 'Filters' })
    expect(trigger).toBeInTheDocument()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByText(/\(\d+\)/)).not.toBeInTheDocument()
  })

  it('displays active count badge when filters are applied', () => {
    render(
      <DailyOpsFilterMenu
        candidateFilter="John Doe"
        attendeeFilter="Nikhila"
        candidateOptions={candidateOptions}
        attendeeOptions={attendeeOptions}
        roundOptions={roundOptions}
        technologyOptions={technologyOptions}
      />
    )

    const trigger = screen.getByRole('button', { name: 'Filters (2 active)' })
    expect(trigger).toBeInTheDocument()
    expect(screen.getByText('(2)')).toBeInTheDocument()
  })

  it('opens panel on click with header, subtitle, clear all button, and fields in exact order', () => {
    render(
      <DailyOpsFilterMenu
        candidateOptions={candidateOptions}
        attendeeOptions={attendeeOptions}
        roundOptions={roundOptions}
        technologyOptions={technologyOptions}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))

    const dialog = screen.getByRole('dialog', { name: 'Filter interviews' })
    expect(dialog).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Filters' })).toBeInTheDocument()
    expect(
      screen.getByText('Filter interviews by candidate, attendee, level, profile or sort order.')
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Clear all/i })).toBeInTheDocument()

    // Verify fields in exact order: Candidate -> Attendee -> Level -> Profile / Technology -> Sort by
    const labels = Array.from(dialog.querySelectorAll('.ops-filter-field__label')).map(el =>
      el.textContent.trim()
    )
    expect(labels).toEqual(['Candidate', 'Attendee', 'Level', 'Profile / Technology', 'Sort by'])

    expect(screen.getByRole('combobox', { name: 'Candidate filter' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Attendee filter' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Candidate interview level filter' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Technology filter' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Sort by' })).toBeInTheDocument()
  })

  it('allows changing fields and applying them with onApply callback', () => {
    const onApply = vi.fn()
    render(
      <DailyOpsFilterMenu
        candidateOptions={candidateOptions}
        attendeeOptions={attendeeOptions}
        roundOptions={roundOptions}
        technologyOptions={technologyOptions}
        onApply={onApply}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))

    fireEvent.change(screen.getByRole('combobox', { name: 'Candidate filter' }), {
      target: { value: 'John Doe' },
    })
    fireEvent.change(screen.getByRole('combobox', { name: 'Attendee filter' }), {
      target: { value: 'Bhavana' },
    })
    fireEvent.change(screen.getByRole('combobox', { name: 'Candidate interview level filter' }), {
      target: { value: 'L1' },
    })
    fireEvent.change(screen.getByRole('combobox', { name: 'Technology filter' }), {
      target: { value: 'React' },
    })
    fireEvent.change(screen.getByRole('combobox', { name: 'Sort by' }), {
      target: { value: 'date_desc' },
    })

    const applyBtn = screen.getByRole('button', { name: /Apply \(5\)/i })
    expect(applyBtn).toBeInTheDocument()

    fireEvent.click(applyBtn)

    expect(onApply).toHaveBeenCalledWith({
      candidate: 'John Doe',
      attendee: 'Bhavana',
      round: 'L1',
      technology: 'React',
      sort: 'date_desc',
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('clears draft fields when Clear all is clicked', () => {
    render(
      <DailyOpsFilterMenu
        candidateFilter="John Doe"
        attendeeFilter="Nikhila"
        candidateOptions={candidateOptions}
        attendeeOptions={attendeeOptions}
        roundOptions={roundOptions}
        technologyOptions={technologyOptions}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Filters (2 active)' }))

    expect(screen.getByRole('combobox', { name: 'Candidate filter' })).toHaveValue('John Doe')
    expect(screen.getByRole('combobox', { name: 'Attendee filter' })).toHaveValue('Nikhila')

    fireEvent.click(screen.getByRole('button', { name: /Clear all/i }))

    expect(screen.getByRole('combobox', { name: 'Candidate filter' })).toHaveValue('')
    expect(screen.getByRole('combobox', { name: 'Attendee filter' })).toHaveValue('')
    expect(screen.getByRole('combobox', { name: 'Sort by' })).toHaveValue('date_asc')
  })

  it('resets all filters and closes panel when Reset is clicked', () => {
    const onApply = vi.fn()
    render(
      <DailyOpsFilterMenu
        candidateFilter="John Doe"
        attendeeFilter="Nikhila"
        candidateOptions={candidateOptions}
        attendeeOptions={attendeeOptions}
        roundOptions={roundOptions}
        technologyOptions={technologyOptions}
        onApply={onApply}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Filters (2 active)' }))

    fireEvent.click(screen.getByRole('button', { name: 'Reset' }))

    expect(onApply).toHaveBeenCalledWith({
      candidate: '',
      attendee: '',
      round: '',
      technology: '',
      sort: 'date_asc',
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('closes on Escape key press and refocuses trigger button', () => {
    render(
      <DailyOpsFilterMenu
        candidateOptions={candidateOptions}
        attendeeOptions={attendeeOptions}
        roundOptions={roundOptions}
        technologyOptions={technologyOptions}
      />
    )

    const trigger = screen.getByRole('button', { name: 'Filters' })
    fireEvent.click(trigger)
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(document.activeElement).toBe(trigger)
  })

  it('closes when clicking outside the panel', () => {
    render(
      <div>
        <div data-testid="outside">Outside</div>
        <DailyOpsFilterMenu
          candidateOptions={candidateOptions}
          attendeeOptions={attendeeOptions}
          roundOptions={roundOptions}
          technologyOptions={technologyOptions}
        />
      </div>
    )

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    fireEvent.mouseDown(screen.getByTestId('outside'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
