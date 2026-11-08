/**
 * 9. GENUINE SELECTION ALARM — a 30-second rising brass alarm.
 *
 * Identity: a fast square-wave C-major run that resolves on a held chord,
 * alternating with a two-tone siren, repeated for thirty seconds. It is built
 * to be heard across an office, not at a desk: square waves, a compressor to
 * keep the peaks loud without clipping, and no fade until the very end.
 *
 * It is not a variant of selectionFanfare.js (a single 1.4 s sawtooth flourish)
 * and shares no generator with it. The fanfare still plays for the selection
 * classifications that are not good news (offer declined / revoked).
 */

import { getSharedAudioContext, releaseNodes, sustain } from '../soundEngine.js'

export const ALARM_SECONDS = 30
const PHRASE = 2.0 // seconds per repeat of the pattern
const RUN = [523.25, 659.25, 783.99, 1046.5] // C5 E5 G5 C6
const CHORD = [523.25, 659.25, 783.99, 1046.5]
const SIREN = [880, 1174.66] // A5 / D6

let active = null // { nodes, master }

function clampVolume(volume) {
  const v = Number(volume)
  return Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 1
}

/** Linear 0..1 slider to a gain that sounds even across the range. */
export function alarmGain(volume) {
  const v = clampVolume(volume)
  return v === 0 ? 0.0001 : Math.max(0.0001, v * v)
}

function schedulePhrase(ctx, dest, at, nodes) {
  // Quick run up the triad...
  RUN.forEach((freq, i) => {
    nodes.push(sustain(ctx, dest, { type: 'square', freq, start: at + i * 0.09, duration: 0.12, peak: 0.55, attack: 0.008 }))
  })
  // ...a held chord...
  const chordAt = at + RUN.length * 0.09
  for (const freq of CHORD) {
    nodes.push(sustain(ctx, dest, { type: 'sawtooth', freq, start: chordAt, duration: 0.6, peak: 0.4, attack: 0.02 }))
  }
  // ...then a two-tone siren to fill the rest of the phrase.
  const sirenAt = chordAt + 0.65
  for (let i = 0; i < 4; i += 1) {
    nodes.push(sustain(ctx, dest, { type: 'square', freq: SIREN[i % 2], start: sirenAt + i * 0.22, duration: 0.2, peak: 0.6, attack: 0.01 }))
  }
}

/**
 * Start the 30-second alarm at `volume` (0..1). Stops any alarm already
 * sounding first, so two can never overlap. Returns false without audio.
 */
export function startSelectionAlarm(volume = 1, seconds = ALARM_SECONDS) {
  stopSelectionAlarm()
  const ctx = getSharedAudioContext()
  if (!ctx) return false
  const build = () => {
    try {
      const t0 = ctx.currentTime + 0.02
      const nodes = []
      const master = ctx.createGain()
      master.gain.setValueAtTime(alarmGain(volume), t0)
      const comp = ctx.createDynamicsCompressor ? ctx.createDynamicsCompressor() : null
      if (comp) {
        comp.threshold?.setValueAtTime?.(-18, t0)
        comp.ratio?.setValueAtTime?.(6, t0)
        master.connect(comp)
        comp.connect(ctx.destination)
        nodes.push(comp)
      } else {
        master.connect(ctx.destination)
      }
      nodes.push(master)
      const phrases = Math.max(1, Math.round(seconds / PHRASE))
      for (let p = 0; p < phrases; p += 1) schedulePhrase(ctx, master, t0 + p * PHRASE, nodes)
      active = { nodes, master, ctx }
    } catch {
      /* audio is best-effort and must never break a render */
    }
  }
  if (ctx.state === 'suspended') ctx.resume().then(build).catch(() => {})
  else build()
  return true
}

/** Change the loudness of an alarm that is already sounding. */
export function setSelectionAlarmVolume(volume) {
  if (!active) return
  try {
    const { master, ctx } = active
    master.gain.cancelScheduledValues?.(ctx.currentTime)
    master.gain.setValueAtTime(alarmGain(volume), ctx.currentTime)
  } catch {
    /* ignore */
  }
}

/** Silence the alarm now. Safe to call when nothing is playing. */
export function stopSelectionAlarm() {
  if (!active) return
  const { nodes, ctx } = active
  active = null
  releaseNodes(nodes, ctx?.currentTime || 0)
}

export function isSelectionAlarmSounding() {
  return active !== null
}
