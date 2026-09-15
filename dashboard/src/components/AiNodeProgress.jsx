import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Spinner } from '../Loader.jsx'
import { newAnalysisId, watchAnalysis } from './aiAnalysisStatus.js'
import './AiNodeProgress.css'

/**
 * The one status for an upload an AI node is reading, everywhere in Operations.
 *
 * The booking page's payment and invite, a candidate's payment screenshot and
 * its replacement, a resume, and a referrer expense screenshot run the same
 * Ollama work, and each used to describe it differently -- "Processing
 * screenshot…", "AI analyzing (~30-60s)…", rotating "Verifying details…" -- with
 * only the booking page naming the node. They all show this instead:
 *
 *   while it runs   Waiting for AI node…                          3.1s
 *                   ● RTX 4060 · Analysing…                       8.4s
 *                   ● RTX 4060 · Analysing… · switched from Praveen
 *   when it ends    ✓ Analysed by RTX 4060 in 12.3s
 *                   ✕ Not verified · Analysed by RTX 4060 in 12.3s
 *
 * Every node named comes from the server. The page sends an analysis id with
 * the upload, follows it on the status stream while the upload is in flight,
 * and takes the upload's own response as the final word; an upload that ends
 * without one claims no node at all. The timer is the page's own measurement
 * of the wait, from sending the upload to its answer, and it stops there.
 */

/** Elapsed time: "12.3s" under a minute, then "m:ss". */
export function formatElapsed(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return ''
  const s = Math.max(0, seconds)
  if (s < 60) return `${s.toFixed(1)}s`
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

const nodeNames = (list) => [...new Set((Array.isArray(list) ? list : []).filter(Boolean))]

/**
 * "Analysed by RTX 4060 + Jagadeesh in 12.3s", every node that answered in
 * order; without a time, only the nodes. `failedOn` adds the nodes the request
 * moved away from: "… · switched from Praveen". Empty when no node answered.
 */
export function analysedByLine(nodes, seconds, failedOn) {
  const answered = nodeNames(nodes)
  if (!answered.length) return ''
  const time = formatElapsed(seconds)
  const left = nodeNames(failedOn)
  return `Analysed by ${answered.join(' + ')}${time ? ` in ${time}` : ''}` +
    (left.length ? ` · switched from ${left.join(' + ')}` : '')
}

/** Seconds an analysis has run, or ran; null before it starts. */
export function analysisSeconds(analysis, now = Date.now()) {
  if (!analysis?.startedAt) return null
  const end = analysis.finishedAt ?? Math.max(now, analysis.startedAt)
  return (end - analysis.startedAt) / 1000
}

const WAITING = Object.freeze({ state: 'waiting', node: null, analysed_by: [], failed_on: [] })
const NO_NODE = Object.freeze({ state: 'done', node: null, analysed_by: [], failed_on: [] })

/**
 * Start following one upload's analysis.
 *
 * `onChange` receives a snapshot -- { id, status, startedAt, finishedAt,
 * outcome, failureLabel } -- each time something changes. Send `run.id` with
 * the upload as `analysis_id`. Pass `id` to follow an id already sent, and
 * `startedAt` to keep counting from an earlier start (a second request for the
 * same read).
 *
 *   run.finish(response.analysis, { ok, failureLabel })   the upload answered
 *   run.detach()   AI is over but the page is still working without it
 *   run.cancel()   abandoned; nothing more is reported
 */
export function startAiAnalysis(apiBase, onChange, { id = newAnalysisId(), startedAt = Date.now() } = {}) {
  let snapshot = { id, status: WAITING, startedAt, finishedAt: null, outcome: null, failureLabel: '' }
  let over = false
  let stopWatch = null
  const emit = (patch) => {
    if (over) return
    snapshot = { ...snapshot, ...patch }
    onChange(snapshot)
  }
  const stop = () => {
    stopWatch?.()
    stopWatch = null
  }
  onChange(snapshot)
  stopWatch = watchAnalysis(apiBase, id, (status) => emit({ status }))
  return {
    id,
    startedAt,
    finish(serverAnalysis, { ok = true, failureLabel = '' } = {}) {
      stop()
      const status = serverAnalysis && serverAnalysis.state === 'done' ? serverAnalysis : NO_NODE
      emit({ status, finishedAt: Date.now(), outcome: ok ? 'success' : 'failure', failureLabel })
      over = true
    },
    detach() {
      stop()
      emit({ status: null })
    },
    cancel() {
      stop()
      over = true
    },
  }
}

/**
 * One analysis at a time, as component state. `begin()` returns the run; a
 * second `begin()` abandons the first, and `reset()` clears the status away.
 */
export function useAiAnalysis(apiBase) {
  const [analysis, setAnalysis] = useState(null)
  const runRef = useRef(null)
  const tokenRef = useRef(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      runRef.current?.cancel()
    }
  }, [])

  const begin = useCallback((options) => {
    runRef.current?.cancel()
    const token = {}
    tokenRef.current = token
    const run = startAiAnalysis(apiBase, (snapshot) => {
      if (mountedRef.current && tokenRef.current === token) setAnalysis(snapshot)
    }, options)
    runRef.current = run
    return run
  }, [apiBase])

  const reset = useCallback(() => {
    runRef.current?.cancel()
    runRef.current = null
    tokenRef.current = null
    setAnalysis(null)
  }, [])

  return { analysis, begin, reset }
}

/** Re-render ten times a second while `active`, for the running timer. */
function useNow(active) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return undefined
    setNow(Date.now())
    const timer = setInterval(() => setNow(Date.now()), 100)
    return () => clearInterval(timer)
  }, [active])
  return now
}

/**
 * The status line for one analysis snapshot, from `useAiAnalysis` or
 * `startAiAnalysis`. Renders nothing until an analysis has started.
 *
 * `idle` is said while the upload is in flight but no node wait applies -- the
 * page has stopped following AI and is reading another way. `finishedLabel`
 * ends a read no node took part in ("Invite read in 2.1s"), and a failed run
 * leads with its `failureLabel` ("Not verified").
 */
export default function AiNodeProgress({ analysis, idle = 'Analysing…', finishedLabel = 'Finished', className = '' }) {
  const active = Boolean(analysis?.startedAt && !analysis?.finishedAt)
  const now = useNow(active)
  if (!analysis?.startedAt) return null

  const status = analysis.status
  const time = formatElapsed(analysisSeconds(analysis, now))
  const failed = nodeNames(status?.failed_on)
  const tone = active ? 'active' : analysis.outcome === 'failure' ? 'failure' : 'success'
  const classes = ['ai-node-progress', `ai-node-progress--${tone}`, className].filter(Boolean).join(' ')

  if (active) {
    const node = status && status.state !== 'waiting' ? status.node : null
    let label
    if (node) {
      label = (
        <>
          <span className="ai-node-progress__dot" aria-hidden="true">●</span> {node} · Analysing…
          {failed.length > 0 && (
            <span className="ai-node-progress__failover"> · switched from {failed.join(' + ')}</span>
          )}
        </>
      )
    } else if (status && status.state !== 'done') {
      label = failed.length ? `${failed.join(' + ')} failed · waiting for another AI node…` : 'Waiting for AI node…'
    } else {
      label = idle
    }
    return (
      <div className={classes}>
        <Spinner size={16} className="ai-node-progress__spinner" />
        <span className="ai-node-progress__label" role="status" aria-live="polite">{label}</span>
        <span className="ai-node-progress__timer" role="timer">{time}</span>
      </div>
    )
  }

  const answered = status?.state === 'done' ? nodeNames(status.analysed_by) : []
  const failure = tone === 'failure'
  const failureLabel = analysis.failureLabel || 'Failed'
  let line
  if (answered.length) {
    line = `${failure ? `✕ ${failureLabel} · ` : '✓ '}${analysedByLine(answered, analysisSeconds(analysis), failed)}`
  } else {
    line = failure ? `✕ ${failureLabel} after ${time}` : `✓ ${finishedLabel} in ${time}`
    if (failed.length) line += ` · AI failed on ${failed.join(' + ')}`
  }
  return (
    <div className={classes} role="status">
      <span className="ai-node-progress__line">{line}</span>
    </div>
  )
}
