import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from '../components/ui/Icon.jsx'

/**
 * The roster's Status cell (owner's mockup, 15 Nov; it was "Notes").
 *
 * Only the badge sits in the row -- Excellent, Good, Average, Needs
 * Improvement, Negative -- or "Note" when a remark was saved without feedback.
 * The remark itself opens in a small card under the badge: on hover or
 * keyboard focus on a desktop, on tap on a phone (tap again, tap elsewhere or
 * Escape closes it).
 *
 * The badge is a button whenever there is a note, so tapping it never opens the
 * row's Edit dialog (rowEdit ignores clicks on buttons). The card is portalled
 * to <body> with fixed coordinates, so the table's scroll box cannot clip it.
 */
export function FeedbackStatusBadge({ feedback, note }) {
  const text = String(note || '').trim()
  const [hovered, setHovered] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [pos, setPos] = useState(null)
  const badgeRef = useRef(null)
  const cardRef = useRef(null)
  const cardId = useId()
  const open = !!text && (hovered || pinned)

  const close = useCallback(() => { setHovered(false); setPinned(false) }, [])

  useLayoutEffect(() => {
    if (!open) { setPos(null); return }
    const badge = badgeRef.current?.getBoundingClientRect()
    if (!badge) return
    const card = cardRef.current?.getBoundingClientRect()
    const width = card?.width || 0
    const height = card?.height || 0
    const vw = window.innerWidth || document.documentElement.clientWidth || 0
    const vh = window.innerHeight || document.documentElement.clientHeight || 0
    // Under the badge, its left edge at the badge's centre; nudged back inside
    // the screen when that would run off the right (the last column) or bottom.
    let left = badge.left + badge.width / 2 - 14
    if (vw && left + width > vw - 8) left = Math.max(8, vw - 8 - width)
    let top = badge.bottom + 8
    let above = false
    if (vh && height && top + height > vh - 8 && badge.top - 8 - height >= 8) {
      top = badge.top - 8 - height
      above = true
    }
    const arrow = Math.min(Math.max(badge.left + badge.width / 2 - left, 12), Math.max(12, width - 12))
    setPos(prev => (prev && prev.left === left && prev.top === top && prev.arrow === arrow && prev.above === above
      ? prev : { left, top, arrow, above }))
  }, [open, text])

  useEffect(() => {
    if (!open) return undefined
    function onPointerDown(e) {
      if (badgeRef.current?.contains(e.target) || cardRef.current?.contains(e.target)) return
      close()
    }
    function onKeyDown(e) { if (e.key === 'Escape') close() }
    // The card is placed once; moving the page under it would leave it behind.
    function onMove() { close() }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('touchstart', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('touchstart', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
    }
  }, [open, close])

  if (!feedback && !text) return <span className="ops-status-empty">—</span>

  const label = feedback ? feedback.label : 'Note'
  const tone = feedback ? feedback.tone : 'note'
  const className = `ops-feedback-pill ops-feedback-pill--${tone} ops-status-badge`

  if (!text) {
    return (
      <span className={className} data-feedback={feedback.value} title={`Status: ${label}`}>
        {label}
      </span>
    )
  }

  return (
    <>
      <button
        ref={badgeRef}
        type="button"
        className={`${className} ops-status-badge--has-note${open ? ' ops-status-badge--open' : ''}`}
        data-feedback={feedback?.value}
        aria-label={`Status: ${label}. Show notes`}
        aria-expanded={open}
        aria-describedby={open ? cardId : undefined}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onFocus={() => setHovered(true)}
        onBlur={() => { setHovered(false); setPinned(false) }}
        onClick={event => {
          event.stopPropagation()
          // A tap focuses (opens) then clicks (pins); the next tap closes,
          // even though the badge keeps focus.
          if (pinned) close()
          else setPinned(true)
        }}
      >
        {label}
      </button>
      {open && createPortal(
        <div
          ref={cardRef}
          id={cardId}
          role="tooltip"
          className={`ops-status-note${pos?.above ? ' ops-status-note--above' : ''}`}
          style={{
            left: pos ? `${pos.left}px` : 0,
            top: pos ? `${pos.top}px` : 0,
            visibility: pos ? 'visible' : 'hidden',
            '--ops-status-note-arrow': pos ? `${pos.arrow}px` : '14px',
          }}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
        >
          <Icon name="info" size={14} strokeWidth={2} />
          <span className="ops-status-note__text">{text}</span>
        </div>,
        document.body,
      )}
    </>
  )
}
