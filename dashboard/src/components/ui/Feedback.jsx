import React from 'react'

const TONES = new Set(['neutral', 'accent', 'info', 'success', 'warning', 'danger'])

/** A short status or count: "4 handlers", "Verified", "1 pending". */
export function Badge({ tone = 'neutral', className = '', children, ...props }) {
  const resolved = TONES.has(tone) ? tone : 'neutral'
  return (
    <span className={['ui-badge', resolved !== 'neutral' ? `ui-badge--${resolved}` : '', className].filter(Boolean).join(' ')} {...props}>
      {children}
    </span>
  )
}

/**
 * A message about the page: an error to fix (danger, announced at once), a
 * warning, a confirmation or a note.
 */
export function Alert({ tone = 'danger', className = '', role, children, ...props }) {
  const resolved = TONES.has(tone) ? tone : 'danger'
  return (
    <p
      className={['ui-alert', resolved !== 'info' && resolved !== 'accent' ? `ui-alert--${resolved}` : '', className].filter(Boolean).join(' ')}
      role={role || (resolved === 'danger' ? 'alert' : 'status')}
      {...props}
    >
      {children}
    </p>
  )
}

/** A confirmation that floats in the corner (full width on phones). */
export function Toast({ className = '', children, ...props }) {
  return <div className={['ui-toast', className].filter(Boolean).join(' ')} role="status" {...props}>{children}</div>
}

/** What a list or table shows when it has nothing in it. */
export function EmptyState({ className = '', children, ...props }) {
  return <p className={['ui-empty', className].filter(Boolean).join(' ')} {...props}>{children}</p>
}
