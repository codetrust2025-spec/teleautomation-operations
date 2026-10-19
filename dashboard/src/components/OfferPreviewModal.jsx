import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useDialogA11y } from '../hooks/useDialogA11y.js'
import { Icon } from './ui/Icon.jsx'

/**
 * An offer letter, opened where it is listed (Data Room -> Offers, 19 Oct).
 *
 * The file is fetched once, exactly as the server stores it, and shown as it
 * is: a PDF drawn page by page with pdf.js, an image as the image. Nothing is
 * converted or re-encoded, and Download hands over the same stored file.
 *
 * pdf.js rather than an <iframe>: Safari on an iPhone shows only the first
 * page of a PDF in a frame, with no way to scroll or zoom it. Drawing the
 * pages ourselves behaves the same on every phone and desktop. The legacy
 * build is the one that still runs on older iPhones, and it is loaded only
 * when a letter is opened, so the rest of the dashboard does not carry it.
 *
 * Every page is accounted for. The server says how many pages the stored file
 * holds (X-Offer-Page-Count); the preview shows "Page 2 of 4", draws each page
 * on its own -- one that cannot be drawn keeps its place and says so, and the
 * pages after it still appear -- and warns when it shows fewer pages than the
 * file holds. A PDF that was uploaded with a password asks for it; the
 * password opens the file here, in the browser, and goes nowhere else.
 */

export const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3, 4]
const MIN_ZOOM = ZOOM_STEPS[0]
const MAX_ZOOM = ZOOM_STEPS[ZOOM_STEPS.length - 1]
// Pixels drawn per page, at most. Safari refuses a canvas above ~16.7 million
// pixels and runs out of canvas memory well before that across many pages, so
// a sharp page on a 3x screen is traded for a slightly softer one when zoomed.
const MAX_CANVAS_PIXELS = 6_000_000
// Used until the box has been measured (and by jsdom, which measures nothing).
const FALLBACK_WIDTH = 800
// A4 in points: the shape a page that could not be read is given.
const A4 = { width: 595, height: 842 }

/** pdf.js, on demand. An object so tests can replace the loader. */
export const pdfLoader = {
  async load() {
    const [pdfjs, worker] = await Promise.all([
      import('pdfjs-dist/legacy/build/pdf.mjs'),
      import('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'),
    ])
    pdfjs.GlobalWorkerOptions.workerSrc = worker.default
    return pdfjs
  },
}

const clampZoom = value => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round(value * 100) / 100))
const nextStep = zoom => ZOOM_STEPS.find(step => step > zoom + 0.001) ?? MAX_ZOOM
const previousStep = zoom => [...ZOOM_STEPS].reverse().find(step => step < zoom - 0.001) ?? MIN_ZOOM
const pagesText = n => `${n} page${n === 1 ? '' : 's'}`
const listText = numbers => (numbers.length === 1 ? `${numbers[0]}` : `${numbers.slice(0, -1).join(', ')} and ${numbers[numbers.length - 1]}`)

/** Whether an offer letter's stored file is an image rather than a PDF. */
export function offerIsImage(offer) {
  return offer?.file?.kind === 'image' || offer?.file_type === 'image' || /\.(png|jpe?g|webp)$/i.test(String(offer?.filename || ''))
}

function sizeSlot(slot, width) {
  const baseWidth = Number(slot.dataset.baseWidth) || A4.width
  const baseHeight = Number(slot.dataset.baseHeight) || A4.height
  slot.style.width = `${Math.floor(width)}px`
  slot.style.height = `${Math.floor(baseHeight * (width / baseWidth))}px`
}

/**
 * Every page of a PDF, drawn at `width` CSS pixels and sharp on the screen it
 * is shown on. Every page has its place from the start, so the letter is its
 * full length at once and any page can be gone to before it is drawn. Each is
 * then drawn on its own: one that cannot be drawn keeps its place with a note,
 * and `onReport` hears which were drawn and which not.
 */
function PdfPages({ doc, width, onReport }) {
  const hostRef = useRef(null)
  const reportRef = useRef(onReport)
  reportRef.current = onReport

  // Before the first paint: the places for every page, and any zoom applied at
  // once to what is already drawn (each page is then redrawn sharp and swapped in).
  useLayoutEffect(() => {
    const host = hostRef.current
    if (!host || !doc || !width) return
    for (const slot of host.querySelectorAll('.offer-preview__page')) sizeSlot(slot, width)
    while (host.children.length < doc.numPages) {
      const number = host.children.length + 1
      const slot = document.createElement('div')
      slot.className = 'offer-preview__page offer-preview__page--pending'
      slot.dataset.baseWidth = String(A4.width)
      slot.dataset.baseHeight = String(A4.height)
      slot.dataset.page = String(number)
      slot.setAttribute('role', 'img')
      slot.setAttribute('aria-label', `Page ${number} of ${doc.numPages}, still being drawn`)
      sizeSlot(slot, width)
      host.appendChild(slot)
    }
  }, [doc, width])

  useEffect(() => {
    const host = hostRef.current
    if (!host || !doc || !width) return undefined
    let cancelled = false
    const tasks = []

    ;(async () => {
      const ratio = (typeof window !== 'undefined' && window.devicePixelRatio) || 1
      const failed = []
      let drawn = 0
      for (let number = 1; number <= doc.numPages; number += 1) {
        let slot
        try {
          const page = await doc.getPage(number)
          if (cancelled) return
          const base = page.getViewport({ scale: 1 })
          const cssScale = width / base.width
          const cssWidth = Math.floor(base.width * cssScale)
          const cssHeight = Math.floor(base.height * cssScale)
          const sharpness = Math.max(1, Math.min(ratio, Math.sqrt(MAX_CANVAS_PIXELS / Math.max(1, cssWidth * cssHeight))))
          const viewport = page.getViewport({ scale: cssScale * sharpness })
          const canvas = document.createElement('canvas')
          canvas.className = 'offer-preview__page'
          canvas.width = Math.floor(viewport.width)
          canvas.height = Math.floor(viewport.height)
          canvas.style.width = `${cssWidth}px`
          canvas.style.height = `${cssHeight}px`
          canvas.dataset.baseWidth = String(base.width)
          canvas.dataset.baseHeight = String(base.height)
          canvas.dataset.page = String(number)
          canvas.setAttribute('role', 'img')
          canvas.setAttribute('aria-label', `Page ${number} of ${doc.numPages}`)
          const task = page.render({ canvasContext: canvas.getContext('2d'), viewport })
          tasks.push(task)
          await task.promise
          if (cancelled) return
          slot = canvas
          drawn += 1
        } catch (err) {
          // A newer zoom, or the preview closing, cancels the drawing; that is not a lost page.
          if (cancelled || err?.name === 'RenderingCancelledException') return
          failed.push(number)
          slot = document.createElement('div')
          slot.className = 'offer-preview__page offer-preview__page--failed'
          slot.dataset.baseWidth = String(A4.width)
          slot.dataset.baseHeight = String(A4.height)
          slot.dataset.page = String(number)
          slot.setAttribute('role', 'img')
          slot.setAttribute('aria-label', `Page ${number} of ${doc.numPages} could not be drawn`)
          slot.textContent = `Page ${number} could not be drawn here. Download the file to see it.`
          sizeSlot(slot, width)
        }
        const previous = host.children[number - 1]
        if (previous) previous.replaceWith(slot)
        else host.appendChild(slot)
        reportRef.current?.({ drawn, failed: [...failed], done: false })
      }
      while (host.children.length > doc.numPages) host.lastChild.remove()
      reportRef.current?.({ drawn, failed, done: true })
    })()

    return () => {
      cancelled = true
      for (const task of tasks) task.cancel?.()
    }
  }, [doc, width])

  return <div ref={hostRef} className="offer-preview__pages" />
}

export function OfferPreviewModal({ offer, previewUrl, downloadUrl, onClose }) {
  const dialogRef = useDialogA11y(true, onClose)
  const bodyRef = useRef(null)
  const contentRef = useRef(null)
  const passwordRef = useRef(null)
  const [file, setFile] = useState({ status: 'loading' })
  const [zoom, setZoom] = useState(1)
  const [fitWidth, setFitWidth] = useState(0)
  const [expectedPages, setExpectedPages] = useState(Number.isInteger(offer?.file?.pages) ? offer.file.pages : null)
  const [drawing, setDrawing] = useState({ drawn: 0, failed: [], done: false })
  const [currentPage, setCurrentPageState] = useState(1)
  // Read by Previous and Next, so presses that land together each move a page.
  const currentPageRef = useRef(1)
  const setCurrentPage = useCallback((number) => {
    currentPageRef.current = number
    setCurrentPageState(number)
  }, [])
  const [password, setPassword] = useState('')
  const zoomRef = useRef(zoom)
  zoomRef.current = zoom
  // The stored bytes, kept for another attempt at a password-protected PDF.
  const bytesRef = useRef(null)
  const pdfjsRef = useRef(null)
  const docRef = useRef(null)
  const aliveRef = useRef(true)

  // The page behind does not scroll while the letter is open.
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [])

  useEffect(() => () => {
    aliveRef.current = false
    docRef.current?.destroy?.()
  }, [])

  const openPdf = useCallback(async (attempt) => {
    const pdfjs = pdfjsRef.current || (pdfjsRef.current = await pdfLoader.load())
    // pdf.js takes the bytes it is given for its worker, so each attempt gets its own copy.
    const task = pdfjs.getDocument({ data: new Uint8Array(bytesRef.current.slice(0)), ...(attempt ? { password: attempt } : {}) })
    try {
      return await task.promise
    } catch (err) {
      task.destroy?.()
      throw err
    }
  }, [])

  const showPdf = useCallback((doc) => {
    docRef.current = doc
    setDrawing({ drawn: 0, failed: [], done: false })
    setCurrentPage(1)
    setFile({ status: 'ready', kind: 'pdf', doc, pages: doc.numPages })
  }, [setCurrentPage])

  // The stored file, once, exactly as served.
  useEffect(() => {
    let cancelled = false
    let objectUrl = ''
    ;(async () => {
      try {
        const res = await fetch(previewUrl, { credentials: 'include' })
        if (!res.ok) {
          const data = await res.json().catch(() => ({}))
          throw new Error(data.detail || `The file could not be opened (${res.status}).`)
        }
        const type = (res.headers.get('content-type') || '').split(';')[0].trim().toLowerCase()
        const counted = Number.parseInt(res.headers.get('x-offer-page-count') || '', 10)
        if (Number.isInteger(counted)) setExpectedPages(counted)
        const bytes = await res.arrayBuffer()
        if (cancelled) return
        if (type.startsWith('image/')) {
          objectUrl = URL.createObjectURL(new Blob([bytes], { type }))
          setFile({ status: 'ready', kind: 'image', url: objectUrl })
        } else if (type === 'application/pdf') {
          bytesRef.current = bytes
          try {
            const doc = await openPdf('')
            if (cancelled) { doc.destroy?.(); return }
            showPdf(doc)
          } catch (err) {
            if (err?.name !== 'PasswordException') throw err
            if (!cancelled) setFile({ status: 'password', kind: 'pdf', wrong: false })
          }
        } else {
          throw new Error('This file is neither a PDF nor an image.')
        }
      } catch (err) {
        if (!cancelled) setFile({ status: 'error', message: err?.message || 'The file could not be opened.' })
      }
    })()
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL?.(objectUrl)
    }
  }, [previewUrl, openPdf, showPdf])

  useEffect(() => {
    if (file.status === 'password') passwordRef.current?.focus()
  }, [file.status, file.wrong])

  const submitPassword = async (event) => {
    event.preventDefault()
    const attempt = password
    if (!attempt) return
    setPassword('') // not kept, whatever happens next
    setFile({ status: 'opening', kind: 'pdf' })
    try {
      const doc = await openPdf(attempt)
      if (!aliveRef.current) { doc.destroy?.(); return }
      showPdf(doc)
    } catch (err) {
      if (!aliveRef.current) return
      if (err?.name === 'PasswordException') setFile({ status: 'password', kind: 'pdf', wrong: true })
      else setFile({ status: 'error', message: err?.message || 'The file could not be opened.' })
    }
  }

  // 100% is the width of the box: the whole page across, on a phone or a desktop.
  useEffect(() => {
    const body = bodyRef.current
    if (!body) return undefined
    const measure = () => {
      const styles = window.getComputedStyle(body)
      const padding = (parseFloat(styles.paddingLeft) || 0) + (parseFloat(styles.paddingRight) || 0)
      setFitWidth(Math.max(0, Math.floor(body.clientWidth - padding)))
    }
    measure()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure)
      return () => window.removeEventListener('resize', measure)
    }
    const observer = new ResizeObserver(measure)
    observer.observe(body)
    return () => observer.disconnect()
  }, [])

  // Which page is being read: the last one whose top has passed a third of the
  // way down the box, or the last page once the end is reached. Worked out on
  // each scroll, which is cheap for a letter's few pages, and does not wait on
  // an animation frame, which a hidden tab never gets.
  useEffect(() => {
    const body = bodyRef.current
    if (!body || file.kind !== 'pdf' || file.status !== 'ready') return undefined
    const track = () => {
      const slots = [...body.querySelectorAll('.offer-preview__page')]
      if (!slots.length) return
      if (body.scrollTop + body.clientHeight >= body.scrollHeight - 2) { setCurrentPage(slots.length); return }
      const mark = body.getBoundingClientRect().top + body.clientHeight / 3
      let reading = 1
      slots.forEach((slot, index) => { if (slot.getBoundingClientRect().top <= mark) reading = index + 1 })
      setCurrentPage(reading)
    }
    body.addEventListener('scroll', track, { passive: true })
    return () => body.removeEventListener('scroll', track)
  }, [file.kind, file.status, setCurrentPage])

  const goToPage = useCallback((number) => {
    const body = bodyRef.current
    const slots = body ? body.querySelectorAll('.offer-preview__page') : []
    const target = slots[number - 1]
    if (!body || !target) return
    // Straight to the page, as a PDF reader does: a smooth scroll would pass through
    // the pages between and report each of them on the way.
    body.scrollTop = Math.max(0, target.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 8)
    setCurrentPage(number)
  }, [setCurrentPage])

  // A zoom keeps the reader at the same place in the letter.
  const applyZoom = useCallback((value) => {
    const body = bodyRef.current
    const next = clampZoom(value)
    if (next === zoomRef.current) return
    const across = body && body.scrollWidth > body.clientWidth ? (body.scrollLeft + body.clientWidth / 2) / body.scrollWidth : 0.5
    const down = body && body.scrollHeight > body.clientHeight ? (body.scrollTop + body.clientHeight / 2) / body.scrollHeight : 0
    // Ahead of the render, so presses that land together (a held key) all count.
    zoomRef.current = next
    setZoom(next)
    window.requestAnimationFrame?.(() => {
      if (!body) return
      body.scrollLeft = Math.max(0, across * body.scrollWidth - body.clientWidth / 2)
      if (down) body.scrollTop = Math.max(0, down * body.scrollHeight - body.clientHeight / 2)
    })
  }, [])
  const zoomIn = useCallback(() => applyZoom(nextStep(zoomRef.current)), [applyZoom])
  const zoomOut = useCallback(() => applyZoom(previousStep(zoomRef.current)), [applyZoom])
  const fit = useCallback(() => applyZoom(1), [applyZoom])

  // Ctrl/Cmd + wheel (and a trackpad pinch, which arrives as one) zooms on a
  // desktop; two fingers zoom on a phone. The pinch scales what is drawn while
  // the fingers move and redraws it sharp when they lift.
  useEffect(() => {
    const body = bodyRef.current
    if (!body) return undefined
    const onWheel = (event) => {
      if (!event.ctrlKey && !event.metaKey) return
      event.preventDefault()
      applyZoom(zoomRef.current * (event.deltaY < 0 ? 1.1 : 1 / 1.1))
    }
    let pinch = null
    const distance = touches => Math.hypot(touches[0].clientX - touches[1].clientX, touches[0].clientY - touches[1].clientY)
    const onTouchStart = (event) => {
      if (event.touches.length !== 2) return
      const rect = contentRef.current?.getBoundingClientRect()
      pinch = {
        start: distance(event.touches),
        scale: 1,
        originX: rect ? (event.touches[0].clientX + event.touches[1].clientX) / 2 - rect.left : 0,
        originY: rect ? (event.touches[0].clientY + event.touches[1].clientY) / 2 - rect.top : 0,
      }
    }
    const onTouchMove = (event) => {
      if (!pinch || event.touches.length !== 2) return
      event.preventDefault()
      pinch.scale = clampZoom(zoomRef.current * (distance(event.touches) / pinch.start)) / zoomRef.current
      const content = contentRef.current
      if (content) {
        content.style.transformOrigin = `${pinch.originX}px ${pinch.originY}px`
        content.style.transform = `scale(${pinch.scale})`
      }
    }
    const onTouchEnd = () => {
      if (!pinch) return
      const { scale } = pinch
      pinch = null
      if (contentRef.current) contentRef.current.style.transform = ''
      applyZoom(zoomRef.current * scale)
    }
    // Safari's own pinch would zoom the whole page, toolbar and all.
    const stopGesture = event => event.preventDefault()
    body.addEventListener('wheel', onWheel, { passive: false })
    body.addEventListener('touchstart', onTouchStart, { passive: true })
    body.addEventListener('touchmove', onTouchMove, { passive: false })
    body.addEventListener('touchend', onTouchEnd)
    body.addEventListener('touchcancel', onTouchEnd)
    body.addEventListener('gesturestart', stopGesture)
    body.addEventListener('gesturechange', stopGesture)
    return () => {
      body.removeEventListener('wheel', onWheel)
      body.removeEventListener('touchstart', onTouchStart)
      body.removeEventListener('touchmove', onTouchMove)
      body.removeEventListener('touchend', onTouchEnd)
      body.removeEventListener('touchcancel', onTouchEnd)
      body.removeEventListener('gesturestart', stopGesture)
      body.removeEventListener('gesturechange', stopGesture)
    }
  }, [applyZoom])

  const onKeyDown = (event) => {
    if (event.target instanceof HTMLElement && event.target.closest('input, textarea, select')) return
    if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomIn() }
    else if (event.key === '-' || event.key === '_') { event.preventDefault(); zoomOut() }
    else if (event.key === '0') { event.preventDefault(); fit() }
  }

  const width = Math.floor((fitWidth || FALLBACK_WIDTH) * zoom)
  const isImage = file.kind === 'image' || (file.status !== 'ready' && offerIsImage(offer))
  const what = isImage ? 'image' : 'PDF'
  const title = offer?.candidate || offer?.filename || 'Offer letter'
  const percent = `${Math.round(zoom * 100)}%`
  const pdfReady = file.status === 'ready' && file.kind === 'pdf'
  const total = pdfReady ? file.pages : expectedPages
  const fromPhoto = Boolean(offer?.file?.photo_of_one_page)

  // What the viewer could not show, said plainly rather than left for the reader to notice.
  const problems = []
  if (pdfReady && expectedPages !== null && expectedPages !== file.pages) {
    problems.push(`The stored file has ${pagesText(expectedPages)}, but the viewer could read ${pagesText(file.pages)} from it. Download the file to see all of it.`)
  }
  if (pdfReady && drawing.failed.length) {
    problems.push(`Page ${listText(drawing.failed)} of ${file.pages} could not be drawn here. Download the file to see ${drawing.failed.length === 1 ? 'it' : 'them'}.`)
  }
  const imported = offer?.imported_at ? new Date(offer.imported_at) : null
  const importedOn = imported && !Number.isNaN(imported.getTime())
    ? imported.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
    : ''

  return createPortal(
    <div className="offer-preview-backdrop" role="presentation" onClick={onClose}>
      <div
        className="offer-preview"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="offer-preview-title"
        onClick={event => event.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="offer-preview__bar">
          <div className="offer-preview__title">
            <strong id="offer-preview-title">{title}</strong>
            <span>
              {[offer?.filename, offer?.company_name,
                file.kind === 'image' ? 'image' : total !== null && total !== undefined ? pagesText(total) : '']
                .filter(Boolean).join(' · ')}
            </span>
          </div>
          <div className="offer-preview__tools" role="toolbar" aria-label="Preview controls">
            <button type="button" className="offer-preview__btn" onClick={zoomOut} disabled={zoom <= MIN_ZOOM} aria-label="Zoom out">−</button>
            <button type="button" className="offer-preview__btn offer-preview__zoom" onClick={fit} aria-label={`Zoom ${percent}. Fit to width`} title="Fit to width">{percent}</button>
            <button type="button" className="offer-preview__btn" onClick={zoomIn} disabled={zoom >= MAX_ZOOM} aria-label="Zoom in">+</button>
            <a className="offer-preview__btn offer-preview__download" href={downloadUrl} download aria-label={`Download ${what}`}>
              <Icon name="download" size={16} />
              <span>Download</span>
            </a>
          </div>
          <button type="button" className="offer-preview__btn offer-preview__close" onClick={onClose} aria-label="Close preview">×</button>
        </div>
        <div className="offer-preview__body" ref={bodyRef} tabIndex={-1} aria-busy={file.status === 'loading' || file.status === 'opening'}>
          {fromPhoto && (
            <p className="offer-preview__notice" role="note">
              This letter is a photo of one page, stored by the WhatsApp import{importedOn ? ` of ${importedOn}` : ''}
              {offer?.source_archive ? ` from the ${offer.source_archive}` : ''}. The original PDF
              {offer?.file?.original_name ? `, ${offer.file.original_name},` : ''} was not stored, so any further pages or
              annexures are not in this file. Upload the original PDF with ↑ on this offer to replace the photo.
            </p>
          )}
          {problems.map(text => <p key={text} className="offer-preview__notice offer-preview__notice--warn" role="alert">{text}</p>)}
          {(file.status === 'loading' || file.status === 'opening') && <p className="offer-preview__status">Opening the {what}…</p>}
          {file.status === 'error' && <p className="offer-preview__status offer-preview__status--error" role="alert">{file.message}</p>}
          {file.status === 'password' && (
            <form className="offer-preview__password" onSubmit={submitPassword}>
              <p>
                <strong>This PDF is password-protected.</strong> It was uploaded that way
                {expectedPages ? `, and holds ${pagesText(expectedPages)}` : ''}. Enter its password to see it here. The
                password opens the file in this browser only: it is not sent or saved.
              </p>
              {file.wrong && <p className="offer-preview__status--error" role="alert">That password did not open it. Try again.</p>}
              <label>
                PDF password
                <input
                  ref={passwordRef}
                  className="cand-input"
                  type="password"
                  autoComplete="off"
                  value={password}
                  onChange={event => setPassword(event.target.value)}
                />
              </label>
              <div className="offer-preview__password-actions">
                <button type="submit" className="cand-btn cand-btn--primary" disabled={!password}>Open PDF</button>
                <span>Download gives the file as uploaded, still protected.</span>
              </div>
            </form>
          )}
          {file.status === 'ready' && (
            <div className="offer-preview__content" ref={contentRef}>
              {file.kind === 'pdf'
                ? <PdfPages doc={file.doc} width={width} onReport={setDrawing} />
                : <img className="offer-preview__image" src={file.url} alt={`Offer letter: ${title}`} style={{ width: `${width}px` }} />}
            </div>
          )}
        </div>
        {pdfReady && (
          <div className="offer-preview__pager" role="navigation" aria-label="Pages">
            <button type="button" className="offer-preview__btn" onClick={() => goToPage(currentPageRef.current - 1)} disabled={currentPage <= 1} aria-label="Previous page">‹</button>
            <span className="offer-preview__page-of" aria-live="polite">Page {currentPage} of {file.pages}</span>
            <button type="button" className="offer-preview__btn" onClick={() => goToPage(currentPageRef.current + 1)} disabled={currentPage >= file.pages} aria-label="Next page">›</button>
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}

export default OfferPreviewModal
