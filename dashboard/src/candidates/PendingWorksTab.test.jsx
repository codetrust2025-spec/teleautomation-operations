/**
 * The Pending Works tab, and the badge it explains.
 *
 * The Candidates badge counted work items and was rendered on Daily Ops, while
 * the Candidates page never asked for the data at all — so the number had
 * nowhere to land and did not mean what its label implied. The badge now counts
 * candidates, and this tab itemises them on the page that names it.
 *
 * The eight tasks below are the eight production is holding: seven candidates
 * missing a resume and one missing a phone number.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import { PendingWorksTab } from './PendingWorksTab.jsx'

const HERE = dirname(fileURLToPath(import.meta.url))

const work = (name, kind, priority, technology = 'DevOps') => ({
  id: `${kind}:${name}`,
  kind,
  label: kind === 'missing_resume' ? 'Upload resume' : 'Add phone number',
  priority,
  candidate_id: `id-${name}`,
  candidate_name: name,
  technology,
})

/** Exactly what production returns today. */
const LIVE = {
  status: 'ok',
  count: 8,
  candidate_count: 8,
  by_kind: { missing_resume: 7, missing_phone: 1 },
  works: [
    work('Naveen Prakash', 'missing_resume', 20),
    work('Deepa Shetty', 'missing_resume', 20),
    work('konduru Sai Srinivas', 'missing_resume', 20),
    work('lavanya', 'missing_resume', 20),
    work('Arun Kumar Pillai', 'missing_resume', 20),
    work('ANJALI ADHIKARI', 'missing_resume', 20),
    work('Vekateshwarlu Penugonda', 'missing_resume', 20),
    work('Ravali', 'missing_phone', 50),
  ],
}


/** The summary line, whitespace-normalised: it is built from several nodes. */
function summaryText() {
  const el = document.querySelector('.cand-pending__summary')
  return (el?.textContent || '').replace(/\s+/g, ' ').trim()
}

function stub(payload, { ok = true } = {}) {
  const spy = vi.fn(() => Promise.resolve({ ok, json: () => Promise.resolve(payload) }))
  vi.stubGlobal('fetch', spy)
  return spy
}

beforeEach(() => { sessionStorage.clear() })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('where the numbers come from', () => {
  it('asks the existing endpoint for the whole pipeline', async () => {
    const spy = stub(LIVE)
    render(<PendingWorksTab />)
    await waitFor(() => expect(spy).toHaveBeenCalled())
    const url = String(spy.mock.calls[0][0])
    expect(url).toContain('/candidates/pending-works')
    expect(url).toContain('month=all')
  })

  it('reports the payload totals rather than counting rows itself', async () => {
    // count and candidate_count deliberately disagree with the rows here: if
    // the header recomputed them it would show 2, not the server's figures.
    stub({ ...LIVE, count: 41, candidate_count: 33, works: LIVE.works.slice(0, 2) })
    render(<PendingWorksTab />)
    await waitFor(() => expect(screen.getByText(/33/)).toBeInTheDocument())
    expect(screen.getByText(/41/)).toBeInTheDocument()
  })

  it('separates candidates needing attention from pending tasks', async () => {
    stub(LIVE)
    render(<PendingWorksTab />)
    await waitFor(() =>
      expect(screen.getByText(/candidates need attention/)).toBeInTheDocument())
    expect(screen.getByText(/pending tasks/)).toBeInTheDocument()
    const summary = screen.getByText(/candidates need attention/).textContent
    expect(summary.replace(/\s+/g, ' ')).toContain('8 candidates need attention · 8 pending tasks')
  })

  it('says it in the singular for one of each', async () => {
    stub({ ...LIVE, count: 1, candidate_count: 1, works: [LIVE.works[7]] })
    render(<PendingWorksTab />)
    await waitFor(() => expect(summaryText()).toContain('candidate needs attention'))
    expect(summaryText()).toContain('1 pending task')
  })
})

describe('what each row shows', () => {
  it('lists every candidate with a gap', async () => {
    stub(LIVE)
    render(<PendingWorksTab />)
    await waitFor(() => expect(screen.getByText('Ravali')).toBeInTheDocument())
    for (const name of ['Naveen Prakash', 'lavanya', 'ANJALI ADHIKARI']) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
  })

  it('shows the technology, the missing item and a priority', async () => {
    stub(LIVE)
    render(<PendingWorksTab />)
    await waitFor(() => expect(screen.getAllByText('Upload resume').length).toBe(7))
    expect(screen.getByText('Add phone number')).toBeInTheDocument()
    expect(screen.getAllByText('DevOps').length).toBeGreaterThan(0)
    expect(screen.getAllByText('High').length).toBe(7)
    expect(screen.getByText('Low')).toBeInTheDocument()
  })

  it('keeps the raw priority available rather than only a band', async () => {
    stub(LIVE)
    render(<PendingWorksTab />)
    await waitFor(() => expect(screen.getByText('Low')).toBeInTheDocument())
    expect(screen.getByText('Low')).toHaveAttribute('title', 'Priority 50')
  })

  it('offers the action that matches the gap', async () => {
    stub(LIVE)
    render(<PendingWorksTab />)
    await waitFor(() => expect(screen.getAllByText('Upload Resume').length).toBe(7))
    expect(screen.getByText('Add Phone')).toBeInTheDocument()
  })

  it('falls back to editing the candidate for any other gap', async () => {
    stub({
      ...LIVE, count: 1, candidate_count: 1,
      works: [{ ...work('Ada', 'missing_reference', 10), label: 'Assign referrer' }],
    })
    render(<PendingWorksTab />)
    await waitFor(() => expect(screen.getByText('Edit Candidate')).toBeInTheDocument())
  })
})

describe('grouping', () => {
  it('puts several gaps under one candidate', async () => {
    stub({
      ...LIVE, count: 2, candidate_count: 1,
      works: [work('Ravali', 'missing_resume', 20), work('Ravali', 'missing_phone', 50)],
    })
    render(<PendingWorksTab />)
    await waitFor(() => expect(screen.getByText('Ravali')).toBeInTheDocument())
    // Named once, with both tasks listed under it.
    expect(screen.getAllByText('Ravali')).toHaveLength(1)
    expect(screen.getByText('2 tasks')).toBeInTheDocument()
    expect(screen.getByText('Upload resume')).toBeInTheDocument()
    expect(screen.getByText('Add phone number')).toBeInTheDocument()
  })

  it('does not label a single-task candidate with a count', async () => {
    stub(LIVE)
    render(<PendingWorksTab />)
    await waitFor(() => expect(screen.getByText('Ravali')).toBeInTheDocument())
    expect(screen.queryByText('1 tasks')).toBeNull()
  })

  it('brings the most urgent candidate first', async () => {
    stub(LIVE)
    render(<PendingWorksTab />)
    await waitFor(() => expect(screen.getByText('Ravali')).toBeInTheDocument())
    const names = screen.getAllByText(/Naveen Prakash|Ravali/).map(n => n.textContent)
    // Ravali is the only Low priority, so it sorts last.
    expect(names[names.length - 1]).toBe('Ravali')
  })
})

describe('acting on a task', () => {
  it('stashes the intent the page already knows how to open', async () => {
    stub(LIVE)
    const onOpen = vi.fn()
    render(<PendingWorksTab onOpenCandidate={onOpen} />)
    await waitFor(() => expect(screen.getByText('Add Phone')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Add Phone'))
    expect(onOpen).toHaveBeenCalled()
    const intent = JSON.parse(sessionStorage.getItem('cand-open-pending'))
    expect(intent.candidate_name).toBe('Ravali')
    expect(intent.kind).toBe('missing_phone')
  })
})

describe('keeping the counts current', () => {
  it('re-reads when attention returns to the tab', async () => {
    stub(LIVE)
    render(<PendingWorksTab />)
    await waitFor(() => expect(summaryText()).toContain('8 pending tasks'))
    stub({ ...LIVE, count: 7, candidate_count: 7, works: LIVE.works.slice(0, 7) })
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    await waitFor(() => expect(summaryText()).toContain('7 pending tasks'))
  })

  it('re-reads when the roster announces a change', async () => {
    stub(LIVE)
    render(<PendingWorksTab />)
    await waitFor(() => expect(summaryText()).toContain('8 pending tasks'))
    stub({ ...LIVE, count: 0, candidate_count: 0, works: [] })
    await act(async () => {
      window.dispatchEvent(new CustomEvent('teleautomation:pending-work-changed'))
    })
    await waitFor(() => expect(screen.getByText('No pending work')).toBeInTheDocument())
  })

  it('can be refreshed by hand', async () => {
    const first = stub(LIVE)
    render(<PendingWorksTab />)
    await waitFor(() => expect(first).toHaveBeenCalled())
    const again = stub({ ...LIVE, count: 3, candidate_count: 3, works: LIVE.works.slice(0, 3) })
    fireEvent.click(screen.getByText('Refresh'))
    await waitFor(() => expect(again).toHaveBeenCalled())
  })
})

describe('when there is nothing to do', () => {
  it('says so instead of showing an empty table', async () => {
    stub({ status: 'ok', count: 0, candidate_count: 0, works: [] })
    render(<PendingWorksTab />)
    await waitFor(() => expect(screen.getByText('No pending work')).toBeInTheDocument())
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('reports a failed read rather than claiming nothing is pending', async () => {
    stub({ status: 'error', message: 'nope' }, { ok: false })
    render(<PendingWorksTab />)
    await waitFor(() => expect(screen.getByText('nope')).toBeInTheDocument())
    expect(screen.queryByText('No pending work')).toBeNull()
  })
})

describe('how it is wired in', () => {
  const module = readFileSync(resolve(HERE, 'candidatesModule.jsx'), 'utf8')
  const app = readFileSync(resolve(HERE, '..', 'App.jsx'), 'utf8')

  it('adds the tab beside the existing three', () => {
    expect(module).toContain('Pending Works')
    expect(module.indexOf('Earnings')).toBeLessThan(module.indexOf('Pending Works'))
    expect(module).toContain('setCandTab("pending")')
  })

  it('renders the tab body', () => {
    expect(module).toContain('{candTab === "pending" && (')
    expect(module).toContain('<PendingWorksTab')
  })

  it('leaves the other tabs alone', () => {
    for (const tab of ['candTab === "overview"', 'candTab === "performers"',
                       'candTab === "candidates"']) {
      expect(module).toContain(tab)
    }
  })

  it('switches the sidebar badge to candidates, not tasks', () => {
    expect(app).toContain('pending?.candidateCount || 0')
    expect(app).not.toContain('pending?.count || 0')
  })

  it('says what the badge counts', () => {
    expect(app).toContain('need attention')
  })

  it('still hides the badge at zero', () => {
    expect(app).toContain('badgeValue > 0 &&')
  })

  it('does not disturb the other badges', () => {
    expect(app).toContain("item.badge === 'slots'")
    expect(app).toContain("item.badge === 'gmail-expired'")
    expect(app).toContain("item.badge === 'mail'")
    expect(app).toContain('pending?.pendingInterviewCount || 0')
  })
})
