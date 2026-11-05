/**
 * One row-click Edit for every editable table (Daily Ops design, shared 5 Nov).
 */
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useRowEdit, ROW_EDIT_CLASS } from './rowEdit.js'
import { ConfirmProvider } from '../context/ConfirmContext.jsx'
import { DataRoomPromptsTab } from '../components/DataRoomPromptsTab.jsx'
import { editRow } from '../test/rowEdit.js'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.getSelection()?.removeAllRanges()
})

function Table({ onOpen }) {
  const rowProps = useRowEdit(onOpen)
  return (
    <table><tbody>
      <tr className={ROW_EDIT_CLASS} {...rowProps({ id: 'a' }, 'Alpha')}>
        <td><span>Alpha text</span></td>
        <td><button type="button">Copy</button><a href="#x">Open</a><select aria-label="Pick"><option>1</option></select></td>
      </tr>
      <tr {...rowProps({ id: 'b' }, 'Beta', false)}><td>Beta</td></tr>
    </tbody></table>
  )
}

describe('useRowEdit', () => {
  it('opens on a click on the row or its plain text, and on Enter or Space', () => {
    const onOpen = vi.fn()
    render(<Table onOpen={onOpen} />)
    fireEvent.click(screen.getByText('Alpha text'))
    fireEvent.click(editRow('Alpha'))
    fireEvent.keyDown(editRow('Alpha'), { key: 'Enter' })
    fireEvent.keyDown(editRow('Alpha'), { key: ' ' })
    expect(onOpen).toHaveBeenCalledTimes(4)
    expect(onOpen).toHaveBeenLastCalledWith({ id: 'a' })
  })

  it('leaves controls inside the row, keys aimed at them, and a text selection alone', () => {
    const onOpen = vi.fn()
    render(<Table onOpen={onOpen} />)
    const row = editRow('Alpha')
    fireEvent.click(within(row).getByRole('button', { name: 'Copy' }))
    fireEvent.click(within(row).getByRole('link', { name: 'Open' }))
    fireEvent.click(within(row).getByRole('combobox', { name: 'Pick' }))
    fireEvent.keyDown(within(row).getByRole('button', { name: 'Copy' }), { key: 'Enter' })
    const range = document.createRange()
    range.selectNodeContents(screen.getByText('Alpha text'))
    window.getSelection().addRange(range)
    fireEvent.click(screen.getByText('Alpha text'))
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('gives a row that cannot be edited nothing: no focus stop, no dialog', () => {
    const onOpen = vi.fn()
    render(<Table onOpen={onOpen} />)
    const beta = screen.getByText('Beta').closest('tr')
    expect(beta).not.toHaveAttribute('tabindex')
    fireEvent.click(beta)
    expect(onOpen).not.toHaveBeenCalled()
  })
})

describe('the Data Room Prompts table', () => {
  it('opens a prompt from its row, keeps Copy on the row, and deletes from the dialog', async () => {
    const calls = []
    vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
      calls.push({ url: String(url), method: init.method || 'GET' })
      return { ok: true, json: async () => ({ status: 'ok' }) }
    }))
    const onReload = vi.fn()
    render(
      <ConfirmProvider>
        <DataRoomPromptsTab prompts={[{ id: 'p1', title: 'Greeting', source: 'ops', body: 'Hello there' }]} onReload={onReload} />
      </ConfirmProvider>,
    )
    const row = editRow('Greeting')
    expect(within(row).queryByRole('button', { name: /^(Edit|Delete)$/ })).toBeNull()
    expect(within(row).getByRole('button', { name: 'Copy' })).toBeInTheDocument()
    fireEvent.click(row)
    const dialog = screen.getByRole('dialog', { name: 'Edit prompt' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))
    const confirmDelete = await waitFor(() => {
      const all = screen.getAllByRole('button', { name: 'Delete', hidden: true })
      if (all.length < 2) throw new Error('confirm not open')
      return all[all.length - 1]
    })
    fireEvent.click(confirmDelete)
    await waitFor(() => expect(onReload).toHaveBeenCalled())
    expect(calls.filter((c) => c.method === 'DELETE').map((c) => c.url)).toEqual([expect.stringMatching(/\/vault\/prompts\/p1$/)])
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Edit prompt' })).toBeNull())
  })
})
