/**
 * The one AI node status every upload in Operations shows.
 *
 * Driven here on its own: the snapshots the pages hand it, the run that
 * follows the server's reports, and the hook that holds one run as state. The
 * pages that use it are covered in their own tests.
 */
import React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, renderHook, screen } from '@testing-library/react'
import AiNodeProgress, {
  analysedByLine,
  formatElapsed,
  startAiAnalysis,
  useAiAnalysis,
} from './AiNodeProgress.jsx'

class FakeEventSource {
  static instances = []

  constructor(url) {
    this.url = url
    this.closed = false
    FakeEventSource.instances.push(this)
  }

  close() {
    this.closed = true
  }
}

const stream = () => FakeEventSource.instances[FakeEventSource.instances.length - 1]
const push = (status) =>
  act(() => stream().onmessage({ data: JSON.stringify({ analysed_by: [], failed_on: [], ...status }) }))

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** A snapshot as startAiAnalysis emits it, started `seconds` ago. */
function running(status, seconds = 3) {
  return { id: 'a'.repeat(32), status, startedAt: Date.now() - seconds * 1000, finishedAt: null, outcome: null, failureLabel: '' }
}

function finished(status, { seconds = 12.3, outcome = 'success', failureLabel = '' } = {}) {
  const finishedAt = Date.now()
  return { id: 'a'.repeat(32), status, startedAt: finishedAt - seconds * 1000, finishedAt, outcome, failureLabel }
}

const done = (analysed_by, failed_on = []) => ({ state: 'done', node: analysed_by.at(-1) || null, analysed_by, failed_on })
const line = () => document.querySelector('.ai-node-progress__line').textContent
const live = () => document.querySelector('.ai-node-progress--active')

describe('the words', () => {
  it('times under a minute to a tenth of a second, then in minutes', () => {
    expect(formatElapsed(12.34)).toBe('12.3s')
    expect(formatElapsed(65)).toBe('1:05')
    expect(formatElapsed(null)).toBe('')
  })

  it('names every node that answered, once, in order', () => {
    expect(analysedByLine(['RTX 4060', 'Jagadeesh', 'RTX 4060'], 9.84)).toBe('Analysed by RTX 4060 + Jagadeesh in 9.8s')
    expect(analysedByLine(['RTX 4060'])).toBe('Analysed by RTX 4060')
    expect(analysedByLine(['RTX 4060'], 4, ['Praveen'])).toBe('Analysed by RTX 4060 in 4.0s · switched from Praveen')
    expect(analysedByLine([], 4)).toBe('')
  })
})

describe('while an upload is read', () => {
  it('shows nothing until an analysis has started', () => {
    const { container } = render(<AiNodeProgress analysis={null} />)
    expect(container.innerHTML).toBe('')
  })

  it('waits for a node, with the time so far', () => {
    render(<AiNodeProgress analysis={running({ state: 'waiting', node: null, analysed_by: [], failed_on: [] })} />)
    expect(screen.getByText('Waiting for AI node…')).toBeTruthy()
    expect(document.querySelector('.ai-node-progress__timer').textContent).toMatch(/^3\.\ds$/)
    expect(document.querySelector('[role="timer"]')).not.toBeNull()
  })

  it('names the node exactly as the server does, including one it has never seen', () => {
    render(<AiNodeProgress analysis={running({ state: 'running', node: 'RTX 5090', analysed_by: [], failed_on: [] })} />)
    expect(live().textContent).toContain('● RTX 5090 · Analysing…')
  })

  it('says a failover is one: the node that failed, then the node it moved to', () => {
    const { rerender } = render(
      <AiNodeProgress analysis={running({ state: 'waiting', node: null, analysed_by: [], failed_on: ['Praveen'] })} />,
    )
    expect(screen.getByText('Praveen failed · waiting for another AI node…')).toBeTruthy()

    rerender(<AiNodeProgress analysis={running({ state: 'running', node: 'RTX 4060', analysed_by: [], failed_on: ['Praveen'] })} />)
    expect(live().textContent).toContain('● RTX 4060 · Analysing… · switched from Praveen')
  })

  it('says what the page is doing instead once it has stopped following a node', () => {
    render(<AiNodeProgress analysis={running(null)} idle="Reading invite…" />)
    expect(live().textContent).toContain('Reading invite…')
    expect(live().textContent).not.toMatch(/waiting for ai node|analysing/i)
  })

  it('counts while it runs', async () => {
    render(<AiNodeProgress analysis={running({ state: 'waiting', node: null, analysed_by: [], failed_on: [] }, 0)} />)
    const timer = () => Number(document.querySelector('.ai-node-progress__timer').textContent.replace(/s$/, ''))
    const first = timer()
    await act(() => new Promise((resolve) => setTimeout(resolve, 350)))
    expect(timer()).toBeGreaterThan(first)
  })
})

describe('once it has been read', () => {
  it('says which node read it and how long it took, and stops counting', () => {
    render(<AiNodeProgress analysis={finished(done(['RTX 4060']))} />)
    expect(line()).toBe('✓ Analysed by RTX 4060 in 12.3s')
    expect(document.querySelector('.ai-node-progress__timer')).toBeNull()
    expect(document.querySelector('.ai-node-progress--success')).not.toBeNull()
  })

  it('keeps the failover in the finished line', () => {
    render(<AiNodeProgress analysis={finished(done(['RTX 4060'], ['Praveen']))} />)
    expect(line()).toBe('✓ Analysed by RTX 4060 in 12.3s · switched from Praveen')
  })

  it('leads a failure with what failed, and still names the node', () => {
    render(<AiNodeProgress analysis={finished(done(['Jagadeesh']), { outcome: 'failure', failureLabel: 'Not verified' })} />)
    expect(line()).toBe('✕ Not verified · Analysed by Jagadeesh in 12.3s')
    expect(document.querySelector('.ai-node-progress--failure')).not.toBeNull()
  })

  it('claims no node when none answered', () => {
    const { rerender } = render(<AiNodeProgress analysis={finished(done([]), { seconds: 2 })} finishedLabel="Invite read" />)
    expect(line()).toBe('✓ Invite read in 2.0s')

    rerender(<AiNodeProgress analysis={finished(done([], ['Praveen']), { seconds: 30, outcome: 'failure', failureLabel: 'Not saved' })} />)
    expect(line()).toBe('✕ Not saved after 30.0s · AI failed on Praveen')
  })
})

describe('a run follows the server', () => {
  it('starts waiting, follows the stream for its own id, and takes the answer as final', () => {
    const snapshots = []
    const run = startAiAnalysis('', (snapshot) => snapshots.push(snapshot))
    expect(run.id).toMatch(/^[0-9a-f]{32}$/)
    expect(stream().url).toBe(`/public/slots/analysis/${run.id}/events`)
    expect(snapshots.at(-1).status.state).toBe('waiting')

    push({ state: 'running', node: 'Jagadeesh' })
    expect(snapshots.at(-1).status.node).toBe('Jagadeesh')

    run.finish(done(['RTX 4060']), { ok: true })
    const last = snapshots.at(-1)
    expect(last.outcome).toBe('success')
    expect(last.status.analysed_by).toEqual(['RTX 4060'])
    expect(last.finishedAt).toBeGreaterThanOrEqual(last.startedAt)
    expect(stream().closed).toBe(true)

    // Nothing after the answer changes it.
    const count = snapshots.length
    run.finish(done(['Praveen']), { ok: false })
    expect(snapshots).toHaveLength(count)
  })

  it('names no node when the upload ended without the server saying', () => {
    const snapshots = []
    const run = startAiAnalysis('', (snapshot) => snapshots.push(snapshot))
    push({ state: 'running', node: 'RTX 4060' })
    run.finish(undefined, { ok: false, failureLabel: 'Upload failed' })
    expect(snapshots.at(-1).status.analysed_by).toEqual([])
    expect(snapshots.at(-1).failureLabel).toBe('Upload failed')
  })

  it('reports nothing more once cancelled', () => {
    const snapshots = []
    const run = startAiAnalysis('', (snapshot) => snapshots.push(snapshot))
    run.cancel()
    expect(stream().closed).toBe(true)
    run.finish(done(['RTX 4060']))
    expect(snapshots).toHaveLength(1)
  })

  it('keeps counting from an earlier start for a second request of the same read', () => {
    const startedAt = Date.now() - 5000
    const run = startAiAnalysis('', () => {}, { startedAt })
    expect(run.startedAt).toBe(startedAt)
  })
})

describe('the hook holds one run at a time', () => {
  it('abandons the first run when a second begins', () => {
    const { result } = renderHook(() => useAiAnalysis(''))
    let first
    let second
    act(() => { first = result.current.begin() })
    act(() => { second = result.current.begin() })
    expect(second.id).not.toBe(first.id)

    // The first upload answering late does not overwrite the second's status.
    act(() => first.finish(done(['Praveen'])))
    expect(result.current.analysis.id).toBe(second.id)
    expect(result.current.analysis.finishedAt).toBeNull()

    act(() => second.finish(done(['RTX 4060'])))
    expect(result.current.analysis.status.analysed_by).toEqual(['RTX 4060'])

    act(() => result.current.reset())
    expect(result.current.analysis).toBeNull()
  })
})
