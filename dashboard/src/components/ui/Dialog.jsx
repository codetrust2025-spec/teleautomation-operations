import React, { useId } from 'react'
import { useDialogA11y } from '../../hooks/useDialogA11y.js'
import { Button } from './Button.jsx'

/**
 * A small modal dialog: title, body, actions and an optional footnote. Focus
 * moves in on open, Tab stays inside, Escape and the × close it, and focus
 * returns to what opened it (useDialogA11y).
 */
export function Dialog({ open, title, onClose, actions, note, closeLabel = 'Close', className = '', children }) {
  const titleId = useId()
  const dialogRef = useDialogA11y(open, onClose)
  if (!open) return null
  return (
    <div className="ui-dialog-backdrop" role="presentation">
      <section ref={dialogRef} className={['ui-dialog', className].filter(Boolean).join(' ')} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        {onClose && (
          <Button variant="ghost" size="sm" className="ui-dialog__close" aria-label={closeLabel} onClick={onClose}>×</Button>
        )}
        <h2 className="ui-dialog__title" id={titleId}>{title}</h2>
        {children && <div className="ui-dialog__body">{children}</div>}
        {actions && <div className="ui-dialog__actions">{actions}</div>}
        {note && <p className="ui-dialog__note">{note}</p>}
      </section>
    </div>
  )
}

export default Dialog
