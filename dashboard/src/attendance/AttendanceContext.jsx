import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'
import { Button } from '../components/ui/Button.jsx'
import { Dialog } from '../components/ui/Dialog.jsx'
import { Alert, Toast } from '../components/ui/Feedback.jsx'
import './attendance.css'

const AttendanceContext = createContext(null)
const MAX_TIMER_MS = 2_147_000_000
const DISMISS_FOR_MS = 30 * 60 * 1000

async function responseJson(response) {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail || payload.message || 'Attendance request failed')
  return payload
}

function formatMarkedTime(value) {
  if (!value) return ''
  return new Date(value).toLocaleTimeString('en-IN', {
    hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Kolkata',
  })
}

export function AttendanceProvider({ children }) {
  const auth = useAuth()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [marking, setMarking] = useState(false)
  const [error, setError] = useState('')
  const [dismissedUntil, setDismissedUntil] = useState(0)
  const [confirmation, setConfirmation] = useState('')

  const refresh = useCallback(async () => {
    if (!auth.authenticated) return null
    setLoading(true)
    try {
      const payload = await responseJson(await fetch(`${API}/attendance/status`, { credentials: 'include' }))
      setData(payload)
      setError('')
      return payload
    } catch (requestError) {
      setError(requestError.message)
      return null
    } finally {
      setLoading(false)
    }
  }, [auth.authenticated])

  useEffect(() => {
    if (!auth.authenticated) {
      setData(null)
      setError('')
      return undefined
    }
    refresh()
    const onFocus = () => refresh()
    const onVisibility = () => { if (document.visibilityState === 'visible') refresh() }
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [auth.authenticated, refresh])

  useEffect(() => {
    const next = data?.popup?.next_check_at
    if (!next || !auth.authenticated) return undefined
    const delay = Math.max(1000, Math.min(MAX_TIMER_MS, new Date(next).getTime() - Date.now() + 250))
    const timer = window.setTimeout(refresh, delay)
    return () => window.clearTimeout(timer)
  }, [auth.authenticated, data?.popup?.next_check_at, refresh])

  useEffect(() => {
    if (!dismissedUntil || dismissedUntil <= Date.now()) return undefined
    const timer = window.setTimeout(() => setDismissedUntil(0), dismissedUntil - Date.now() + 50)
    return () => window.clearTimeout(timer)
  }, [dismissedUntil])

  const mark = useCallback(async () => {
    setMarking(true)
    setError('')
    try {
      const payload = await responseJson(await fetch(`${API}/attendance/mark`, {
        method: 'POST',
        credentials: 'include',
      }))
      const markedAt = payload.attendance?.marked_at
      setConfirmation(`Attendance marked${markedAt ? ` at ${formatMarkedTime(markedAt)}` : ''}`)
      setDismissedUntil(0)
      await refresh()
      window.setTimeout(() => setConfirmation(''), 4000)
      return { ok: true, payload }
    } catch (requestError) {
      setError(requestError.message)
      return { ok: false, error: requestError.message }
    } finally {
      setMarking(false)
    }
  }, [refresh])

  const dismiss = useCallback(() => {
    setDismissedUntil(Date.now() + DISMISS_FOR_MS)
    setError('')
  }, [])

  const promptOpen = Boolean(
    auth.authenticated
    && data?.popup?.eligible
    && !data?.popup?.marked
    && dismissedUntil <= Date.now(),
  )

  const value = useMemo(() => ({
    data, loading, error, marking, confirmation, promptOpen, refresh, mark, dismiss,
  }), [data, loading, error, marking, confirmation, promptOpen, refresh, mark, dismiss])

  return (
    <AttendanceContext.Provider value={value}>
      {children}
      <Dialog
        open={promptOpen}
        title={`Good morning, ${data?.profile?.display_name || ''} 👋`}
        onClose={dismiss}
        closeLabel="Dismiss attendance reminder"
        className="attendance-prompt"
        actions={<>
          <Button variant="primary" disabled={marking} onClick={mark}>{marking ? 'Marking…' : 'Mark Attendance'}</Button>
          <Button variant="ghost" disabled={marking} onClick={dismiss}>Not now</Button>
        </>}
        note="Attendance can only be verified from the approved office network."
      >
        <p>Please mark your attendance for today.</p>
        {error && <Alert>{error}</Alert>}
      </Dialog>
      {confirmation && <Toast>✓ {confirmation}</Toast>}
    </AttendanceContext.Provider>
  )
}

export function useAttendance() {
  const context = useContext(AttendanceContext)
  if (!context) throw new Error('useAttendance must be used within AttendanceProvider')
  return context
}
