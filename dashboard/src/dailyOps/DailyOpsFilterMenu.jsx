import React, { useEffect, useId, useRef, useState } from 'react'
import { Icon } from '../components/ui/Icon.jsx'
import { DEFAULT_ROSTER_SORT, ROSTER_SORT_OPTIONS, rosterSortLabel } from './rosterSort.js'

/**
 * FilterPanel & FilterTrigger for Daily Ops / Interview roster.
 * Matches the mockup:
 * - Trigger: [icon] Filters (n) [chevron]
 * - Floating dark-themed panel aligned to the right
 * - Header: filter icon, title "Filters", subtitle, "Clear all" button
 * - Form fields: Candidate, Attendee, Level, Profile / Technology, Sort by
 * - Footer: "Reset" secondary button, "Apply (n)" primary button
 */
export function DailyOpsFilterMenu({
  // Filter values
  candidateFilter = '',
  attendeeFilter = '',
  roundFilter = '',
  technologyFilter = '',
  sortOrder = DEFAULT_ROSTER_SORT,
  // Options
  candidateOptions = [],
  attendeeOptions = ['Nikhila', 'Bhavana', 'Tool'],
  roundOptions = ['L1', 'L2', 'HR', 'Final', 'Screening'],
  technologyOptions = [],
  // Apply callback
  onApply,
  // Outside reset helper if needed
  onResetAll,
}) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef(null)
  const panelRef = useRef(null)
  const panelId = useId()

  // Draft state inside the popover
  const [draftCandidate, setDraftCandidate] = useState(candidateFilter)
  const [draftAttendee, setDraftAttendee] = useState(attendeeFilter)
  const [draftRound, setDraftRound] = useState(roundFilter)
  const [draftTechnology, setDraftTechnology] = useState(technologyFilter)
  const [draftSort, setDraftSort] = useState(sortOrder)

  // Sync draft with incoming props when opening
  useEffect(() => {
    if (open) {
      setDraftCandidate(candidateFilter)
      setDraftAttendee(attendeeFilter)
      setDraftRound(roundFilter)
      setDraftTechnology(technologyFilter)
      setDraftSort(sortOrder)
    }
  }, [open, candidateFilter, attendeeFilter, roundFilter, technologyFilter, sortOrder])

  // Active count calculation (candidate, attendee, round, tech, and non-default sort)
  // For the badge, count active applied filters
  const appliedCount = [
    candidateFilter,
    attendeeFilter,
    roundFilter,
    technologyFilter,
    sortOrder !== DEFAULT_ROSTER_SORT ? sortOrder : '',
  ].filter(Boolean).length

  // Draft active count for Apply (n)
  const draftCount = [
    draftCandidate,
    draftAttendee,
    draftRound,
    draftTechnology,
    draftSort !== DEFAULT_ROSTER_SORT ? draftSort : '',
  ].filter(Boolean).length

  // Close on outside click or Escape
  useEffect(() => {
    if (!open) return undefined

    function onPointerDown(e) {
      if (triggerRef.current?.contains(e.target)) return
      if (panelRef.current?.contains(e.target)) return
      setOpen(false)
    }

    function onKeyDown(e) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setOpen(false)
        triggerRef.current?.focus()
      }
    }

    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  // Focus trap / focus management
  useEffect(() => {
    if (open) {
      const firstInput = panelRef.current?.querySelector('select, button')
      firstInput?.focus()
    }
  }, [open])

  function handleClearAll() {
    setDraftCandidate('')
    setDraftAttendee('')
    setDraftRound('')
    setDraftTechnology('')
    setDraftSort(DEFAULT_ROSTER_SORT)
  }

  function handleReset() {
    // Reverts draft to currently applied filters or clears back to default
    setDraftCandidate('')
    setDraftAttendee('')
    setDraftRound('')
    setDraftTechnology('')
    setDraftSort(DEFAULT_ROSTER_SORT)
    onApply?.({
      candidate: '',
      attendee: '',
      round: '',
      technology: '',
      sort: DEFAULT_ROSTER_SORT,
    })
    setOpen(false)
    triggerRef.current?.focus()
  }

  function handleApply(e) {
    e?.preventDefault()
    onApply?.({
      candidate: draftCandidate,
      attendee: draftAttendee,
      round: draftRound,
      technology: draftTechnology,
      sort: draftSort,
    })
    setOpen(false)
    triggerRef.current?.focus()
  }

  return (
    <div className="ops-filter-dropdown-wrap">
      <button
        ref={triggerRef}
        type="button"
        className={`ops-filter-trigger${open ? ' ops-filter-trigger--open' : ''}${appliedCount > 0 ? ' ops-filter-trigger--active' : ''}`}
        onClick={() => setOpen(prev => !prev)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={appliedCount > 0 ? `Filters (${appliedCount} active)` : 'Filters'}
      >
        <span className="ops-filter-trigger__icon" aria-hidden="true">
          <Icon name="filter" size={14} strokeWidth={2.2} />
        </span>
        <span className="ops-filter-trigger__label">Filters</span>
        {appliedCount > 0 && (
          <span className="ops-filter-trigger__badge">({appliedCount})</span>
        )}
        <span className="ops-filter-trigger__chevron" aria-hidden="true">
          <Icon name="chevron-down" size={13} strokeWidth={2.2} />
        </span>
      </button>

      {open && (
        <div
          ref={panelRef}
          id={panelId}
          className="ops-filter-panel"
          role="dialog"
          aria-modal="false"
          aria-label="Filter interviews"
        >
          {/* Header */}
          <div className="ops-filter-panel__header">
            <div className="ops-filter-panel__title-wrap">
              <span className="ops-filter-panel__title-icon" aria-hidden="true">
                <Icon name="filter" size={16} strokeWidth={2.2} />
              </span>
              <div>
                <h3 className="ops-filter-panel__title">Filters</h3>
                <p className="ops-filter-panel__subtitle">
                  Filter interviews by candidate, attendee, level, profile or sort order.
                </p>
              </div>
            </div>
            <button
              type="button"
              className="ops-filter-panel__clear-btn"
              onClick={handleClearAll}
              aria-label="Clear all filter fields"
            >
              Clear all
            </button>
          </div>

          {/* Form Fields */}
          <form className="ops-filter-panel__body" onSubmit={handleApply}>
            {/* 1. Candidate */}
            <div className="ops-filter-field">
              <label htmlFor="filter-candidate" className="ops-filter-field__label">
                Candidate
              </label>
              <div className="ops-filter-field__select-wrap">
                <select
                  id="filter-candidate"
                  className="ops-filter-field__select"
                  value={draftCandidate}
                  onChange={e => setDraftCandidate(e.target.value)}
                  aria-label="Candidate filter"
                >
                  <option value="">All candidates</option>
                  {candidateOptions.map(item => (
                    <option key={item.name} value={item.name}>
                      {item.name} {item.scheduled ? `(${item.scheduled})` : ''}
                    </option>
                  ))}
                </select>
                <span className="ops-filter-field__arrow" aria-hidden="true">
                  <Icon name="chevron-down" size={12} strokeWidth={2.2} />
                </span>
              </div>
            </div>

            {/* 2. Attendee */}
            <div className="ops-filter-field">
              <label htmlFor="filter-attendee" className="ops-filter-field__label">
                Attendee
              </label>
              <div className="ops-filter-field__select-wrap">
                <select
                  id="filter-attendee"
                  className="ops-filter-field__select"
                  value={draftAttendee}
                  onChange={e => setDraftAttendee(e.target.value)}
                  aria-label="Attendee filter"
                >
                  <option value="">Everyone</option>
                  {attendeeOptions.map(name => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
                <span className="ops-filter-field__arrow" aria-hidden="true">
                  <Icon name="chevron-down" size={12} strokeWidth={2.2} />
                </span>
              </div>
            </div>

            {/* 3. Level */}
            <div className="ops-filter-field">
              <label htmlFor="filter-level" className="ops-filter-field__label">
                Level
              </label>
              <div className="ops-filter-field__select-wrap">
                <select
                  id="filter-level"
                  className="ops-filter-field__select"
                  value={draftRound}
                  onChange={e => setDraftRound(e.target.value)}
                  aria-label="Candidate interview level filter"
                >
                  <option value="">All levels</option>
                  {roundOptions.map(r => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
                <span className="ops-filter-field__arrow" aria-hidden="true">
                  <Icon name="chevron-down" size={12} strokeWidth={2.2} />
                </span>
              </div>
            </div>

            {/* 4. Profile / Technology */}
            <div className="ops-filter-field">
              <label htmlFor="filter-technology" className="ops-filter-field__label">
                Profile / Technology
              </label>
              <div className="ops-filter-field__select-wrap">
                <select
                  id="filter-technology"
                  className="ops-filter-field__select"
                  value={draftTechnology}
                  onChange={e => setDraftTechnology(e.target.value)}
                  aria-label="Technology filter"
                >
                  <option value="">All profiles</option>
                  {technologyOptions.map(t => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
                <span className="ops-filter-field__arrow" aria-hidden="true">
                  <Icon name="chevron-down" size={12} strokeWidth={2.2} />
                </span>
              </div>
            </div>

            {/* 5. Sort by */}
            <div className="ops-filter-field">
              <label htmlFor="filter-sort" className="ops-filter-field__label">
                Sort by
              </label>
              <div className="ops-filter-field__select-wrap">
                <select
                  id="filter-sort"
                  className="ops-filter-field__select"
                  value={draftSort}
                  onChange={e => setDraftSort(e.target.value)}
                  aria-label="Sort by"
                >
                  {ROSTER_SORT_OPTIONS.map(option => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <span className="ops-filter-field__arrow" aria-hidden="true">
                  <Icon name="chevron-down" size={12} strokeWidth={2.2} />
                </span>
              </div>
            </div>

            {/* Bottom Actions */}
            <div className="ops-filter-panel__footer">
              <button
                type="button"
                className="ops-filter-panel__btn ops-filter-panel__btn--reset"
                onClick={handleReset}
              >
                Reset
              </button>
              <button
                type="submit"
                className="ops-filter-panel__btn ops-filter-panel__btn--apply"
              >
                Apply{draftCount > 0 ? ` (${draftCount})` : ''}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
