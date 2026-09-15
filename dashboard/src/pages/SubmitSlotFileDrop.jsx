import React, { useEffect, useMemo, useRef, useState } from 'react'

export function SubmitSlotFileDrop({
  label,
  hint,
  accept = 'image/*',
  disabled = false,
  busy = false,
  file,
  previewUrl,
  onFile,
  compact = false,
  // A payment settled in instalments has one screenshot per transfer, so that
  // field collects a list. The invite drop stays single-file.
  multiple = false,
  files,
  onFiles,
  // What an attached file is called on screen. Uploaded file names are never
  // shown -- a phone names a screenshot after a hash or a timestamp, which
  // says nothing to the person and leaks whatever the name happens to hold.
  attachedLabel = 'Screenshot attached',
  removeLabel = 'Remove screenshot',
}) {
  const inputRef = useRef(null)
  const [drag, setDrag] = useState(false)
  const picked = useMemo(() => (multiple ? files || [] : []), [multiple, files])
  const [thumbs, setThumbs] = useState([])

  // The preview URLs are owned here so a form holding several screenshots does
  // not have to track and revoke one object URL per file.
  useEffect(() => {
    if (!multiple) return undefined
    const urls = picked.map(f => URL.createObjectURL(f))
    setThumbs(urls)
    return () => urls.forEach(url => URL.revokeObjectURL(url))
  }, [multiple, picked])

  const hasSelection = multiple ? picked.length > 0 : Boolean(file)

  function pick() {
    if (!disabled && !busy) inputRef.current?.click()
  }

  function addFiles(list) {
    const images = Array.from(list || []).filter(f => f && (!f.type || f.type.startsWith('image/')))
    if (images.length) onFiles([...picked, ...images])
  }

  function onInput(ev) {
    if (multiple) addFiles(ev.target.files)
    else onFile(ev.target.files?.[0] || null)
    ev.target.value = ''
  }

  function onDrop(ev) {
    ev.preventDefault()
    setDrag(false)
    if (disabled || busy) return
    if (multiple) { addFiles(ev.dataTransfer.files); return }
    const f = ev.dataTransfer.files?.[0]
    if (f && f.type.startsWith('image/')) onFile(f)
  }

  return (
    <div className={`submit-slot-drop-wrap${compact ? ' submit-slot-drop-wrap--compact' : ''}`}>
      {label ? <span className="submit-slot-field-label">{label}</span> : null}
      <div
        className={[
          'submit-slot-drop',
          drag ? 'submit-slot-drop--drag' : '',
          hasSelection ? 'submit-slot-drop--has-file' : '',
          disabled || busy ? 'submit-slot-drop--disabled' : '',
        ].filter(Boolean).join(' ')}
        onDragOver={ev => { ev.preventDefault(); if (!disabled && !busy) setDrag(true) }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        onClick={pick}
        onKeyDown={ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick() } }}
        role="button"
        tabIndex={disabled || busy ? -1 : 0}
        aria-disabled={disabled || busy}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          multiple={multiple}
          className="submit-slot-drop-input"
          disabled={disabled || busy}
          onChange={onInput}
          tabIndex={-1}
        />
        {previewUrl ? (
          <div className="submit-slot-drop-thumb">
            <img src={previewUrl} alt="" />
          </div>
        ) : file && !multiple ? (
          <div className="submit-slot-drop-icon submit-slot-drop-icon--file" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
              <path d="M6 2h8l4 4v16H6z" strokeLinejoin="round" />
              <path d="M14 2v5h5" strokeLinejoin="round" />
            </svg>
          </div>
        ) : (
          <div className="submit-slot-drop-icon" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M12 16V4m0 0L8 8m4-4 4 4" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M4 14v2a4 4 0 004 4h8a4 4 0 004-4v-2" strokeLinecap="round" />
            </svg>
          </div>
        )}
        <div className="submit-slot-drop-copy">
          {multiple ? (
            picked.length ? (
              <>
                <strong>{picked.length} screenshot{picked.length === 1 ? '' : 's'} ready</strong>
                <span>Tap to add another</span>
              </>
            ) : (
              <>
                <strong>{compact ? 'Upload screenshots' : 'Drop payment screenshots here'}</strong>
                <span>one per payment · PNG, JPG, WebP</span>
              </>
            )
          ) : file ? (
            <>
              <strong>{attachedLabel}</strong>
              <span>Tap to replace</span>
            </>
          ) : (
            <>
              <strong>{compact ? 'Upload screenshot' : 'Drop invite screenshot here'}</strong>
              <span>or tap to browse · PNG, JPG, WebP</span>
            </>
          )}
        </div>
        {file && !multiple && (
          <button
            type="button"
            className="submit-slot-drop-remove"
            aria-label={removeLabel}
            onClick={ev => {
              ev.stopPropagation()
              onFile(null)
            }}
          >
            ×
          </button>
        )}
      </div>
      {multiple && picked.length > 0 && (
        <ul className="submit-slot-drop-list">
          {picked.map((f, index) => (
            <li className="submit-slot-drop-list__item" key={`${f.name}-${f.size}-${index}`}>
              {thumbs[index] ? <img src={thumbs[index]} alt="" /> : null}
              <span className="submit-slot-drop-list__name">Screenshot {index + 1}</span>
              <button
                type="button"
                className="submit-slot-drop-list__remove"
                aria-label={`Remove screenshot ${index + 1}`}
                disabled={disabled || busy}
                onClick={() => onFiles(picked.filter((_, other) => other !== index))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {hint ? <p className="submit-slot-drop-hint">{hint}</p> : null}
    </div>
  )
}
