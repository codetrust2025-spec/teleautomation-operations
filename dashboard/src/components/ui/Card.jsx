import React from 'react'

/**
 * A card: one bordered surface with an optional head (title, description,
 * actions), a body and a footnote. Sections of a page are cards.
 */
export function Card({
  as: Tag = 'section',
  title,
  titleId,
  headingLevel = 3,
  description,
  actions,
  footer,
  flush = false,
  className = '',
  children,
  ...props
}) {
  const Heading = `h${headingLevel}`
  const hasHead = title || description || actions
  return (
    <Tag className={['ui-card', flush ? 'ui-card--flush' : '', className].filter(Boolean).join(' ')} {...props}>
      {hasHead && (
        <div className="ui-card__head">
          {(title || description) && (
            <div className="ui-card__titles">
              {title && <Heading className="ui-card__title" id={titleId}>{title}</Heading>}
              {description && <p className="ui-card__description">{description}</p>}
            </div>
          )}
          {actions && <div className="ui-card__actions">{actions}</div>}
        </div>
      )}
      {children != null && children !== false && (hasHead ? <div className="ui-card__body">{children}</div> : children)}
      {footer && <p className="ui-card__foot">{footer}</p>}
    </Tag>
  )
}

/** A figure with its label and a hint line, as a card. */
export function StatTile({ label, value, hint, className = '', ...props }) {
  return (
    <Card as="article" className={['ui-stat', className].filter(Boolean).join(' ')} {...props}>
      <span className="ui-stat__label">{label}</span>
      <strong className="ui-stat__value">{value}</strong>
      {hint && <small className="ui-stat__hint">{hint}</small>}
    </Card>
  )
}

/** Cards side by side: `columns` on desktop, two on tablet, one on phones. */
export function CardGrid({ columns = 3, className = '', style, children, ...props }) {
  return (
    <div className={['ui-grid', className].filter(Boolean).join(' ')} style={{ '--ui-grid-cols': columns, ...style }} {...props}>
      {children}
    </div>
  )
}

export default Card
