/**
 * Screenshot pages of the visual harness at fixed widths.
 *
 *   npm i --no-save playwright-core     # once; drives the installed Edge/Chrome
 *   npx vite --port 3100                # serves visual/index.html
 *   node visual/capture.mjs --out ../.visual/before --pages "Attendance:admin,Attendance:handler"
 *   node visual/capture.mjs --out ../.visual/before --pages "AI Controls:admin:Monitoring"
 *
 * Writes <out>/<page>-<role>-<width>.png (full page) and <out>/report.json
 * with console errors and any API route the fixtures do not answer yet.
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from 'playwright-core'

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, arg, i, all) => (
  arg.startsWith('--') ? [...pairs, [arg.slice(2), all[i + 1]]] : pairs), []))
const out = args.out || 'visual-out'
const base = args.base || 'http://127.0.0.1:3100/visual/index.html'
const widths = (args.widths || '390,768,1024,1440').split(',').map(Number)
const pages = (args.pages || 'Attendance:admin').split(',').map(spec => {
  const [view, role = 'admin', tab = ''] = spec.split(':')
  return { view, role, tab }
})
const HEIGHT = { 390: 844, 768: 1024, 1024: 768, 1440: 900 }

mkdirSync(out, { recursive: true })
const browser = await chromium.launch({ channel: args.channel || 'msedge' })
const report = []
for (const { view, role, tab } of pages) {
  for (const width of widths) {
    const context = await browser.newContext({
      viewport: { width, height: HEIGHT[width] || 900 },
      deviceScaleFactor: 1,
      isMobile: width < 600,
      hasTouch: width < 600,
      reducedMotion: 'reduce',
      timezoneId: 'Asia/Kolkata',
      locale: 'en-IN',
    })
    const page = await context.newPage()
    const errors = []
    page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })
    page.on('pageerror', err => errors.push(String(err)))
    await page.goto(`${base}?role=${role}&view=${encodeURIComponent(view)}`)
    await page.waitForSelector('body[data-harness-ready="1"]', { timeout: 15000 }).catch(() => {})
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(600)
    // A phone opens with the drawer closed, as a person sees it.
    await page.evaluate(() => document.querySelector('.business-nav-backdrop')?.click())
    await page.waitForTimeout(200)
    // "Label:role:Tab" opens one of the page's own tabs (a button with that
    // text); "~Text" matches a button whose name starts with it, such as a
    // button that carries a count ("Interviews 61").
    if (tab) {
      const loose = tab.startsWith('~')
      const escaped = tab.slice(1).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      const name = loose ? new RegExp(`^${escaped}`) : tab
      await page.getByRole('button', { name, exact: !loose }).first().click()
      await page.waitForTimeout(400)
    }
    // From 900px up the shell is one screen tall and the page scrolls inside
    // .desktop-body, so a full-page shot would stop at the fold. Growing the
    // window to the content's height shows all of it with the real layout.
    const inner = await page.evaluate(() => {
      const body = document.querySelector('.desktop-body')
      return body && body.scrollHeight > body.clientHeight ? body.scrollHeight - body.clientHeight : 0
    })
    if (inner > 0) {
      await page.setViewportSize({ width, height: (HEIGHT[width] || 900) + inner })
      await page.waitForTimeout(300)
    }
    const slug = text => text.toLowerCase().replace(/[^a-z0-9]+/g, '-')
    const name = `${slug(view)}${tab ? `-${slug(tab.replace(/^~/, ''))}` : ''}-${role}-${width}.png`
    await page.screenshot({ path: join(out, name), fullPage: true })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    const unmocked = await page.evaluate(() => [...new Set(window.__unmockedRoutes || [])])
    report.push({ view, tab, role, width, file: name, horizontalOverflowPx: overflow, errors, unmocked })
    await context.close()
  }
}
await browser.close()
writeFileSync(join(out, 'report.json'), JSON.stringify(report, null, 2))
for (const r of report) {
  console.log(`${r.file}  overflow ${r.horizontalOverflowPx}px  errors ${r.errors.length}  unmocked ${r.unmocked.length}`)
}
