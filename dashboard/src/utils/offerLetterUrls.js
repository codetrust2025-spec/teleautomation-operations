// The live folder comes from the server (`offer_folder_url` on /data-room),
// never from this file: a literal here is published by the public repository
// and baked into the browser bundle. Empty means "no folder configured", and
// every caller may pass the configured value in explicitly.
const DEFAULT_OFFER_FOLDER = ''

function extractDriveFolderId(url) {
  const m = String(url || '').match(/\/folders\/([a-zA-Z0-9_-]+)/)
  return m ? m[1] : null
}

function extractDriveFileId(url, row) {
  const s = String(url || '')
  const fromPath = s.match(/\/file\/d\/([a-zA-Z0-9_-]+)/)
  if (fromPath) return fromPath[1]
  const fromQuery = s.match(/[?&]id=([a-zA-Z0-9_-]+)/)
  if (fromQuery) return fromQuery[1]
  const fromRow = String(row?.drive_file_id || '').trim()
  return fromRow || null
}

/** Inline preview for one offer letter (never the whole folder grid). */
export function offerLetterPreviewEmbed(row, fallbackFolder = DEFAULT_OFFER_FOLDER) {
  const direct = row?.file_url || row?.view_url || row?.url
  const fileId = extractDriveFileId(direct, row)

  if (fileId) {
    return {
      mode: 'file',
      embedUrl: `https://drive.google.com/file/d/${fileId}/preview`,
      openUrl: `https://drive.google.com/file/d/${fileId}/view`,
    }
  }

  const filename = String(row?.filename || '').trim()
  const folderUrl = row?.folder_url || fallbackFolder
  const folderId = extractDriveFolderId(folderUrl)
  const openUrl =
    filename && folderId
      ? `https://drive.google.com/drive/folders/${folderId}?q=${encodeURIComponent(filename)}`
      : folderUrl || fallbackFolder

  return {
    mode: 'missing',
    embedUrl: null,
    openUrl,
  }
}

/** Same-origin PDF preview (cached from Drive or uploaded) — like resume /preview. */
export function offerLetterPreviewApiUrl(row, apiBase = '') {
  const id = String(row?.id || '').trim()
  if (!id) return null
  const base = (apiBase || '').replace(/\/$/, '')
  return `${base}/data-room/offer-letters/${encodeURIComponent(id)}/preview`
}

export function offerLetterDownloadApiUrl(row, apiBase = '') {
  const id = String(row?.id || '').trim()
  if (!id) return null
  const base = (apiBase || '').replace(/\/$/, '')
  return `${base}/data-room/offer-letters/${encodeURIComponent(id)}/download`
}

/** External link (fallback). */
export function offerLetterViewUrl(row, fallbackFolder = DEFAULT_OFFER_FOLDER) {
  return offerLetterPreviewEmbed(row, fallbackFolder).openUrl
}

export { DEFAULT_OFFER_FOLDER }
