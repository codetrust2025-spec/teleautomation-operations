import React, { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Icon } from '../components/ui/Icon.jsx'
import { toFilterList } from './DailyOpsFilterMenu.jsx'

/** Every round the backend accepts (VALID_INTERVIEW_ROUNDS), in interview order. */
export const INTERVIEW_LEVELS = ['Screening', 'L1', 'L2', 'L3', 'HR', 'Final']

/** The three ways the menu filters, in the order the switch shows them. */
export const PROFILE_ROUND_MODES = [
  { id: 'profile', label: 'Profile-wise' },
  { id: 'round', label: 'Round-wise' },
  { id: 'both', label: 'Both' },
]
export const PROFILE_ROUND_MODE_KEY = 'ta.dailyOps.profileRoundMode.v1'

function readMode() {
  try {
    const stored = window.localStorage.getItem(PROFILE_ROUND_MODE_KEY)
    return PROFILE_ROUND_MODES.some(m => m.id === stored) ? stored : 'both'
  } catch {
    return 'both'
  }
}

function saveMode(mode) {
  try {
    window.localStorage.setItem(PROFILE_ROUND_MODE_KEY, mode)
  } catch {
    // A private window or blocked storage: the mode lasts for this page only.
  }
}

/**
 * Profile + Round: the one Daily Ops filter for profiles and rounds (Nov 2026).
 *
 * Two checkbox lists in one menu -- profiles (technologies) and interview
 * rounds -- ticked in any combination. Each tick applies at once; there is no
 * Apply button. Inside a list the ticks are alternatives (Java or Python);
 * the two lists must both match (Java or Python, AND L2 or HR), like every
 * other Daily Ops filter.
 *
 * The page holds the two selections (technologyFilter, roundFilter); this is
 * the only control that edits them, apart from the page Reset. The separate
 * Levels button and the Filters panel's Level and Profile fields were
 * duplicates and were removed (15 Nov).
 */
export function ProfileRoundFilter({
  profiles = [],
  rounds = [],
  profileOptions = [],
  roundOptions = INTERVIEW_LEVELS,
  onChange,
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const wrapRef = useRef(null)
  const triggerRef = useRef(null)
  const menuId = useId()

  const chosenProfiles = toFilterList(profiles)
  const chosenRounds = toFilterList(rounds)
  const profileSet = new Set(chosenProfiles)
  const roundSet = new Set(chosenRounds)
  const count = chosenProfiles.length + chosenRounds.length

  // Profile-wise shows only profiles, Round-wise only rounds, Both shows both.
  // A list that is hidden never filters: choosing a one-list mode clears the
  // other list at once, and should the hidden list ever hold values anyway
  // (a remembered mode that no longer fits), the menu shows Both.
  const [mode, setMode] = useState(readMode)
  const effectiveMode =
    (mode === 'profile' && chosenRounds.length) || (mode === 'round' && chosenProfiles.length)
      ? 'both'
      : mode
  const showProfiles = effectiveMode !== 'round'
  const showRounds = effectiveMode !== 'profile'

  function chooseMode(next) {
    setMode(next)
    saveMode(next)
    if (next === 'profile' && chosenRounds.length) emit({ round: [] })
    if (next === 'round' && chosenProfiles.length) emit({ technology: [] })
  }

  // A ticked profile stays listed even when the period no longer has it.
  const allProfiles = useMemo(() => {
    const known = new Set(profileOptions)
    return [...chosenProfiles.filter(p => !known.has(p)), ...profileOptions]
  }, [profileOptions.join('\u0001'), chosenProfiles.join('\u0001')])
  const needle = query.trim().toLowerCase()
  const shownProfiles = needle ? allProfiles.filter(p => p.toLowerCase().includes(needle)) : allProfiles

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
    else setQuery('')
  }, [open])

  function emit(next) {
    onChange?.({ technology: chosenProfiles, round: chosenRounds, ...next })
  }

  function toggleProfile(profile) {
    emit({
      technology: profileSet.has(profile)
        ? chosenProfiles.filter(p => p !== profile)
        : [...chosenProfiles, profile],
    })
  }

  function toggleRound(round) {
    // Kept in interview order, whatever order they were ticked in.
    emit({
      round: roundSet.has(round)
        ? chosenRounds.filter(r => r !== round)
        : roundOptions.filter(r => r === round || roundSet.has(r)),
    })
  }

  const modeLabel = PROFILE_ROUND_MODES.find(m => m.id === effectiveMode).label
  const describe = [
    count > 0 && `Mode: ${modeLabel}`,
    chosenProfiles.length && `Profiles: ${chosenProfiles.join(', ')}`,
    chosenRounds.length && `Rounds: ${chosenRounds.join(', ')}`,
  ].filter(Boolean).join('\n')
  const hint = {
    profile: 'Applies as you tick. Ticked profiles match any of them; rounds are not filtered.',
    round: 'Applies as you tick. Ticked rounds match any of them; profiles are not filtered.',
    both: 'Applies as you tick. Ticks in one list match any of them; profiles and rounds must both match.',
  }[effectiveMode]

  const option = (value, checked, onToggle) => (
    <label key={value} title={value} className={`ops-levels-option${checked ? ' ops-levels-option--checked' : ''}`}>
      <input type="checkbox" checked={checked} onChange={onToggle} aria-label={value} />
      <span className="ops-levels-option__box" aria-hidden="true">
        {checked && <Icon name="check" size={11} strokeWidth={3} />}
      </span>
      <span className="ops-profile-round-option__label">{value}</span>
    </label>
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
        aria-label={count ? `Profile and round (${count} selected)` : 'Profile and round'}
        title={describe || 'Filter by profile and interview round'}
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
                onClick={() => onChange?.({ technology: [], round: [] })}
                disabled={!count}
                aria-label="Clear profile and round"
              >
                Clear
              </button>
            </div>

            <div className="ops-profile-round-modes" role="radiogroup" aria-label="Filter by">
              {PROFILE_ROUND_MODES.map(m => (
                <button
                  key={m.id}
                  type="button"
                  role="radio"
                  aria-checked={effectiveMode === m.id}
                  className={`ops-profile-round-modes__btn${effectiveMode === m.id ? ' ops-profile-round-modes__btn--on' : ''}`}
                  onClick={() => chooseMode(m.id)}
                >
                  {m.label}
                </button>
              ))}
            </div>

            {showProfiles && (
            <div className="ops-profile-round-menu__section">
              <div className="ops-profile-round-menu__label">
                <span>Profiles</span>
                <span className="ops-profile-round-menu__state">
                  {chosenProfiles.length ? `${chosenProfiles.length} selected` : 'All profiles'}
                </span>
              </div>
              {allProfiles.length > 6 && (
                <label className="ops-profile-round-menu__search">
                  <span aria-hidden="true"><Icon name="search" size={13} /></span>
                  <input
                    type="search"
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    placeholder="Search profiles..."
                    aria-label="Search profiles"
                  />
                </label>
              )}
              <div className="ops-profile-round-menu__profiles" role="group" aria-label="Profiles">
                {shownProfiles.length === 0 && (
                  <p className="ops-profile-round-menu__empty">
                    {allProfiles.length ? 'No match' : 'No profiles in this period'}
                  </p>
                )}
                {shownProfiles.map(p => option(p, profileSet.has(p), () => toggleProfile(p)))}
              </div>
            </div>
            )}

            {showRounds && (
            <div className="ops-profile-round-menu__section">
              <div className="ops-profile-round-menu__label">
                <span>Interview rounds</span>
                <span className="ops-profile-round-menu__state">
                  {chosenRounds.length ? `${chosenRounds.length} selected` : 'All rounds'}
                </span>
              </div>
              <div className="ops-levels-menu__list" role="group" aria-label="Interview rounds">
                {roundOptions.map(r => option(r, roundSet.has(r), () => toggleRound(r)))}
              </div>
            </div>
            )}

            <p className="ops-levels-menu__hint">{hint}</p>
          </div>
        </>
      )}
    </div>
  )
}
