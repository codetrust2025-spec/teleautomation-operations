import React from 'react'
import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const roster = fs.readFileSync(path.join(__dirname, 'InterviewRoster.jsx'), 'utf8')
const provider = fs.readFileSync(path.join(__dirname, 'PendingWorksProvider.jsx'), 'utf8')

describe('Awaiting outcome section in Daily Ops', () => {
  it('tracks awaitingRows from the monitor payload', () => {
    expect(roster).toContain('const [awaitingRows, setAwaitingRows] = useState([])')
    expect(roster).toContain('const awaiting = data.awaiting_interviews || []')
    expect(roster).toContain('setAwaitingRows(awaiting)')
  })

  it('calculates totalPending as upcoming pending + awaitingRows length', () => {
    expect(roster).toMatch(/const totalPending = upcomingOnly\s*\?\s*\(nextCounts\.pending_count \|\| 0\) \+ awaiting\.length\s*:\s*nextCounts\.pending_count/)
    expect(roster).toContain('publishPendingWorkChanged(totalPending)')
  })

  it('renders the separate Awaiting outcome section when upcomingOnly and awaiting rows exist', () => {
    expect(roster).toContain('className="ops-awaiting-outcome-section"')
    expect(roster).toContain('<h3>Awaiting outcome</h3>')
    expect(roster).toContain('className="ops-awaiting-badge"')
    expect(roster).toContain('ops-interview-row--awaiting')
  })

  it('PendingWorksProvider polls upcoming with a 30-day window matching Daily Ops preset', () => {
    expect(provider).toMatch(/usePendingInterviewsQuery\(\{\s*enabled: authReady,\s*deferMs: 8000,\s*days: 30,\s*\}\)/)
  })
})
