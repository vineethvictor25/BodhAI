import { useRef, useState } from 'react'

const API_BASE = 'http://localhost:8000/api'

export default function FileUpload({ onUploaded }) {
  const inputRef = useRef(null)
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [lastResults, setLastResults] = useState([])

  async function uploadFiles(fileList) {
    if (!fileList || fileList.length === 0) return
    setUploading(true)
    setLastResults([])

    const formData = new FormData()
    Array.from(fileList).forEach((file) => formData.append('files', file))

    try {
      const res = await fetch(`${API_BASE}/upload`, {
        method: 'POST',
        body: formData,
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || `Upload failed (${res.status})`)
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
        className={`dropzone ${dragOver ? 'dropzone-active' : ''}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          uploadFiles(e.dataTransfer.files)
        }}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.doc,.png,.jpg,.jpeg"
          hidden
          onChange={(e) => {
            uploadFiles(e.target.files)
            e.target.value = ''
          }}
        />
        {uploading ? (
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
