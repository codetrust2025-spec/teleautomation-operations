// The shared Operations kit (components/ui + styles/ui.css): what every page
// moved onto it can rely on.
import React, { useState } from 'react'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Button } from './Button.jsx'
import { Card, CardGrid, StatTile } from './Card.jsx'
import { Alert, Badge, EmptyState, Toast } from './Feedback.jsx'
import { Field, FormRow, Input } from './Field.jsx'
import { Disclosure, Tabs } from './Tabs.jsx'
import { List, ListRow } from './List.jsx'
import { DataTable } from './DataTable.jsx'
import { Dialog } from './Dialog.jsx'

const here = dirname(fileURLToPath(import.meta.url))
const read = (path) => readFileSync(join(here, path), 'utf-8').replace(/\r\n/g, '\n')
const UI_CSS = read('../../styles/ui.css')
const TOKENS = read('../../styles/tokens.css')
const INDEX = read('../../index.css')
const withoutComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

afterEach(cleanup)

describe('Button', () => {
  it('is a secondary 36px button unless told otherwise', () => {
    render(<Button>Refresh</Button>)
    const button = screen.getByRole('button', { name: 'Refresh' })
    expect(button).toHaveClass('ui-btn')
    expect(button.className).not.toMatch(/ui-btn--/)
    expect(button).toHaveAttribute('type', 'button')
  })

  it('names its variant, size and width in classes', () => {
    render(<Button variant="primary" size="sm" block>Add holiday</Button>)
    expect(screen.getByRole('button')).toHaveClass('ui-btn', 'ui-btn--primary', 'ui-btn--sm', 'ui-btn--block')
  })

  it('maps the names the earlier Button took onto the kit', () => {
    render(<><Button variant="toolbar">A</Button><Button variant="success">B</Button><Button size="xs">C</Button></>)
    expect(screen.getByRole('button', { name: 'A' }).className).toBe('ui-btn')
    expect(screen.getByRole('button', { name: 'B' })).toHaveClass('ui-btn--primary')
    expect(screen.getByRole('button', { name: 'C' })).toHaveClass('ui-btn--sm')
  })

  it('cannot be pressed twice while it is working', () => {
    const onClick = vi.fn()
    render(<Button loading loadingLabel="Saving…" onClick={onClick}>Save</Button>)
    const button = screen.getByRole('button', { name: /Saving/ })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    fireEvent.click(button)
    expect(onClick).not.toHaveBeenCalled()
  })
})

describe('Card, StatTile, CardGrid', () => {
  it('titles a card with a heading and keeps actions beside it', () => {
    render(<Card title="Public holidays" description="Excluded from attendance." actions={<Button>Add</Button>}>body</Card>)
    expect(screen.getByRole('heading', { level: 3, name: 'Public holidays' })).toHaveClass('ui-card__title')
    expect(screen.getByText('Excluded from attendance.')).toHaveClass('ui-card__description')
    expect(screen.getByRole('button', { name: 'Add' }).closest('.ui-card__actions')).not.toBeNull()
    expect(screen.getByText('body')).toHaveClass('ui-card__body')
  })

  it('shows a figure with its label and hint', () => {
    render(<StatTile label="Attendance Success" value="90%" hint="9 / 10 Working Days" />)
    expect(screen.getByText('90%')).toHaveClass('ui-stat__value')
    expect(screen.getByText('Attendance Success').closest('article')).toHaveClass('ui-card', 'ui-stat')
  })

  it('passes its column count to the grid', () => {
    const { container } = render(<CardGrid columns={4}><span /></CardGrid>)
    expect(container.firstChild).toHaveClass('ui-grid')
    expect(container.firstChild.style.getPropertyValue('--ui-grid-cols')).toBe('4')
  })
})

describe('Badge, Alert, Toast, EmptyState', () => {
  it('colours a badge by tone and falls back to neutral', () => {
    render(<><Badge tone="success">Verified</Badge><Badge tone="purple">x</Badge></>)
    expect(screen.getByText('Verified')).toHaveClass('ui-badge', 'ui-badge--success')
    expect(screen.getByText('x').className).toBe('ui-badge')
  })

  it('announces errors at once and other messages politely', () => {
    render(<><Alert>Request failed</Alert><Alert tone="success">Saved</Alert></>)
    expect(screen.getByRole('alert')).toHaveTextContent('Request failed')
    expect(screen.getByRole('status')).toHaveClass('ui-alert--success')
  })

  it('renders a toast and an empty state', () => {
    render(<><Toast>✓ Marked</Toast><EmptyState>Nothing yet.</EmptyState></>)
    expect(screen.getByRole('status')).toHaveClass('ui-toast')
    expect(screen.getByText('Nothing yet.')).toHaveClass('ui-empty')
  })
})

describe('Field and Input', () => {
  it('labels its control, even when the label is hidden', () => {
    render(<FormRow><Field label="Holiday name" hideLabel><Input placeholder="Holiday name" /></Field></FormRow>)
    const input = screen.getByLabelText('Holiday name')
    expect(input).toHaveClass('ui-input')
    expect(screen.getByText('Holiday name', { selector: 'label' })).toHaveClass('ui-field__label--hidden')
  })
})

describe('List', () => {
  it('lays out title, meta and actions', () => {
    render(<List><ListRow title="14 Nov 2026" meta="Children's Day" actions={<Button>Remove</Button>} /></List>)
    expect(screen.getByText('14 Nov 2026')).toHaveClass('ui-list__title')
    expect(screen.getByText("Children's Day")).toHaveClass('ui-list__meta')
    expect(screen.getByRole('button', { name: 'Remove' }).closest('.ui-list__actions')).not.toBeNull()
  })
})

describe('DataTable', () => {
  const columns = [
    { key: 'name', header: 'Name', primary: true },
    { key: 'days', header: 'Working days', numeric: true, render: (row) => `${row.done} / ${row.of}` },
  ]

  it('labels every cell so a phone can show it as "label: value"', () => {
    render(<DataTable columns={columns} rows={[{ id: 1, name: 'Asha Rao', done: 9, of: 10 }]} rowKey={(row) => row.id} />)
    const cell = screen.getByText('9 / 10')
    expect(cell).toHaveAttribute('data-label', 'Working days')
    expect(cell).toHaveClass('ui-table__num')
    expect(screen.getByText('Asha Rao')).toHaveAttribute('data-primary', '')
    expect(screen.getByRole('table')).toHaveClass('ui-table', 'ui-table--cards')
  })

  it('says when there is nothing, or that it is loading', () => {
    const { rerender } = render(<DataTable columns={columns} rows={[]} empty="No records." />)
    expect(screen.getByText('No records.').closest('tr')).toHaveClass('ui-table__empty')
    rerender(<DataTable columns={columns} rows={[{ id: 1, name: 'A', done: 1, of: 1 }]} loading loadingText="Loading history…" />)
    expect(screen.getByText('Loading history…')).toBeInTheDocument()
    expect(screen.queryByText('A')).toBeNull()
  })

  it('can stay a table on phones', () => {
    render(<DataTable columns={columns} rows={[]} cards={false} />)
    expect(screen.getByRole('table')).not.toHaveClass('ui-table--cards')
  })
})

describe('Dialog', () => {
  function Harness() {
    const [open, setOpen] = useState(false)
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>Open</button>
        <Dialog open={open} title="Good morning" onClose={() => setOpen(false)} actions={<Button variant="primary">Mark</Button>}>
          <p>Please mark your attendance.</p>
        </Dialog>
      </>
    )
  }

  it('opens labelled by its title, takes focus, and closes on Escape and on ×', async () => {
    render(<Harness />)
    const trigger = screen.getByRole('button', { name: 'Open' })
    trigger.focus()
    fireEvent.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'Good morning' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    await waitFor(() => expect(document.activeElement).toBe(trigger))
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('styles/ui.css and styles/tokens.css', () => {
  it('uses no literal colours: every colour is a token', () => {
    expect(withoutComments(UI_CSS)).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/)
  })

  it('only reads tokens that are defined', () => {
    const defined = new Set([...`${TOKENS}\n${INDEX}`.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]))
    const used = new Set([...UI_CSS.matchAll(/var\((--[\w-]+)/g)].map((m) => m[1]))
    const local = new Set(['--ui-grid-cols', '--ui-form-cols'])
    expect([...used].filter((name) => !defined.has(name) && !local.has(name))).toEqual([])
  })

  it('sets text no smaller than 11px and only from the type scale', () => {
    expect(withoutComments(UI_CSS)).not.toMatch(/font-size:\s*\d/)
    const sizes = [...TOKENS.matchAll(/--fs-[\w-]+:\s*(\d+)px/g)].map((m) => Number(m[1]))
    expect(Math.min(...sizes)).toBe(11)
  })

  it('changes layout only at the agreed breakpoints', () => {
    const widths = new Set([...UI_CSS.matchAll(/\((?:max|min)-width:\s*(\d+)px\)/g)].map((m) => m[1]))
    expect([...widths].sort()).toEqual(['599', '600', '899'])
  })

  it('keeps the dark theme: the accent is used for primary actions, focus, links and the current tab only', () => {
    const accentRules = [...withoutComments(UI_CSS).matchAll(/([^{}]+)\{[^}]*var\(--accent(?:-strong|-stronger|-text)?\)[^}]*\}/g)].map((m) => m[1].trim())
    for (const selector of accentRules) {
      expect(selector).toMatch(/ui-btn--primary|ui-btn--link|ui-input:focus-visible|ui-tabs__tab\[aria-pressed="true"\]/)
    }
  })
})

describe('Tabs and Disclosure', () => {
  it('marks the current tab pressed and reports the one chosen', () => {
    const onChange = vi.fn()
    render(<Tabs label="Sections" tabs={[{ id: 'a', label: 'First', icon: 'code' }, { id: 'b', label: 'Second' }]} value="a" onChange={onChange} />)
    const nav = screen.getByRole('navigation', { name: 'Sections' })
    const [first, second] = nav.querySelectorAll('button')
    expect(first).toHaveAttribute('aria-pressed', 'true')
    expect(first).toHaveClass('ui-tabs__tab')
    expect(second).toHaveAttribute('aria-pressed', 'false')
    expect(second.textContent).toBe('Second')
    fireEvent.click(second)
    expect(onChange).toHaveBeenCalledWith('b')
  })

  it('keeps detail behind a summary, open only when asked', () => {
    const { container } = render(<><Disclosure summary="More">Hidden detail</Disclosure><Disclosure summary="Open one" open>Shown</Disclosure></>)
    const [closed, open] = container.querySelectorAll('details.ui-disclosure')
    expect(closed.open).toBe(false)
    expect(open.open).toBe(true)
    expect(screen.getByText('More').tagName).toBe('SUMMARY')
  })
})
