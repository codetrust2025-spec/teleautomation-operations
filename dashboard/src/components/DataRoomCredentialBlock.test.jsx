/**
 * The copy block must not offer a password that no longer exists.
 *
 * Handler passwords are hashed and the server stopped returning them, so
 * `row.password` is undefined for handlers. Interpolating it produced
 * "Password: undefined", which reads as a broken row rather than as a password
 * that deliberately cannot be read back.
 */
import { describe, it, expect } from 'vitest'
import { formatCredentialBlock } from './DataRoomPanel.jsx'

describe('formatCredentialBlock', () => {
  it('says a handler password cannot be recovered instead of printing undefined', () => {
    const block = formatCredentialBlock('ops.example', {
      username: 'referrer-thrilok',
      reference: 'Thrilok',
    })

    expect(block).not.toContain('undefined')
    expect(block).toContain('Username: referrer-thrilok')
    expect(block).toContain('Reference: Thrilok')
    expect(block).toContain('Not available (hashed)')
  })

  it('still prints a password the server does return', () => {
    const block = formatCredentialBlock(
      'ops.example',
      { username: 'operations_admin', password: 'admin-pw' },
      { isAdmin: true },
    )

    expect(block).toContain('Password: admin-pw')
    expect(block).toContain('Role: admin (full dashboard)')
  })
})
