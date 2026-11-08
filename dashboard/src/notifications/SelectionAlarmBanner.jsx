import { useEffect, useState } from 'react'
import { unlockNotificationSound } from '../utils/notificationSound.js'
import {
  getSelectionAlarmState,
  setSelectionAlarmMuted,
  setSelectionAlarmVolumeLevel,
  stopSelectionAlarmSound,
  stopSelectionAlarmTest,
  subscribeSelectionAlarm,
  testSelectionAlarm,
} from './selectionAlarm.js'

function useSelectionAlarm() {
  const [state, setState] = useState(getSelectionAlarmState)
  useEffect(() => subscribeSelectionAlarm(setState), [])
  return state
}

const label = (a) => a.status || String(a.classification || '').replaceAll('_', ' ')

/** Volume slider and Mute toggle. Shared by the banner and the Mail Alerts header. */
export function SelectionAlarmControls({ showTest = false }) {
  const { volume, muted, pending } = useSelectionAlarm()
  const [testing, setTesting] = useState(false)
  const percent = Math.round(volume * 100)
  return (
    <div className="selection-alarm__controls" role="group" aria-label="Selection alarm sound">
      <label className="selection-alarm__volume">
        <span>Alarm volume</span>
        <input
          type="range" min="0" max="100" step="5" value={percent}
          aria-label="Selection alarm volume" aria-valuetext={`${percent}%`}
          onChange={(event) => setSelectionAlarmVolumeLevel(Number(event.target.value) / 100)}
        />
        <output>{percent}%</output>
      </label>
      <button
        type="button" className="selection-alarm__btn" aria-pressed={muted}
        onClick={() => { unlockNotificationSound(); setSelectionAlarmMuted(!muted) }}
      >{muted ? 'Unmute' : 'Mute'}</button>
      {showTest && !pending.length && (
        <button
          type="button" className="selection-alarm__btn"
          onClick={() => {
            unlockNotificationSound()
            if (testing) { stopSelectionAlarmTest(); setTesting(false) } else { testSelectionAlarm(); setTesting(true) }
          }}
        >{testing ? 'Stop test' : 'Test alarm'}</button>
      )}
    </div>
  )
}

/**
 * Pinned banner while any genuine selection alarm is unacknowledged. It is on
 * every page (mounted by GlobalNotificationSounds) and returns after a refresh
 * until Stop Sound, or the alert is opened, read or dismissed in Mail Alerts.
 */
export function SelectionAlarmBanner() {
  const { pending, muted } = useSelectionAlarm()
  if (!pending.length) return null
  const latest = pending[pending.length - 1]
  const who = [latest.candidate_name || 'Candidate', latest.company_name].filter(Boolean).join(' · ')
  return (
    <div className="selection-alarm" role="alertdialog" aria-live="assertive" aria-label="Selection alert">
      <div className="selection-alarm__text">
        <strong>🎉 {label(latest)}</strong>
        <span>{who}</span>
        {pending.length > 1 && <small>{pending.length} selection alerts waiting</small>}
        {muted && <small>Sound muted — the alert stays until you stop it</small>}
      </div>
      <SelectionAlarmControls />
      <button
        type="button" className="selection-alarm__btn selection-alarm__btn--stop"
        onClick={() => { unlockNotificationSound(); stopSelectionAlarmSound() }}
      >Stop Sound</button>
    </div>
  )
}

export default SelectionAlarmBanner
