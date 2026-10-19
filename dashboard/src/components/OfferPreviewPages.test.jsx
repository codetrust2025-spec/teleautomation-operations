/**
 * Data Room -> Offers: every page, counted honestly (19 Oct).
 *
 * The server says how many pages the stored file holds; the preview shows "Page 1 of 4" with previous and next,
 * draws every page on its own (one that cannot be drawn keeps its place and is named, and the rest still appear),
 * warns when it shows fewer pages than the file holds, asks for the password of a PDF uploaded with one, and says
 * plainly when a letter is a photo of a single page rather than inventing the pages it lacks. pdf.js is a stand-in:
 * jsdom cannot draw.
 */
import React from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../context/ConfirmContext.jsx'
import { DataRoomOffersTab } from './DataRoomOffersTab.jsx'
import { pdfLoader } from './OfferPreviewModal.jsx'

const BYTES = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x37, 5, 6, 7])
const file = (pages, extra = {}) => ({ stored: true, kind: 'pdf', pages, password_protected: false, photo_of_one_page: false, original_name: '', ...extra })
// Invented letters, shaped like production's five.
const OFFERS = {
  four: { id: 'offer_four', filename: 'Four_Page_Offer.pdf', candidate: 'Asha Example', company_name: 'Example Ltd', has_pdf: true, file: file(4) },
  one: { id: 'offer_one', filename: 'One_Page_Offer.pdf', candidate: 'Kiran Example', has_pdf: true, file: file(1) },
  locked: { id: 'offer_locked', filename: 'Locked.pdf', candidate: 'Meera Example', has_pdf: true, file: file(4, { password_protected: true }) },
  photo: {
    id: 'offer_photo', filename: '00000001-PHOTO-2026-06-04.pdf', candidate: 'Ravi Example', has_pdf: true,
    source_archive: 'Example WhatsApp archive', imported_at: '2026-10-05T08:54:14+00:00',
    file: file(1, { photo_of_one_page: true, original_name: '00000002-OfferLetter.pdf' }),
  },
  none: { id: 'offer_none', filename: 'Missing.pdf', candidate: 'No File', has_pdf: false, file: { stored: false } },
}

let calls
let served
let pdf

function fakePdfjs({ numPages = 4, password = '', failPages = [] } = {}) {
  const opened = []
  const doc = {
    numPages,
    destroy: vi.fn(),
    getPage: vi.fn(async n => ({
      getViewport: ({ scale }) => ({ width: 600 * scale, height: 800 * scale }),
      render: vi.fn(() => ({
        promise: failPages.includes(n) ? Promise.reject(new Error('Invalid page content')) : Promise.resolve(),
        cancel: vi.fn(),
      })),
    })),
  }
  const getDocument = vi.fn((options) => {
    opened.push({ password: options.password, bytes: [...options.data] })
    if (password && options.password !== password) {
      const error = Object.assign(new Error(options.password ? 'Incorrect Password' : 'No password given'),
        { name: 'PasswordException', code: options.password ? 2 : 1 })
      return { promise: Promise.reject(error), destroy: vi.fn() }
    }
    return { promise: Promise.resolve(doc), destroy: vi.fn() }
  })
  return { doc, getDocument, opened }
}

beforeEach(() => {
  calls = []
  served = { pages: '4' }
  pdf = fakePdfjs()
  vi.spyOn(pdfLoader, 'load').mockImplementation(async () => ({ getDocument: (...args) => pdf.getDocument(...args) }))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ({}))
  vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
    calls.push({ url: String(url), init })
    return {
      ok: true,
      status: 200,
      headers: { get: name => ({ 'content-type': 'application/pdf', 'x-offer-page-count': served.pages })[name.toLowerCase()] ?? null },
      arrayBuffer: async () => BYTES.slice().buffer,
      json: async () => ({}),
    }
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document.body.style.overflow = ''
})

const renderTab = offers => render(<ConfirmProvider><DataRoomOffersTab offers={offers} onReload={() => {}} /></ConfirmProvider>)
const dialog = () => screen.getByRole('dialog', { name: /Example/ })
const slots = () => [...document.querySelectorAll('.offer-preview__page')]
const pageOf = () => document.querySelector('.offer-preview__page-of').textContent

async function open(offer, pages = 4) {
  renderTab([offer])
  fireEvent.click(screen.getByRole('button', { name: `View PDF for ${offer.candidate}` }))
  await waitFor(() => expect(slots()).toHaveLength(pages))
  return dialog()
}

describe('the page count', () => {
  it('shows Page 1 of 4, and previous and next move through the pages', async () => {
    const preview = await open(OFFERS.four)
    expect(preview.textContent).toContain('Four_Page_Offer.pdf · Example Ltd · 4 pages')
    expect(pageOf()).toBe('Page 1 of 4')
    const pager = within(screen.getByRole('navigation', { name: 'Pages' }))
    expect(pager.getByRole('button', { name: 'Previous page' })).toBeDisabled()
    fireEvent.click(pager.getByRole('button', { name: 'Next page' }))
    expect(pageOf()).toBe('Page 2 of 4')
    fireEvent.click(pager.getByRole('button', { name: 'Next page' }))
    fireEvent.click(pager.getByRole('button', { name: 'Next page' }))
    expect(pageOf()).toBe('Page 4 of 4')
    expect(pager.getByRole('button', { name: 'Next page' })).toBeDisabled()
    fireEvent.click(pager.getByRole('button', { name: 'Previous page' }))
    expect(pageOf()).toBe('Page 3 of 4')
    expect(slots().map(s => s.getAttribute('aria-label'))).toEqual(['Page 1 of 4', 'Page 2 of 4', 'Page 3 of 4', 'Page 4 of 4'])
  })

  it('gives every page its place at once, so Next reaches a page that is still being drawn', async () => {
    // Found in a real browser: pages appeared only once drawn, so Next to an undrawn page did nothing.
    pdf = fakePdfjs()
    pdf.doc.getPage.mockImplementation(async n => ({
      getViewport: ({ scale }) => ({ width: 600 * scale, height: 800 * scale }),
      render: () => ({ promise: n === 1 ? Promise.resolve() : new Promise(() => {}), cancel: vi.fn() }),
    }))
    renderTab([OFFERS.four])
    fireEvent.click(screen.getByRole('button', { name: 'View PDF for Asha Example' }))
    await waitFor(() => expect(document.querySelectorAll('canvas.offer-preview__page')).toHaveLength(1))
    expect(slots()).toHaveLength(4)                                         // the whole letter's length, at once
    expect(slots()[3].getAttribute('aria-label')).toBe('Page 4 of 4, still being drawn')
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(pageOf()).toBe('Page 3 of 4')
  })

  it('moves a page for every press, even two that land together', async () => {
    await open(OFFERS.four)
    const next = screen.getByRole('button', { name: 'Next page' })
    act(() => { next.click(); next.click() })
    expect(pageOf()).toBe('Page 3 of 4')
  })

  it('says a one-page letter is one page, and adds nothing to it', async () => {
    served.pages = '1'
    pdf = fakePdfjs({ numPages: 1 })
    const preview = await open(OFFERS.one, 1)
    expect(pageOf()).toBe('Page 1 of 1')
    expect(preview.textContent).toContain('One_Page_Offer.pdf · 1 page')
    expect(within(preview).queryByRole('alert')).toBeNull()
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
  })

  it('warns when it can show fewer pages than the stored file holds', async () => {
    served.pages = '5'
    const preview = await open({ ...OFFERS.four, file: file(5) })
    expect(within(preview).getByRole('alert')).toHaveTextContent(
      'The stored file has 5 pages, but the viewer could read 4 pages from it. Download the file to see all of it.')
    expect(pageOf()).toBe('Page 1 of 4')
  })
})

describe('a page that cannot be drawn', () => {
  it('keeps its place, is named, and the pages after it still appear', async () => {
    pdf = fakePdfjs({ failPages: [2] })
    const preview = await open(OFFERS.four)
    await waitFor(() => expect(within(preview).getByRole('alert')).toHaveTextContent(
      'Page 2 of 4 could not be drawn here. Download the file to see it.'))
    expect(slots().map(s => s.tagName)).toEqual(['CANVAS', 'DIV', 'CANVAS', 'CANVAS'])
    expect(slots()[1].getAttribute('aria-label')).toBe('Page 2 of 4 could not be drawn')
  })
})

describe('a PDF uploaded with a password', () => {
  it('asks for it, says when it is wrong, and then shows every page', async () => {
    pdf = fakePdfjs({ password: 'invented-pass' })
    renderTab([OFFERS.locked])
    fireEvent.click(screen.getByRole('button', { name: 'View PDF for Meera Example' }))
    const field = await screen.findByLabelText('PDF password')
    expect(dialog().textContent).toContain('This PDF is password-protected. It was uploaded that way, and holds 4 pages.')
    expect(field).toHaveAttribute('type', 'password')

    fireEvent.keyDown(field, { key: '0' })                                     // typing in the field is not a zoom
    fireEvent.change(field, { target: { value: 'not-it' } })
    fireEvent.submit(field.closest('form'))
    expect(await screen.findByText('That password did not open it. Try again.')).toBeInTheDocument()
    expect(screen.getByLabelText('PDF password').value).toBe('')            // never kept

    fireEvent.change(screen.getByLabelText('PDF password'), { target: { value: 'invented-pass' } })
    fireEvent.submit(screen.getByLabelText('PDF password').closest('form'))
    await waitFor(() => expect(slots()).toHaveLength(4))
    expect(pageOf()).toBe('Page 1 of 4')

    // Opened three times from the same bytes, each a copy; the password went to pdf.js and nowhere else.
    expect(pdf.opened.map(o => o.password)).toEqual([undefined, 'not-it', 'invented-pass'])
    for (const attempt of pdf.opened) expect(attempt.bytes).toEqual([...BYTES])
    expect(calls).toHaveLength(1)
    expect(JSON.stringify(calls)).not.toContain('invented-pass')
  })
})

describe('a photo of one page', () => {
  it('is said to be a photo of one page, with the name of the original that was not stored', async () => {
    served.pages = '1'
    pdf = fakePdfjs({ numPages: 1 })
    const preview = await open(OFFERS.photo, 1)
    const note = within(preview).getByRole('note')
    expect(note.textContent.replace(/\s+/g, ' ')).toBe(
      'This letter is a photo of one page, stored by the WhatsApp import of 5 Oct 2026 from the Example WhatsApp archive. '
      + 'The original PDF, 00000002-OfferLetter.pdf, was not stored, so any further pages or annexures are not in this file. '
      + 'Upload the original PDF with ↑ on this offer to replace the photo.')
    expect(pageOf()).toBe('Page 1 of 1')
  })
})

describe('the list', () => {
  it('shows how many pages each stored file holds, and which need a password or are photos', () => {
    renderTab([OFFERS.four, OFFERS.locked, OFFERS.photo, OFFERS.none])
    const cells = [...document.querySelectorAll('td.dr-offer-pages')].map(td => td.textContent)
    expect(cells).toEqual(['4 pages', '4 pagesPassword', '1 pagePhoto of page 1', 'No file'])
    expect(screen.getByText(/1 offer letter is a photo of\s+one page/)).toBeInTheDocument()
    expect([...document.querySelectorAll('thead th')].map(th => th.textContent)).toContain('Pages')
  })

  it('says nothing about photos when there are none', () => {
    renderTab([OFFERS.four, OFFERS.one])
    expect(document.querySelector('.dr-offer-photo-notice')).toBeNull()
  })
})
