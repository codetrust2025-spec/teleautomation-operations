import { useCallback } from 'react'

/*
 * One row-click behaviour for every editable table (Daily Ops design, 12 Oct;
 * shared 5 Nov). Activating a row -- click, Enter or Space while it has focus --
 * opens that row's Edit dialog; Save, Cancel and Delete live in the dialog, so
 * the row carries no Edit / Delete buttons of its own.
 *
 * A click keeps its own behaviour when it lands on a control inside the row (a
 * link, a copy button, a select, View), when text is being selected, or when it
 * only bubbled through React from a portal (a dialog on document.body).
 */
export const ROW_INTERACTIVE_SELECTOR = [
  'button', 'a', 'select', 'input', 'textarea', 'label', 'summary',
  '[role="button"]', '[role="menu"]', '[role="menuitem"]', '[role="dialog"]',
  '[data-row-action]',
  '.ops-attendance-select', '.ops-slot-shot-thumb',
].join(',')

/** The class every editable row carries (pointer, hover, focus ring). */
export const ROW_EDIT_CLASS = 'row-edit'

export function rowEventHitsInteractive(event, rowEl) {
  const target = event.target
  if (!(target instanceof Element)) return false
  const hit = target.closest(ROW_INTERACTIVE_SELECTOR)
  // A match only counts when it is inside this row (closest can escape the row
  // through an ancestor; a portalled dialog is handled by the contains() check).
  return !!hit && (!rowEl || rowEl.contains(hit)) && hit !== rowEl
}

/** True when a row click should open the row's Edit dialog. */
export function shouldActivateRow(event) {
  const rowEl = event.currentTarget
  if (!(event.target instanceof Node) || !rowEl.contains(event.target)) return false
  if (rowEventHitsInteractive(event, rowEl)) return false
  const selection = typeof window !== 'undefined' && window.getSelection && window.getSelection()
  if (selection && String(selection).length > 0) return false
  return true
}

/** True when a key on a focused row should open its Edit dialog. */
export function isRowActivationKey(event) {
  if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return false
  // Keys aimed at a control inside the row are left to that control.
  return event.target === event.currentTarget
}

/**
 * useRowEdit(open) returns rowProps(row, label, enabled = true): spread it on a
 * <tr>. `label` names the row for screen readers ("Edit <label>").
 */
export function useRowEdit(open) {
  return useCallback((row, label, enabled = true) => {
    if (!enabled) return {}
    return {
      onClick: event => { if (shouldActivateRow(event)) open(row) },
      onKeyDown: event => {
        if (!isRowActivationKey(event)) return
        event.preventDefault()
        open(row)
      },
      tabIndex: 0,
      'aria-haspopup': 'dialog',
      'aria-label': `${label} — press Enter to edit`,
    }
  }, [open])
}
