/**
 * Following a booking upload's AI node, as the server reports it.
 *
 * The page listens on the server's event stream, and falls back to asking once
 * a second when the stream cannot get through. Both paths are driven here with
 * the browser pieces replaced, since jsdom has no EventSource.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { analysedByText, newAnalysisId, watchAnalysis } from './aiAnalysisStatus.js'

class FakeEventSource {
  static instances = []

  constructor(url) {
    this.url = url
    this.closed = false
    FakeEventSource.instances.push(this)
  }

  emit(status) {
    this.onmessage?.({ data: JSON.stringify(status) })
  }

  fail() {
    this.onerror?.(new Event('error'))
  }

  close() {
    this.closed = true
  }
}

const reply = body => Promise.resolve({ ok: true, json: () => Promise.resolve(body) })

describe('newAnalysisId', () => {
  it('is 32 lowercase hex characters, the only shape the server accepts', () => {
    expect(newAnalysisId()).toMatch(/^[0-9a-f]{32}$/)
  })

  it('is different every time', () => {
    const ids = new Set(Array.from({ length: 50 }, newAnalysisId))
    expect(ids.size).toBe(50)
  })
})

describe('watchAnalysis over the event stream', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.stubGlobal('fetch', vi.fn(() => reply({})))
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('listens on the stream for this analysis and passes every status on', () => {
    const seen = []
    const stop = watchAnalysis('https://example.test', 'a'.repeat(32), status => seen.push(status))
    const [source] = FakeEventSource.instances
    expect(source.url).toBe(`https://example.test/public/slots/analysis/${'a'.repeat(32)}/events`)

    source.emit({ state: 'waiting', node: null, analysed_by: [] })
    source.emit({ state: 'running', node: 'Jagadeesh', analysed_by: [] })
    source.emit({ state: 'waiting', node: null, analysed_by: [] })
    source.emit({ state: 'running', node: 'Praveen', analysed_by: [] })

    expect(seen.map(s => s.node)).toEqual([null, 'Jagadeesh', null, 'Praveen'])
    expect(fetch).not.toHaveBeenCalled()
    stop()
    expect(source.closed).toBe(true)
  })

  it('stops by itself once the analysis is done', () => {
    const seen = []
    watchAnalysis('', 'b'.repeat(32), status => seen.push(status))
    const [source] = FakeEventSource.instances
    source.emit({ state: 'done', node: 'RTX 4060', analysed_by: ['RTX 4060'] })
    source.emit({ state: 'running', node: 'Jagadeesh', analysed_by: [] })
    expect(source.closed).toBe(true)
    expect(seen).toHaveLength(1)
  })

  it('reports nothing after it is stopped', () => {
    const seen = []
    const stop = watchAnalysis('', 'c'.repeat(32), status => seen.push(status))
    stop()
    FakeEventSource.instances[0].emit({ state: 'running', node: 'RTX 4060', analysed_by: [] })
    expect(seen).toEqual([])
  })

  it('asks by request instead when the stream fails before saying anything', async () => {
    fetch.mockImplementation(() => reply({ status: 'ok', analysis: { state: 'running', node: 'RTX 4060', analysed_by: [] } }))
    const seen = []
    const stop = watchAnalysis('', 'd'.repeat(32), status => seen.push(status))
    FakeEventSource.instances[0].fail()
    expect(FakeEventSource.instances[0].closed).toBe(true)
    await vi.waitFor(() => expect(seen.map(s => s.node)).toEqual(['RTX 4060']))
    expect(fetch.mock.calls[0][0]).toBe(`/public/slots/analysis/${'d'.repeat(32)}`)
    stop()
  })

  it('asks by request instead when the stream stays silent (a buffering proxy)', async () => {
    vi.useFakeTimers()
    fetch.mockImplementation(() => reply({ status: 'ok', analysis: { state: 'done', node: 'Praveen', analysed_by: ['Praveen'] } }))
    const seen = []
    watchAnalysis('', 'e'.repeat(32), status => seen.push(status))
    await vi.advanceTimersByTimeAsync(2900)
    expect(fetch).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(200)
    expect(FakeEventSource.instances[0].closed).toBe(true)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(seen.map(s => s.state)).toEqual(['done'])
    // Done ends the polling too.
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('leaves a dropped stream to reconnect once it has been heard from', async () => {
    const stop = watchAnalysis('', 'f'.repeat(32), () => {})
    const [source] = FakeEventSource.instances
    source.emit({ state: 'running', node: 'RTX 4060', analysed_by: [] })
    source.fail()
    expect(source.closed).toBe(false)
    expect(fetch).not.toHaveBeenCalled()
    stop()
  })
})

describe('watchAnalysis without EventSource', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('polls once a second until the analysis is done', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('EventSource', undefined)
    const answers = [
      { state: 'waiting', node: null, analysed_by: [] },
      { state: 'running', node: 'Jagadeesh', analysed_by: [] },
      { state: 'done', node: 'Jagadeesh', analysed_by: ['Jagadeesh'] },
    ]
    vi.stubGlobal('fetch', vi.fn(() => reply({ status: 'ok', analysis: answers.shift() })))
    const seen = []
    watchAnalysis('', '1'.repeat(32), status => seen.push(status))
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(1000)
    await vi.advanceTimersByTimeAsync(1000)
    await vi.advanceTimersByTimeAsync(3000)
    expect(seen.map(s => s.state)).toEqual(['waiting', 'running', 'done'])
    expect(fetch).toHaveBeenCalledTimes(3)
  })

  it('ignores answers that are not an analysis status', async () => {
    vi.stubGlobal('EventSource', undefined)
    vi.stubGlobal('fetch', vi.fn(() => reply({ status: 'ok', candidates: [] })))
    const seen = []
    const stop = watchAnalysis('', '2'.repeat(32), status => seen.push(status))
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled())
    stop()
    expect(seen).toEqual([])
  })
})

describe('analysedByText', () => {
  it('names the node once the analysis is done', () => {
    expect(analysedByText({ state: 'done', analysed_by: ['RTX 4060'] })).toBe('Analysed by RTX 4060')
  })

  it('names every node that answered, in order', () => {
    expect(analysedByText({ state: 'done', analysed_by: ['RTX 4060', 'Jagadeesh'] }))
      .toBe('Analysed by RTX 4060 + Jagadeesh')
  })

  it('says nothing while the analysis is still running', () => {
    expect(analysedByText({ state: 'running', node: 'RTX 4060', analysed_by: ['RTX 4060'] })).toBe('')
  })

  it('says nothing when no node answered', () => {
    expect(analysedByText({ state: 'done', analysed_by: [] })).toBe('')
    expect(analysedByText(null)).toBe('')
  })
})
