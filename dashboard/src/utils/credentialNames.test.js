/**
 * Data Room credential names (20 Oct): the browser's twin of features/credential_names.py.
 *
 * The cases are shared (tests/fixtures/credential_names.json) and run against both, so the table and every
 * exported file always name a credential alike.
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { LOGINS_TYPE, credentialType, standardCredentialName } from './credentialNames.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const { cases: CASES } = JSON.parse(readFileSync(join(HERE, '..', '..', '..', 'tests', 'fixtures', 'credential_names.json'), 'utf8'))

describe('a credential name', () => {
  it('has cases to check', () => expect(CASES.length).toBeGreaterThan(10))

  it.each(CASES)('%j under %j reads %j', (stored, service, shown) => {
    expect(standardCredentialName(stored, service)).toBe(shown)
  })

  it.each(CASES)('named twice, %j stays as it is', (_stored, service, shown) => {
    expect(standardCredentialName(shown, service)).toBe(shown)
  })

  it('can keep its type word', () => {
    expect(standardCredentialName('asha rao naukri', 'Naukri', { dropType: false })).toBe('Asha Rao Naukri')
  })
})

describe('a credential type', () => {
  it.each([
    ['Naukri', false, 'Naukri'], ['Gmail', false, 'Gmail'], ['Telegram', false, 'Telegram'],
    ['Tool', false, LOGINS_TYPE], ['', false, 'Not Set'], ['LinkedIn', false, 'Not Set'], ['', true, LOGINS_TYPE],
  ])('%j (login %j) is %j', (service, login, shown) => {
    expect(credentialType(service, { login })).toBe(shown)
  })
})
