import React, { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Icon } from '../components/ui/Icon.jsx'
import { toFilterList } from './DailyOpsFilterMenu.jsx'
import { INTERVIEW_LEVELS } from './InterviewLevelsFilter.jsx'

/**
 * Profile + Round: a filter of its own beside Levels (Nov 2026).
 *
 * Two checkbox lists in one menu -- profiles (technologies) and interview
 * rounds -- ticked in any combination. Each tick applies at once; there is no
 * Apply button. Inside a list the ticks are alternatives (Java or Python);
 * the two lists must both match (Java or Python, AND L2 or HR), like every
 * other Daily Ops filter.
 *
 * It holds no selection of its own: profiles are the Filters panel's Profile /
 * Technology field and rounds are the Levels selection, so all three controls
 * always agree.
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

  const describe = [
    chosenProfiles.length && `Profiles: ${chosenProfiles.join(', ')}`,
    chosenRounds.length && `Rounds: ${chosenRounds.join(', ')}`,
  ].filter(Boolean).join('\n')

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

            <p className="ops-levels-menu__hint">
              Applies as you tick. Ticks in one list match any of them; profiles and rounds must both match.
            </p>
          </div>
        </>
      )}
    </div>
  )
}
