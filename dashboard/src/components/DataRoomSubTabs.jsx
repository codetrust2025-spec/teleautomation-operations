import React, { useRef } from 'react'

/**
 * The sections of one Data Room tab, one open at a time: a tab list (arrow keys,
 * Home and End move between them, as in any tab list) above the open section's
 * panel. Only the open section is mounted, so what one section holds is never
 * on the page while another is open.
 *
 * tabs: [{ id, label, count, hint }]
 */
export function DataRoomSubTabs({ label, idPrefix, tabs, active, onChange, children }) {
  const buttons = useRef({})
  const ids = tabs.map((tab) => tab.id)

  const onKeyDown = (e) => {
    const at = ids.indexOf(active)
    const next = e.key === 'ArrowRight' ? ids[(at + 1) % ids.length]
      : e.key === 'ArrowLeft' ? ids[(at - 1 + ids.length) % ids.length]
        : e.key === 'Home' ? ids[0]
          : e.key === 'End' ? ids[ids.length - 1]
            : null
    if (!next) return
    e.preventDefault()
    onChange(next)
    buttons.current[next]?.focus()
  }

  return (
    <>
      <div className="dr-subtabs" role="tablist" aria-label={label} onKeyDown={onKeyDown}>
        {tabs.map((tab) => {
          const selected = tab.id === active
          return (
            <button
              key={tab.id}
              ref={(el) => { buttons.current[tab.id] = el }}
              type="button"
              role="tab"
              id={`${idPrefix}-tab-${tab.id}`}
              aria-controls={`${idPrefix}-panel-${tab.id}`}
              aria-selected={selected}
              aria-label={`${tab.label} (${tab.count})`}
              tabIndex={selected ? 0 : -1}
              title={tab.hint}
              className={`dr-subtab${selected ? ' dr-subtab--active' : ''}`}
              onClick={() => onChange(tab.id)}
            >
              <span className="dr-subtab-label">{tab.label}</span>
              <span className="dr-subtab-count">{tab.count}</span>
            </button>
          )
        })}
      </div>
      <div
        className="dr-subpanel"
        role="tabpanel"
        id={`${idPrefix}-panel-${active}`}
        aria-labelledby={`${idPrefix}-tab-${active}`}
      >
        {children}
      </div>
    </>
  )
}
