import React, { forwardRef, useId } from 'react'

/**
 * A labelled form control. The label is always there for screen readers;
 * `hideLabel` only hides it visually, for compact rows whose placeholder and
 * position already say what goes in.
 *
 *   <Field label="Holiday name"><Input value={…} onChange={…} /></Field>
 */
export function Field({ label, hideLabel = false, id, className = '', children }) {
  const generated = useId()
  const controlId = id || generated
  const control = React.isValidElement(children) ? React.cloneElement(children, { id: children.props.id || controlId }) : children
  return (
    <div className={['ui-field', className].filter(Boolean).join(' ')}>
      <label className={hideLabel ? 'ui-field__label ui-field__label--hidden' : 'ui-field__label'} htmlFor={controlId}>{label}</label>
      {control}
    </div>
  )
}

export const Input = forwardRef(function Input({ className = '', type = 'text', ...props }, ref) {
  return <input ref={ref} type={type} className={['ui-input', className].filter(Boolean).join(' ')} {...props} />
})

/** Controls in one row that stack on phones; the last column can be `auto`. */
export function FormRow({ columns, className = '', style, children, ...props }) {
  return (
    <div className={['ui-form-row', className].filter(Boolean).join(' ')} style={columns ? { '--ui-form-cols': columns, ...style } : style} {...props}>
      {children}
    </div>
  )
}
