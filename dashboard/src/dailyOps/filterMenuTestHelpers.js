import { fireEvent, screen, within } from '@testing-library/react'

/** The checkbox list of one Filters field, by its accessible name
 *  ('Candidate filter' or 'Attendee filter'). */
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

/** Tick (or untick) a value in the Profile + Round control -- 'Booking type'
 *  or 'Interview rounds' -- opening its menu first if needed. Applies at once. */
export function tickProfileRound(list, value) {
  if (!screen.queryByRole('group', { name: list })) {
    fireEvent.click(screen.getByRole('button', { name: /^Profile and round/ }))
  }
  fireEvent.click(within(screen.getByRole('group', { name: list })).getByRole('checkbox', { name: value }))
}

/** The values ticked in one Profile + Round list (its menu must be open). */
export function tickedProfileRound(list) {
  return within(screen.getByRole('group', { name: list })).getAllByRole('checkbox')
    .filter(box => box.checked).map(box => box.getAttribute('aria-label'))
}

/** Close whichever menu is open. */
export function closeMenus() {
  fireEvent.keyDown(document, { key: 'Escape' })
}
