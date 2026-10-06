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
  // Candidates: the row and toolbar actions, the proof and resume chips.
  pencil: (
    <>
      <path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17z" />
      <path d="m14 7 3 3" />
    </>
  ),
  trash: (
    <>
      <path d="M4.5 7h15M10 4h4M6.5 7l1 13h9l1-13" />
      <path d="M10 11v5.5M14 11v5.5" />
    </>
  ),
  list: <path d="M9 6.5h11M9 12h11M9 17.5h11M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01" />,
  download: (
    <>
      <path d="M12 4v11M7.5 10.5 12 15l4.5-4.5" />
      <path d="M4.5 19.5h15" />
    </>
  ),
  chart: <path d="M4.5 19.5h15M7.5 16.5v-5M12 16.5v-10M16.5 16.5v-7" />,
  paperclip: <path d="m20 11.5-7.8 7.8a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4L15 6.9" />,
  file: (
    <>
      <path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z" />
      <path d="M14 3.5v5h5M9 13h6M9 16.5h4" />
    </>
  ),
  camera: (
    <>
      <path d="M4 8.5a2 2 0 0 1 2-2h2l1.5-2h5L16 6.5h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z" />
      <circle cx="12" cy="13" r="3.5" />
    </>
  ),
  phone: <path d="M5 4.5h3.5l1.5 4-2 1.5a11 11 0 0 0 6 6l1.5-2 4 1.5V19a1.5 1.5 0 0 1-1.6 1.5A16 16 0 0 1 3.5 6.1 1.5 1.5 0 0 1 5 4.5z" />,
  // Data Room: copy a value, and the tick that confirms it was copied.
  copy: (
    <>
      <rect x="8.5" y="8.5" width="12" height="12" rx="2" />
      <path d="M15.5 8.5V5.5a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3" />
    </>
  ),
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  plus: <path d="M12 5v14M5 12h14" />,
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
