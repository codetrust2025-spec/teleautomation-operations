import { beforeEach, describe, expect, it } from 'vitest'
import { installRecordingAudioStub } from '../audioTestStub.js'
import { alarmGain, isSelectionAlarmSounding, startSelectionAlarm, stopSelectionAlarm } from './selectionAlarm.js'

let audio
beforeEach(() => {
  audio = installRecordingAudioStub()
  stopSelectionAlarm()
})

describe('genuine selection alarm sound', () => {
  it('fills thirty seconds with loud square-wave phrases', async () => {
    expect(startSelectionAlarm(1, 30)).toBe(true)
    await Promise.resolve()
    const { starts, oscillatorTypes } = audio.record
    expect(oscillatorTypes).toContain('square')
    const span = Math.max(...starts) - Math.min(...starts)
    expect(span).toBeGreaterThan(27)
    expect(span).toBeLessThan(30)
    expect(isSelectionAlarmSounding()).toBe(true)
  })

  it('never overlaps itself: starting again replaces the running alarm', async () => {
    startSelectionAlarm(1, 30)
    await Promise.resolve()
    const first = audio.record.oscillators
    startSelectionAlarm(1, 30)
    await Promise.resolve()
    expect(audio.record.oscillators).toBe(first * 2)
    stopSelectionAlarm()
    expect(isSelectionAlarmSounding()).toBe(false)
  })

  it('maps the volume slider onto gain, with zero silent', () => {
    expect(alarmGain(1)).toBe(1)
    expect(alarmGain(0.5)).toBeCloseTo(0.25)
    expect(alarmGain(0)).toBeLessThan(0.001)
    expect(alarmGain('junk')).toBe(1)
  })
})
