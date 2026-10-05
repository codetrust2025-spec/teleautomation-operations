/**
 * Locks the decommission of Daily Briefing, Mail Audit, Payment
 * Reconciliation, BGV Register, Handler Kit and Settings.
 *
 * A sidebar that merely looks right is not evidence: the previous shells kept
 * whole panels alive behind view ids that nothing linked to. These tests assert
 * the sidebar contents *and* that the implementations are gone from the tree,
 * so a future re-import is a failing test rather than a silent resurrection.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(SRC, rel), 'utf8')

const SIDEBAR = ['Daily Ops', 'Mail Alerts', 'AI Mail Review', 'Candidates', 'Data Room', 'Attendance', 'Slot Booking']

const REMOVED_MODULES = [
  'components/DailyBriefingCard.jsx',
  'components/OutcomeAuditPanel.jsx',
  'components/PaymentReconciliationPanel.jsx',
  'components/BgvRegisterPanel.jsx',
  'components/HandlerKitPanel.jsx',
  'components/OcrPolicyPanel.jsx',
  'components/MailMonitoringTabs.jsx',
  'components/dailyBriefing.css',
  'outcomeAudit.css',
]

// View ids the removed panels were mounted on. `settings` is matched with its
// quotes so it cannot collide with unrelated words in the shell.
const REMOVED_VIEW_IDS = ['daily-briefing', 'outcome-audit', 'payment-reconciliation', 'bgv-register', 'handler-kit', "'settings'"]

describe('operations sidebar', () => {
  const app = read('App.jsx')
  const labels = [...app.matchAll(/\blabel:\s*'([^']+)'/g)].map((m) => m[1])

  it('lists exactly the shipped features, in order', () => {
    expect(labels).toEqual(SIDEBAR)
  })

  it('names no decommissioned feature', () => {
    for (const gone of ['Daily briefing', 'Mail Audit', 'Payment reconciliation', 'BGV register', 'Handler kit', 'Settings']) {
      expect(labels).not.toContain(gone)
    }
  })
})

describe('decommissioned features are absent from the tree', () => {
  for (const module of REMOVED_MODULES) {
    it(`${module} no longer exists`, () => {
      expect(existsSync(join(SRC, module))).toBe(false)
    })
  }

  it('the shell mounts none of their view ids', () => {
    const app = read('App.jsx')
    for (const id of REMOVED_VIEW_IDS) expect(app).not.toContain(id)
  })

  it('mail alerts no longer links across to the audit page', () => {
    const notifications = read('components/MailMonitoringNotifications.jsx')
    expect(notifications).not.toContain('MailMonitoringTabs')
    expect(notifications).not.toContain('outcome-audit')
  })
})

describe('OCR survives inside AI Mail Review', () => {
  it('is rendered by the AI Mail Review panel', () => {
    const panel = read('components/RecruitmentMailPanelRedesign.jsx')
    expect(panel).toContain('OcrToggle')
    expect(panel).toContain('<OcrToggle />')
  })

  it('drives the existing policy routes rather than a new mechanism', () => {
    const toggle = read('components/OcrToggle.jsx')
    expect(toggle).toContain('/ai/ocr-policy')
    expect(toggle).toContain("method: 'PUT'")
  })

  it('keeps the write admin-only on the client as well as the server', () => {
    const toggle = read('components/OcrToggle.jsx')
    expect(toggle).toContain('isAdmin')
    expect(toggle).toMatch(/disabled=\{saving \|\| !isAdmin\}/)
  })
})
