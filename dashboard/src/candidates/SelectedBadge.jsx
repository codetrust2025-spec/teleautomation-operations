import React from 'react'
import { SELECTED_LABEL, isSelectedCandidate } from './candidateOutcome.js'
import './selectedBadge.css'

const MEANING = 'Selected: a profile-wise candidate whose profile is Closed / Completed'

/** The one ★ Selected badge every page draws beside a selected candidate. */
export function SelectedBadge({ className = '' }) {
  return (
    <span className={`ta-selected-badge${className ? ` ${className}` : ''}`} title={MEANING}>
      <span className="ta-selected-badge__star" aria-hidden="true">★</span>
      {SELECTED_LABEL}
    </span>
  )
}

/** The badge for a candidate or interview row, when that candidate is Selected. */
export function CandidateSelectedBadge({ row, className }) {
  return isSelectedCandidate(row) ? <SelectedBadge className={className} /> : null
}

export default SelectedBadge
