/**
 * Invented data for the visual harness. People, usernames and amounts are
 * made up; dates are fixed so screenshots compare day to day.
 */
const HANDLERS = [
  { account_id: 'h-1', display_name: 'Asha Rao', username: 'asha.rao', today_status: 'VERIFIED', marked_at: '2026-11-12T03:42:00Z', attended_working_days: 9, required_working_days: 10, attendance_ratio: 90, office_network_verified: true, eligibility_amount: 18000 },
  { account_id: 'h-2', display_name: 'Vikram Shetty', username: 'vikram.s', today_status: 'NOT_MARKED', marked_at: null, attended_working_days: 7, required_working_days: 10, attendance_ratio: 70, office_network_verified: null, eligibility_amount: 12000 },
  { account_id: 'h-3', display_name: 'Meera Iyer', username: 'meera.iyer', today_status: 'VERIFIED', marked_at: '2026-11-12T03:55:00Z', attended_working_days: 10, required_working_days: 10, attendance_ratio: 100, office_network_verified: true, eligibility_amount: 20000 },
  { account_id: 'h-4', display_name: 'Karan Mehta', username: 'karan.m', today_status: 'NOT_REQUIRED', today_reason: 'PUBLIC_HOLIDAY', marked_at: null, attended_working_days: 8, required_working_days: 10, attendance_ratio: 80, office_network_verified: false, eligibility_amount: null },
]

const RECORDS = [
  { attendance_date: '2026-11-12', marked_at: '2026-11-12T03:42:00Z', status: 'VERIFIED', office_network_verified: true },
  { attendance_date: '2026-11-11', marked_at: '2026-11-11T03:51:00Z', status: 'VERIFIED', office_network_verified: true },
  { attendance_date: '2026-11-10', marked_at: '2026-11-10T04:08:00Z', status: 'VERIFIED', office_network_verified: false },
]

const ELIGIBILITY = {
  attendance_ratio: 90, attended_working_days: 9, required_working_days: 10, eligibility_amount: 18000,
  period_start: '2026-11-01', period_end: '2026-11-30',
}

export const routes = [
  [/^\/auth\/status$/, ({ role }) => ({
    enabled: true, authenticated: true, username: role === 'admin' ? 'owner' : 'asha.rao', role,
    display_name: role === 'admin' ? 'Owner' : 'Asha Rao', account_id: role === 'admin' ? 'a-1' : 'h-1',
  })],
  [/^\/attendance\/status$/, {
    profile: { display_name: 'Asha Rao' }, eligibility: ELIGIBILITY, records: RECORDS,
    popup: { attendance_date: '2026-11-12', marked: true, show: false },
  }],
  [/^\/attendance\/checkout$/, {
    date: '2026-11-12', can_check_out: false, checked_out: false, window_opens_at: '6:30 PM',
    blockers: [
      { kind: 'INTERVIEW_OUTCOME_PENDING', summary: 'Interviews still need an outcome', count: 2, items: [
        { name: 'Ravi Kumar', company: 'Altimetrik', time: '11:00 AM' },
        { name: 'Neha Joshi', company: 'Infocepts', time: '3:30 PM' },
      ] },
      { kind: 'DAILY_TASKS_PENDING', summary: 'Pending works are open', count: 1, items: [{ label: 'Upload payment proof' }] },
    ],
  }],
  [/^\/attendance\/holidays$/, { holidays: [
    { holiday_date: '2026-11-14', name: 'Children’s Day (office closed)', active: true },
    { holiday_date: '2026-11-01', name: 'Karnataka Rajyotsava', active: true },
  ] }],
  [/^\/attendance\/salary-recommendations$/, { recommendations: [
    { id: 'r-1', display_name: 'Vikram Shetty', previous_eligibility_amount: 15000, recommended_eligibility_amount: 12000, attended_working_days: 7, required_working_days: 10, attendance_ratio: 70 },
  ] }],
  [/^\/attendance\/admin\/users$/, {
    users: HANDLERS.map(h => ({ account_id: h.account_id, name: h.display_name, username: h.username, role: 'handler', active: true, password_configured: true, last_login: '2026-11-12T04:10:00Z', account_source: 'operations' })),
    findings: { duplicate_identity_groups: [], inactive_usernames: [], orphaned_usernames: [], multiple_auth_source_usernames: [] },
  }],
  [/^\/attendance\/admin\/overview$/, { users: HANDLERS, period_start: '2026-11-01', period_end: '2026-11-30' }],
  [/^\/attendance\/admin\/shutdowns$/, { global: { active: false }, handlers: { 'h-4': { active: true, handler_active: true, scope: 'HANDLER' } }, audit_events: [{ action: 'SHUTDOWN', scope: 'HANDLER' }] }],
  [/^\/attendance\/admin\/history$/, { profile: { display_name: 'Asha Rao' }, eligibility: ELIGIBILITY, records: RECORDS }],
]
