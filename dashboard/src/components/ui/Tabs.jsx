import React from 'react'
import { Icon } from './Icon.jsx'

/**
 * Sections of one page, as a row of buttons on a rule. The current one is
 * underlined in the accent (the only "current item" use of blue); the panels
 * themselves are the page's to render, so a page can keep both mounted.
 *
 *   <Tabs label="AI Controls sections" tabs={[{ id: 'a', label: 'A', icon: 'code' }]} value={tab} onChange={setTab} />
 */
export function Tabs({ label, tabs, value, onChange, className = '' }) {
  return (
    <nav className={['ui-tabs', className].filter(Boolean).join(' ')} aria-label={label}>
      {tabs.map(tab => (
        <button key={tab.id} type="button" className="ui-tabs__tab" aria-pressed={value === tab.id} onClick={() => onChange(tab.id)}>
          {tab.icon && <Icon name={tab.icon} />}{tab.label}
        </button>
      ))}
    </nav>
  )
}

/** Detail an operator opens when they need it: a quiet summary line. */
export function Disclosure({ summary, open = false, className = '', children }) {
  return (
    <details className={['ui-disclosure', className].filter(Boolean).join(' ')} open={open}>
      <summary className="ui-disclosure__summary">{summary}</summary>
      <div className="ui-disclosure__body">{children}</div>
    </details>
  )
}

export default Tabs
