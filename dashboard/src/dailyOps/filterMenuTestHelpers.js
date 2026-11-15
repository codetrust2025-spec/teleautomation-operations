import { fireEvent, screen, within } from '@testing-library/react'

/** The checkbox list of one Filters field, by its accessible name
 *  ('Candidate filter', 'Attendee filter', 'Candidate interview level filter',
 *  'Technology filter'). */
export function filterGroup(name) {
  return screen.getByRole('group', { name })
}

/** Tick (or untick) one value in a Filters field. */
export function toggleFilterValue(field, value) {
  fireEvent.click(within(filterGroup(field)).getByRole('checkbox', { name: value }))
}

/** The values ticked in a Filters field, in list order. */
export function checkedFilterValues(field) {
  return within(filterGroup(field))
    .queryAllByRole('checkbox')
    .filter(box => box.checked)
    .map(box => box.getAttribute('aria-label'))
}

export function openFilters() {
  fireEvent.click(screen.getByRole('button', { name: /^Filters/ }))
}

export function applyFilters() {
  fireEvent.click(screen.getByRole('button', { name: /^Apply/ }))
}
