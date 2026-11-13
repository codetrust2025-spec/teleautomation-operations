import React, { forwardRef } from 'react'
import { ButtonContent } from '../../Loader.jsx'

/**
 * The one button of the Operations kit (styles/ui.css).
 *
 *   primary   the page's main action; at most one per card
 *   secondary everything else (the default)
 *   ghost     low-emphasis actions beside a primary one ("Not now", "Close")
 *   danger    actions that stop, remove or reject something
 *   link      an action that reads as a link inside text or a table cell
 *
 * Sizes: sm 32px, md 36px (default), lg 44px. On touch screens every button
 * is at least 44px tall. `loading` keeps the width and shows a spinner.
 */
const VARIANTS = new Set(['primary', 'secondary', 'ghost', 'danger', 'link'])
// Names the earlier Button accepted, mapped onto the kit.
const ALIASES = { success: 'primary', warning: 'secondary', toolbar: 'secondary', segment: 'secondary' }
const SIZES = { xs: 'sm', sm: 'sm', md: '', lg: 'lg' }

export const Button = forwardRef(function Button({
  children,
  className = '',
  variant = 'secondary',
  size = 'md',
  block = false,
  loading = false,
  loadingLabel,
  active = false,
  type = 'button',
  disabled,
  ...props
}, ref) {
  const resolved = VARIANTS.has(variant) ? variant : (ALIASES[variant] || 'secondary')
  const sizeClass = SIZES[size] ?? ''
  const classes = [
    'ui-btn',
    resolved !== 'secondary' ? `ui-btn--${resolved}` : '',
    sizeClass ? `ui-btn--${sizeClass}` : '',
    block ? 'ui-btn--block' : '',
    className,
  ].filter(Boolean).join(' ')

  return (
    <button
      ref={ref}
      type={type}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      aria-pressed={active || undefined}
      {...props}
    >
      <ButtonContent loading={loading} loadingLabel={loadingLabel}>
        {children}
      </ButtonContent>
    </button>
  )
})

export default Button
