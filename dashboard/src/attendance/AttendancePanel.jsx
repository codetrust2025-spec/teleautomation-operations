import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { API } from '../config.js'
import { useAuth } from '../context/AuthContext.jsx'
import { useAttendance } from './AttendanceContext.jsx'
import { DailyCheckout } from './DailyCheckout.jsx'
import { Button } from '../components/ui/Button.jsx'
import { Card, CardGrid, StatTile } from '../components/ui/Card.jsx'
import { Alert, Badge, EmptyState } from '../components/ui/Feedback.jsx'
import { Field, FormRow, Input } from '../components/ui/Field.jsx'
import { List, ListRow } from '../components/ui/List.jsx'
import { DataTable } from '../components/ui/DataTable.jsx'

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
  const [shutdowns, setShutdowns] = useState({ global: { active: false }, handlers: {}, audit_events: [] })
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
      // This is deliberately independent of the existing attendance summary:
      // a failed control read must never hide historical attendance data.
      Promise.resolve(fetch(`${API}/attendance/admin/shutdowns`, { credentials: 'include' }))
        .then(json).then(setShutdowns).catch(() => {})
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

  const transitionShutdown = async (scope, accountId, action) => {
    setBusy(true)
    try {
      const endpoint = action === 'SHUTDOWN'
        ? `${API}/attendance/admin/shutdown`
        : `${API}/attendance/admin/reactivate`
      await json(await fetch(endpoint, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope, account_id: accountId || null, reason: '' }),
      }))
      await Promise.all([loadAdmin(), attendance.refresh()])
    } catch (requestError) {
      setAdminError(requestError.message)
    } finally { setBusy(false) }
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

  const recordColumns = [
    { key: 'attendance_date', header: 'Working date', primary: true, render: row => formatDate(row.attendance_date) },
    { key: 'marked_at', header: 'Marked at', render: row => formatDateTime(row.marked_at) },
    { key: 'status', header: 'Status', render: row => <Badge tone={row.status === 'VERIFIED' ? 'success' : 'neutral'}>{row.status}</Badge> },
    { key: 'office', header: 'Office network', render: row => (row.office_network_verified ? 'Verified' : 'Not verified') },
  ]

  return (
    <div className="ui-page attendance-page">
      {/* The shell header names the page; this row holds only its controls. */}
      <div className="ui-toolbar">
        <Button onClick={refreshPage} disabled={attendance.loading}>Refresh</Button>
      </div>
      {attendance.error && <Alert>{attendance.error}</Alert>}
      {!isAdmin && <CardGrid columns={3}>
        <StatTile label="Today" value={todayStatus} hint={attendance.data?.profile?.display_name || auth.displayName} />
        <StatTile label="Attendance Success" value={ratioLabel} hint={eligibility ? `${eligibility.attended_working_days} / ${eligibility.required_working_days} Working Days` : '—'} />
        <StatTile label="Attendance eligibility tier" value={eligibility ? money.format(eligibility.eligibility_amount) : '—'} hint="Salary changes require payroll-admin approval" />
      </CardGrid>}

      {!isAdmin && <DailyCheckout />}

      {!isAdmin && <Card
        title="Your verified attendance"
        description={eligibility ? `${formatDate(eligibility.period_start)} – ${formatDate(eligibility.period_end)}` : 'Current evaluation period'}
      >
        <DataTable columns={recordColumns} rows={attendance.data?.records || []} rowKey={row => row.attendance_date} empty="No verified attendance in this period." />
      </Card>}

      {isAdmin && <>
        <Card
          title="Attendance processing control"
          description="Shutdown pauses new attendance and eligibility processing without changing historical records. Global shutdown overrides individual reactivation."
          footer={shutdowns.audit_events?.length > 0 ? `Audit trail: ${shutdowns.audit_events.length} recorded shutdown transitions. Latest: ${shutdowns.audit_events[0].action} ${shutdowns.audit_events[0].scope.toLowerCase()}.` : null}
        >
          <List>
            <ListRow
              title="Global shutdown"
              meta={shutdowns.global?.active ? 'Shutdown — all handlers are paused' : 'Active — all handlers may process attendance'}
              actions={<Button variant={shutdowns.global?.active ? 'secondary' : 'danger'} disabled={busy} onClick={() => transitionShutdown('GLOBAL', null, shutdowns.global?.active ? 'REACTIVATE' : 'SHUTDOWN')}>{shutdowns.global?.active ? 'Reactivate global' : 'Shutdown all handlers'}</Button>}
            />
          </List>
          <DataTable
            rows={overview}
            rowKey={row => `shutdown:${row.account_id}`}
            empty="No active handler accounts found."
            columns={[
              { key: 'handler', header: 'Handler', primary: true, render: row => <>{row.display_name}<small className="ui-table__note">{row.username}</small></> },
              { key: 'status', header: 'Processing status', render: row => { const state = shutdowns.handlers?.[row.account_id] || {}; const label = state.active ? (state.scope === 'GLOBAL' ? 'Shutdown by global control' : 'Shutdown') : 'Active'; return <Badge tone={state.active ? 'danger' : 'success'}>{label}</Badge> } },
              { key: 'control', header: 'Control', render: row => { const state = shutdowns.handlers?.[row.account_id] || {}; return <Button size="sm" disabled={busy || state.scope === 'GLOBAL'} onClick={() => transitionShutdown('HANDLER', row.account_id, state.handler_active ? 'REACTIVATE' : 'SHUTDOWN')}>{state.handler_active ? 'Reactivate' : 'Shutdown'}</Button> } },
            ]}
          />
        </Card>

        <Card
          title="All Users Attendance"
          description={`${overviewPeriod ? `${formatDate(overviewPeriod.start)} – ${formatDate(overviewPeriod.end)}` : 'Current evaluation period'} · Handler accounts only`}
          actions={<Badge tone="info">{overview.length} handlers</Badge>}
        >
          {adminError && <Alert>{adminError}</Alert>}
          <DataTable
            rows={overview}
            rowKey={row => row.account_id}
            empty="No active handler accounts found."
            columns={[
              { key: 'name', header: 'Name', primary: true, render: row => <><Button variant="link" onClick={() => openHistory(row)}>{row.display_name}</Button><small className="ui-table__note">{row.username}</small></> },
              { key: 'today', header: 'Today status', render: row => <Badge tone={row.today_status === 'VERIFIED' ? 'success' : row.today_status === 'NOT_MARKED' || !row.today_status ? 'warning' : 'neutral'}>{overviewStatus(row)}</Badge> },
              { key: 'marked', header: 'Marked time', render: row => (row.marked_at ? formatDateTime(row.marked_at) : '—') },
              { key: 'days', header: 'Working days', numeric: true, render: row => `${row.attended_working_days} / ${row.required_working_days}` },
              { key: 'ratio', header: 'Attendance %', numeric: true, render: row => ratio(row.attendance_ratio) },
              { key: 'office', header: 'Office verified', render: row => (row.office_network_verified == null ? '—' : row.office_network_verified ? 'Verified' : 'Not verified') },
              { key: 'eligibility', header: 'Eligibility', numeric: true, render: row => (row.eligibility_amount == null ? 'Paused' : money.format(row.eligibility_amount)) },
            ]}
          />
        </Card>

        {selectedHistory && <Card
          title={`${selectedHistory.profile.display_name} attendance history`}
          description={selectedHistory.eligibility ? `${formatDate(selectedHistory.eligibility.period_start)} – ${formatDate(selectedHistory.eligibility.period_end)}` : 'Attendance history'}
          actions={<Button variant="ghost" onClick={() => setSelectedHistory(null)}>Close</Button>}
        >
          <DataTable columns={recordColumns} rows={selectedHistory.records} rowKey={row => row.attendance_date} loading={historyLoading} loadingText="Loading attendance history…" empty="No verified attendance records." />
        </Card>}

        <Card title="Public holidays" description="Configured dates are excluded from attendance and eligibility calculations.">
          <form onSubmit={addHoliday}>
            <FormRow columns="minmax(150px, 180px) minmax(200px, 1fr) auto">
              <Field label="Holiday date" hideLabel><Input type="date" value={holidayDate} onChange={event => setHolidayDate(event.target.value)} required /></Field>
              <Field label="Holiday name" hideLabel><Input value={holidayName} onChange={event => setHolidayName(event.target.value)} placeholder="Holiday name" maxLength={160} required /></Field>
              <Button variant="primary" type="submit" disabled={busy}>Add holiday</Button>
            </FormRow>
          </form>
          <List className="attendance-holiday-list">
            {holidays.filter(row => row.active).map(row => <ListRow key={row.holiday_date} title={formatDate(row.holiday_date)} meta={row.name} actions={<Button size="sm" variant="ghost" disabled={busy} onClick={() => removeHoliday(row.holiday_date)}>Remove</Button>} />)}
            {holidays.filter(row => row.active).length === 0 && <EmptyState>No public holidays configured for this month.</EmptyState>}
          </List>
        </Card>

        <Card
          title="Salary recommendations"
          description="Eligibility never changes salary until an authorized admin approves it."
          actions={<Badge tone={recommendations.length ? 'warning' : 'neutral'}>{recommendations.length} pending</Badge>}
        >
          <List>
            {recommendations.map(row => <ListRow
              key={row.id}
              title={`${row.display_name}: ${money.format(row.previous_eligibility_amount)} → ${money.format(row.recommended_eligibility_amount)}`}
              meta={`${row.attended_working_days} / ${row.required_working_days} working days · ${row.attendance_ratio == null ? 'No ratio' : `${Number(row.attendance_ratio).toFixed(2)}%`}`}
              actions={<><Button variant="danger" disabled={busy} onClick={() => review(row.id, 'REJECT')}>Reject</Button><Button variant="primary" disabled={busy} onClick={() => review(row.id, 'APPROVE')}>Approve & apply</Button></>}
            />)}
            {recommendations.length === 0 && <EmptyState>No salary changes are awaiting approval.</EmptyState>}
          </List>
        </Card>

        <Card
          title="Operations users"
          description="Credential-safe account inventory and attendance identity mapping."
          footer={findings ? `Duplicates: ${findings.duplicate_identity_groups.length} · Inactive: ${findings.inactive_usernames.length} · Orphaned: ${findings.orphaned_usernames.length} · Multiple auth sources: ${findings.multiple_auth_source_usernames.length}` : null}
        >
          <DataTable
            rows={users}
            rowKey={row => `${row.account_id}:${row.account_source}`}
            empty="No operations users found."
            columns={[
              { key: 'name', header: 'Name', primary: true },
              { key: 'username', header: 'Username' },
              { key: 'role', header: 'Role' },
              { key: 'active', header: 'Status', render: row => <Badge tone={row.active ? 'success' : 'neutral'}>{row.active ? 'Active' : 'Inactive'}</Badge> },
              { key: 'password', header: 'Password', render: row => (row.password_configured ? 'Configured' : 'Not configured') },
              { key: 'last_login', header: 'Last login', render: row => formatDateTime(row.last_login) },
              { key: 'account_source', header: 'Account source' },
            ]}
          />
        </Card>
      </>}
    </div>
  )
}
