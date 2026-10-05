import React from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../context/ConfirmContext.jsx'
import { DataRoomInterviewDataTab } from './DataRoomInterviewDataTab.jsx'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.body.style.overflow = ''
})

const RECORDS = [
  {
    id: 'r1', candidate: 'Asha Verma', production_candidate: 'Asha V', category: 'interview',
    event_date: '2026-06-26', summary: 'L1 interview attended', review_status: 'recorded',
    source_file: 'WhatsApp Chat - Example.zip/_chat.txt', source_timestamp: '26/06/26 18:02', source_ref: 'line 120',
  },
  {
    id: 'r2', candidate: 'Ravi Kumar', category: 'interview', event_date: '2026-09-02',
    summary: 'Two different times given for one interview', review_status: 'conflict',
    review_reason: '11 PM and 12 PM both appear', source_file: 'chat.txt',
  },
  {
    id: 'r3', candidate: 'Ravi Kumar', category: 'payment', event_date: '2026-08-04',
    summary: 'Balance said to be pending', review_status: 'needs_review', source_file: 'chat.txt',
  },
]

function renderTab(records = RECORDS) {
  return render(
    <ConfirmProvider>
      <DataRoomInterviewDataTab records={records} onReload={() => {}} />
    </ConfirmProvider>,
  )
}

describe('Data Room Interview Data tab', () => {
  it('counts records, flagged items and distinct candidates', () => {
    renderTab()
    const stat = (title) => screen.getByText(title, { selector: '.dr-tab-stat-title' })
      .closest('.dr-tab-stat').querySelector('.dr-tab-stat-value').textContent
    expect(stat('Records')).toBe('3')
    expect(stat('Needs review')).toBe('2')
    expect(stat('Candidates')).toBe('2')
  })

  it('shows every record with its source, newest first', () => {
    renderTab()
    const rows = screen.getAllByRole('row').slice(1)
    expect(rows.map(r => within(r).getAllByRole('cell')[0].textContent)).toEqual(['2026-09-02', '2026-08-04', '2026-06-26'])
    const first = rows[2]
    expect(within(first).getByText('WhatsApp Chat - Example.zip/_chat.txt')).toBeInTheDocument()
    expect(within(first).getByText('26/06/26 18:02')).toBeInTheDocument()
    expect(within(first).getByText('Production: Asha V')).toBeInTheDocument()
  })

  it('marks conflicts and review items, with the reason', () => {
    renderTab()
    expect(screen.getByText('Conflict', { selector: '.dr-interview-status' })).toHaveClass('dr-interview-status--conflict')
    expect(screen.getByText('11 PM and 12 PM both appear')).toBeInTheDocument()
    expect(screen.getByText('Needs review', { selector: '.dr-interview-status' })).toHaveClass('dr-interview-status--needs_review')
  })

  it('filters to what needs attention', () => {
    renderTab()
    fireEvent.change(screen.getByLabelText('Filter by review status'), { target: { value: 'attention' } })
    const rows = screen.getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(screen.queryByText('L1 interview attended')).not.toBeInTheDocument()
  })

  it('saves a new record to the interview_data vault section with an id', async () => {
    const calls = []
    vi.stubGlobal('fetch', async (url, init) => {
      calls.push({ url: String(url), method: init.method, body: JSON.parse(init.body) })
      return { ok: true, json: async () => ({ status: 'ok' }) }
    })
    renderTab([])
    fireEvent.click(screen.getByRole('button', { name: '+ Add record' }))
    const dialog = screen.getByRole('dialog', { name: 'Add interview record' })
    const input = (label) => within(dialog).getByText(label).closest('label').querySelector('input, textarea, select')
    fireEvent.change(input('Candidate (as in source)'), { target: { value: 'Asha Verma' } })
    fireEvent.change(input('Summary'), { target: { value: 'Interview rescheduled' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await new Promise(resolve => setTimeout(resolve, 0))

    expect(calls).toHaveLength(1)
    expect(calls[0].method).toBe('POST')
    expect(calls[0].url).toContain('/data-room/credentials/vault/interview_data')
    expect(calls[0].body.id).toMatch(/^asha_verma_/)
    expect(calls[0].body.review_status).toBe('recorded')
  })

  it('refuses a record with no summary', async () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    renderTab([])
    fireEvent.click(screen.getByRole('button', { name: '+ Add record' }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('A summary is required.')).toBeInTheDocument()
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})
