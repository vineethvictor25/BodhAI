import { useEffect, useRef, useState } from 'react'
import MessageBubble from './MessageBubble.jsx'
import { apiFetch } from '../api.js'

export default function ChatWindow({ hasDocuments }) {
  const [messages, setMessages] = useState([
    {
      role: 'bot',
      content:
        "Hi! Upload your prescriptions or medical reports on the left, " +
        "then ask me anything about them — e.g. \"What medicines was I " +
        "prescribed?\" or \"Are my sugar levels normal?\"",
    },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  async function sendMessage() {
    const text = input.trim()
    if (!text || loading) return

    setMessages((m) => [...m, { role: 'user', content: text }])
    setInput('')
    setLoading(true)

    try {
      const res = await apiFetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Something went wrong.')
      }
      const data = await res.json()
      setMessages((m) => [
        ...m,
        { role: 'bot', content: data.answer, sources: data.sources },
      ])
    } catch (err) {
      setMessages((m) => [
        ...m,
        { role: 'bot', content: `⚠️ ${err.message}` },
      ])
    } finally {
      setLoading(false)
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  return (
    <div className="chat-window">
      <div className="messages">
        {messages.map((m, i) => (
          <MessageBubble key={i} role={m.role} content={m.content} sources={m.sources} />
        ))}
        {loading && (
          <div className="bubble-row bubble-row-bot">
            <div className="bubble bubble-bot typing">Thinking…</div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="input-row">
        <textarea
          rows={1}
          placeholder={
            hasDocuments
              ? 'Ask about your prescriptions or reports…'
              : 'Upload a document first, then ask a question…'
          }
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button onClick={sendMessage} disabled={loading || !input.trim()}>
          Send
        </button>
      </div>
    </div>
  )
}
