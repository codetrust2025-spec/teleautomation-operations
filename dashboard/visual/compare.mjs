/**
 * Compare two capture folders pixel by pixel and write a side-by-side page.
 *
 *   node visual/compare.mjs --before ../.visual/before --after ../.visual/after --out ../.visual/compare.html
 *
 * Prints, per screenshot, the share of pixels that differ (a channel off by
 * more than 24 of 255) and whether the page height changed.
 */
import { readFileSync, readdirSync, writeFileSync, existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { chromium } from 'playwright-core'

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, arg, i, all) => (
  arg.startsWith('--') ? [...pairs, [arg.slice(2), all[i + 1]]] : pairs), []))
const before = resolve(args.before)
const after = resolve(args.after)
const files = readdirSync(after).filter(name => name.endsWith('.png') && existsSync(join(before, name))).sort()
const dataUrl = path => `data:image/png;base64,${readFileSync(path).toString('base64')}`

const browser = await chromium.launch({ channel: args.channel || 'msedge' })
const page = await browser.newPage()
const results = []
for (const name of files) {
  const result = await page.evaluate(async ([a, b]) => {
    const load = src => new Promise((ok, fail) => { const img = new Image(); img.onload = () => ok(img); img.onerror = fail; img.src = src })
    const [ia, ib] = await Promise.all([load(a), load(b)])
    const w = Math.max(ia.width, ib.width)
    const h = Math.max(ia.height, ib.height)
    const draw = img => { const c = document.createElement('canvas'); c.width = w; c.height = h; const x = c.getContext('2d'); x.fillStyle = '#ff00ff'; x.fillRect(0, 0, w, h); x.drawImage(img, 0, 0); return x.getImageData(0, 0, w, h).data }
    const da = draw(ia)
    const db = draw(ib)
    let diff = 0
    for (let i = 0; i < da.length; i += 4) {
      if (Math.abs(da[i] - db[i]) > 24 || Math.abs(da[i + 1] - db[i + 1]) > 24 || Math.abs(da[i + 2] - db[i + 2]) > 24) diff++
    }
    return { diffShare: diff / (w * h), beforeHeight: ia.height, afterHeight: ib.height }
  }, [dataUrl(join(before, name)), dataUrl(join(after, name))])
  results.push({ name, ...result })
  console.log(`${name}  ${(result.diffShare * 100).toFixed(2)}% pixels differ  height ${result.beforeHeight} -> ${result.afterHeight}`)
}
await browser.close()

if (args.out) {
  const rows = results.map(r => `<section><h2>${r.name} <small>${(r.diffShare * 100).toFixed(2)}% of pixels differ · height ${r.beforeHeight} → ${r.afterHeight}px</small></h2><div class="pair"><figure><figcaption>Before</figcaption><img src="${dataUrl(join(before, r.name))}"></figure><figure><figcaption>After</figcaption><img src="${dataUrl(join(after, r.name))}"></figure></div></section>`).join('\n')
  writeFileSync(args.out, `<!doctype html><meta charset="utf-8"><title>Visual comparison</title><style>body{background:#0b0d12;color:#e7edf6;font:14px system-ui;margin:24px}h2{font-size:15px}small{color:#94a3b8;font-weight:400}.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}img{max-width:100%;border:1px solid #253145}figcaption{color:#94a3b8;margin-bottom:6px}</style>${rows}`)
}
writeFileSync(join(after, 'compare.json'), JSON.stringify(results, null, 2))
