import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Icon } from '../components/ui/Icon.jsx'
import { DEFAULT_ROSTER_SORT, ROSTER_SORT_OPTIONS, rosterSortLabel } from './rosterSort.js'

/**
 * Filters for Daily Ops / Interview roster (multi-select since Nov 2026).
 *
 * - Trigger: [icon] Filters (n) [summary] [chevron]; n counts every chosen value.
 * - Panel: a floating panel (a bottom sheet on phones) with one checkbox list
 *   per field (Candidate, searchable, and Attendee) and Sort by. Profiles and
 *   rounds are filtered by the Profile + Round control beside it (15 Nov).
 * - Matching: values inside one field are alternatives (Bhavana OR Nikhila);
 *   different fields must all match (that level AND that attendee).
 * - Nothing changes until Apply. "Clear all" empties the panel's choices
 *   (still needs Apply); Reset clears and applies straight away.
 */

/** A filter value as a clean list: older callers pass one string. */
export function toFilterList(value) {
  if (Array.isArray(value)) return value.filter(v => typeof v === 'string' && v.trim() !== '')
  if (typeof value === 'string' && value.trim() !== '') return [value]
  return []
}

/** "HR, L2 +1" -- a short, readable form of a field's choices. */
export function summariseValues(values, max = 2) {
  const list = toFilterList(values)
  if (list.length <= max) return list.join(', ')
  return `${list.slice(0, max).join(', ')} +${list.length - max}`
}

// Profile and round are filtered by the Profile + Round control, not here.
const FIELD_KEYS = ['candidate', 'attendee']

function countValues(state) {
  return FIELD_KEYS.reduce((sum, key) => sum + state[key].length, 0)
    + (state.sort !== DEFAULT_ROSTER_SORT ? 1 : 0)
}

function MultiSelectField({
  id,
  label,
  groupLabel,
  options,
  selected,
  onChange,
  searchable = false,
  searchPlaceholder = 'Search...',
  layout = 'chips',
  anyLabel,
}) {
  const [query, setQuery] = useState('')
  const selectedSet = useMemo(() => new Set(selected), [selected])
  // In a long scrolling list, what was already chosen when the panel opened
  // comes first, so a ticked name is never hidden below the fold. Taken once
  // per opening: moving a row as it is ticked would jump under the pointer.
  const [pinned] = useState(() => (layout === 'list' ? selected : []))
  // A chosen value stays on the list even when the period no longer has it,
  // so it can still be seen and unticked.
  const allOptions = useMemo(() => {
    const known = new Set(options.map(o => o.value))
    const extra = selected.filter(v => !known.has(v)).map(v => ({ value: v, label: v }))
    const list = [...extra, ...options]
    if (!pinned.length) return list
    const first = new Set(pinned)
    return [...list.filter(o => first.has(o.value)), ...list.filter(o => !first.has(o.value))]
  }, [options, selected, pinned])
  const needle = query.trim().toLowerCase()
  const shown = needle
    ? allOptions.filter(o => o.label.toLowerCase().includes(needle))
    : allOptions

  function toggle(value) {
    onChange(selectedSet.has(value) ? selected.filter(v => v !== value) : [...selected, value])
  }

  return (
    <div className="ops-filter-field" role="group" aria-labelledby={`${id}-label`}>
      <div className="ops-filter-field__head">
        <span id={`${id}-label`} className="ops-filter-field__label">{label}</span>
        <span className="ops-filter-field__state" aria-live="polite">
          {selected.length ? `${selected.length} selected` : anyLabel}
        </span>
        {selected.length > 0 && (
          <button
            type="button"
            className="ops-filter-field__clear"
            onClick={() => onChange([])}
            aria-label={`Clear ${label} filter`}
          >
            Clear
          </button>
        )}
      </div>
      {searchable && (
        <label className="ops-filter-field__search">
          <span className="ops-filter-field__search-icon" aria-hidden="true">
            <Icon name="search" size={13} />
          </span>
          <input
            type="search"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={searchPlaceholder}
            aria-label={`Search ${label.toLowerCase()} options`}
          />
        </label>
      )}
      <div
        className={`ops-filter-options ops-filter-options--${layout}`}
        role="group"
        aria-label={groupLabel}
      >
        {shown.length === 0 && (
          <p className="ops-filter-options__empty">
            {allOptions.length ? 'No match' : 'None in this period'}
          </p>
        )}
        {shown.map(option => {
          const checked = selectedSet.has(option.value)
          return (
            <label
              key={option.value}
              className={`ops-filter-option${checked ? ' ops-filter-option--checked' : ''}`}
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggle(option.value)}
                aria-label={option.label}
              />
              <span className="ops-filter-option__box" aria-hidden="true">
                {checked && <Icon name="check" size={11} strokeWidth={3} />}
              </span>
              <span className="ops-filter-option__label">{option.label}</span>
              {option.hint != null && option.hint !== '' && (
                <span className="ops-filter-option__hint" aria-hidden="true">{option.hint}</span>
              )}
            </label>
          )
        })}
      </div>
    </div>
  )
}

export function DailyOpsFilterMenu({
  // Applied values: a list per field (a single string is still accepted)
  candidateFilter = [],
  attendeeFilter = [],
  sortOrder = DEFAULT_ROSTER_SORT,
  // Options
  candidateOptions = [],
  attendeeOptions = ['Nikhila', 'Bhavana', 'Tool'],
  // Called with { candidate, attendee } as lists, and sort
  onApply,
}) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef(null)
  const panelRef = useRef(null)
  const panelId = useId()

  // Keyed on the values, not the arrays: a caller's inline or default `[]` is
  // a new array every render, and the draft-sync effect below would loop.
  const appliedKey = JSON.stringify([
    toFilterList(candidateFilter),
    toFilterList(attendeeFilter),
    sortOrder || DEFAULT_ROSTER_SORT,
  ])
  const applied = useMemo(() => {
    const [candidate, attendee, sort] = JSON.parse(appliedKey)
    return { candidate, attendee, sort }
  }, [appliedKey])

  const [draft, setDraft] = useState(applied)
  const setDraftField = key => value => setDraft(prev => ({ ...prev, [key]: value }))

  // The panel opens on what is applied; unapplied ticks are dropped on close.
  useEffect(() => {
    if (open) setDraft(applied)
  }, [open, applied])

  const appliedCount = countValues(applied)
  const draftCount = countValues(draft)

  const summaryParts = [
    applied.candidate.length && summariseValues(applied.candidate, 1),
    applied.attendee.length && summariseValues(applied.attendee),
  ].filter(Boolean)
  const summary = summaryParts.join(' · ')
  const fullSummary = [
    applied.candidate.length && `Candidate: ${applied.candidate.join(', ')}`,
    applied.attendee.length && `Attendee: ${applied.attendee.join(', ')}`,
    applied.sort !== DEFAULT_ROSTER_SORT && `Sort: ${rosterSortLabel(applied.sort)}`,
  ].filter(Boolean).join('\n')

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

  useEffect(() => {
    if (open) panelRef.current?.querySelector('input, select, button')?.focus()
  }, [open])

  // Where the trigger sits moves with the width (Filters wraps under the
  // search between 768 and 1100px), so the panel is kept inside the viewport
  // here rather than by one fixed alignment: right-aligned to the button ran
  // 29px off the left edge at 900px. Its height is capped to the room below
  // the button, so Apply never ends up under the fold. The phone bottom sheet
  // (position: fixed) is laid out by CSS alone.
  useLayoutEffect(() => {
    if (!open) return undefined
    const panel = panelRef.current
    if (!panel || typeof window === 'undefined') return undefined
    function place() {
      panel.style.left = ''
      panel.style.right = ''
      panel.style.maxHeight = ''
      if (window.getComputedStyle(panel).position === 'fixed') return
      const margin = 8
      const viewport = document.documentElement.clientWidth || window.innerWidth
      const rect = panel.getBoundingClientRect()
      if (!rect.width) return
      const parent = panel.offsetParent?.getBoundingClientRect() || { left: 0, right: viewport }
      if (rect.left < margin) {
        panel.style.right = 'auto'
        panel.style.left = `${Math.round(margin - parent.left)}px`
      } else if (rect.right > viewport - margin) {
        panel.style.left = 'auto'
        panel.style.right = `${Math.round(parent.right - (viewport - margin))}px`
      }
      const room = window.innerHeight - rect.top - 12
      panel.style.maxHeight = `${Math.max(320, Math.round(room))}px`
    }
    place()
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [open])

  function emit(state) {
    onApply?.({
      candidate: state.candidate,
      attendee: state.attendee,
      sort: state.sort,
    })
    setOpen(false)
    triggerRef.current?.focus()
  }

  function handleClearAll() {
    setDraft({ candidate: [], attendee: [], sort: DEFAULT_ROSTER_SORT })
  }

  function handleReset() {
    emit({ candidate: [], attendee: [], sort: DEFAULT_ROSTER_SORT })
  }

  function handleApply(e) {
    e?.preventDefault()
    emit(draft)
  }

  const candidateList = candidateOptions.map(item => ({
    value: item.name,
    label: item.name,
    hint: item.scheduled ? String(item.scheduled) : '',
  }))
  const simple = list => list.map(v => ({ value: v, label: v }))

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
        title={fullSummary || 'Filter interviews'}
      >
        <span className="ops-filter-trigger__icon" aria-hidden="true">
          <Icon name="filter" size={14} strokeWidth={2.2} />
        </span>
        <span className="ops-filter-trigger__label">Filters</span>
        {appliedCount > 0 && (
          <span className="ops-filter-trigger__badge">({appliedCount})</span>
        )}
        {summary && (
          <span className="ops-filter-trigger__summary" aria-hidden="true">{summary}</span>
        )}
        <span className="ops-filter-trigger__chevron" aria-hidden="true">
          <Icon name="chevron-down" size={13} strokeWidth={2.2} />
        </span>
      </button>

      {open && (
        <>
          <div className="ops-filter-backdrop" aria-hidden="true" onClick={() => setOpen(false)} />
          <div
            ref={panelRef}
            id={panelId}
            className="ops-filter-panel"
            role="dialog"
            aria-modal="false"
            aria-label="Filter interviews"
          >
            <div className="ops-filter-panel__header">
              <div className="ops-filter-panel__title-wrap">
                <span className="ops-filter-panel__title-icon" aria-hidden="true">
                  <Icon name="filter" size={16} strokeWidth={2.2} />
                </span>
                <div>
                  <h3 className="ops-filter-panel__title">Filters</h3>
                  <p className="ops-filter-panel__subtitle">
                    Tick several values in a field to match any of them. Different fields must all match.
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

            <form className="ops-filter-panel__body" onSubmit={handleApply}>
              <div className="ops-filter-panel__fields">
                <MultiSelectField
                  id={`${panelId}-candidate`}
                  label="Candidate"
                  groupLabel="Candidate filter"
                  anyLabel="All candidates"
                  options={candidateList}
                  selected={draft.candidate}
                  onChange={setDraftField('candidate')}
                  searchable
                  searchPlaceholder="Search candidates..."
                  layout="list"
                />
                <MultiSelectField
                  id={`${panelId}-attendee`}
                  label="Attendee"
                  groupLabel="Attendee filter"
                  anyLabel="Everyone"
                  options={simple(attendeeOptions)}
                  selected={draft.attendee}
                  onChange={setDraftField('attendee')}
                />
                <div className="ops-filter-field">
                  <label htmlFor={`${panelId}-sort`} className="ops-filter-field__label">
                    Sort by
                  </label>
                  <div className="ops-filter-field__select-wrap">
                    <select
                      id={`${panelId}-sort`}
                      className="ops-filter-field__select"
                      value={draft.sort}
                      onChange={e => setDraftField('sort')(e.target.value)}
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
              </div>

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
        </>
      )}
    </div>
  )
}
