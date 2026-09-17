import React from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AttendancePanel } from './AttendancePanel.jsx'

let authValue
let attendanceValue
vi.mock('../context/AuthContext.jsx', () => ({ useAuth: () => authValue }))
vi.mock('./AttendanceContext.jsx', () => ({ useAttendance: () => attendanceValue }))

describe('attendance and earnings panel', () => {
  afterEach(() => cleanup())
  beforeEach(() => {
    authValue = { role: 'handler', displayName: 'Venu' }
    attendanceValue = {
      loading: false, error: '', refresh: vi.fn(),
      data: {
        profile: { display_name: 'Venu' },
        popup: { attendance_date: '2026-09-01', reason: 'ALREADY_MARKED', marked: true },
        eligibility: {
          period_start: '2026-09-01', period_end: '2026-09-01',
          attended_working_days: 1, required_working_days: 1,
          attendance_ratio: 100, eligibility_amount: 40000,
        },
        records: [{ attendance_date: '2026-09-01', marked_at: '2026-09-01T09:10:00+05:30', status: 'VERIFIED', office_network_verified: true }],
      },
    }
    vi.stubGlobal('fetch', vi.fn())
  })

  it('displays the server-authoritative ratio and eligibility tier', () => {
    render(<AttendancePanel />)
    expect(screen.getByText('100% ✅')).toBeInTheDocument()
    expect(screen.getByText('1 / 1 Working Days')).toBeInTheDocument()
    expect(screen.getByText(/₹40,000/)).toBeInTheDocument()
    expect(screen.getByText('Salary changes require payroll-admin approval')).toBeInTheDocument()
  })

  it('does not expose holiday or salary approval controls to handlers', () => {
    render(<AttendancePanel />)
    expect(screen.queryByRole('heading', { name: 'Public holidays' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Salary recommendations' })).not.toBeInTheDocument()
    // A handler's panel reads its own check-out state and nothing else. It
    // used to fetch nothing at all, so this asserts the admin endpoints stay
    // unreached rather than that the panel is silent.
    const urls = fetch.mock.calls.map(([url]) => String(url))
    for (const admin of ['/attendance/holidays', '/attendance/salary-recommendations',
      '/attendance/admin/users', '/attendance/admin/overview']) {
      expect(urls.some(url => url.includes(admin))).toBe(false)
    }
    expect(urls.every(url => url.includes('/attendance/checkout'))).toBe(true)
  })

  it('loads credential-safe admin data without rendering credentials', async () => {
    authValue = { role: 'admin', displayName: 'Operations Admin' }
    fetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({ holidays: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ recommendations: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        users: [{ name: 'Venu', username: 'venu-login', role: 'handler', active: true, password_configured: true, last_login: null, account_source: 'config/dashboard_handlers.yaml', account_id: 'handler:venu-login' }],
        findings: { duplicate_identity_groups: [], inactive_usernames: [], orphaned_usernames: [], multiple_auth_source_usernames: [] },
      }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        period_start: '2026-09-01', period_end: '2026-09-01',
        users: [{
          account_id: 'handler:referrer-thrilok', username: 'thrilok', display_name: 'Thrilok',
          today_status: 'VERIFIED', today_reason: 'ALREADY_MARKED',
          marked_at: '2026-09-01T11:51:00+05:30', office_network_verified: true,
          attended_working_days: 1, required_working_days: 1,
          attendance_ratio: 100, eligibility_amount: 40000,
        }],
      }) })
    render(<AttendancePanel />)
    expect(await screen.findByRole('heading', { name: 'All Users Attendance' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Your verified attendance' })).not.toBeInTheDocument()
    expect(screen.queryByText('Attendance eligibility tier')).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Thrilok' })).toBeInTheDocument()
    expect(await screen.findByText('venu-login')).toBeInTheDocument()
    expect(screen.getByText('Configured')).toBeInTheDocument()
    expect(screen.queryByText(/password123|hash|token/i)).not.toBeInTheDocument()
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4))
  })

  it('opens a selected handler history by stable account ID', async () => {
    authValue = { role: 'admin', displayName: 'Operations Admin' }
    fetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({ holidays: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ recommendations: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ users: [], findings: null }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        period_start: '2026-09-01', period_end: '2026-09-01',
        users: [{
          account_id: 'handler:referrer-thrilok', username: 'thrilok', display_name: 'Thrilok',
          today_status: 'VERIFIED', today_reason: 'ALREADY_MARKED',
          marked_at: '2026-09-01T11:51:00+05:30', office_network_verified: true,
          attended_working_days: 1, required_working_days: 1,
          attendance_ratio: 100, eligibility_amount: 40000,
        }],
      }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        profile: { display_name: 'Thrilok', account_id: 'handler:referrer-thrilok' },
        eligibility: { period_start: '2026-09-01', period_end: '2026-09-01' },
        records: [{
          attendance_date: '2026-09-01', marked_at: '2026-09-01T11:51:00+05:30',
          status: 'VERIFIED', office_network_verified: true,
        }],
      }) })

    render(<AttendancePanel />)
    fireEvent.click(await screen.findByRole('button', { name: 'Thrilok' }))
    expect(await screen.findByRole('heading', { name: 'Thrilok attendance history' })).toBeInTheDocument()
    expect(fetch.mock.calls[4][0]).toContain(
      '/attendance/admin/history?account_id=handler%3Areferrer-thrilok',
    )
    expect(screen.getAllByText('VERIFIED').length).toBeGreaterThan(0)
  })
})
