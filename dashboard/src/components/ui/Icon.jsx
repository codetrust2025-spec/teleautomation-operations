import React from 'react'

/**
 * One small line-icon set for the shell and Daily Ops.
 *
 * The sidebar mixed an emoji bell with geometric block characters, two of
 * which vanished whenever their count was zero, and Daily Ops drew its search
 * and refresh marks from whatever the font had for U+2315 and U+21BB. These are
 * drawn on one 24-unit grid with one stroke, so every mark is the same size,
 * weight and colour (currentColor) wherever it appears.
 */
const PATHS = {
  // Daily Ops: a clipboard with a checklist.
  clipboard: (
    <>
      <rect x="6" y="4" width="12" height="17" rx="2" />
      <path d="M9 4.5V3.5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1" />
      <path d="M9 10h6M9 14h6M9 18h3" />
    </>
  ),
  bell: (
    <>
      <path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z" />
      <path d="M10 20.5a2 2 0 0 0 4 0" />
    </>
  ),
  mail: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m4 7 8 6 8-6" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20a6.5 6.5 0 0 1 13 0" />
      <path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.2a6.5 6.5 0 0 1 3.5 5.8" />
    </>
  ),
  // Data Room: a stack of records.
  database: (
    <>
      <ellipse cx="12" cy="5.5" rx="7.5" ry="2.5" />
      <path d="M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13" />
      <path d="M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5" />
    </>
  ),
  'user-check': (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20a6.5 6.5 0 0 1 13 0" />
      <path d="m15.5 11 2 2 4-4" />
    </>
  ),
  'calendar-plus': (
    <>
      <rect x="3.5" y="5" width="17" height="15.5" rx="2" />
      <path d="M3.5 9.5h17M8 3v4M16 3v4M12 12.5v5M9.5 15h5" />
    </>
  ),
  bolt: <path d="M13 2.5 5 13.5h6l-1 8 8-11h-6z" />,
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 12a8 8 0 1 1-2.3-5.6" />
      <path d="M20 4v5h-5" />
    </>
  ),
  // Reset: back to where the page started.
  reset: (
    <>
      <path d="M4 12a8 8 0 1 0 2.3-5.6" />
      <path d="M4 4v5h5" />
    </>
  ),
  'more-vertical': (
    <>
      <circle cx="12" cy="5.5" r="1.2" />
      <circle cx="12" cy="12" r="1.2" />
      <circle cx="12" cy="18.5" r="1.2" />
    </>
  ),
  'image-off': (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
      <path d="m3.5 16 5-5 4 4M14.5 13l2-2 4 4" />
      <path d="M3 3l18 18" />
    </>
  ),
  sparkles: (
    <>
      <path d="M10 3.5 11.6 8.4 16.5 10 11.6 11.6 10 16.5 8.4 11.6 3.5 10 8.4 8.4z" />
      <path d="M18 14.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" />
    </>
  ),
}

export const ICON_NAMES = Object.keys(PATHS)

export function Icon({ name, size = 16, className = '', strokeWidth = 1.8, title }) {
  const shape = PATHS[name]
  if (!shape) return null
  return (
    <svg
      className={`ta-icon ta-icon--${name}${className ? ` ${className}` : ''}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? 'img' : undefined}
      focusable="false"
    >
      {title && <title>{title}</title>}
      {shape}
    </svg>
  )
}
