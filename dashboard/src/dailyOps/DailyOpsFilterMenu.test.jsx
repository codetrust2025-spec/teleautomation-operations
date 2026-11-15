import React from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DailyOpsFilterMenu } from './DailyOpsFilterMenu.jsx'
import { applyFilters, checkedFilterValues, filterGroup, openFilters, toggleFilterValue } from './filterMenuTestHelpers.js'

describe('DailyOpsFilterMenu', () => {
  afterEach(() => {
    cleanup()
  })
  const candidateOptions = [
    { name: 'John Doe', scheduled: 2 },
    { name: 'Jane Smith', scheduled: 1 },
  ]
  const attendeeOptions = ['Nikhila', 'Bhavana', 'Tool']
  const roundOptions = ['Screening', 'L1', 'L2', 'L3', 'HR', 'Final']
  const technologyOptions = ['React', 'Node.js', 'Python']
  const LEVEL = 'Candidate interview level filter'

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

  it('opens a panel with header, the AND/OR rule, Clear all, and fields in order', () => {
    render(
      <DailyOpsFilterMenu
        candidateOptions={candidateOptions}
        attendeeOptions={attendeeOptions}
        roundOptions={roundOptions}
        technologyOptions={technologyOptions}
      />
    )

    openFilters()

    const dialog = screen.getByRole('dialog', { name: 'Filter interviews' })
    expect(screen.getByRole('heading', { level: 3, name: 'Filters' })).toBeInTheDocument()
    expect(
      screen.getByText('Tick several values in a field to match any of them. Different fields must all match.')
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Clear all/i })).toBeInTheDocument()

    const labels = Array.from(dialog.querySelectorAll('.ops-filter-field__label')).map(el => el.textContent.trim())
    expect(labels).toEqual(['Candidate', 'Attendee', 'Level', 'Profile / Technology', 'Sort by'])

    for (const name of ['Candidate filter', 'Attendee filter', LEVEL, 'Technology filter']) {
      expect(filterGroup(name)).toBeInTheDocument()
    }
    expect(screen.getByRole('combobox', { name: 'Sort by' })).toBeInTheDocument()
    expect(within(filterGroup(LEVEL)).getAllByRole('checkbox').map(b => b.getAttribute('aria-label')))
      .toEqual(roundOptions)
  })

  it('offers every level the backend accepts by default, L3 included', () => {
    render(<DailyOpsFilterMenu />)
    openFilters()
    expect(within(filterGroup(LEVEL)).getAllByRole('checkbox').map(b => b.getAttribute('aria-label')))
      .toEqual(['Screening', 'L1', 'L2', 'L3', 'HR', 'Final'])
  })

  it('lets several values be ticked in one field and applies them as lists', () => {
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

    openFilters()
    toggleFilterValue(LEVEL, 'HR')
    toggleFilterValue(LEVEL, 'L2')
    toggleFilterValue(LEVEL, 'L3')
    toggleFilterValue('Candidate filter', 'John Doe')
    toggleFilterValue('Candidate filter', 'Jane Smith')
    toggleFilterValue('Attendee filter', 'Bhavana')
    toggleFilterValue('Technology filter', 'React')
    fireEvent.change(screen.getByRole('combobox', { name: 'Sort by' }), { target: { value: 'date_desc' } })

    // Seven values plus a non-default sort.
    fireEvent.click(screen.getByRole('button', { name: 'Apply (8)' }))

    expect(onApply).toHaveBeenCalledWith({
      candidate: ['John Doe', 'Jane Smith'],
      attendee: ['Bhavana'],
      round: ['HR', 'L2', 'L3'],
      technology: ['React'],
      sort: 'date_desc',
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('unticks a value on a second click', () => {
    const onApply = vi.fn()
    render(<DailyOpsFilterMenu roundOptions={roundOptions} onApply={onApply} />)
    openFilters()
    toggleFilterValue(LEVEL, 'HR')
    toggleFilterValue(LEVEL, 'L2')
    toggleFilterValue(LEVEL, 'HR')
    expect(checkedFilterValues(LEVEL)).toEqual(['L2'])
    applyFilters()
    expect(onApply.mock.calls[0][0].round).toEqual(['L2'])
  })

  it('changes nothing until Apply: closing drops the unapplied ticks', () => {
    const onApply = vi.fn()
    render(<DailyOpsFilterMenu roundFilter={['HR']} roundOptions={roundOptions} onApply={onApply} />)
    openFilters()
    toggleFilterValue(LEVEL, 'L2')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onApply).not.toHaveBeenCalled()
    openFilters()
    expect(checkedFilterValues(LEVEL)).toEqual(['HR'])
  })

  it('shows the applied choices on the button as a count and a summary', () => {
    render(<DailyOpsFilterMenu roundFilter={['HR', 'L2', 'L3']} attendeeFilter={['Bhavana']} roundOptions={roundOptions} />)
    const trigger = screen.getByRole('button', { name: 'Filters (4 active)' })
    expect(trigger).toHaveTextContent('(4)')
    expect(trigger.querySelector('.ops-filter-trigger__summary')).toHaveTextContent('Bhavana · HR, L2, L3')
    expect(trigger.getAttribute('title')).toBe('Attendee: Bhavana\nLevel: HR, L2, L3')
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

  it('clears one field with its own Clear link', () => {
    render(<DailyOpsFilterMenu roundFilter={['HR', 'L2']} attendeeFilter={['Tool']} roundOptions={roundOptions} />)
    openFilters()
    fireEvent.click(screen.getByRole('button', { name: 'Clear Level filter' }))
    expect(checkedFilterValues(LEVEL)).toEqual([])
    expect(checkedFilterValues('Attendee filter')).toEqual(['Tool'])
  })

  it('still accepts a single string per field from older callers', () => {
    render(<DailyOpsFilterMenu candidateFilter="John Doe" attendeeFilter="Nikhila" candidateOptions={candidateOptions} />)
    expect(screen.getByRole('button', { name: 'Filters (2 active)' })).toBeInTheDocument()
    openFilters()
    expect(checkedFilterValues('Candidate filter')).toEqual(['John Doe'])
    expect(checkedFilterValues('Attendee filter')).toEqual(['Nikhila'])
  })

  it('clears draft fields when Clear all is clicked, without applying', () => {
    const onApply = vi.fn()
    render(
      <DailyOpsFilterMenu
        candidateFilter={['John Doe']}
        attendeeFilter={['Nikhila', 'Tool']}
        sortOrder="date_desc"
        candidateOptions={candidateOptions}
        attendeeOptions={attendeeOptions}
        onApply={onApply}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Filters (4 active)' }))
    fireEvent.click(screen.getByRole('button', { name: /Clear all/i }))

    expect(checkedFilterValues('Candidate filter')).toEqual([])
    expect(checkedFilterValues('Attendee filter')).toEqual([])
    expect(screen.getByRole('combobox', { name: 'Sort by' })).toHaveValue('date_asc')
    expect(onApply).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Apply' })).toBeInTheDocument()
  })

  it('resets all filters and closes panel when Reset is clicked', () => {
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
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }))

    expect(onApply).toHaveBeenCalledWith({ candidate: [], attendee: [], round: [], technology: [], sort: 'date_asc' })
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
