import React from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DailyOpsFilterMenu } from './DailyOpsFilterMenu.jsx'
import { applyFilters, checkedFilterValues, filterGroup, openFilters, toggleFilterValue } from './filterMenuTestHelpers.js'

/**
 * The Filters panel: Candidate and Attendee (multi-select, Apply to use) and
 * Sort by. Profiles and rounds are not here any more -- the Profile + Round
 * control beside it is the one place for them (15 Nov).
 */
describe('DailyOpsFilterMenu', () => {
  afterEach(() => {
    cleanup()
  })
  const candidateOptions = [
    { name: 'John Doe', scheduled: 2 },
    { name: 'Jane Smith', scheduled: 1 },
  ]
  const attendeeOptions = ['Nikhila', 'Bhavana', 'Tool']

  it('renders closed by default with no active count badge when filters are empty', () => {
    render(<DailyOpsFilterMenu candidateOptions={candidateOptions} attendeeOptions={attendeeOptions} />)

    const trigger = screen.getByRole('button', { name: 'Filters' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByText(/\(\d+\)/)).not.toBeInTheDocument()
  })

  it('displays active count badge when filters are applied', () => {
    render(
      <DailyOpsFilterMenu
        candidateFilter={['John Doe']}
        attendeeFilter={['Nikhila']}
        candidateOptions={candidateOptions}
        attendeeOptions={attendeeOptions}
      />
    )

    expect(screen.getByRole('button', { name: 'Filters (2 active)' })).toBeInTheDocument()
    expect(screen.getByText('(2)')).toBeInTheDocument()
  })

  it('opens a panel with Candidate, Attendee and Sort by only -- no Level or Profile', () => {
    render(<DailyOpsFilterMenu candidateOptions={candidateOptions} attendeeOptions={attendeeOptions} />)

    openFilters()

    const dialog = screen.getByRole('dialog', { name: 'Filter interviews' })
    expect(screen.getByRole('heading', { level: 3, name: 'Filters' })).toBeInTheDocument()
    expect(
      screen.getByText('Tick several values in a field to match any of them. Different fields must all match.')
    ).toBeInTheDocument()

    const labels = Array.from(dialog.querySelectorAll('.ops-filter-field__label')).map(el => el.textContent.trim())
    expect(labels).toEqual(['Candidate', 'Attendee', 'Sort by'])
    expect(filterGroup('Candidate filter')).toBeInTheDocument()
    expect(filterGroup('Attendee filter')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Candidate interview level filter' })).toBeNull()
    expect(screen.queryByRole('group', { name: 'Technology filter' })).toBeNull()
    expect(screen.getByRole('combobox', { name: 'Sort by' })).toBeInTheDocument()
  })

  it('lets several values be ticked in one field and applies them as lists', () => {
    const onApply = vi.fn()
    render(
      <DailyOpsFilterMenu candidateOptions={candidateOptions} attendeeOptions={attendeeOptions} onApply={onApply} />
    )

    openFilters()
    toggleFilterValue('Candidate filter', 'John Doe')
    toggleFilterValue('Candidate filter', 'Jane Smith')
    toggleFilterValue('Attendee filter', 'Bhavana')
    toggleFilterValue('Attendee filter', 'Tool')
    fireEvent.change(screen.getByRole('combobox', { name: 'Sort by' }), { target: { value: 'date_desc' } })

    // Four values plus a non-default sort.
    fireEvent.click(screen.getByRole('button', { name: 'Apply (5)' }))

    expect(onApply).toHaveBeenCalledWith({
      candidate: ['John Doe', 'Jane Smith'],
      attendee: ['Bhavana', 'Tool'],
      sort: 'date_desc',
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('unticks a value on a second click', () => {
    const onApply = vi.fn()
    render(<DailyOpsFilterMenu attendeeOptions={attendeeOptions} onApply={onApply} />)
    openFilters()
    toggleFilterValue('Attendee filter', 'Nikhila')
    toggleFilterValue('Attendee filter', 'Tool')
    toggleFilterValue('Attendee filter', 'Nikhila')
    expect(checkedFilterValues('Attendee filter')).toEqual(['Tool'])
    applyFilters()
    expect(onApply.mock.calls[0][0].attendee).toEqual(['Tool'])
  })

  it('changes nothing until Apply: closing drops the unapplied ticks', () => {
    const onApply = vi.fn()
    render(<DailyOpsFilterMenu attendeeFilter={['Tool']} attendeeOptions={attendeeOptions} onApply={onApply} />)
    openFilters()
    toggleFilterValue('Attendee filter', 'Bhavana')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onApply).not.toHaveBeenCalled()
    openFilters()
    expect(checkedFilterValues('Attendee filter')).toEqual(['Tool'])
  })

  it('shows the applied choices on the button as a count and a summary', () => {
    render(<DailyOpsFilterMenu candidateFilter={['John Doe']} attendeeFilter={['Bhavana', 'Tool']} />)
    const trigger = screen.getByRole('button', { name: 'Filters (3 active)' })
    expect(trigger).toHaveTextContent('(3)')
    expect(trigger.querySelector('.ops-filter-trigger__summary')).toHaveTextContent('John Doe · Bhavana, Tool')
    expect(trigger.getAttribute('title')).toBe('Candidate: John Doe\nAttendee: Bhavana, Tool')
  })

  it('shortens a long summary but keeps the whole of it in the tooltip', () => {
    render(<DailyOpsFilterMenu candidateFilter={['John Doe', 'Jane Smith', 'Asha Rao']} />)
    const trigger = screen.getByRole('button', { name: 'Filters (3 active)' })
    expect(trigger.querySelector('.ops-filter-trigger__summary')).toHaveTextContent('John Doe +2')
    expect(trigger.getAttribute('title')).toBe('Candidate: John Doe, Jane Smith, Asha Rao')
  })

  it('searches the candidate list without losing ticks', () => {
    render(<DailyOpsFilterMenu candidateOptions={candidateOptions} />)
    openFilters()
    toggleFilterValue('Candidate filter', 'John Doe')
    const search = screen.getByRole('searchbox', { name: 'Search candidate options' })
    fireEvent.change(search, { target: { value: 'jane' } })
    expect(within(filterGroup('Candidate filter')).getAllByRole('checkbox').map(b => b.getAttribute('aria-label')))
      .toEqual(['Jane Smith'])
    fireEvent.change(search, { target: { value: '' } })
    expect(checkedFilterValues('Candidate filter')).toEqual(['John Doe'])
  })

  it('keeps a chosen candidate on the list after the period no longer has them', () => {
    render(<DailyOpsFilterMenu candidateFilter={['Old Person']} candidateOptions={candidateOptions} />)
    openFilters()
    expect(checkedFilterValues('Candidate filter')).toEqual(['Old Person'])
  })

  it('lists the candidates already chosen first when the panel opens', () => {
    const many = ['Asha Varma', 'Bala Kumar', 'Chitra Rao', 'Dev Prasad'].map(name => ({ name, scheduled: 1 }))
    render(<DailyOpsFilterMenu candidateFilter={['Dev Prasad']} candidateOptions={many} />)
    openFilters()
    const order = () => within(filterGroup('Candidate filter')).getAllByRole('checkbox').map(b => b.getAttribute('aria-label'))
    expect(order()).toEqual(['Dev Prasad', 'Asha Varma', 'Bala Kumar', 'Chitra Rao'])
    // Ticking another does not move rows under the pointer.
    toggleFilterValue('Candidate filter', 'Chitra Rao')
    expect(order()).toEqual(['Dev Prasad', 'Asha Varma', 'Bala Kumar', 'Chitra Rao'])
  })

  it('has no clear or reset of its own: the page Reset is the only one (25 Nov)', () => {
    render(
      <DailyOpsFilterMenu
        candidateFilter={['John Doe']}
        attendeeFilter={['Tool']}
        sortOrder="date_desc"
        candidateOptions={candidateOptions}
        attendeeOptions={attendeeOptions}
      />
    )
    openFilters()
    const dialog = screen.getByRole('dialog', { name: 'Filter interviews' })
    const buttons = [...dialog.querySelectorAll('button')].map(b => b.textContent.trim())
    expect(buttons.filter(t => /clear|reset/i.test(t))).toEqual([])
    expect(buttons).toContain('Apply (3)')
  })

  it('still accepts a single string per field from older callers', () => {
    render(<DailyOpsFilterMenu candidateFilter="John Doe" attendeeFilter="Nikhila" candidateOptions={candidateOptions} />)
    expect(screen.getByRole('button', { name: 'Filters (2 active)' })).toBeInTheDocument()
    openFilters()
    expect(checkedFilterValues('Candidate filter')).toEqual(['John Doe'])
    expect(checkedFilterValues('Attendee filter')).toEqual(['Nikhila'])
  })

  it('unticking every value and applying empties the fields', () => {
    const onApply = vi.fn()
    render(
      <DailyOpsFilterMenu
        candidateFilter={['John Doe']}
        attendeeFilter={['Nikhila']}
        candidateOptions={candidateOptions}
        attendeeOptions={attendeeOptions}
        onApply={onApply}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Filters (2 active)' }))
    toggleFilterValue('Candidate filter', 'John Doe')
    toggleFilterValue('Attendee filter', 'Nikhila')
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))

    expect(onApply).toHaveBeenCalledWith({ candidate: [], attendee: [], sort: 'date_asc' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('keeps the floating panel inside the viewport and Apply above the fold', () => {
    const original = HTMLElement.prototype.getBoundingClientRect
    HTMLElement.prototype.getBoundingClientRect = function rect() {
      if (this.classList?.contains('ops-filter-panel')) {
        // Right-aligned to a button near the left edge: 29px off-screen.
        return { left: -29, right: 411, top: 228, bottom: 908, width: 440, height: 680, x: -29, y: 228 }
      }
      return original.call(this)
    }
    const height = window.innerHeight
    window.innerHeight = 900
    try {
      render(<DailyOpsFilterMenu />)
      openFilters()
      const panel = screen.getByRole('dialog')
      expect(panel.style.left).toBe('8px')
      expect(panel.style.right).toBe('auto')
      expect(panel.style.maxHeight).toBe('660px')
    } finally {
      HTMLElement.prototype.getBoundingClientRect = original
      window.innerHeight = height
    }
  })

  it('closes on Escape key press and refocuses trigger button', () => {
    render(<DailyOpsFilterMenu candidateOptions={candidateOptions} />)

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
        <DailyOpsFilterMenu candidateOptions={candidateOptions} />
      </div>
    )

    openFilters()
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    fireEvent.mouseDown(screen.getByTestId('outside'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
