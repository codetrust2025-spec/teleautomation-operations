import React, { useEffect, useId, useRef, useState } from 'react'
import { Icon } from '../components/ui/Icon.jsx'
import { toFilterList } from './DailyOpsFilterMenu.jsx'

/** Every level the backend accepts (VALID_INTERVIEW_ROUNDS), in interview order. */
export const INTERVIEW_LEVELS = ['Screening', 'L1', 'L2', 'L3', 'HR', 'Final']

/**
 * Interview Levels: a filter of its own beside Filters (Nov 2026).
 *
 * Each tick applies at once -- there is no Apply button. Several levels are
 * alternatives (HR or L2 or L3); together with the Filters panel's fields they
 * must all match. It shares one selection with the panel's Level field, so the
 * two never disagree: ticking here shows there, and the reverse.
 */
export function InterviewLevelsFilter({ value = [], onChange, levels = INTERVIEW_LEVELS }) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)
  const triggerRef = useRef(null)
  const menuId = useId()
  const selected = toFilterList(value)
  const chosen = new Set(selected)
  const count = selected.length

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
    if (open) wrapRef.current?.querySelector('.ops-levels-menu input')?.focus()
  }, [open])

  function toggle(level) {
    // Kept in interview order, whatever order they were ticked in.
    const next = chosen.has(level)
      ? selected.filter(v => v !== level)
      : levels.filter(v => v === level || chosen.has(v))
    onChange?.(next)
  }

  return (
    <div className="ops-levels-filter" ref={wrapRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`ops-levels-trigger${open ? ' ops-levels-trigger--open' : ''}${count ? ' ops-levels-trigger--active' : ''}`}
        onClick={() => setOpen(prev => !prev)}
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={count ? `Interview levels (${count} selected: ${selected.join(', ')})` : 'Interview levels'}
        title={count ? `Interview levels: ${selected.join(', ')}` : 'Filter by interview level'}
      >
        <Icon name="list" size={14} strokeWidth={2.1} />
        <span className="ops-levels-trigger__label">Levels</span>
        {count > 0 && (
          <span className="ops-levels-trigger__badge" aria-hidden="true">({count})</span>
        )}
        <span className="ops-levels-trigger__chevron" aria-hidden="true">
          <Icon name="chevron-down" size={13} strokeWidth={2.2} />
        </span>
      </button>

      {open && (
        <>
        {/* Phones only (CSS): the menu is a bottom sheet over a dimmed page. */}
        <div className="ops-levels-backdrop" aria-hidden="true" onClick={() => setOpen(false)} />
        <div className="ops-levels-menu" id={menuId}>
          <div className="ops-levels-menu__head">
            <span className="ops-levels-menu__title">Interview Levels</span>
            <button
              type="button"
              className="ops-levels-menu__clear"
              onClick={() => onChange?.([])}
              disabled={!count}
              aria-label="Show all interview levels"
            >
              All levels
            </button>
          </div>
          <div className="ops-levels-menu__list" role="group" aria-label="Interview levels">
            {levels.map(level => {
              const checked = chosen.has(level)
              return (
                <label key={level} className={`ops-levels-option${checked ? ' ops-levels-option--checked' : ''}`}>
                  <input type="checkbox" checked={checked} onChange={() => toggle(level)} aria-label={level} />
                  <span className="ops-levels-option__box" aria-hidden="true">
                    {checked && <Icon name="check" size={11} strokeWidth={3} />}
                  </span>
                  <span>{level}</span>
                </label>
              )
            })}
          </div>
          <p className="ops-levels-menu__hint">Applies as you tick. Several levels match any of them.</p>
        </div>
        </>
      )}
    </div>
  )
}
