import { useEffect, useState } from 'react'

/** Phones: the width at which Daily Ops switches to cards and bottom sheets (dailyOps.css). */
export const PHONE_QUERY = '(max-width: 767px)'

function matches(query) {
  try {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false
  } catch {
    return false
  }
}

/** Whether a CSS media query matches now, following changes (rotation, resizing). */
export function useMediaQuery(query) {
  const [value, setValue] = useState(() => matches(query))
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined
    const list = window.matchMedia(query)
    const update = () => setValue(list.matches)
    update()
    if (list.addEventListener) list.addEventListener('change', update)
    else list.addListener?.(update)
    return () => {
      if (list.removeEventListener) list.removeEventListener('change', update)
      else list.removeListener?.(update)
    }
  }, [query])
  return value
}
