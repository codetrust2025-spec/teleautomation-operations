import { useEffect, useState } from 'react'
import { unlockNotificationSound } from '../utils/notificationSound.js'
import { isInterviewBellMuted, setInterviewBellMuted, subscribeInterviewBell, testInterviewBell } from './interviewBell.js'

/** Mute and Test for the Interview Related bell, on the Interview Related page. */
export function InterviewBellControls() {
  const [muted, setMuted] = useState(isInterviewBellMuted)
  useEffect(() => subscribeInterviewBell(setMuted), [])
  return (
    <div className="selection-alarm__controls" role="group" aria-label="Interview alert sound">
      <span className="interview-bell__label">Interview bell</span>
      <button
        type="button" className="selection-alarm__btn" aria-pressed={muted}
        onClick={() => { unlockNotificationSound(); setInterviewBellMuted(!muted) }}
      >{muted ? 'Unmute' : 'Mute'}</button>
      <button
        type="button" className="selection-alarm__btn"
        onClick={() => { unlockNotificationSound(); testInterviewBell() }}
      >Test bell</button>
    </div>
  )
}
