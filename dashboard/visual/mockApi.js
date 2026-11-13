/**
 * Visual harness only: answers the dashboard's API calls in the browser so a
 * page renders with fixed, invented data and no server. Never part of the
 * production build (vite builds index.html only).
 *
 * ?role=admin|handler picks the signed-in role; unknown routes answer {} and
 * are listed in window.__unmockedRoutes so fixtures can be filled in.
 */
import { routes } from './fixtures.js'

const params = new URLSearchParams(window.location.search)
export const ROLE = params.get('role') || 'admin'
export const VIEW = params.get('view') || ''

window.__unmockedRoutes = []
const realFetch = window.fetch.bind(window)

function respond(body, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  }))
}

window.fetch = (input, init = {}) => {
  const url = new URL(typeof input === 'string' ? input : input.url, window.location.origin)
  if (url.origin !== window.location.origin || url.pathname.startsWith('/src/') || url.pathname.startsWith('/@')) {
    return realFetch(input, init)
  }
  const method = (init.method || 'GET').toUpperCase()
  for (const [pattern, handler] of routes) {
    const match = url.pathname.match(pattern)
    if (match) return respond(typeof handler === 'function' ? handler({ url, method, match, role: ROLE }) : handler)
  }
  window.__unmockedRoutes.push(`${method} ${url.pathname}`)
  return respond({})
}

// No live streams in the harness.
window.EventSource = class { constructor() { this.readyState = 2 } addEventListener() {} removeEventListener() {} close() {} }
window.WebSocket = class { constructor() { this.readyState = 3 } addEventListener() {} removeEventListener() {} close() {} send() {} }
