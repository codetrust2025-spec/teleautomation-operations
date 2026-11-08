import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const sound = vi.hoisted(() => ({ start: vi.fn(() => true), stop: vi.fn(), volume: vi.fn() }))
vi.mock('./sounds/selectionAlarm.js', () => ({
  ALARM_SECONDS: 30,
  startSelectionAlarm: sound.start,
  stopSelectionAlarm: sound.stop,
  setSelectionAlarmVolume: sound.volume,
}))
const other = vi.hoisted(() => ({ play: vi.fn(() => true) }))
vi.mock('./notificationSounds.js', () => ({ playNotification: other.play }))

import { INTERVIEW_BOOKING_CLASSIFICATIONS, SELECTION_CLASSIFICATIONS } from '../utils/mailAlertSound.js'
import { __resetNotificationEvents, notifyTrackedMail } from './notificationEvents.js'
import {
  GENUINE_SELECTION_CLASSIFICATIONS, PENDING_KEY, REPEAT_MS,
  __resetSelectionAlarm, acknowledgeSelectionAlarm, getSelectionAlarmState, isGenuineSelection,
  raiseSelectionAlarm, setSelectionAlarmMuted, setSelectionAlarmVolumeLevel, stopSelectionAlarmSound,
  subscribeSelectionAlarm,
} from './selectionAlarm.js'

const offer = (id = 'n-1', extra = {}) => ({
  event: 'notification_created', classification: 'offer_received', notification_id: id,
  candidate_name: 'Asha Example', company_name: 'Example Corp', status: 'Offer Received', ...extra,
})

beforeEach(() => {
  vi.useFakeTimers()
  __resetSelectionAlarm()
  __resetNotificationEvents()
  sound.start.mockClear(); sound.stop.mockClear(); sound.volume.mockClear(); other.play.mockClear()
})
afterEach(() => { __resetSelectionAlarm(); vi.useRealTimers() })

describe('which mail can start the selection alarm', () => {
  it('never alarms for any Interview Related classification', () => {
    for (const classification of [...INTERVIEW_BOOKING_CLASSIFICATIONS, 'interview_shortlisted']) {
      expect(isGenuineSelection(classification)).toBe(false)
      expect(raiseSelectionAlarm(offer(`i-${classification}`, { classification }))).toBe(false)
    }
    expect(sound.start).not.toHaveBeenCalled()
    expect(getSelectionAlarmState().pending).toEqual([])
  })

  it('interview mail still plays its own bell and nothing else', () => {
    expect(notifyTrackedMail(offer('iv', { classification: 'interview_confirmed' }))).toBe(true)
    expect(other.play).toHaveBeenCalledWith('mail_interview_booking')
    expect(sound.start).not.toHaveBeenCalled()
  })

  it('declined and revoked offers keep the short fanfare, not the alarm', () => {
    for (const classification of ['offer_declined', 'offer_revoked']) {
      expect(isGenuineSelection(classification)).toBe(false)
      notifyTrackedMail(offer(`x-${classification}`, { classification }))
    }
    expect(other.play).toHaveBeenCalledTimes(2)
    expect(other.play).toHaveBeenCalledWith('mail_selection')
    expect(sound.start).not.toHaveBeenCalled()
  })

  it('every genuine classification is a tracked Selection Related one', () => {
    for (const c of GENUINE_SELECTION_CLASSIFICATIONS) expect(SELECTION_CLASSIFICATIONS).toContain(c)
  })

  it('a genuine selection alarms instead of the fanfare, never both', () => {
    expect(notifyTrackedMail(offer())).toBe(true)
    expect(sound.start).toHaveBeenCalledTimes(1)
    expect(sound.start).toHaveBeenCalledWith(1, 30)
    expect(other.play).not.toHaveBeenCalled()
  })
})

describe('duplicates', () => {
  it('one alert id alarms once, even from a second delivery', () => {
    expect(raiseSelectionAlarm(offer('dup'))).toBe(true)
    expect(raiseSelectionAlarm(offer('dup', { event_id: 'retry-2' }))).toBe(false)
    expect(getSelectionAlarmState().pending).toHaveLength(1)
    expect(sound.start).toHaveBeenCalledTimes(1)
  })

  it('a second alert joins the running alarm instead of starting another', () => {
    raiseSelectionAlarm(offer('a'))
    raiseSelectionAlarm(offer('b', { classification: 'joining_confirmed' }))
    expect(sound.start).toHaveBeenCalledTimes(1)
    expect(getSelectionAlarmState().pending.map(a => a.id)).toEqual(['a', 'b'])
  })

  it('an acknowledged alert never alarms again', () => {
    raiseSelectionAlarm(offer('ack'))
    stopSelectionAlarmSound()
    expect(raiseSelectionAlarm(offer('ack'))).toBe(false)
  })
})

describe('30 seconds, every 60 seconds, until acknowledged', () => {
  it('repeats each minute and stops on Stop Sound', () => {
    raiseSelectionAlarm(offer())
    expect(sound.start).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(REPEAT_MS)
    vi.advanceTimersByTime(REPEAT_MS)
    expect(sound.start).toHaveBeenCalledTimes(3)
    stopSelectionAlarmSound()
    expect(sound.stop).toHaveBeenCalled()
    vi.advanceTimersByTime(REPEAT_MS * 5)
    expect(sound.start).toHaveBeenCalledTimes(3)
    expect(getSelectionAlarmState().pending).toEqual([])
  })

  it('opening the last pending alert in Mail Alerts stops the repeats', () => {
    raiseSelectionAlarm(offer('a'))
    raiseSelectionAlarm(offer('b'))
    expect(acknowledgeSelectionAlarm('a')).toBe(true)
    vi.advanceTimersByTime(REPEAT_MS)
    expect(sound.start).toHaveBeenCalledTimes(2)
    acknowledgeSelectionAlarm('b')
    vi.advanceTimersByTime(REPEAT_MS * 3)
    expect(sound.start).toHaveBeenCalledTimes(2)
  })

  it('mute silences without acknowledging, and unmute resumes on the next cycle', () => {
    raiseSelectionAlarm(offer())
    setSelectionAlarmMuted(true)
    expect(sound.stop).toHaveBeenCalled()
    vi.advanceTimersByTime(REPEAT_MS * 2)
    expect(sound.start).toHaveBeenCalledTimes(1)
    expect(getSelectionAlarmState().pending).toHaveLength(1)
    setSelectionAlarmMuted(false)
    vi.advanceTimersByTime(REPEAT_MS)
    expect(sound.start).toHaveBeenCalledTimes(2)
  })

  it('volume is clamped, remembered and applied to the alarm', () => {
    setSelectionAlarmVolumeLevel(0.4)
    expect(sound.volume).toHaveBeenLastCalledWith(0.4)
    raiseSelectionAlarm(offer())
    expect(sound.start).toHaveBeenLastCalledWith(0.4, 30)
    setSelectionAlarmVolumeLevel(7)
    expect(getSelectionAlarmState().volume).toBe(1)
  })

  it('an unacknowledged alarm resumes after a page refresh', () => {
    localStorage.setItem(PENDING_KEY, JSON.stringify([{ id: 'kept', classification: 'offer_received', at: Date.now() }]))
    const seen = []
    const off = subscribeSelectionAlarm(state => seen.push(state.pending.length))
    expect(seen[0]).toBe(1)
    expect(sound.start).toHaveBeenCalledTimes(1)
    off()
  })

  it('a pending alert older than a day is dropped, not alarmed', () => {
    localStorage.setItem(PENDING_KEY, JSON.stringify([{ id: 'old', classification: 'offer_received', at: Date.now() - 2 * 86400000 }]))
    subscribeSelectionAlarm(() => {})
    expect(sound.start).not.toHaveBeenCalled()
  })
})
