/**
 * Attended on a Profile-wise L2, HR or Final round needs at least one of the
 * candidate's Gmail accounts with "Monitoring Active" in AI Mail Review (owner,
 * 26 Nov). One such Gmail is enough; other Gmail accounts needing reconnection
 * do not block. Round-wise bookings, other rounds and other outcomes are not
 * gated. The server holds the same rule (features/attendance_gmail_gate.py) and
 * matches the Gmail by Candidate ID, including linked ids.
 */
export const GATED_ROUNDS = ['L2', 'HR', 'Final']
export const GMAIL_MONITORING_REQUIRED_MESSAGE =
  "Attended needs at least one Gmail with Monitoring Active in AI Mail Review for this candidate. Connect the candidate's Gmail, then mark Attended."

/** Profile-wise unless the booking is explicitly Round-wise (as the server reads it). */
export function isProfileWise(row) {
  return String(row?.service_type || '').trim().toLowerCase() !== 'round_wise'
}

/** Whether marking `status` on this booking, at `round`, needs a monitored Gmail. */
export function requiresMonitoredGmail(row, { status, round }) {
  return status === 'attended' && isProfileWise(row) && GATED_ROUNDS.includes(String(round || '').trim())
}

/** `{ active, monitored }` for this booking's candidate, from the server. */
export async function fetchMonitoredGmail(api, rowId, fetchImpl = fetch) {
  const res = await fetchImpl(`${api}/candidates/${encodeURIComponent(rowId)}/attended-gmail`, { credentials: 'include' })
  const data = await res.json()
  if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Gmail monitoring could not be checked right now.')
  return { active: !!data.active, monitored: Array.isArray(data.monitored) ? data.monitored : [] }
}
