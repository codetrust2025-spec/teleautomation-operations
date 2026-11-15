// How an attended interview went, best to worst. Values match the server's
// normalise_interview_feedback scale; "negative" is shared with the legacy
// two-value scale so old rows stay valid.
export const FEEDBACK_OPTIONS = [
  { value: 'excellent', label: 'Excellent' },
  { value: 'good', label: 'Good' },
  { value: 'average', label: 'Average' },
  { value: 'needs_improvement', label: 'Needs Improvement' },
  { value: 'negative', label: 'Negative' },
]

// What the Status column shows for a stored feedback value: its own label and
// a tone. The column used to print "Positive" for the legacy "positive" and
// "Negative" for everything else, so a row the operator marked Good or
// Excellent read "Negative". Every value now names itself; only "negative"
// says Negative.
// Colours per the owner's Status mockup (15 Nov): Excellent teal, Good green,
// Average yellow, Needs Improvement orange, Negative red.
export const FEEDBACK_TONES = {
  excellent: 'excellent',
  good: 'positive',
  positive: 'positive',
  average: 'caution',
  needs_improvement: 'warning',
  negative: 'negative',
}

export function feedbackMeta(value) {
  const key = String(value || '').trim()
  if (!key) return null
  const option = FEEDBACK_OPTIONS.find(o => o.value === key)
  const label = option
    ? option.label
    : key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, ' ')
  return { value: key, label, tone: FEEDBACK_TONES[key] || 'neutral' }
}

/** The Status filter's values, in the owner's mockup order (15 Nov). */
export const FEEDBACK_FILTER_VALUES = ['good', 'excellent', 'average', 'needs_improvement', 'negative']

/** A row's stored feedback, as the filter compares it. */
export function rowFeedback(row) {
  return String(row?.interview_feedback || '').trim().toLowerCase()
}

/** Rows whose feedback is any of `values`; every row when none is chosen. */
export function filterByFeedback(rows, values) {
  const list = Array.isArray(rows) ? rows : []
  if (!values || values.length === 0) return list
  const wanted = new Set(values)
  return list.filter(row => wanted.has(rowFeedback(row)))
}

/** How many rows carry each feedback value ({ good: 3, ... }); rows with none are not counted. */
export function countFeedback(rows) {
  const counts = {}
  for (const row of Array.isArray(rows) ? rows : []) {
    const key = rowFeedback(row)
    if (key) counts[key] = (counts[key] || 0) + 1
  }
  return counts
}
