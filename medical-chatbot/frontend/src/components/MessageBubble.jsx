export default function MessageBubble({ role, content, sources }) {
  const isUser = role === 'user'
  return (
    <div className={`bubble-row ${isUser ? 'bubble-row-user' : 'bubble-row-bot'}`}>
      <div className={`bubble ${isUser ? 'bubble-user' : 'bubble-bot'}`}>
        <p style={{ whiteSpace: 'pre-wrap' }}>{content}</p>

        {sources && sources.length > 0 && (
          <details className="sources">
            <summary>Sources ({sources.length})</summary>
            <ul>
              {sources.map((s, i) => (
                <li key={i}>
                  <strong>{s.filename}</strong>
                  <div className="snippet">{s.snippet}</div>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  )
}
