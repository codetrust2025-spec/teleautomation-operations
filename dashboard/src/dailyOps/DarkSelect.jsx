import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

/**
 * A dark-theme custom dropdown that replaces a native <select> wherever the
 * native option popup is a problem.
 *
 * Why it exists: the "Mark as Attended" modal is a flex column whose body
 * scrolls (`overflow-y: auto`) and whose footer sits below it. A native
 * <select>'s option list is an OS-drawn popup that escapes the modal's layout
 * and, on some platforms, painted over the Note field and the footer. This
 * menu is a plain element the app styles itself, and it is rendered through a
 * portal to <body> and positioned under its trigger, so:
 *   - it is never clipped by the scrolling body,
 *   - it never pushes the footer (it is out of flow entirely), and
 *   - the footer stays put and visible while it is open.
 *
 * The trigger is a real button in the form's flow, so the layout the modal
 * computes with the menu closed is the layout it keeps while the menu is open.
 *
 * Keyboard: Enter/Space/ArrowDown/ArrowUp open; while open, Up/Down move the
 * active option, Enter selects it, Escape closes without changing the value.
 * Closes on outside click, on scroll/resize (the anchored position would
 * otherwise drift), and on selection.
 */
export function DarkSelect({
  value,
  options,
  onChange,
  placeholder = 'Select…',
  ariaLabel,
  id,
  disabled = false,
  required = false,
  className = '',
}) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState(null)
  const [activeIndex, setActiveIndex] = useState(-1)
  const triggerRef = useRef(null)
  const menuRef = useRef(null)
  const reactId = useId()
  const listboxId = (id || reactId) + '-listbox'

  const selected = options.find(o => o.value === value) || null
  const selectedIndex = options.findIndex(o => o.value === value)

  const place = useCallback(() => {
    const rect = triggerRef.current?.getBoundingClientRect()
    if (!rect) return
    // The menu should be only as tall as its options need, so it covers the
    // Note field and footer no more than necessary. Each option is ~36px plus
    // the list's 8px of vertical padding; that content height is the ceiling,
    // never the whole viewport.
    const OPTION_H = 36
    const contentHeight = options.length * OPTION_H + 8
    // Flip above the trigger when there is not enough room below it, so the
    // menu stays on screen on a short viewport (an iPhone in particular).
    const spaceBelow = window.innerHeight - rect.bottom
    const above = spaceBelow < contentHeight && rect.top > spaceBelow
    const roomForFlip = (above ? rect.top : spaceBelow) - 12
    setPosition({
      left: rect.left,
      width: rect.width,
      top: above ? undefined : rect.bottom + 4,
      bottom: above ? window.innerHeight - rect.top + 4 : undefined,
      // Snug to the options; only scrolls if even the flipped side is tighter
      // than the content, which for 3-5 short options effectively never happens.
      maxHeight: Math.max(120, Math.min(contentHeight, roomForFlip)),
    })
  }, [options.length])

  function openMenu() {
    if (disabled) return
    place()
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0)
    setOpen(true)
  }

  function closeMenu() {
    setOpen(false)
    setActiveIndex(-1)
  }

  function choose(index) {
    const opt = options[index]
    if (!opt) return
    onChange(opt.value)
    closeMenu()
    triggerRef.current?.focus()
  }

  useLayoutEffect(() => {
    if (open) place()
  }, [open, place])

  useEffect(() => {
    if (!open) return undefined
    function onDocMouseDown(event) {
      if (!triggerRef.current?.contains(event.target) && !menuRef.current?.contains(event.target)) {
        closeMenu()
      }
    }
    // An anchored menu must not float away from a trigger that scrolls out
    // from under it; closing is the honest response to the anchor moving.
    function onReposition() { closeMenu() }
    document.addEventListener('mousedown', onDocMouseDown)
    window.addEventListener('resize', onReposition)
    // Capture: catch scrolls on any ancestor (the modal body), not just window.
    window.addEventListener('scroll', onReposition, true)
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown)
      window.removeEventListener('resize', onReposition)
      window.removeEventListener('scroll', onReposition, true)
    }
  }, [open])

  function onTriggerKeyDown(event) {
    if (disabled) return
    if (!open) {
      if (['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(event.key)) {
        event.preventDefault()
        openMenu()
      }
      return
    }
    if (event.key === 'Escape') { event.preventDefault(); closeMenu(); return }
    if (event.key === 'ArrowDown') { event.preventDefault(); setActiveIndex(i => Math.min(options.length - 1, i + 1)); return }
    if (event.key === 'ArrowUp') { event.preventDefault(); setActiveIndex(i => Math.max(0, i - 1)); return }
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(activeIndex); return }
    if (event.key === 'Tab') { closeMenu() }
  }

  const menu = open && position && createPortal(
    <ul
      ref={menuRef}
      id={listboxId}
      role="listbox"
      aria-label={ariaLabel}
      className="dark-select__menu"
      style={{
        position: 'fixed',
        left: position.left,
        top: position.top,
        bottom: position.bottom,
        width: position.width,
        maxHeight: position.maxHeight,
      }}
    >
      {options.map((opt, index) => (
        <li
          key={opt.value}
          role="option"
          aria-selected={opt.value === value}
          className={
            'dark-select__option'
            + (index === activeIndex ? ' dark-select__option--active' : '')
            + (opt.value === value ? ' dark-select__option--selected' : '')
          }
          onMouseEnter={() => setActiveIndex(index)}
          // mousedown, not click: the document mousedown listener would
          // otherwise close the menu before the click landed.
          onMouseDown={event => { event.preventDefault(); choose(index) }}
        >
          <span>{opt.label}</span>
          {opt.value === value && <span className="dark-select__tick" aria-hidden="true">✓</span>}
        </li>
      ))}
    </ul>,
    document.body,
  )

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        id={id}
        className={`cand-input dark-select__trigger${open ? ' dark-select__trigger--open' : ''}${className ? ' ' + className : ''}`}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-label={ariaLabel}
        aria-required={required || undefined}
        disabled={disabled}
        onClick={() => (open ? closeMenu() : openMenu())}
        onKeyDown={onTriggerKeyDown}
      >
        <span className={selected ? 'dark-select__value' : 'dark-select__value dark-select__value--placeholder'}>
          {selected ? selected.label : placeholder}
        </span>
        <span className="dark-select__caret" aria-hidden="true" />
      </button>
      {menu}
    </>
  )
}
