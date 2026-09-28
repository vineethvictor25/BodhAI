import { useRef, useState } from 'react'
import { apiFetch, readResponse } from '../api.js'

export default function FileUpload({ onUploaded, ownerId = null, disabled = false }) {
  const inputRef = useRef(null)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [lastResults, setLastResults] = useState([])

  async function uploadFiles(fileList) {
    if (disabled || !fileList || fileList.length === 0) return
    setUploading(true)
    setLastResults([])

    const formData = new FormData()
    Array.from(fileList).forEach((file) => formData.append('files', file))
    if (ownerId) formData.append('owner_id', ownerId)

    try {
      const res = await apiFetch('/upload', {
        method: 'POST',
        body: formData,
      })
      const data = await readResponse(res)
      setLastResults(data.results || [])
      onUploaded?.()
    } catch (err) {
      setLastResults([{ filename: 'Upload', status: 'error', reason: err.message }])
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="upload-box">
      <div
        className={`dropzone ${dragOver ? 'dropzone-active' : ''} ${disabled ? 'dropzone-disabled' : ''}`}
        aria-disabled={disabled}
        onClick={() => !disabled && inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); if (!disabled) setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          if (!disabled) uploadFiles(e.dataTransfer.files)
        }}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.doc,.png,.jpg,.jpeg"
          disabled={disabled}
          hidden
          onChange={(e) => {
            uploadFiles(e.target.files)
            e.target.value = ''
          }}
        />
        {disabled ? (
          <p>Create a patient account before uploading records.</p>
        ) : uploading ? (
          <p>Uploading & processing…</p>
        ) : (
          <>
            <p><strong>Click to upload</strong> or drag & drop</p>
            <p className="hint">PDF, Word (.docx), or photos of prescriptions/reports</p>
          </>
        )}
      </div>

      {lastResults.length > 0 && (
        <ul className="upload-results">
          {lastResults.map((r, i) => (
            <li key={i} className={`status-${r.status}`}>
              <strong>{r.filename}</strong> — {r.status}
              {r.status === 'indexed' && (
                <span className="upload-details">
                  {r.chunks_added} chunks · {Number(r.characters_extracted || 0).toLocaleString()} characters
                </span>
              )}
              {r.reason ? `: ${r.reason}` : ''}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
