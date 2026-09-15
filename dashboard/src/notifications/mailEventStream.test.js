import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  __resetMailEventStream,
  getMailAlertTrace,
  subscribeMailEvents,
  subscribeNodeActivity,
} from './mailEventStream.js'

class FakeSocket {
  static OPEN = 1
  static latest = null

  constructor() {
    this.readyState = FakeSocket.OPEN
    FakeSocket.latest = this
    queueMicrotask(() => this.onopen?.())
  }

  send() {}
  close() {}
}

class FakeChannel {
  static latest = null

  constructor() { FakeChannel.latest = this }
  postMessage() {}
  close() {}
}

const response = body => Promise.resolve({ ok: true, json: () => Promise.resolve(body) })

describe('mail event stream correlation', () => {
  beforeEach(() => {
    __resetMailEventStream()
    localStorage.clear()
    vi.stubGlobal('WebSocket', FakeSocket)
    vi.stubGlobal('BroadcastChannel', FakeChannel)
    vi.stubGlobal('fetch', vi.fn(() => response({ enabled: true })))
  })

  afterEach(() => {
    __resetMailEventStream()
    FakeSocket.latest = null
    FakeChannel.latest = null
    vi.unstubAllGlobals()
  })

  it('deduplicates the same event across WebSocket and cross-tab delivery', async () => {
    const received = []
    const unsubscribe = subscribeMailEvents((payload, meta) => received.push([payload, meta]))
    await vi.waitFor(() => expect(FakeSocket.latest).not.toBeNull())
    const event = {
      event: 'notification_created', event_id: 'event-1',
      notification_id: 'notification-1', classification: 'offer_received',
    }

    FakeSocket.latest.onmessage({ data: JSON.stringify(event) })
    FakeChannel.latest.onmessage({ data: event })

    expect(received).toHaveLength(1)
    expect(received[0][1]).toEqual({ fromSocket: true })
    expect(getMailAlertTrace()).toEqual([
      expect.objectContaining({
        stage: 'browser_received', event: 'notification_created', event_id: 'event-1',
        notification_id: 'notification-1', transport: 'websocket',
      }),
    ])
    unsubscribe()
  })
})

describe('AI node activity on the shared socket', () => {
  const activity = {
    event: 'ai_node_activity', boot: 'boot-1', version: 7,
    nodes: { rtx4060: [{ kind: 'booking_analysis', label: 'Booking analysis' }] },
  }

  beforeEach(() => {
    __resetMailEventStream()
    localStorage.clear()
    vi.stubGlobal('WebSocket', FakeSocket)
    vi.stubGlobal('BroadcastChannel', FakeChannel)
    vi.stubGlobal('fetch', vi.fn(() => response({ enabled: true })))
  })

  afterEach(() => {
    __resetMailEventStream()
    FakeSocket.latest = null
    FakeChannel.latest = null
    vi.unstubAllGlobals()
  })

  it('reaches activity subscribers and nothing on the mail path', async () => {
    const mail = []
    const nodes = []
    const offMail = subscribeMailEvents(payload => mail.push(payload))
    const offNodes = subscribeNodeActivity(payload => nodes.push(payload))
    await vi.waitFor(() => expect(FakeSocket.latest).not.toBeNull())
    const channelPost = vi.spyOn(FakeChannel.latest, 'postMessage')

    FakeSocket.latest.onmessage({ data: JSON.stringify(activity) })

    expect(nodes).toEqual([activity])
    // Not an alert: no mail subscriber refetches or sounds, nothing is traced,
    // mirrored to other tabs, or used as the replay cursor.
    expect(mail).toEqual([])
    expect(getMailAlertTrace()).toEqual([])
    expect(channelPost).not.toHaveBeenCalled()
    expect(localStorage.getItem('teleautomation-mail-last-event-id')).toBeNull()
    offMail()
    offNodes()
  })

  it('is delivered every time, never deduplicated like an alert', async () => {
    const nodes = []
    const off = subscribeNodeActivity(payload => nodes.push(payload))
    await vi.waitFor(() => expect(FakeSocket.latest).not.toBeNull())
    FakeSocket.latest.onmessage({ data: JSON.stringify(activity) })
    FakeSocket.latest.onmessage({ data: JSON.stringify(activity) })
    expect(nodes).toHaveLength(2)
    off()
  })

  it('opens the socket on its own when nothing else is listening', async () => {
    const off = subscribeNodeActivity(() => {})
    await vi.waitFor(() => expect(FakeSocket.latest).not.toBeNull())
    off()
  })
})
