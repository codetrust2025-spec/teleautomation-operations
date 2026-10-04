/**
 * The Selected email modal, in the order it is actually read.
 *
 * The email is the thing being judged, and it was third: below a nine-card
 * metric grid, inside a section capped at 260px that scrolled the label and
 * the From/To/Subject/Received lines along with the body — so about two lines
 * of the email were visible, and long lines ran off sideways because <pre>
 * overrides the wrapping it inherits.
 */
import React from 'react'
import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const css = fs.readFileSync(
  path.join(__dirname, '..', 'recruitmentMail.css'), 'utf8',
)
const jsx = fs.readFileSync(
  path.join(__dirname, 'MailMonitoringNotifications.jsx'), 'utf8',
)

/** Body of the last rule with this exact selector. */
function rule(selector) {
  const found = [...css.matchAll(
    new RegExp(`(?:^|\\n)${selector}\\s*\\{([^}]*)\\}`, 'g'),
  )]
  return found.length ? found[found.length - 1][1] : ''
}

describe('the email comes first', () => {
  it('is rendered above the metric grid', () => {
    const email = jsx.indexOf('<OriginalEmail')
    const metrics = jsx.indexOf('<dl>')
    expect(email).toBeGreaterThan(-1)
    expect(metrics).toBeGreaterThan(-1)
    expect(email).toBeLessThan(metrics)
  })

  it('sits directly under the dialog header', () => {
    expect(jsx).toMatch(/<\/header>\s*<OriginalEmail/)
  })

  it('is followed by the summary, then the two AI sections', () => {
    const email = jsx.indexOf('<OriginalEmail')
    const summary = jsx.indexOf('<strong>Summary</strong>')
    const reason = jsx.indexOf('<summary>Detection reason</summary>')
    const action = jsx.indexOf('<summary>Recommended action</summary>')
    expect(email).toBeLessThan(summary)
    expect(summary).toBeLessThan(reason)
    expect(reason).toBeLessThan(action)
  })
})

describe('the body is not clipped', () => {
  it('no longer caps the whole view', () => {
    // The cap here was what squeezed the body down to a couple of lines.
    expect(rule('\\.gmail-view')).not.toMatch(/max-height/)
  })

  it('caps the body alone, and generously', () => {
    const body = rule('\\.gmail-view__body')
    expect(body).toMatch(/max-height:\s*min\(46vh,\s*520px\)/)
    expect(body).toMatch(/overflow-y:\s*auto/)
  })

  it('needs no reserved height now that it renders real paragraphs', () => {
    // The old `min-height: 180px` propped open a <pre> block. Real paragraphs
    // size themselves, so a three-line email is three lines tall rather than
    // three lines followed by a gap.
    expect(rule('\\.gmail-view__body')).not.toMatch(/min-height/)
  })

  it('wraps long lines instead of running off sideways', () => {
    // A meeting URL is one unbroken token; without this it pushes the dialog.
    const body = rule('\\.gmail-view__body')
    expect(body).toMatch(/overflow-wrap:\s*anywhere/)
    expect(body).toMatch(/overflow-x:\s*hidden/)
  })
})

const originalEmailJsx = fs.readFileSync(
  path.join(__dirname, 'OriginalEmail.jsx'), 'utf8',
)

describe('the header lines stay put', () => {
  it('still says who it is from, to whom, when, and about what', () => {
    // The four facts survived the redesign. They are laid out as an email
    // header now rather than as labelled From:/To:/Received:/Subject: lines.
    for (const part of [
      'gmail-view__subject', 'gmail-view__sender',
      'gmail-view__recipient', 'gmail-view__received',
    ]) {
      expect(originalEmailJsx).toContain(part)
    }
  })

  it('leaves the header outside the scrolling body', () => {
    // Only the body scrolls, so the sender and the date cannot scroll out of
    // view the way the old meta line could.
    const header = originalEmailJsx.indexOf('gmail-view__from')
    const body = originalEmailJsx.indexOf('gmail-view__body')
    expect(header).toBeGreaterThan(-1)
    expect(header).toBeLessThan(body)
    expect(rule('\\.gmail-view__from')).not.toMatch(/overflow/)
  })
})

describe('the lower AI sections fold away', () => {
  it('keeps the detector\'s own wording under the plain reason, inside the same open section', () => {
    // The backend rebuilds the reason in words and passes the original as
    // ai_reason_technical; it must not open a section of its own.
    const reason = jsx.indexOf('<summary>Detection reason</summary>')
    const technical = jsx.indexOf('item.ai_reason_technical &&')
    const action = jsx.indexOf('<summary>Recommended action</summary>')
    expect(technical).toBeGreaterThan(reason)
    expect(technical).toBeLessThan(action)
    expect(jsx.slice(reason, technical)).not.toContain('</details>')
  })

  it('renders Detection reason and Recommended action as details', () => {
    expect(jsx).toMatch(/<details className="mail-detail__aside" open>\s*<summary>Detection reason<\/summary>/)
    expect(jsx).toMatch(/<details className="mail-detail__aside" open>\s*<summary>Recommended action<\/summary>/)
  })

  it('leaves the summary itself always visible', () => {
    // Priority 2 is not something the reader should have to open.
    const summaryBlock = jsx.slice(
      jsx.indexOf('className="mail-detail__copy"'),
      jsx.indexOf('<details className="mail-detail__aside"'),
    )
    expect(summaryBlock).toContain('<strong>Summary</strong>')
    expect(summaryBlock).not.toContain('<details')
  })

  it('opens them by default, so nothing is hidden without being asked', () => {
    expect(jsx).not.toMatch(/<details className="mail-detail__aside">/)
  })
})

describe('responsive', () => {
  it('shortens the body on narrow viewports', () => {
    expect(css).toMatch(/@media \(max-width: 900px\) \{\s*\.gmail-view__body \{[^}]*max-height:\s*40vh/)
  })

  it('declares that override after the base rule so it wins', () => {
    // Equal specificity: source order decides. The first attempt put this
    // above the base rule, where it did nothing.
    const base = css.indexOf('\n.gmail-view__body {')
    const override = css.search(/@media \(max-width: 900px\) \{\s*\.gmail-view__body/)
    expect(base).toBeGreaterThan(-1)
    expect(override).toBeGreaterThan(base)
  })
})
