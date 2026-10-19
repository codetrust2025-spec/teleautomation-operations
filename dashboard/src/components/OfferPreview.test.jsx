/**
 * Data Room -> Offers: View PDF opens the letter itself (19 Oct).
 *
 * Every offer letter has a View button that opens the stored file in a preview on the page -- a PDF drawn page by
 * page, an image as the image -- with zoom, scrolling, Download and Close. The file is fetched once and handed to the
 * viewer byte for byte. pdf.js is replaced by a stand-in here: jsdom cannot draw, so these tests prove what is asked
 * of pdf.js and what the page does with the answer, not the pixels.
 */
import React from 'react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmProvider } from '../context/ConfirmContext.jsx'
import { DataRoomOffersTab } from './DataRoomOffersTab.jsx'
import { pdfLoader } from './OfferPreviewModal.jsx'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(HERE, '..', 'index.css'), 'utf8').replace(/\r\n/g, '\n')

// Invented letters.
const OFFERS = [
  { id: 'offer_asha', filename: 'Asha_Example_Offer.pdf', candidate: 'Asha Example', company_name: 'Example Technologies', size_kb: 90, has_pdf: true },
  { id: 'offer_ravi', filename: 'Ravi offer photo.jpg', candidate: 'Ravi Example', company_name: 'Example Solutions', size_kb: 40, has_pdf: true, file_type: 'image' },
]
const PDF_BYTES = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x37, 1, 2, 3, 4])
const PNG_BYTES = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 9, 8, 7])

let calls
let served
let pdf

function fakePdfjs(numPages = 2) {
  const tasks = []
  const doc = {
    numPages,
    destroy: vi.fn(),
    getPage: vi.fn(async () => ({
      getViewport: ({ scale }) => ({ width: 600 * scale, height: 800 * scale }),
      render: vi.fn(() => {
        const task = { promise: Promise.resolve(), cancel: vi.fn() }
        tasks.push(task)
        return task
      }),
    })),
  }
  return { doc, tasks, getDocument: vi.fn(() => ({ promise: Promise.resolve(doc) })) }
}

beforeEach(() => {
  calls = []
  served = {
    offer_asha: { status: 200, type: 'application/pdf', bytes: PDF_BYTES },
    offer_ravi: { status: 200, type: 'image/png', bytes: PNG_BYTES },
  }
  pdf = fakePdfjs()
  vi.spyOn(pdfLoader, 'load').mockImplementation(async () => ({ getDocument: pdf.getDocument }))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ({}))
  vi.stubGlobal('URL', Object.assign(Object.create(URL), {
    createObjectURL: vi.fn(blob => `blob:${blob.type}:${blob.size}`),
    revokeObjectURL: vi.fn(),
  }))
  vi.stubGlobal('fetch', vi.fn(async (url, init = {}) => {
    calls.push({ url: String(url), credentials: init.credentials, method: init.method || 'GET' })
    const id = /offer-letters\/([^/]+)\/preview$/.exec(String(url))?.[1]
    const file = served[id] || { status: 404, type: 'application/json', detail: 'Offer letter not found' }
    return {
      ok: file.status === 200,
      status: file.status,
      headers: { get: name => (name.toLowerCase() === 'content-type' ? file.type : null) },
      arrayBuffer: async () => file.bytes.slice().buffer,
      json: async () => ({ detail: file.detail }),
    }
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document.body.style.overflow = ''
})

const renderTab = (offers = OFFERS) => render(
  <ConfirmProvider><DataRoomOffersTab offers={offers} onReload={() => {}} /></ConfirmProvider>,
)
const preview = () => screen.getByRole('dialog', { name: /Example/ })
const pages = () => [...document.querySelectorAll('.offer-preview__page')]
const zoomLabel = () => within(preview()).getByRole('button', { name: /Fit to width/ }).textContent

async function openAsha() {
  renderTab()
  fireEvent.click(screen.getByRole('button', { name: 'View PDF for Asha Example' }))
  await waitFor(() => expect(pages()).toHaveLength(2))
  return preview()
}

describe('every offer letter', () => {
  it('has a View button -- View PDF for a PDF, View image for an image -- and no new-tab link', () => {
    renderTab()
    expect(screen.getByRole('button', { name: 'View PDF for Asha Example' })).toHaveTextContent('View PDF')
    expect(screen.getByRole('button', { name: 'View image for Ravi Example' })).toHaveTextContent('View image')
    expect(document.querySelector('a[target="_blank"][href*="/preview"]')).toBeNull()
    expect(calls).toHaveLength(0)                                       // nothing is fetched until asked
  })

  it('keeps its Download link to the stored file', () => {
    renderTab()
    const link = screen.getByRole('link', { name: 'Download PDF for Asha Example' })
    expect(link.getAttribute('href')).toMatch(/\/data-room\/offer-letters\/offer_asha\/download$/)
    expect(link.hasAttribute('download')).toBe(true)
  })
})

describe('the preview of a PDF', () => {
  it('opens on the page, fetches the file once and hands pdf.js the bytes exactly', async () => {
    const dialog = await openAsha()
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog.parentElement.parentElement).toBe(document.body)
    expect(calls).toEqual([{ url: expect.stringMatching(/\/data-room\/offer-letters\/offer_asha\/preview$/), credentials: 'include', method: 'GET' }])
    const handed = pdf.getDocument.mock.calls[0][0].data
    expect([...handed]).toEqual([...PDF_BYTES])
    expect(dialog.textContent).toContain('Asha_Example_Offer.pdf · Example Technologies · 2 pages')
    expect(pages().map(p => p.getAttribute('aria-label'))).toEqual(['Page 1 of 2', 'Page 2 of 2'])
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('zooms in and out in steps, back to the width of the box, and redraws every page', async () => {
    await openAsha()
    expect(zoomLabel()).toBe('100%')
    expect(pages()[0].style.width).toBe('800px')                          // the width of the box (jsdom: the fallback)
    fireEvent.click(within(preview()).getByRole('button', { name: 'Zoom in' }))
    expect(zoomLabel()).toBe('125%')
    await waitFor(() => expect(pages().map(p => p.style.width)).toEqual(['1000px', '1000px']))
    fireEvent.click(within(preview()).getByRole('button', { name: 'Zoom out' }))
    fireEvent.click(within(preview()).getByRole('button', { name: 'Zoom out' }))
    expect(zoomLabel()).toBe('75%')
    await waitFor(() => expect(pages()[1].style.width).toBe('600px'))
    fireEvent.click(within(preview()).getByRole('button', { name: /Fit to width/ }))
    expect(zoomLabel()).toBe('100%')
  })

  it('stops at 50% and 400%, and answers + - and 0 from the keyboard', async () => {
    await openAsha()
    const out = within(preview()).getByRole('button', { name: 'Zoom out' })
    fireEvent.click(out)
    fireEvent.click(out)
    expect(zoomLabel()).toBe('50%')
    expect(out).toBeDisabled()
    fireEvent.keyDown(preview(), { key: '0' })
    expect(zoomLabel()).toBe('100%')
    for (let i = 0; i < 10; i += 1) fireEvent.keyDown(preview(), { key: '+' })
    expect(zoomLabel()).toBe('400%')
    expect(within(preview()).getByRole('button', { name: 'Zoom in' })).toBeDisabled()
    fireEvent.keyDown(preview(), { key: '-' })
    expect(zoomLabel()).toBe('300%')
  })

  it('counts every press, even two that land together', async () => {
    await openAsha()
    const zoomIn = within(preview()).getByRole('button', { name: 'Zoom in' })
    act(() => { zoomIn.click(); zoomIn.click() })
    expect(zoomLabel()).toBe('150%')
  })

  it('zooms with Ctrl and the mouse wheel (a trackpad pinch)', async () => {
    await openAsha()
    const body = document.querySelector('.offer-preview__body')
    fireEvent.wheel(body, { deltaY: -100, ctrlKey: true })
    expect(zoomLabel()).toBe('110%')
    fireEvent.wheel(body, { deltaY: 100 })                                  // a plain wheel scrolls; it does not zoom
    expect(zoomLabel()).toBe('110%')
  })

  it('scrolls inside its own box, in both directions', () => {
    const rule = /\.offer-preview__body \{([^}]*)\}/.exec(CSS)[1]
    expect(rule).toContain('overflow: auto')
    expect(rule).toContain('-webkit-overflow-scrolling: touch')
  })

  it('downloads the stored file', async () => {
    await openAsha()
    const download = within(preview()).getByRole('link', { name: 'Download PDF' })
    expect(download.getAttribute('href')).toMatch(/\/data-room\/offer-letters\/offer_asha\/download$/)
    expect(download.hasAttribute('download')).toBe(true)
  })

  it('closes with the close button, Escape or a tap outside, and lets pdf.js go', async () => {
    await openAsha()
    fireEvent.click(within(preview()).getByRole('button', { name: 'Close preview' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(pdf.doc.destroy).toHaveBeenCalled()
    expect(document.body.style.overflow).toBe('')

    fireEvent.click(screen.getByRole('button', { name: 'View PDF for Asha Example' }))
    await waitFor(() => expect(pages()).toHaveLength(2))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'View PDF for Asha Example' }))
    await waitFor(() => expect(pages()).toHaveLength(2))
    fireEvent.click(document.querySelector('.offer-preview-backdrop'))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it("shows the server's reason when there is no file", async () => {
    served.offer_asha = { status: 404, type: 'application/json', detail: 'No file on the server yet.' }
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'View PDF for Asha Example' }))
    expect(await within(preview()).findByRole('alert')).toHaveTextContent('No file on the server yet.')
    expect(pdfLoader.load).not.toHaveBeenCalled()
  })
})

describe('the preview of an image', () => {
  it('shows the image as stored, without pdf.js, and zooms it', async () => {
    renderTab()
    fireEvent.click(screen.getByRole('button', { name: 'View image for Ravi Example' }))
    const image = await screen.findByRole('img', { name: 'Offer letter: Ravi Example' })
    const blob = URL.createObjectURL.mock.calls[0][0]
    expect(blob.type).toBe('image/png')
    const read = await new Promise(resolve => {
      const reader = new FileReader()
      reader.onload = () => resolve(new Uint8Array(reader.result))
      reader.readAsArrayBuffer(blob)
    })
    expect([...read]).toEqual([...PNG_BYTES])                            // the image as served, not re-encoded
    expect(image.getAttribute('src')).toBe(`blob:image/png:${PNG_BYTES.length}`)
    expect(pdfLoader.load).not.toHaveBeenCalled()
    expect(image.style.width).toBe('800px')
    fireEvent.click(within(preview()).getByRole('button', { name: 'Zoom in' }))
    expect(image.style.width).toBe('1000px')
    expect(within(preview()).getByRole('link', { name: 'Download image' })).toBeInTheDocument()
    fireEvent.click(within(preview()).getByRole('button', { name: 'Close preview' }))
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(image.getAttribute('src'))
  })
})

describe('from the edit dialog', () => {
  it('opens the same preview over it, and Escape closes only the preview', async () => {
    renderTab([OFFERS[0]])
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    const edit = screen.getByRole('dialog', { name: 'Edit offer letter' })
    fireEvent.click(within(edit).getByRole('button', { name: 'View PDF' }))
    await waitFor(() => expect(pages()).toHaveLength(2))
    await act(async () => { fireEvent.keyDown(document, { key: 'Escape' }) })
    expect(screen.queryByRole('dialog', { name: /Asha Example/ })).toBeNull()
    expect(screen.getByRole('dialog', { name: 'Edit offer letter' })).toBeInTheDocument()
  })
})

describe('uploading', () => {
  it('takes a PDF or an image, and refuses anything else before sending it', () => {
    renderTab([OFFERS[0]])
    const input = document.querySelector('.dr-offer-actions input[type="file"]')
    expect(input.getAttribute('accept')).toContain('image/png')
    expect(input.getAttribute('accept')).toContain('application/pdf')
    fireEvent.change(input, { target: { files: [new File([new Uint8Array([1])], 'letter.gif', { type: 'image/gif' })] } })
    expect(screen.getByText('Upload the offer letter as a PDF, PNG, JPG or WebP file.')).toBeInTheDocument()
    expect(calls).toHaveLength(0)
  })
})

describe('on a phone', () => {
  it('fills the screen, keeps its buttons finger-sized and lists each offer as a card', () => {
    const phone = CSS.slice(CSS.indexOf('/* Offer letter preview'))
    const media = /@media \(max-width: 640px\) \{([\s\S]*?)\n\}/.exec(phone)[1]
    expect(media).toMatch(/\.offer-preview \{[^}]*height: 100dvh/)
    expect(media).toMatch(/\.offer-preview__btn \{[^}]*min-width: 44px[^}]*height: 44px/)
    expect(media).toMatch(/\.dr-offers-tab \.dr-tab-table thead \{ display: none; \}/)
  })
})
