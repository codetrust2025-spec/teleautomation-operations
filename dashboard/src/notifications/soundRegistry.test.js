import { describe, expect, it } from 'vitest'
import { NOTIFICATION_IDS, NOTIFICATION_SOUNDS } from './soundRegistry.js'

describe('Operations notification catalogue', () => {
  it('contains only Operations-owned events', () => {
    expect(NOTIFICATION_IDS).toEqual(['mail_interview_booking', 'mail_selection', 'gmail_reconnect', 'interview_reminder', 'mail_selection_alarm'])
    expect(Object.keys(NOTIFICATION_SOUNDS)).toEqual(NOTIFICATION_IDS)
  })

  it('has a distinct sound and dedupe policy for every event', () => {
    expect(new Set(NOTIFICATION_IDS.map(id => NOTIFICATION_SOUNDS[id].play)).size).toBe(NOTIFICATION_IDS.length)
    for (const id of NOTIFICATION_IDS) expect(NOTIFICATION_SOUNDS[id].dedupe).toBeTruthy()
  })
})
