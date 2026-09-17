import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'
import { useAttendance } from './AttendanceContext.jsx'
import { DailyCheckout } from './DailyCheckout.jsx'

const money = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })

async function json(response) {
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail || payload.message || 'Request failed')
  return payload
}

function formatDate(value) {
  if (!value) return '—'
  return new Date(`${String(value).slice(0, 10)}T00:00:00`).toLocaleDateString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

function formatDateTime(value) {
  if (!value) return 'Never recorded'
  return new Date(value).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit',
    timeZone: 'Asia/Kolkata',
  })
}

function overviewStatus(row) {
  if (row.today_status === 'VERIFIED') return 'Verified'
  if (row.today_status === 'NOT_REQUIRED') {
    return row.today_reason === 'PUBLIC_HOLIDAY' ? 'Public holiday' : 'Not required'
  }
  if (row.today_status === 'NOT_OPEN') return 'Opens at 9:00 AM'
  return 'Not marked'
}

function ratio(value) {
  if (value == null) return 'Not yet calculated'
  const number = Number(value)
  return `${number.toFixed(number % 1 ? 2 : 0)}%${number === 100 ? ' ✅' : ''}`
}

export function AttendancePanel() {
  const auth = useAuth()
  const attendance = useAttendance()
  const [holidays, setHolidays] = useState([])
  const [recommendations, setRecommendations] = useState([])
  const [users, setUsers] = useState([])
  const [findings, setFindings] = useState(null)
  const [overview, setOverview] = useState([])
  const [overviewPeriod, setOverviewPeriod] = useState(null)
  const [selectedHistory, setSelectedHistory] = useState(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [holidayDate, setHolidayDate] = useState('')
  const [holidayName, setHolidayName] = useState('')
  const [adminError, setAdminError] = useState('')
  const [busy, setBusy] = useState(false)
  const isAdmin = auth.role === 'admin'
  const month = attendance.data?.popup?.attendance_date?.slice(0, 7) || new Date().toISOString().slice(0, 7)
  const [yearValue, monthValue] = month.split('-').map(Number)

  const loadAdmin = useCallback(async () => {
    if (!isAdmin) return
    try {
      const [holidayPayload, recommendationPayload, userPayload, overviewPayload] = await Promise.all([
        fetch(`${API}/attendance/holidays?year=${yearValue}&month=${monthValue}`, { credentials: 'include' }).then(json),
        fetch(`${API}/attendance/salary-recommendations?review_status=PENDING`, { credentials: 'include' }).then(json),
        fetch(`${API}/attendance/admin/users`, { credentials: 'include' }).then(json),
        fetch(`${API}/attendance/admin/overview`, { credentials: 'include' }).then(json),
      ])
      setHolidays(holidayPayload.holidays || [])
      setRecommendations(recommendationPayload.recommendations || [])
      setUsers(userPayload.users || [])
      setFindings(userPayload.findings || null)
      setOverview(overviewPayload.users || [])
      setOverviewPeriod({ start: overviewPayload.period_start, end: overviewPayload.period_end })
      setAdminError('')
    } catch (requestError) {
      setAdminError(requestError.message)
    }
  }, [isAdmin, monthValue, yearValue])

  useEffect(() => { loadAdmin() }, [loadAdmin])

  const openHistory = async row => {
    setHistoryLoading(true)
    setAdminError('')
    try {
      const payload = await json(await fetch(
        `${API}/attendance/admin/history?account_id=${encodeURIComponent(row.account_id)}`,
        { credentials: 'include' },
      ))
      setSelectedHistory(payload)
    } catch (requestError) {
      setAdminError(requestError.message)
    } finally {
      setHistoryLoading(false)
    }
  }

  const refreshPage = async () => {
    if (isAdmin) await Promise.all([attendance.refresh(), loadAdmin()])
    else await attendance.refresh()
  }

  const addHoliday = async event => {
    event.preventDefault()
    setBusy(true)
    try {
      await json(await fetch(`${API}/attendance/holidays`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ holiday_date: holidayDate, name: holidayName }),
      }))
      setHolidayDate('')
      setHolidayName('')
      await Promise.all([loadAdmin(), attendance.refresh()])
    } catch (requestError) {
      setAdminError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  const removeHoliday = async value => {
    setBusy(true)
    try {
      await json(await fetch(`${API}/attendance/holidays/${value}`, { method: 'DELETE', credentials: 'include' }))
      await Promise.all([loadAdmin(), attendance.refresh()])
    } catch (requestError) {
      setAdminError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  const review = async (id, decision) => {
    setBusy(true)
    try {
      await json(await fetch(`${API}/attendance/salary-recommendations/${id}/review`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, note: '' }),
      }))
      await loadAdmin()
    } catch (requestError) {
      setAdminError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  const eligibility = attendance.data?.eligibility
  const popup = attendance.data?.popup
  const ratioLabel = ratio(eligibility?.attendance_ratio)
  const todayStatus = useMemo(() => {
    if (!popup) return 'Loading attendance…'
    if (popup.marked) return 'Attendance verified today'
    if (popup.reason === 'SUNDAY') return 'Sunday — attendance not required'
    if (popup.reason === 'PUBLIC_HOLIDAY') return 'Public holiday — attendance not required'
    if (popup.reason === 'BEFORE_START_TIME') return 'Attendance opens at 9:00 AM'
    return 'Attendance is not yet marked'
  }, [popup])

  return (
    <div className="attendance-page">
      <header className="attendance-page__header">
        <div><p className="attendance-eyebrow">WORKING-DAY ATTENDANCE</p><h2>Attendance & earnings eligibility</h2></div>
        <button type="button" className="attendance-secondary-button" onClick={refreshPage} disabled={attendance.loading}>Refresh</button>
      </header>
      {attendance.error && <p className="attendance-error" role="alert">{attendance.error}</p>}
      {!isAdmin && <section className="attendance-summary-grid">
        <article className="attendance-stat"><span>Today</span><strong>{todayStatus}</strong><small>{attendance.data?.profile?.display_name || auth.displayName}</small></article>
        <article className="attendance-stat"><span>Attendance Success</span><strong>{ratioLabel}</strong><small>{eligibility ? `${eligibility.attended_working_days} / ${eligibility.required_working_days} Working Days` : '—'}</small></article>
        <article className="attendance-stat"><span>Attendance eligibility tier</span><strong>{eligibility ? money.format(eligibility.eligibility_amount) : '—'}</strong><small>Salary changes require payroll-admin approval</small></article>
      </section>}

      {!isAdmin && <DailyCheckout />}

      {!isAdmin && <section className="attendance-section">
        <div className="attendance-section__head"><div><h3>Your verified attendance</h3><p>{eligibility ? `${formatDate(eligibility.period_start)} – ${formatDate(eligibility.period_end)}` : 'Current evaluation period'}</p></div></div>
        <div className="attendance-table-wrap"><table className="attendance-table"><thead><tr><th>Working date</th><th>Marked at</th><th>Status</th><th>Office network</th></tr></thead><tbody>
          {(attendance.data?.records || []).length === 0 && <tr><td colSpan={4}>No verified attendance in this period.</td></tr>}
          {(attendance.data?.records || []).map(row => <tr key={row.attendance_date}><td>{formatDate(row.attendance_date)}</td><td>{formatDateTime(row.marked_at)}</td><td>{row.status}</td><td>{row.office_network_verified ? 'Verified' : 'Not verified'}</td></tr>)}
        </tbody></table></div>
      </section>}

      {isAdmin && <>
        <section className="attendance-section">
          <div className="attendance-section__head"><div><h3>All Users Attendance</h3><p>{overviewPeriod ? `${formatDate(overviewPeriod.start)} – ${formatDate(overviewPeriod.end)}` : 'Current evaluation period'} · Handler accounts only</p></div><span className="attendance-count">{overview.length} handlers</span></div>
          {adminError && <p className="attendance-error" role="alert">{adminError}</p>}
          <div className="attendance-table-wrap"><table className="attendance-table"><thead><tr><th>Name</th><th>Today status</th><th>Marked time</th><th>Working days</th><th>Attendance %</th><th>Office verified</th><th>Eligibility</th></tr></thead><tbody>
            {overview.length === 0 && <tr><td colSpan={7}>No active handler accounts found.</td></tr>}
            {overview.map(row => <tr key={row.account_id}>
              <td><button type="button" className="attendance-user-link" onClick={() => openHistory(row)}>{row.display_name}</button><small className="attendance-cell-note">{row.username}</small></td>
              <td>{overviewStatus(row)}</td>
              <td>{row.marked_at ? formatDateTime(row.marked_at) : '—'}</td>
              <td>{row.attended_working_days} / {row.required_working_days}</td>
              <td>{ratio(row.attendance_ratio)}</td>
              <td>{row.office_network_verified == null ? '—' : row.office_network_verified ? 'Verified' : 'Not verified'}</td>
              <td>{money.format(row.eligibility_amount)}</td>
            </tr>)}
          </tbody></table></div>
        </section>

        {selectedHistory && <section className="attendance-section">
          <div className="attendance-section__head"><div><h3>{selectedHistory.profile.display_name} attendance history</h3><p>{selectedHistory.eligibility ? `${formatDate(selectedHistory.eligibility.period_start)} – ${formatDate(selectedHistory.eligibility.period_end)}` : 'Attendance history'}</p></div><button type="button" className="attendance-secondary-button" onClick={() => setSelectedHistory(null)}>Close</button></div>
          <div className="attendance-table-wrap"><table className="attendance-table"><thead><tr><th>Working date</th><th>Marked at</th><th>Status</th><th>Office network</th></tr></thead><tbody>
            {historyLoading && <tr><td colSpan={4}>Loading attendance history…</td></tr>}
            {!historyLoading && selectedHistory.records.length === 0 && <tr><td colSpan={4}>No verified attendance records.</td></tr>}
            {!historyLoading && selectedHistory.records.map(row => <tr key={row.attendance_date}><td>{formatDate(row.attendance_date)}</td><td>{formatDateTime(row.marked_at)}</td><td>{row.status}</td><td>{row.office_network_verified ? 'Verified' : 'Not verified'}</td></tr>)}
          </tbody></table></div>
        </section>}

        <section className="attendance-section">
          <div className="attendance-section__head"><div><h3>Public holidays</h3><p>Configured dates are excluded from attendance and eligibility calculations.</p></div></div>
          <form className="attendance-holiday-form" onSubmit={addHoliday}>
            <input aria-label="Holiday date" type="date" value={holidayDate} onChange={event => setHolidayDate(event.target.value)} required />
            <input aria-label="Holiday name" value={holidayName} onChange={event => setHolidayName(event.target.value)} placeholder="Holiday name" maxLength={160} required />
            <button className="attendance-primary-button" type="submit" disabled={busy}>Add holiday</button>
          </form>
          <div className="attendance-list">{holidays.filter(row => row.active).map(row => <div className="attendance-list__row" key={row.holiday_date}><span><strong>{formatDate(row.holiday_date)}</strong><small>{row.name}</small></span><button type="button" disabled={busy} onClick={() => removeHoliday(row.holiday_date)}>Remove</button></div>)}{holidays.filter(row => row.active).length === 0 && <p>No public holidays configured for this month.</p>}</div>
        </section>

        <section className="attendance-section">
          <div className="attendance-section__head"><div><h3>Salary recommendations</h3><p>Eligibility never changes salary until an authorized admin approves it.</p></div><span className="attendance-count">{recommendations.length} pending</span></div>
          <div className="attendance-list">{recommendations.map(row => <div className="attendance-list__row attendance-list__row--recommendation" key={row.id}><span><strong>{row.display_name}: {money.format(row.previous_eligibility_amount)} → {money.format(row.recommended_eligibility_amount)}</strong><small>{row.attended_working_days} / {row.required_working_days} working days · {row.attendance_ratio == null ? 'No ratio' : `${Number(row.attendance_ratio).toFixed(2)}%`}</small></span><div><button type="button" disabled={busy} onClick={() => review(row.id, 'REJECT')}>Reject</button><button className="attendance-approve" type="button" disabled={busy} onClick={() => review(row.id, 'APPROVE')}>Approve & apply</button></div></div>)}{recommendations.length === 0 && <p>No salary changes are awaiting approval.</p>}</div>
        </section>

        <section className="attendance-section">
          <div className="attendance-section__head"><div><h3>Operations users</h3><p>Credential-safe account inventory and attendance identity mapping.</p></div></div>
          <div className="attendance-table-wrap"><table className="attendance-table"><thead><tr><th>Name</th><th>Username</th><th>Role</th><th>Status</th><th>Password</th><th>Last login</th><th>Account source</th></tr></thead><tbody>{users.map(row => <tr key={`${row.account_id}:${row.account_source}`}><td>{row.name}</td><td>{row.username}</td><td>{row.role}</td><td>{row.active ? 'Active' : 'Inactive'}</td><td>{row.password_configured ? 'Configured' : 'Not configured'}</td><td>{formatDateTime(row.last_login)}</td><td>{row.account_source}</td></tr>)}</tbody></table></div>
          {findings && <p className="attendance-findings">Duplicates: {findings.duplicate_identity_groups.length} · Inactive: {findings.inactive_usernames.length} · Orphaned: {findings.orphaned_usernames.length} · Multiple auth sources: {findings.multiple_auth_source_usernames.length}</p>}
        </section>
      </>}
    </div>
  )
}
