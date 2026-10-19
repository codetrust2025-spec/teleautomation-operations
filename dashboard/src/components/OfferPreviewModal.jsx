import React, { useCallback, useEffect, useRef, useState } from 'react'
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

/** Whether an offer letter's stored file is an image rather than a PDF. */
export function offerIsImage(offer) {
  return offer?.file_type === 'image' || /\.(png|jpe?g|webp)$/i.test(String(offer?.filename || ''))
}

/** Every page of a PDF, drawn at `width` CSS pixels and sharp on the screen it is shown on. */
function PdfPages({ doc, width }) {
  const hostRef = useRef(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host || !doc || !width) return undefined
    let cancelled = false
    const tasks = []

    // Resize what is already drawn at once, so a zoom is seen immediately;
    // each page is then redrawn sharp at the new size and swapped in.
    for (const canvas of host.querySelectorAll('canvas')) {
      const scale = width / Number(canvas.dataset.baseWidth)
      canvas.style.width = `${Math.floor(Number(canvas.dataset.baseWidth) * scale)}px`
      canvas.style.height = `${Math.floor(Number(canvas.dataset.baseHeight) * scale)}px`
    }

    ;(async () => {
      const ratio = (typeof window !== 'undefined' && window.devicePixelRatio) || 1
      for (let number = 1; number <= doc.numPages; number += 1) {
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
        canvas.setAttribute('role', 'img')
        canvas.setAttribute('aria-label', `Page ${number} of ${doc.numPages}`)
        const task = page.render({ canvasContext: canvas.getContext('2d'), viewport })
        tasks.push(task)
        try {
          await task.promise
        } catch {
          return // cancelled by a newer zoom, or the preview was closed
        }
        if (cancelled) return
        const previous = host.children[number - 1]
        if (previous) previous.replaceWith(canvas)
        else host.appendChild(canvas)
      }
      while (host.children.length > doc.numPages) host.lastChild.remove()
    })().catch(() => { /* a page that cannot be drawn leaves the ones before it */ })

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
  const [file, setFile] = useState({ status: 'loading' })
  const [zoom, setZoom] = useState(1)
  const [fitWidth, setFitWidth] = useState(0)
  const zoomRef = useRef(zoom)
  zoomRef.current = zoom

  // The page behind does not scroll while the letter is open.
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [])

  // The stored file, once, exactly as served.
  useEffect(() => {
    let cancelled = false
    let doc = null
    let objectUrl = ''
    ;(async () => {
      try {
        const res = await fetch(previewUrl, { credentials: 'include' })
        if (!res.ok) {
          const data = await res.json().catch(() => ({}))
          throw new Error(data.detail || `The file could not be opened (${res.status}).`)
        }
        const type = (res.headers.get('content-type') || '').split(';')[0].trim().toLowerCase()
        const bytes = await res.arrayBuffer()
        if (cancelled) return
        if (type.startsWith('image/')) {
          objectUrl = URL.createObjectURL(new Blob([bytes], { type }))
          setFile({ status: 'ready', kind: 'image', url: objectUrl })
        } else if (type === 'application/pdf') {
          const pdfjs = await pdfLoader.load()
          doc = await pdfjs.getDocument({ data: new Uint8Array(bytes) }).promise
          if (cancelled) return
          setFile({ status: 'ready', kind: 'pdf', doc, pages: doc.numPages })
        } else {
          throw new Error('This file is neither a PDF nor an image.')
        }
      } catch (err) {
        if (!cancelled) setFile({ status: 'error', message: err?.message || 'The file could not be opened.' })
      }
    })()
    return () => {
      cancelled = true
      doc?.destroy?.()
      if (objectUrl) URL.revokeObjectURL?.(objectUrl)
    }
  }, [previewUrl])

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

  // A zoom keeps the reader at the same place in the letter.
  const applyZoom = useCallback((value) => {
    const body = bodyRef.current
    const next = clampZoom(value)
    if (next === zoomRef.current) return
    const across = body && body.scrollWidth > body.clientWidth ? (body.scrollLeft + body.clientWidth / 2) / body.scrollWidth : 0.5
    const down = body && body.scrollHeight > body.clientHeight ? (body.scrollTop + body.clientHeight / 2) / body.scrollHeight : 0
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
              {[offer?.filename, offer?.company_name, file.kind === 'pdf' ? `${file.pages} page${file.pages === 1 ? '' : 's'}` : '']
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
        <div className="offer-preview__body" ref={bodyRef} tabIndex={-1} aria-busy={file.status === 'loading'}>
          {file.status === 'loading' && <p className="offer-preview__status">Opening the {what}…</p>}
          {file.status === 'error' && <p className="offer-preview__status offer-preview__status--error" role="alert">{file.message}</p>}
          {file.status === 'ready' && (
            <div className="offer-preview__content" ref={contentRef}>
              {file.kind === 'pdf'
                ? <PdfPages doc={file.doc} width={width} />
                : <img className="offer-preview__image" src={file.url} alt={`Offer letter: ${title}`} style={{ width: `${width}px` }} />}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}

export default OfferPreviewModal
