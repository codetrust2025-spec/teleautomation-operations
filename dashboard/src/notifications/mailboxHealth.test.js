/**
 * One mailbox-health read per tab at a time, heard by everything that shows it.
 *
 * The reconnect badge (every minute), the alert sound (every two minutes) and
 * the AI Mail Review page (every 30 s) each read the same snapshot on their own
 * timers. Now a read in flight is shared and every reader hears every result.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  __resetMailboxHealth,
  mailboxHealthReadAt,
  onMailboxHealth,
  readMailboxHealth,
} from './mailboxHealth.js'

let release
beforeEach(() => {
  __resetMailboxHealth()
  vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => {
    release = (mailboxes, ok = true) => resolve({ ok, json: async () => ({ mailboxes }) })
  })))
})
afterEach(() => { __resetMailboxHealth(); vi.unstubAllGlobals() })

describe('shared reads', () => {
  it('asks once while a read is on its way', async () => {
    const reads = [readMailboxHealth(), readMailboxHealth(), readMailboxHealth()]
    expect(fetch).toHaveBeenCalledTimes(1)
    release([{ id: 'mb-1' }])
    const bodies = await Promise.all(reads)
    expect(bodies.every(body => body.mailboxes[0].id === 'mb-1')).toBe(true)
  })

  it('asks again once the last read is back', async () => {
    const first = readMailboxHealth()
    release([])
    await first
    readMailboxHealth()
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('keeps the cache-buster so a reconnect is never shown stale', () => {
    readMailboxHealth()
    expect(String(fetch.mock.calls[0][0])).toMatch(/\/api\/candidate-mailboxes\/health\?_=\d+/)
  })
})

describe('everyone hears every snapshot', () => {
  it('hands the result to each listener and records when it came', async () => {
    const heard = []
    onMailboxHealth(body => heard.push(['badge', body.mailboxes.length]))
    onMailboxHealth(body => heard.push(['sound', body.mailboxes.length]))
    expect(mailboxHealthReadAt()).toBe(0)
    const read = readMailboxHealth()
    release([{ id: 'a' }, { id: 'b' }])
    await read
    expect(heard).toEqual([['badge', 2], ['sound', 2]])
    expect(mailboxHealthReadAt()).toBeGreaterThan(0)
  })

  it('does not let one broken listener stop the others', async () => {
    const heard = []
    onMailboxHealth(() => { throw new Error('broken') })
    onMailboxHealth(body => heard.push(body.mailboxes.length))
    const read = readMailboxHealth()
    release([{ id: 'a' }])
    await read
    expect(heard).toEqual([1])
  })

  it('tells nobody anything when the read fails', async () => {
    const heard = []
    onMailboxHealth(body => heard.push(body))
    const read = readMailboxHealth()
    release([], false)
    await expect(read).rejects.toThrow()
    expect(heard).toEqual([])
    expect(mailboxHealthReadAt()).toBe(0)
  })
})
