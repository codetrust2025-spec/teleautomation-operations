import { screen, waitFor } from '@testing-library/react'

// Rows open their Edit dialog when clicked (utils/rowEdit.js). These find the
// row a test would click, by the name the row gives assistive technology.
const SPECIAL = '\\^$.*+?()[]{}|'
const escape = (text) => [...text].map((ch) => (SPECIAL.includes(ch) ? '\\' + ch : ch)).join('')
export const rowEditName = (label) => new RegExp('^' + escape(label) + ' — press Enter to edit$')
export const editRow = (label) => screen.getByRole('row', { name: rowEditName(label) })
export const queryEditRow = (label) => screen.queryByRole('row', { name: rowEditName(label) })
export const firstEditableRow = () => document.querySelector('tr.row-edit')
export const findEditableRow = () => waitFor(() => {
  const row = firstEditableRow()
  if (!row) throw new Error('no editable row yet')
  return row
})
