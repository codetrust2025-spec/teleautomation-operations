/**
 * What the four candidate stages are called, and what they still store.
 *
 * "Failed" was being read as a system failure rather than a rejected candidate,
 * and "Completed" did not obviously mean the file is closed. Both are now
 * relabelled — and only relabelled. The stored values stay `in_progress`,
 * `completed`, `fail` and `dropped`, because `fail` is the value every revenue
 * and slot query already excludes (`stage in {"dropped", "fail"}`); renaming it
 * would mean finding every one of those, and a missed site would quietly let a
 * rejected candidate back into revenue.
 *
 * The pending lists key on the stored value too: only `in_progress` reaches
 * Pending Gmail or Pending Works, so the three terminal stages disappear from
 * both the moment one is set.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const module_ = readFileSync(resolve(HERE, 'candidatesModule.jsx'), 'utf8')
const panel = readFileSync(
  resolve(HERE, '..', 'components', 'RecruitmentMailPanelRedesign.jsx'), 'utf8',
)

/** The stage options exactly as the editor's dropdown declares them. */
function stageOptions() {
  const block = module_.slice(module_.indexOf('const V8 = ['))
  const body = block.slice(0, block.indexOf('];') + 2)
  return [...body.matchAll(/value:\s*"([^"]+)",\s*(?:\/\/[^\n]*\n\s*)*label:\s*"([^"]+)"/g)]
    .map(([, value, label]) => ({ value, label }))
}

/**
 * Every label the module gives a stage, wherever it declares one.
 *
 * There are three places, and the first pass only found one: the editor's
 * dropdown, the stage filter, and the badge the table renders. Relabelling one
 * and shipping leaves the table still saying "Failed", so this collects them
 * all rather than trusting a single list.
 */
function everyStageLabel(value) {
  const pattern = new RegExp(
    `(?:value:\\s*"${value}",|\\b${value}:\\s*\\{)\\s*(?:\\/\\/[^\\n]*\\n\\s*)*label:\\s*"([^"]+)"`,
    'g',
  )
  return [...module_.matchAll(pattern)].map(([, label]) => label)
}

describe('the four stages', () => {
  it('are exactly the ones the backend accepts', () => {
    // VALID_STAGES = {"in_progress", "completed", "fail", "dropped"}
    expect(stageOptions().map(o => o.value).sort())
      .toEqual(['completed', 'dropped', 'fail', 'in_progress'])
  })

  it('stores no new value', () => {
    for (const { value } of stageOptions()) {
      expect(['in_progress', 'completed', 'fail', 'dropped']).toContain(value)
    }
  })
})

describe('what they are called', () => {
  const labelFor = (value) => stageOptions().find(o => o.value === value)?.label

  it('calls fail Rejected', () => {
    expect(labelFor('fail')).toBe('Rejected')
  })

  it('calls completed Closed / Completed', () => {
    expect(labelFor('completed')).toBe('Closed / Completed')
  })

  it('leaves the other two alone', () => {
    expect(labelFor('in_progress')).toBe('In progress')
    expect(labelFor('dropped')).toBe('Dropped')
  })

  it('no longer says Failed anywhere in the stage list', () => {
    expect(stageOptions().map(o => o.label)).not.toContain('Failed')
  })
})

describe('every place a stage is named agrees', () => {
  // The editor dropdown, the stage filter, and the badge on each row. The
  // first attempt relabelled only the dropdown and shipped a table still
  // reading "Failed", which is why this checks all of them.
  it('names fail in three places, all of them Rejected', () => {
    const labels = everyStageLabel('fail')
    expect(labels.length).toBe(3)
    expect(new Set(labels)).toEqual(new Set(['Rejected']))
  })

  it('names completed Closed / Completed, and the filter splits it into Selected and the rest', () => {
    // The editor's list and the row badge name the stored value. The stage
    // filter splits it by outcome (25 Oct): a profile-wise candidate whose
    // stage is completed reads ★ Selected (candidateOutcome.js), and the stage
    // editor relabels that option at render time; see CandidateSelectedStatus.
    const labels = everyStageLabel('completed')
    expect(labels.length).toBe(2)
    expect(new Set(labels)).toEqual(new Set(['Closed / Completed']))
    expect(everyStageLabel('selected')).toEqual(['Selected'])
    expect(everyStageLabel('closed_completed')).toEqual(['Closed / Completed'])
  })

  it('leaves in_progress and dropped consistent too', () => {
    expect(new Set(everyStageLabel('in_progress'))).toEqual(new Set(['In progress']))
    expect(new Set(everyStageLabel('dropped'))).toEqual(new Set(['Dropped']))
  })

  it('has no stage anywhere still labelled Failed', () => {
    for (const value of ['in_progress', 'completed', 'fail', 'dropped']) {
      expect(everyStageLabel(value)).not.toContain('Failed')
    }
  })
})

describe('only in_progress reaches the pending lists', () => {
  /** The Pending Gmail filter, exactly as the panel applies it. */
  const pendingGmail = (rows) => rows.filter((candidate) =>
    String(candidate.service_type || 'profile_service').toLowerCase() === 'profile_service'
    && String(candidate.stage || '').toLowerCase() === 'in_progress')

  const oneOfEach = [
    { id: '1', name: 'Still working', stage: 'in_progress', service_type: 'profile_service' },
    { id: '2', name: 'Closed out', stage: 'completed', service_type: 'profile_service' },
    { id: '3', name: 'Rejected', stage: 'fail', service_type: 'profile_service' },
    { id: '4', name: 'Dropped', stage: 'dropped', service_type: 'profile_service' },
  ]

  it('keeps one candidate in each stage apart', () => {
    expect(pendingGmail(oneOfEach).map(c => c.name)).toEqual(['Still working'])
  })

  it.each(['completed', 'fail', 'dropped'])('drops %s from Pending Gmail', (stage) => {
    expect(pendingGmail(oneOfEach.filter(c => c.stage === stage))).toEqual([])
  })

  it('is the filter the panel actually uses', () => {
    const block = panel.slice(panel.indexOf('const pendingMailboxCandidates'))
    expect(block.slice(0, 600)).toContain('"in_progress"')
    expect(block.slice(0, 600)).toContain('service_type')
  })

  it('asks the backend for in_progress only, for Pending Works', () => {
    // pending_works -> list_candidates(stage="in_progress"), so the three
    // terminal stages never reach the works builder at all.
    const provider = readFileSync(
      resolve(HERE, '..', 'dailyOps', 'PendingWorksProvider.jsx'), 'utf8',
    )
    expect(provider).toContain('/candidates/pending-works')
  })
})
