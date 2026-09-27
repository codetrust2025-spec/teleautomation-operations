import { addDaysIso, todayIso } from './calendarDates.js'

const PRESETS = [
  { id: 'today', label: 'Today' },
  { id: 'upcoming', label: 'Upcoming' },
  { id: 'thisWeek', label: 'This week' },
  { id: 'last7', label: 'Last 7 days' },
]

// Day arithmetic moved to calendarDates.js so the presets and the calendar
// picker cannot drift apart on what "today" is. The versions that lived here
// mixed local-time construction with `toISOString()`, which reads back the UTC
// day: run east of Greenwich in the evening and the two disagreed by a day.

function startOfWeekIso(iso) {
  const date = new Date(`${iso.slice(0, 10)}T12:00:00Z`)
  const day = date.getUTCDay()
  return addDaysIso(iso, day === 0 ? -6 : 1 - day)
}

function endOfWeekIso(iso) {
  return addDaysIso(startOfWeekIso(iso), 6)
}

// Every stored slot, for the views that are not asking about a date: the
// all-time month option and the global unresolved list. Matches `_ALL_TIME_SPAN`
// in features/candidate_store.py.
export const ALL_TIME_RANGE = { from: '2000-01-01', to: '2100-12-31' }

export function resolvePresetRange(presetId) {
  const today = todayIso()
  switch (presetId) {
    case 'today':
      return { from: today, to: today }
    case 'upcoming':
      return { from: today, to: addDaysIso(today, 30) }
    case 'thisWeek':
      return { from: startOfWeekIso(today), to: endOfWeekIso(today) }
    case 'last7':
      return { from: addDaysIso(today, -7), to: today }
    default:
      return null
  }
}

export function detectPresetFromRange(from, to) {
  for (const preset of PRESETS) {
    const range = resolvePresetRange(preset.id)
    if (range?.from === from && range?.to === to) return preset.id
  }
  return 'custom'
}

export { PRESETS, todayIso }
