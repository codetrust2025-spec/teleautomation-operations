import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { formatIstCalendarDay } from '../utils/istTime.js'
import { Icon } from '../components/ui/Icon.jsx'
import {
  CALENDAR_PRESETS,
  MONTH_LABELS,
  WEEKDAY_LABELS,
  addDaysIso,
  addMonthsIso,
  isValidIsoDay,
  monthGrid,
  monthKeyOf,
  parseMonthKey,
  resolveCalendarPreset,
  todayIso,
  toIsoDay,
} from './calendarDates.js'

const POPOVER_WIDTH = 280
const POPOVER_HEIGHT = 460

/** Today / Yesterday / Tomorrow, so the common picks need no counting. */
function relativeDayLabel(iso, today) {
  if (!iso) return ''
  if (iso === today) return 'Today'
  if (iso === addDaysIso(today, -1)) return 'Yesterday'
  if (iso === addDaysIso(today, 1)) return 'Tomorrow'
  return ''
}

/** Where the popover can sit without leaving the viewport. */
function popoverPosition(rect) {
  const room = window.innerHeight - rect.bottom
  const top = room >= POPOVER_HEIGHT + 12 || rect.top < POPOVER_HEIGHT + 12
    ? Math.min(rect.bottom + 6, Math.max(8, window.innerHeight - POPOVER_HEIGHT - 8))
    : rect.top - POPOVER_HEIGHT - 6
  const left = Math.max(8, Math.min(rect.left, window.innerWidth - POPOVER_WIDTH - 8))
  return { top: Math.max(8, top), left }
}

/**
 * The Daily Ops date selector with presets (This Week, This Month, This Year)
 * and full calendar grid for manual day selection.
 * Defaults to all upcoming interviews when cleared or unselected.
 */
export function DateCalendarPicker({
  value = '',
  preset = '',
  placeholder = 'Upcoming',
  onSelectDate,
  onSelectPreset,
  onClear,
  label = 'Filter interviews by date',
}) {
  const today = todayIso()
  const selected = isValidIsoDay(value) ? value : ''
  const isUpcoming = !selected && (!preset || preset === 'upcoming')
  const currentPreset = isUpcoming
    ? CALENDAR_PRESETS.find(p => p.id === 'upcoming')
    : CALENDAR_PRESETS.find(p => p.id === preset)
  const isSet = Boolean((preset && preset !== 'upcoming') || selected)

  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState(null)
  const [view, setView] = useState(() => parseMonthKey(monthKeyOf(selected)) || parseMonthKey(monthKeyOf(today)))
  const [focusedIso, setFocusedIso] = useState(selected || today)
  const triggerRef = useRef(null)
  const popoverRef = useRef(null)
  const gridRef = useRef(null)
  const shouldFocusDay = useRef(false)

  const openPicker = useCallback(() => {
    const anchor = selected || today
    setView(parseMonthKey(monthKeyOf(anchor)) || parseMonthKey(monthKeyOf(today)))
    setFocusedIso(selected || today)
    const rect = triggerRef.current?.getBoundingClientRect()
    setPosition(rect ? popoverPosition(rect) : { top: 80, left: 24 })
    shouldFocusDay.current = true
    setOpen(true)
  }, [selected, today])

  const closePicker = useCallback(({ restoreFocus = true } = {}) => {
    setOpen(false)
    if (restoreFocus) triggerRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!open) return undefined
    function onPointerDown(event) {
      if (triggerRef.current?.contains(event.target)) return
      if (popoverRef.current?.contains(event.target)) return
      closePicker({ restoreFocus: false })
    }
    function onKeyDown(event) {
      if (event.key === 'Escape') { event.stopPropagation(); closePicker() }
    }
    function reposition() {
      const rect = triggerRef.current?.getBoundingClientRect()
      if (rect) setPosition(popoverPosition(rect))
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
    }
  }, [open, closePicker])

  useLayoutEffect(() => {
    if (!open || !shouldFocusDay.current) return
    shouldFocusDay.current = false
    gridRef.current?.querySelector('[data-focused="true"]')?.focus()
  }, [open, focusedIso])

  const weeks = useMemo(() => monthGrid(view.year, view.monthIndex), [view])

  const years = useMemo(() => {
    const known = [today, selected, toIsoDay(view.year, view.monthIndex, 1)]
      .map(iso => parseMonthKey(monthKeyOf(iso))?.year)
      .filter(Boolean)
    const currentYear = Number(today.slice(0, 4))
    const first = Math.min(currentYear - 5, ...known)
    const last = Math.max(currentYear + 5, ...known)
    return Array.from({ length: last - first + 1 }, (unused, index) => first + index)
  }, [selected, today, view])

  function pickDate(iso) {
    if (!isValidIsoDay(iso)) return
    onSelectDate?.(iso)
    closePicker()
  }

  function pickPreset(presetId) {
    onSelectPreset?.(presetId)
    closePicker()
  }

  function stepView(months) {
    const next = parseMonthKey(monthKeyOf(addMonthsIso(toIsoDay(view.year, view.monthIndex, 1), months)))
    if (next) setView(next)
  }

  function moveFocus(days) {
    const next = addDaysIso(focusedIso, days)
    if (!next) return
    setFocusedIso(next)
    const target = parseMonthKey(monthKeyOf(next))
    if (target && (target.year !== view.year || target.monthIndex !== view.monthIndex)) setView(target)
    shouldFocusDay.current = true
  }

  function onGridKeyDown(event) {
    const steps = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }
    if (steps[event.key] !== undefined) { event.preventDefault(); moveFocus(steps[event.key]); return }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      const index = weeks.flat().findIndex(cell => cell.iso === focusedIso)
      if (index < 0) return
      const weekday = index % 7
      moveFocus(event.key === 'Home' ? -weekday : 6 - weekday)
      return
    }
    if (event.key === 'PageUp' || event.key === 'PageDown') {
      event.preventDefault()
      const next = addMonthsIso(focusedIso, event.key === 'PageUp' ? -1 : 1)
      if (!next) return
      setFocusedIso(next)
      setView(parseMonthKey(monthKeyOf(next)) || view)
      shouldFocusDay.current = true
    }
  }

  let triggerText = placeholder
  let triggerTitle = 'All upcoming interviews'

  if (currentPreset && currentPreset.id !== 'upcoming') {
    triggerText = currentPreset.label
    const resolved = resolveCalendarPreset(currentPreset.id, today)
    triggerTitle = resolved
      ? (resolved.from === resolved.to
          ? `${currentPreset.label} (${formatIstCalendarDay(resolved.from)})`
          : `${currentPreset.label} (${formatIstCalendarDay(resolved.from)} – ${formatIstCalendarDay(resolved.to)})`)
      : currentPreset.label
  } else if (selected) {
    const relative = relativeDayLabel(selected, today)
    triggerText = formatIstCalendarDay(selected)
    triggerTitle = relative ? `${triggerText} · ${relative}` : triggerText
  } else if (isUpcoming) {
    triggerText = 'Upcoming'
    triggerTitle = 'All upcoming interviews'
  }

  const popover = open && position && createPortal(
    <div
      ref={popoverRef}
      className="ops-datepicker__popover"
      style={{ top: position.top, left: position.left }}
      role="dialog"
      aria-modal="false"
      aria-label="Choose a date"
    >
      {/* Calendar Presets: This Week, This Month, This Year */}
      <div className="ops-datepicker__presets" role="group" aria-label="Calendar presets">
        {CALENDAR_PRESETS.map(p => {
          const isActive = p.id === 'upcoming' ? isUpcoming : preset === p.id
          return (
            <button
              key={p.id}
              type="button"
              className={`ops-datepicker__preset${isActive ? ' ops-datepicker__preset--active' : ''}`}
              aria-pressed={isActive}
              onClick={() => pickPreset(p.id)}
            >
              {p.label}
            </button>
          )
        })}
      </div>

      <div className="ops-datepicker__nav">
        <button type="button" className="ops-datepicker__step" onClick={() => stepView(-1)} aria-label="Previous month">&#8249;</button>
        <select
          className="ops-datepicker__select"
          value={view.monthIndex}
          onChange={event => setView({ ...view, monthIndex: Number(event.target.value) })}
          aria-label="Month"
        >
          {MONTH_LABELS.map((month, index) => <option key={month} value={index}>{month}</option>)}
        </select>
        <select
          className="ops-datepicker__select ops-datepicker__select--year"
          value={view.year}
          onChange={event => setView({ ...view, year: Number(event.target.value) })}
          aria-label="Year"
        >
          {years.map(year => <option key={year} value={year}>{year}</option>)}
        </select>
        <button type="button" className="ops-datepicker__step" onClick={() => stepView(1)} aria-label="Next month">&#8250;</button>
      </div>

      <div className="ops-datepicker__weekdays" aria-hidden="true">
        {WEEKDAY_LABELS.map(day => <span key={day}>{day}</span>)}
      </div>

      <div className="ops-datepicker__grid" role="grid" ref={gridRef} onKeyDown={onGridKeyDown}>
        {weeks.map(week => (
          <div className="ops-datepicker__week" role="row" key={week[0].iso}>
            {week.map(cell => {
              const isSelected = cell.iso === selected
              const isFocused = cell.iso === focusedIso
              return (
                <div role="gridcell" key={cell.iso} className="ops-datepicker__cell">
                  <button
                    type="button"
                    data-focused={isFocused ? 'true' : 'false'}
                    tabIndex={isFocused ? 0 : -1}
                    className={[
                      'ops-datepicker__day',
                      cell.inMonth ? '' : 'ops-datepicker__day--muted',
                      cell.iso === today ? 'ops-datepicker__day--today' : '',
                      isSelected ? 'ops-datepicker__day--selected' : '',
                    ].filter(Boolean).join(' ')}
                    aria-label={formatIstCalendarDay(cell.iso, { weekday: 'long', month: 'long' })}
                    aria-pressed={isSelected}
                    aria-current={cell.iso === today ? 'date' : undefined}
                    onFocus={() => setFocusedIso(cell.iso)}
                    onClick={() => pickDate(cell.iso)}
                  >
                    {cell.day}
                  </button>
                </div>
              )
            })}
          </div>
        ))}
      </div>

      <div className="ops-datepicker__footer">
        <button
          type="button"
          className="ops-datepicker__link ops-datepicker__link--clear"
          onClick={() => {
            onClear?.()
            closePicker()
          }}
        >
          Clear
        </button>
      </div>
    </div>,
    document.body,
  )

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`cand-input ops-datepicker__trigger${open ? ' ops-datepicker__trigger--open' : ''}${isSet ? ' ops-datepicker__trigger--set' : ''}`}
        onClick={() => (open ? closePicker() : openPicker())}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={label}
        title={triggerTitle}
      >
        <span className="ops-datepicker__icon" aria-hidden="true">
          <Icon name="calendar" size={14} strokeWidth={2.2} />
        </span>
        <span className="ops-datepicker__value">{triggerText}</span>
        <span className="ops-datepicker__caret" aria-hidden="true">
          <Icon name="chevron-down" size={13} strokeWidth={2.2} />
        </span>
      </button>
      {popover}
    </>
  )
}

export default DateCalendarPicker
