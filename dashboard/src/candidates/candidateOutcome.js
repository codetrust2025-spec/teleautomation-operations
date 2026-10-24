/**
 * Selected: the final outcome of a successful profile-wise candidate.
 *
 * A profile-wise candidate whose stage is Closed / Completed has been placed,
 * and every page shows them as ★ Selected -- Candidates, Overview, Earnings,
 * Daily Ops, Bookings Analytics. It is derived, never stored: the stage stays
 * `completed` and the service type `profile_service`, so every filter, payment
 * and complimentary rule that reads those values is unchanged. A round-wise
 * candidate who completes stays Closed / Completed.
 *
 * The server decides (`candidate_is_selected` in features/candidate_store.py)
 * and sends the answer on every candidate and interview row as
 * `candidate_selected`; the rule is repeated here only for a row that predates
 * the field. Selected is the candidate's outcome -- not an interview
 * attendance status, and not an interview's own result, which can also read
 * "Selected" for one round.
 */

export const SELECTED_LABEL = 'Selected'

/** The stored stages and what each is called, Selected aside. */
export const STAGE_LABELS = {
  in_progress: 'In progress',
  completed: 'Closed / Completed',
  fail: 'Rejected',
  dropped: 'Dropped',
}

function isRoundWise(serviceType) {
  return String(serviceType || '').trim().toLowerCase() === 'round_wise'
}

export function isSelectedCandidate(row) {
  if (!row) return false
  if (typeof row.candidate_selected === 'boolean') return row.candidate_selected
  return String(row.stage || '').trim().toLowerCase() === 'completed' && !isRoundWise(row.service_type)
}

/** What a stage is called for a candidate of this service type. */
export function stageLabel(stage, serviceType) {
  const key = String(stage || '').trim().toLowerCase()
  if (key === 'completed' && !isRoundWise(serviceType)) return SELECTED_LABEL
  return STAGE_LABELS[key] || (key ? key.replace(/_/g, ' ') : '—')
}

/** A row's status as the page shows it. */
export function candidateStatusLabel(row) {
  return isSelectedCandidate(row) ? SELECTED_LABEL : stageLabel(row?.stage, row?.service_type)
}
