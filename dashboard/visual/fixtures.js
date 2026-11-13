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


// AI Controls: three invented nodes, one slow and one unreachable, so every
// tone (good, warning, bad, neutral) shows up in the screenshots.
const AI_INVENTORY = [
  { id: 'node-a', label: 'Node A (GPU)', available: true, models: [{ name: 'qwen2.5:7b', size_bytes: 4.7e9 }, { name: 'llama3.1:8b', size_bytes: 4.9e9 }, { name: 'qwen3-vl:8b-instruct', size_bytes: 6.14e9 }, { name: 'nomic-embed-text', size_bytes: 2.7e8 }] },
  { id: 'node-b', label: 'Node B', available: true, models: [{ name: 'qwen2.5:7b', size_bytes: 4.7e9 }, { name: 'validator:9b', size_bytes: 5.4e9 }] },
  { id: 'node-c', label: 'Node C (spare)', available: false, models: [{ name: 'qwen2.5:7b', size_bytes: 4.7e9 }] },
]
const AI_NODES = AI_INVENTORY.map((n, i) => ({
  ...n, endpoint_reachable: i < 2, ready: i === 0, status: i < 2 ? 'online' : 'offline', primary: i === 0,
  response_time_ms: [182, 1240, null][i], loaded_models: i === 0 ? ['qwen2.5:7b'] : [],
  required_models: { 'qwen2.5:7b': true, 'validator:9b': i === 1 }, ollama_version: '0.5.7',
  breaker: { consecutive_failures: [0, 2, 9][i], in_cooldown: i === 2, cooldown_remaining_s: i === 2 ? 45 : 0 },
  gpu: i === 0 ? { size_bytes: 4.76e9, size_vram_bytes: 4.76e9 } : undefined,
}))
const AI_ROUTES = [
  [/^\/api\/ai-recruitment\/mail-model$/, { selection: { model: 'qwen2.5:7b', revision: 3, last_used: { model: 'qwen2.5:7b', node_label: 'Node A (GPU)' } }, nodes: AI_INVENTORY }],
  [/^\/api\/ai-recruitment\/ollama\/nodes$/, { primary_node: 'node-a', nodes: AI_NODES, activity: { boot: 'v', version: 1, nodes: {} } }],
  [/^\/api\/ai-recruitment\/ollama\/status$/, { models: { primary: 'qwen2.5:7b', validator: 'validator:9b', vision: 'qwen3-vl:8b-instruct', fallback: 'llama3.1:8b' }, ollama: { status: 'degraded', configured_model: 'qwen2.5:7b', successful_requests: 1284, failed_requests: 17, last_checked_at: '2026-11-12T04:58:00Z', last_successful_request_at: '2026-11-12T04:57:12Z', error_code: 'NODE_UNREACHABLE', error_message: 'Node C (spare) did not answer within 5 s.' } }],
  [/^\/api\/ai-recruitment\/ollama\/activity$/, { activity: { boot: 'v', version: 2, nodes: {} } }],
  [/^\/api\/ai-recruitment\/metrics$/, { metrics: { queue_depth: 3, ai_retry_pending_total: 0, ai_processing_failure_total: 12, mailbox_sync_success_total: 80199, mailbox_sync_failure_total: 0, websocket_connection_count: 2, ai_processing_duration_ms_avg: 5476.3 } }],
  [/^\/api\/ai-recruitment\/config$/, { enabled: true, mailbox_sync_enabled: true, interview_auto_booking_enabled: true, offer_verification_enabled: false, oauth_configured: true, gmail_pubsub_configured: true }],
  [/^\/api\/candidate-mailboxes\/health$/, { mailboxes: [{ connection_status: 'ERROR' }, { connection_status: 'CONNECTED' }, { connection_status: 'CONNECTED' }] }],
  [/^\/api\/ai-recruitment\/review$/, { events: [
    { id: 'e1', created_at: '2026-11-12T04:50:00Z', ai_model: 'qwen2.5:7b@node-a', validation_status: 'AUTO_VALIDATED', processing_duration_ms: 4210 },
    { id: 'e2', created_at: '2026-11-12T04:41:00Z', ai_model: 'qwen2.5:7b@node-b', validation_status: 'NEEDS_REVIEW', processing_duration_ms: 9870 },
    { id: 'e3', created_at: '2026-11-12T04:30:00Z', ai_model: 'qwen2.5:7b@node-a', validation_status: 'AUTO_VALIDATED', processing_duration_ms: 3990 },
    { id: 'e4', created_at: '2026-11-12T04:12:00Z', ai_model: null, validation_status: null, processing_duration_ms: null },
  ] }],
  [/^\/ai\/pure-ollama-policy$/, { enabled: true }],
  [/^\/ai\/ocr-policy$/, { enabled: false }],
  [/^\/ai\/(pure-ollama|ocr)-policy\/audit$/, { entries: [{ at: '2026-11-11T09:00:00Z', actor: 'owner', previous: false, new: true }] }],
]

export const routes = [
  ...AI_ROUTES,
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
