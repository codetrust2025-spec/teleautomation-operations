/**
 * Test helpers for the Daily Ops roster (12 Oct): a row click opens the row's
 * Edit dialog, and attendance is changed there -- with the note it needs, the
 * attendee for Attended / Not attended and the feedback for Attended -- and
 * saved by the dialog's one Save.
 */
import { act, fireEvent, screen, within } from '@testing-library/react'

export function rowOf(name) {
  return screen.getAllByText(name)[0].closest('tr')
}

/** Click a plain cell of the row, as a person would. */
export function openRowEdit(name) {
  fireEvent.click(rowOf(name).querySelector('td[data-label="Date"]'))
}

export function editDialog() {
  return document.querySelector('.ops-slot-modal')
}

export function setAttendance(value) {
  fireEvent.change(within(editDialog()).getByLabelText('Attendance'), { target: { value } })
}

export function setNote(text) {
  fireEvent.change(within(editDialog()).getByRole('textbox', { name: 'Notes' }), { target: { value: text } })
}

/** Pick an option of one of the dialog's custom dropdowns (attendee, feedback). */
export function pick(triggerName, optionText) {
  fireEvent.click(screen.getByRole('combobox', { name: triggerName }))
  fireEvent.mouseDown(screen.getByRole('option', { name: optionText }))
}

export function saveButton() {
  return within(editDialog()).getByRole('button', { name: 'Save changes' })
}

/** Open the row, set its attendance (with a note, and feedback for Attended) and Save. */
export async function recordOutcome(name, status, { note = 'Recorded from Daily Ops', feedback = 'Good' } = {}) {
  openRowEdit(name)
  setAttendance(status)
  if (status === 'attended') pick('Interview feedback', feedback)
  if (status) setNote(note)
  await act(async () => { fireEvent.click(saveButton()) })
}
