/**
 * Which AI node is reading an upload, as the server reports it.
 *
 * Used by every upload an AI node reads -- the booking page's, and the
 * dashboard's payment, resume and referrer-expense uploads -- through
 * AiNodeProgress. The page never decides or guesses the node. It names the
 * upload with a fresh id before sending it, and the backend gateway -- the only
 * place that knows where the model call actually runs, including when it fails
 * over -- reports against that id. This module follows those reports while the
 * upload is in flight.
 *
 * It listens on a server-sent event stream. If the stream cannot be opened or
 * says nothing (a proxy that buffers it, a browser without EventSource), it
 * asks the same status as a plain request once a second instead. Neither is
 * load-bearing: the upload's own response carries the final answer.
 */

const STREAM_SILENCE_MS = 3000
const POLL_MS = 1000

/** 32 hex characters, the shape the server accepts. */
export function newAnalysisId() {
  const bytes = new Uint8Array(16)
  const source = globalThis.crypto
  if (source && typeof source.getRandomValues === 'function') {
    source.getRandomValues(bytes)
  } else {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  }
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/**
 * Follow one analysis. Calls `onStatus` with each status the server reports
 * and returns a function that stops listening.
 */
export function watchAnalysis(apiBase, analysisId, onStatus) {
  let stopped = false
  let source = null
  let pollTimer = null
  let silenceTimer = null

  const stop = () => {
    stopped = true
    if (source) source.close()
    source = null
    clearTimeout(pollTimer)
    clearTimeout(silenceTimer)
  }

  const deliver = (status) => {
    if (stopped || !status || typeof status.state !== 'string') return
    onStatus(status)
    if (status.state === 'done') stop()
  }

  const poll = async () => {
    if (stopped) return
    try {
      const res = await fetch(`${apiBase}/public/slots/analysis/${analysisId}`, { cache: 'no-store' })
      const data = await res.json()
      deliver(data && data.analysis)
    } catch {
      /* the upload's response still reports which node read it */
    }
    if (!stopped) pollTimer = setTimeout(poll, POLL_MS)
  }

  const pollInstead = () => {
    if (stopped) return
    if (source) source.close()
    source = null
    clearTimeout(silenceTimer)
    if (!pollTimer) poll()
  }

  if (typeof EventSource !== 'function') {
    poll()
    return stop
  }

  let heard = false
  try {
    source = new EventSource(`${apiBase}/public/slots/analysis/${analysisId}/events`)
  } catch {
    poll()
    return stop
  }
  source.onmessage = (event) => {
    heard = true
    clearTimeout(silenceTimer)
    try {
      deliver(JSON.parse(event.data))
    } catch {
      /* ignore a malformed frame; the next one replaces it */
    }
  }
  // After the first event an error is a dropped connection, which EventSource
  // reopens by itself. Before it, the stream is not getting through at all.
  source.onerror = () => {
    if (!heard) pollInstead()
  }
  silenceTimer = setTimeout(() => {
    if (!heard) pollInstead()
  }, STREAM_SILENCE_MS)
  return stop
}

/**
 * "Analysed by RTX 4060", or "Analysed by RTX 4060 + Jagadeesh" when a second
 * node took part: every node that answered, in order. Empty until the analysis
 * is done, and when no node answered at all.
 */
export function analysedByText(status) {
  if (!status || status.state !== 'done' || !Array.isArray(status.analysed_by)) return ''
  const nodes = status.analysed_by.filter(Boolean)
  return nodes.length ? `Analysed by ${nodes.join(' + ')}` : ''
}
