import { useEffect, useState, useCallback, useRef } from 'react'
import FileUpload from './components/FileUpload.jsx'
import ChatWindow from './components/ChatWindow.jsx'

const API_BASE = 'http://localhost:8000/api'

async function readResponse(res) {
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`)
  return data
}

function DocumentRow({ document, busy, working, notice, onReindex, onReplace }) {
  const fileInputRef = useRef(null)

  return (
    <li className="document-row">
      <div className="document-name">
        <span className="doc-icon" aria-hidden="true">📄</span>
        <span>{document.filename}</span>
      </div>
      <div className="document-meta">
        {document.chunk_count} {document.chunk_count === 1 ? 'chunk' : 'chunks'}
        {document.doc_type && document.doc_type !== 'unknown' ? ` · ${document.doc_type.toUpperCase()}` : ''}
      </div>
      <div className="document-actions">
        <button
          type="button"
          onClick={() => onReindex(document.filename)}
          disabled={busy || !document.can_reindex}
          title={document.can_reindex ? 'Rebuild the index from the saved upload' : 'Saved upload is unavailable'}
        >
          {working ? 'Indexing…' : 'Reindex'}
        </button>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={busy}
        >
          {working ? 'Updating…' : 'Replace file'}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.doc,.png,.jpg,.jpeg"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) onReplace(file, document.filename)
            event.target.value = ''
          }}
        />
      </div>
      {notice && <p className={`document-notice ${notice.type}`}>{notice.text}</p>}
    </li>
  )
}

export default function App() {
  const [documents, setDocuments] = useState([])
  const [busyDocument, setBusyDocument] = useState(null)
  const [documentNotice, setDocumentNotice] = useState(null)

  const refreshDocuments = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/documents`)
      const data = await res.json()
      setDocuments(data.documents || [])
    } catch {
      // backend not reachable yet — ignore, sidebar just stays empty
    }
  }, [])

  useEffect(() => {
    refreshDocuments()
  }, [refreshDocuments])

  async function reindexDocument(filename) {
    setBusyDocument(filename)
    setDocumentNotice(null)
    try {
      const res = await fetch(`${API_BASE}/documents/${encodeURIComponent(filename)}/reindex`, {
        method: 'POST',
      })
      const data = await readResponse(res)
      setDocumentNotice({
        filename,
        type: 'success',
        text: `Reindexed · ${data.chunks_added} chunks · ${Number(data.characters_extracted || 0).toLocaleString()} characters`,
      })
      await refreshDocuments()
    } catch (error) {
      setDocumentNotice({ filename, type: 'error', text: error.message })
    } finally {
      setBusyDocument(null)
    }
  }

  async function replaceDocument(file, filename) {
    const fileExtension = file.name.split('.').pop()?.toLowerCase()
    const documentExtension = filename.split('.').pop()?.toLowerCase()
    if (fileExtension !== documentExtension) {
      setDocumentNotice({
        filename,
        type: 'error',
        text: 'Choose a replacement with the same file type.',
      })
      return
    }

    setBusyDocument(filename)
    setDocumentNotice(null)
    const formData = new FormData()
    formData.append('files', file, filename)
    try {
      const res = await fetch(`${API_BASE}/upload`, { method: 'POST', body: formData })
      const data = await readResponse(res)
      const result = data.results?.[0]
      if (!result || result.status !== 'indexed') {
        throw new Error(result?.reason || `Replacement status: ${result?.status || 'unknown'}`)
      }
      setDocumentNotice({
        filename,
        type: 'success',
        text: `Updated · ${result.chunks_added} chunks · ${Number(result.characters_extracted || 0).toLocaleString()} characters`,
      })
      await refreshDocuments()
    } catch (error) {
      setDocumentNotice({ filename, type: 'error', text: error.message })
    } finally {
      setBusyDocument(null)
    }
  }

  async function clearAll() {
    if (!confirm('This will delete all uploaded documents and chat history. Continue?')) return
    await fetch(`${API_BASE}/clear-documents`, { method: 'POST' })
    setDocuments([])
    window.location.reload()
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>🩺 MedDoc Assistant</h1>
        <p className="subtitle">Understand your prescriptions & reports, in plain language.</p>

        <FileUpload onUploaded={refreshDocuments} />

        <div className="doc-list">
          <h3>Your documents ({documents.length})</h3>
          {documents.length === 0 ? (
            <p className="hint">No documents uploaded yet.</p>
          ) : (
            <ul>
              {documents.map((document) => (
                <DocumentRow
                  key={document.filename}
                  document={document}
                  busy={Boolean(busyDocument)}
                  working={busyDocument === document.filename}
                  notice={documentNotice?.filename === document.filename ? documentNotice : null}
                  onReindex={reindexDocument}
                  onReplace={replaceDocument}
                />
              ))}
            </ul>
          )}
        </div>

        {documents.length > 0 && (
          <button className="clear-btn" onClick={clearAll}>
            Clear all documents
          </button>
        )}

        <p className="disclaimer">
          This tool helps you understand your own records. It does not
          replace medical advice from your doctor.
        </p>
      </aside>

      <main className="main">
        <ChatWindow hasDocuments={documents.length > 0} />
      </main>
    </div>
  )
}
