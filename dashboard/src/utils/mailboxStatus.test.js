/**
 * Reconnect-required selection and wording.
 *
 * Every fixture here uses the exact column set `mailbox_health_rows` selects
 * (core/recruitment_mail_store.py) — id, candidate_id, canonical_candidate_id,
 * email_address, connection_status, monitoring_enabled, last_error_code,
 * last_error_message, last_successful_sync_at, updated_at. There is no name
 * column, and that absence is the whole bug: the old summary counted only rows
 * carrying a name, so a real fault reported "0 candidate accounts" while the
 * dashboard listed those same mailboxes as Reconnect Required.
 */

import { describe, expect, it } from 'vitest'
import {
  describeReconnectTargets,
  needsReconnect,
  reconnectRequiredMailboxes,
} from './mailboxStatus.js'

/**
 * A row exactly as /api/candidate-mailboxes/health returns it.
 *
 * The canonical id follows the candidate id unless a test overrides it
 * explicitly, mirroring production where an unaliased row is its own canonical.
 */
const healthRow = (overrides = {}) => {
  const candidateId = overrides.candidate_id ?? 'cand-1'
  return {
    id: 'mailbox-1',
    candidate_id: candidateId,
    canonical_candidate_id: candidateId,
    email_address: 'someone@example.com',
    connection_status: 'ERROR',
    monitoring_enabled: false,
    last_error_code: 'GMAIL_TOKEN_EXPIRED',
    last_error_message: 'Token has been expired or revoked.',
    last_successful_sync_at: null,
    updated_at: '2026-08-12T04:29:03Z',
    ...overrides,
  }
}

describe('needsReconnect', () => {
  it('flags an errored mailbox and an expired or revoked token', () => {
    expect(needsReconnect(healthRow())).toBe(true)
    expect(
      needsReconnect(
        healthRow({ connection_status: 'CONNECTED', last_error_message: 'expired' }),
      ),
    ).toBe(true)
    expect(
      needsReconnect(
        healthRow({ connection_status: 'CONNECTED', last_error_message: 'revoked' }),
      ),
    ).toBe(true)
  })

  it('leaves a healthy mailbox alone', () => {
    expect(
      needsReconnect(
        healthRow({ connection_status: 'CONNECTED', last_error_message: null }),
      ),
    ).toBe(false)
  })
})

describe('reconnectRequiredMailboxes', () => {
  it('selects only the broken rows and keeps the email as the label', () => {
    const rows = reconnectRequiredMailboxes([
      healthRow({ id: 'm1', email_address: 'a@example.com' }),
      healthRow({
        id: 'm2',
        connection_status: 'CONNECTED',
        last_error_message: null,
      }),
    ])
    expect(rows).toHaveLength(1)
    expect(rows[0].id).toBe('m1')
    expect(rows[0].email).toBe('a@example.com')
    // No name column exists upstream, so the label must not depend on one.
    expect(rows[0].name).toBe('')
  })

  it('prefers the canonical candidate id so split rows group as one person', () => {
    const [row] = reconnectRequiredMailboxes([
      healthRow({ candidate_id: 'alias-row', canonical_candidate_id: 'canonical-row' }),
    ])
    expect(row.candidateId).toBe('canonical-row')
  })

  it('survives a missing or malformed payload', () => {
    expect(reconnectRequiredMailboxes(undefined)).toEqual([])
    expect(reconnectRequiredMailboxes(null)).toEqual([])
    expect(reconnectRequiredMailboxes([])).toEqual([])
  })
})

describe('describeReconnectTargets', () => {
  it('never reports a zero count for real broken mailboxes', () => {
    // The regression: production rows carry no name at all.
    const rows = reconnectRequiredMailboxes([
      healthRow({ id: 'm1', candidate_id: 'c1', email_address: 'a@example.com' }),
      healthRow({ id: 'm2', candidate_id: 'c2', email_address: 'b@example.com' }),
      healthRow({ id: 'm3', candidate_id: 'c3', email_address: 'c@example.com' }),
    ])
    const summary = describeReconnectTargets(rows)
    expect(summary).not.toMatch(/\b0\b/)
    expect(summary).not.toContain('0 candidate accounts')
    expect(summary).toBe('3 Gmail accounts across 3 candidates')
  })

  it('names the single broken account by its email', () => {
    const rows = reconnectRequiredMailboxes([
      healthRow({ id: 'm1', email_address: 'vikas.joshi@example.com' }),
    ])
    expect(describeReconnectTargets(rows)).toBe('vikas.joshi@example.com')
  })

  it('counts accounts, not people, when one candidate has several mailboxes', () => {
    const rows = reconnectRequiredMailboxes([
      healthRow({ id: 'm1', candidate_id: 'c1', email_address: 'first@example.com' }),
      healthRow({ id: 'm2', candidate_id: 'c1', email_address: 'second@example.com' }),
    ])
    expect(describeReconnectTargets(rows)).toBe(
      '2 Gmail accounts for first@example.com',
    )
  })

  it('reports both totals when several candidates are affected', () => {
    const rows = reconnectRequiredMailboxes([
      healthRow({ id: 'm1', candidate_id: 'c1', email_address: 'a@example.com' }),
      healthRow({ id: 'm2', candidate_id: 'c1', email_address: 'b@example.com' }),
      healthRow({ id: 'm3', candidate_id: 'c2', email_address: 'c@example.com' }),
    ])
    expect(describeReconnectTargets(rows)).toBe(
      '3 Gmail accounts across 2 candidates',
    )
  })

  it('prefers a candidate name when a richer payload supplies one', () => {
    const rows = reconnectRequiredMailboxes([
      healthRow({ id: 'm1', candidate_name: 'Arun Kumar Pillai' }),
    ])
    expect(describeReconnectTargets(rows)).toBe('Arun Kumar Pillai')
  })

  it('renders nothing for an empty list rather than a zero', () => {
    expect(describeReconnectTargets([])).toBe('')
    expect(describeReconnectTargets(undefined)).toBe('')
  })
})
