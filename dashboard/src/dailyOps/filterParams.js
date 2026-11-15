/**
 * Daily Ops filter fields go to the server as repeated query parameters
 * (`round=HR&round=L2`). The server reads repeated values in one field as
 * alternatives and different fields as all-must-match. A plain string is one
 * value, so callers that still hold a single choice keep working.
 */
export function appendFilterValues(params, key, value) {
  const list = Array.isArray(value) ? value : [value]
  for (const item of list) {
    if (typeof item === 'string' && item.trim() !== '') params.append(key, item)
  }
  return params
}

/** The values as one readable phrase: "HR or L2 or L3". */
export function describeFilterValues(value) {
  const list = (Array.isArray(value) ? value : [value]).filter(v => typeof v === 'string' && v.trim() !== '')
  return list.join(' or ')
}

/** Two filter lists hold the same values in the same order. */
export function sameValues(a, b) {
  return a.length === b.length && a.every((value, index) => value === b[index])
}
