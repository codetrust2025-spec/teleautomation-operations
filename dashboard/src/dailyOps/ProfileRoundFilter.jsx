import React, { useEffect, useId, useRef, useState } from 'react'
import { Icon } from '../components/ui/Icon.jsx'
import { toFilterList } from './DailyOpsFilterMenu.jsx'
import { FeedbackStatusOptions } from './FeedbackStatusFilter.jsx'
import { feedbackMeta } from './feedbackScale.js'

/** The six standard rounds (STANDARD_INTERVIEW_ROUNDS), in interview order. */
export const INTERVIEW_LEVELS = ['Screening', 'L1', 'L2', 'L3', 'HR', 'Final']

/** The server's token for every other booking (owner, 15 Nov): no round
 *  recorded, Assessment, or any label that is not one of the six. */
export const ROUND_OTHER = 'other'
export const ROUND_FILTER_OPTIONS = [...INTERVIEW_LEVELS, ROUND_OTHER]
/** What a round filter value is called on screen. */
export function roundFilterLabel(value) {
  return value === ROUND_OTHER ? 'Others' : value
}

/**
 * The two booking types, as the server's `channel` parameter names them:
 * profile service (most bookings) and round-wise service. Both ticked is the
 * whole roster.
 */
export const BOOKING_TYPES = [
  { id: 'profile', label: 'Profile-wise' },
  { id: 'round_wise', label: 'Round-wise' },
]
export const ALL_BOOKING_TYPES = BOOKING_TYPES.map(t => t.id)

/** The `channel` to send for a set of ticked booking types: one type, or none for both. */
export function channelFor(types) {
  const list = toFilterList(types).filter(t => ALL_BOOKING_TYPES.includes(t))
  return list.length === 1 ? list[0] : ''
}

/**
 * Profile + Round (redesigned 15 Nov to the owner's mockup).
 *
 * - Profile-wise and Round-wise are independent checkboxes for the booking
 *   type. Both ticked shows both types; one ticked shows that type alone. At
 *   least one stays ticked: the last one cannot be unticked.
 * - Interview Round: Screening, L1, L2, L3, HR and Final, then Others (round
 *   not specified, Assessment, any other round), several at once
 *   (any of them). "All Rounds" unticks them all.
 * - Booking type and rounds must both match. Every click applies at once.
 * - "Clear All" ticks both types again and unticks every round.
 * - `status` (phones, owner's iPhone request 16 Nov): the Status filter is a
 *   section of this menu instead of a button of its own -- the same options,
 *   counts and instant clicks. Clear All clears it too.
 */
export function ProfileRoundFilter({
  bookingTypes = ALL_BOOKING_TYPES,
  rounds = [],
  roundOptions = ROUND_FILTER_OPTIONS,
  onChange,
  status = null,
}) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)
  const triggerRef = useRef(null)
  const menuId = useId()

  const chosenTypes = ALL_BOOKING_TYPES.filter(t => toFilterList(bookingTypes).includes(t))
  const types = chosenTypes.length ? chosenTypes : ALL_BOOKING_TYPES
  const typeSet = new Set(types)
  const chosenRounds = toFilterList(rounds)
  const roundSet = new Set(chosenRounds)
  const typeNarrowed = types.length === 1
  const chosenStatus = status ? toFilterList(status.selected) : []
  const count = chosenRounds.length + (typeNarrowed ? 1 : 0) + chosenStatus.length

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
    if (open) wrapRef.current?.querySelector('.ops-profile-round-menu input')?.focus()
  }, [open])

  function emit(next) {
    onChange?.({ bookingTypes: types, round: chosenRounds, ...next })
  }

  function toggleType(type) {
    if (typeSet.has(type)) {
      if (types.length === 1) return // at least one stays ticked
      emit({ bookingTypes: types.filter(t => t !== type) })
    } else {
      emit({ bookingTypes: ALL_BOOKING_TYPES.filter(t => t === type || typeSet.has(t)) })
    }
  }

  function toggleRound(round) {
    // Kept in interview order, whatever order they were ticked in.
    emit({
      round: roundSet.has(round)
        ? chosenRounds.filter(r => r !== round)
        : roundOptions.filter(r => r === round || roundSet.has(r)),
    })
  }

  const typeLabel = typeNarrowed ? BOOKING_TYPES.find(t => t.id === types[0]).label : 'Profile-wise and Round-wise'
  const describe = count
    ? [`Bookings: ${typeLabel}`, chosenRounds.length && `Rounds: ${chosenRounds.map(roundFilterLabel).join(', ')}`,
      chosenStatus.length && `Status: ${chosenStatus.map(v => feedbackMeta(v).label).join(', ')}`].filter(Boolean).join('\n')
    : status ? 'Filter by booking type, interview round and status' : 'Filter by booking type and interview round'

  const box = checked => (
    <span className="ops-levels-option__box" aria-hidden="true">
      {checked && <Icon name="check" size={11} strokeWidth={3} />}
    </span>
  )

  return (
    <div className="ops-levels-filter ops-profile-round-filter" ref={wrapRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`ops-levels-trigger${open ? ' ops-levels-trigger--open' : ''}${count ? ' ops-levels-trigger--active' : ''}`}
        onClick={() => setOpen(prev => !prev)}
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={`${status ? 'Profile, round and status' : 'Profile and round'}${count ? ` (${count} selected)` : ''}`}
        title={describe}
      >
        <Icon name="users" size={14} strokeWidth={2.1} />
        <span className="ops-levels-trigger__label">Profile + Round</span>
        {count > 0 && <span className="ops-levels-trigger__badge" aria-hidden="true">({count})</span>}
        <span className="ops-levels-trigger__chevron" aria-hidden="true">
          <Icon name="chevron-down" size={13} strokeWidth={2.2} />
        </span>
      </button>

      {open && (
        <>
          {/* Phones only (CSS): the menu is a bottom sheet over a dimmed page. */}
          <div className="ops-levels-backdrop" aria-hidden="true" onClick={() => setOpen(false)} />
          <div className="ops-levels-menu ops-profile-round-menu" id={menuId}>
            <div className="ops-levels-menu__head">
              <span className="ops-levels-menu__title">Profile + Round</span>
              <button
                type="button"
                className="ops-levels-menu__clear"
                onClick={() => {
                  onChange?.({ bookingTypes: ALL_BOOKING_TYPES, round: [] })
                  if (chosenStatus.length) status.onChange?.([])
                }}
                disabled={!count}
                aria-label={status ? 'Clear all profile, round and status filters' : 'Clear all profile and round filters'}
              >
                Clear All
              </button>
            </div>

            <div className="ops-profile-round-types" role="group" aria-label="Booking type">
              {BOOKING_TYPES.map(t => {
                const checked = typeSet.has(t.id)
                const last = checked && types.length === 1
                return (
                  <label
                    key={t.id}
                    className={`ops-profile-round-type${checked ? ' ops-profile-round-type--checked' : ''}`}
                    title={last ? 'At least one of Profile-wise or Round-wise stays selected' : undefined}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleType(t.id)}
                      aria-label={t.label}
                      aria-disabled={last || undefined}
                    />
                    {box(checked)}
                    <span>{t.label}</span>
                  </label>
                )
              })}
            </div>

            <div className="ops-profile-round-menu__divider" aria-hidden="true" />

            <div className="ops-profile-round-menu__label">
              <span>Interview Round</span>
              <button
                type="button"
                className="ops-levels-menu__clear"
                onClick={() => emit({ round: [] })}
                disabled={!chosenRounds.length}
                aria-label="All rounds"
              >
                All Rounds
              </button>
            </div>
            <div className="ops-levels-menu__list" role="group" aria-label="Interview rounds">
              {roundOptions.map(r => {
                const checked = roundSet.has(r)
                const label = roundFilterLabel(r)
                return (
                  <label
                    key={r}
                    className={`ops-levels-option${checked ? ' ops-levels-option--checked' : ''}`}
                    title={r === ROUND_OTHER ? 'Needs Review (no round), Assessment, and any round other than Screening, L1, L2, L3, HR or Final' : undefined}
                  >
                    <input type="checkbox" checked={checked} onChange={() => toggleRound(r)} aria-label={label} />
                    {box(checked)}
                    <span>{label}</span>
                  </label>
                )
              })}
            </div>

            {status && (
              <>
                <div className="ops-profile-round-menu__divider" aria-hidden="true" />
                <div className="ops-profile-round-menu__label">
                  <span>Status</span>
                  <button
                    type="button"
                    className="ops-levels-menu__clear"
                    onClick={() => status.onChange?.([])}
                    disabled={!chosenStatus.length}
                    aria-label="Clear all status filters"
                  >
                    All Statuses
                  </button>
                </div>
                <FeedbackStatusOptions selected={chosenStatus} counts={status.counts || {}} onChange={status.onChange} />
              </>
            )}

            <p className="ops-levels-menu__hint">Select at least one filter (Profile-wise or Round-wise).</p>
          </div>
        </>
      )}
    </div>
  )
}
