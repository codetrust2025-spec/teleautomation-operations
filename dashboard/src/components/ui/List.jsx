import React from 'react'

/** Rows of "title, meta line, actions", divided by hairlines. */
export function List({ className = '', children, ...props }) {
  return <div className={['ui-list', className].filter(Boolean).join(' ')} {...props}>{children}</div>
}

export function ListRow({ title, meta, actions, className = '', ...props }) {
  return (
    <div className={['ui-list__row', className].filter(Boolean).join(' ')} {...props}>
      <span className="ui-list__text">
        <strong className="ui-list__title">{title}</strong>
        {meta && <small className="ui-list__meta">{meta}</small>}
      </span>
      {actions && <div className="ui-list__actions">{actions}</div>}
    </div>
  )
}
