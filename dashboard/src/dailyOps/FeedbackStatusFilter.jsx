import React, { useEffect, useId, useRef, useState } from 'react'
import { Icon } from '../components/ui/Icon.jsx'
import { toFilterList } from './DailyOpsFilterMenu.jsx'
import { FEEDBACK_FILTER_VALUES, feedbackMeta } from './feedbackScale.js'

/**
 * Status filter (owner's mockup, 15 Nov): the interview feedback the Status
 * column shows -- Good, Excellent, Average, Needs Improvement, Negative --
 * several at once (any of them), each with how many interviews in the current
 * view carry it. Every click applies at once; there is no Apply button.
 *
 * The counts are over the rows loaded for the period and the other filters,
 * before this one, so ticking Good does not turn Excellent's count to 0.
 * A stored value off the scale (the legacy "positive") is listed after the
 * five while any row in view carries it, so no row is unreachable.
 */
export function FeedbackStatusFilter({ selected = [], counts = {}, onChange }) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)
  const triggerRef = useRef(null)
  const menuId = useId()

  const chosen = toFilterList(selected)

  useEffect(() => {
    if (!open) return undefined
    function onPointerDown(e) {
      if (wrapRef.current?.contains(e.target)) return
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
    if (open) wrapRef.current?.querySelector('.ops-feedback-filter-menu input')?.focus()
  }, [open])

  const describe = chosen.length
    ? `Status: ${chosen.map(v => feedbackMeta(v).label).join(', ')}`
    : 'Filter by interview status (feedback)'

  return (
    <div className="ops-levels-filter ops-feedback-filter" ref={wrapRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`ops-levels-trigger${open ? ' ops-levels-trigger--open' : ''}${chosen.length ? ' ops-levels-trigger--active' : ''}`}
        onClick={() => setOpen(prev => !prev)}
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={chosen.length ? `Status filter (${chosen.length} selected)` : 'Status filter'}
        title={describe}
      >
        <Icon name="bar-chart" size={14} strokeWidth={2.1} />
        <span className="ops-levels-trigger__label">Status</span>
        {chosen.length > 0 && <span className="ops-levels-trigger__badge" aria-hidden="true">({chosen.length})</span>}
        <span className="ops-levels-trigger__chevron" aria-hidden="true">
          <Icon name="chevron-down" size={13} strokeWidth={2.2} />
        </span>
      </button>

      {open && (
        <>
          {/* Phones only (CSS): the menu is a bottom sheet over a dimmed page. */}
          <div className="ops-levels-backdrop" aria-hidden="true" onClick={() => setOpen(false)} />
          <div className="ops-levels-menu ops-feedback-filter-menu" id={menuId}>
            <div className="ops-levels-menu__head">
              <span className="ops-levels-menu__title">Status</span>
            </div>
            <FeedbackStatusOptions selected={chosen} counts={counts} onChange={onChange} />
          </div>
        </>
      )}
    </div>
  )
}

/** The options a Status list offers: the five, then any off-scale value rows carry or that is ticked. */
export function feedbackFilterOptions(selected, counts) {
  const chosen = toFilterList(selected)
  const extras = Object.keys(counts || {})
    .filter(value => !FEEDBACK_FILTER_VALUES.includes(value) && counts[value] > 0)
    .sort()
  // A ticked value stays listed even if the period no longer has it.
  const stale = chosen.filter(value => !FEEDBACK_FILTER_VALUES.includes(value) && !extras.includes(value))
  return [...FEEDBACK_FILTER_VALUES, ...extras, ...stale].map(value => feedbackMeta(value))
}

/**
 * The Status checkboxes with their badges and counts. The Status menu shows
 * them on wider screens; on a phone they are a section of Profile + Round.
 */
export function FeedbackStatusOptions({ selected = [], counts = {}, onChange }) {
  const chosen = toFilterList(selected)
  const chosenSet = new Set(chosen)
  const options = feedbackFilterOptions(chosen, counts)

  function toggle(value) {
    // Kept in the menu's order, whatever order they were ticked in.
    const order = options.map(o => o.value)
    onChange?.(chosenSet.has(value)
      ? chosen.filter(v => v !== value)
      : order.filter(v => v === value || chosenSet.has(v)))
  }

  return (
    <div className="ops-feedback-filter-list" role="group" aria-label="Interview status">
      {options.map(option => {
        const checked = chosenSet.has(option.value)
        const count = counts[option.value] || 0
        return (
          <label
            key={option.value}
            className={`ops-feedback-filter-option${checked ? ' ops-feedback-filter-option--checked' : ''}`}
          >
            <input
              type="checkbox"
              checked={checked}
              onChange={() => toggle(option.value)}
              aria-label={`${option.label} (${count})`}
            />
            <span className="ops-levels-option__box" aria-hidden="true">
              {checked && <Icon name="check" size={11} strokeWidth={3} />}
            </span>
            <span className={`ops-feedback-pill ops-feedback-pill--${option.tone} ops-feedback-filter-pill`} data-feedback={option.value}>
              {option.label}
            </span>
            <span className="ops-feedback-filter-count" aria-hidden="true">({count})</span>
          </label>
        )
      })}
    </div>
  )
}
