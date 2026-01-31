import { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'

const API_URL = 'http://localhost:8000'

export default function App() {
  const [user, setUser] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [showConnections, setShowConnections] = useState(false)
  const messagesEndRef = useRef(null)

  // Check if user has any connections
  useEffect(() => {
    fetch(`${API_URL}/auth/me`, { credentials: 'include' })
      .then(res => res.ok ? res.json() : null)
      .then(data => setUser(data))
      .catch(() => {})
  }, [])

  // Scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = async (e) => {
    e.preventDefault()
    if (!input.trim() || sending) return

    const userMessage = input.trim()
    setInput('')
    setMessages(prev => [...prev, { role: 'user', content: userMessage }])
    setSending(true)

    try {
      // Format history for backend - include tool_calls for assistant messages
      const historyForBackend = messages.map(msg => ({
        role: msg.role,
        content: msg.content,
        tool_calls: msg.tool_calls || [],  // Include tool calls for context
      }))

      const res = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ 
          message: userMessage,
          history: historyForBackend,
        }),
      })

      if (!res.ok) throw new Error('Failed to send message')

      const data = await res.json()
      setMessages(prev => [...prev, { 
        role: 'assistant', 
        content: data.response,
        tool_calls: data.tool_calls || [],  // Store with snake_case to match backend
      }])
    } catch {
      setMessages(prev => [...prev, { 
        role: 'assistant', 
        content: 'Sorry, something went wrong. Make sure the backend is running.' 
      }])
    } finally {
      setSending(false)
    }
  }

  const connectedServices = user?.connected_services || []

  return (
    <div className="min-h-screen bg-zinc-900 flex flex-col">
      {/* Header */}
      <header className="border-b border-zinc-800 px-6 py-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-white">Digital Twin</h1>
        
        {/* Connections dropdown */}
        <div className="relative">
          <button
            onClick={() => setShowConnections(!showConnections)}
            className="flex items-center gap-2 text-sm text-zinc-400 hover:text-white transition px-3 py-2 rounded-lg hover:bg-zinc-800"
          >
            <span>Connections</span>
            <span className="bg-zinc-700 text-xs px-2 py-0.5 rounded-full">
              {connectedServices.length}
            </span>
          </button>
          
          {showConnections && (
            <div className="absolute right-0 top-full mt-2 w-64 bg-zinc-800 border border-zinc-700 rounded-xl shadow-xl p-4 z-10">
              <p className="text-xs text-zinc-500 mb-3">
                Connect services to let the AI access your data
              </p>
              
              {/* Google */}
              <div className="flex items-center justify-between py-2">
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 bg-white rounded-lg flex items-center justify-center">
                    <span className="text-lg">G</span>
                  </div>
                  <span className="text-white text-sm">Google</span>
                </div>
                <div className="flex items-center gap-2">
                  {connectedServices.includes('google') ? (
                    <>
                      <span className="text-xs text-green-400">✓</span>
                      <button
                        onClick={async () => {
                          await fetch(`${API_URL}/auth/disconnect/google`, {
                            method: 'DELETE',
                            credentials: 'include',
                          })
                          // Refresh user data
                          const res = await fetch(`${API_URL}/auth/me`, { credentials: 'include' })
                          if (res.ok) setUser(await res.json())
                          else setUser(null)
                        }}
                        className="text-xs bg-red-600/20 hover:bg-red-600/40 text-red-400 px-3 py-1 rounded-lg transition"
                      >
                        Disconnect
                      </button>
                    </>
                  ) : (
                    <a
                      href={`${API_URL}/auth/google/login`}
                      className="text-xs bg-zinc-700 hover:bg-zinc-600 text-white px-3 py-1 rounded-lg transition"
                    >
                      Connect
                    </a>
                  )}
                </div>
              </div>
              
              {/* Notion */}
              <div className="flex items-center justify-between py-2">
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 bg-white rounded-lg flex items-center justify-center">
                    <span className="text-lg font-semibold">N</span>
                  </div>
                  <span className="text-white text-sm">Notion</span>
                </div>
                <div className="flex items-center gap-2">
                  {connectedServices.includes('notion') ? (
                    <>
                      <span className="text-xs text-green-400">✓</span>
                      <button
                        onClick={async () => {
                          await fetch(`${API_URL}/auth/disconnect/notion`, {
                            method: 'DELETE',
                            credentials: 'include',
                          })
                          // Refresh user data
                          const res = await fetch(`${API_URL}/auth/me`, { credentials: 'include' })
                          if (res.ok) setUser(await res.json())
                          else setUser(null)
                        }}
                        className="text-xs bg-red-600/20 hover:bg-red-600/40 text-red-400 px-3 py-1 rounded-lg transition"
                      >
                        Disconnect
                      </button>
                    </>
                  ) : (
                    <a
                      href={`${API_URL}/auth/notion/login`}
                      className="text-xs bg-zinc-700 hover:bg-zinc-600 text-white px-3 py-1 rounded-lg transition"
                    >
                      Connect
                    </a>
                  )}
                </div>
              </div>
              
              {user && (
                <div className="border-t border-zinc-700 mt-3 pt-3">
                  <p className="text-xs text-zinc-400">{user.email}</p>
                </div>
              )}
            </div>
          )}
        </div>
      </header>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="max-w-3xl mx-auto space-y-4">
          {messages.length === 0 && (
            <div className="text-center text-zinc-500 py-20">
              <p className="text-lg">Hi! I'm your digital twin.</p>
              <p className="text-sm mt-2">
                {connectedServices.length > 0 
                  ? "Ask me about your calendar, emails, Notion pages, or anything else!"
                  : "Connect your services (top right) to let me access your calendar, emails, and Notion."}
              </p>
            </div>
          )}
          
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[80%] px-4 py-3 rounded-2xl ${
                  msg.role === 'user'
                    ? 'bg-blue-600 text-white'
                    : 'bg-zinc-800 text-zinc-100'
                }`}
              >
                {msg.role === 'user' ? (
                  <p className="whitespace-pre-wrap">{msg.content}</p>
                ) : (
                  <>
                    <div className="prose prose-invert prose-sm max-w-none">
                      <ReactMarkdown
                        components={{
                          a: ({ href, children }) => (
                            <a 
                              href={href} 
                              target="_blank" 
                              rel="noopener noreferrer"
                              className="text-blue-400 hover:text-blue-300 underline"
                            >
                              {children}
                            </a>
                          ),
                          p: ({ children }) => (
                            <p className="mb-2 last:mb-0">{children}</p>
                          ),
                          ul: ({ children }) => (
                            <ul className="list-disc list-inside mb-2">{children}</ul>
                          ),
                          ol: ({ children }) => (
                            <ol className="list-decimal list-inside mb-2">{children}</ol>
                          ),
                          li: ({ children }) => (
                            <li className="mb-1">{children}</li>
                          ),
                          strong: ({ children }) => (
                            <strong className="font-semibold text-white">{children}</strong>
                          ),
                          code: ({ children }) => (
                            <code className="bg-zinc-700 px-1 py-0.5 rounded text-sm">{children}</code>
                          ),
                        }}
                      >
                        {msg.content}
                      </ReactMarkdown>
                    </div>
                    
                    {/* Tool calls dropdown */}
                    {msg.tool_calls && msg.tool_calls.length > 0 && (
                      <details className="mt-3 border-t border-zinc-700 pt-2">
                        <summary className="text-xs text-zinc-500 cursor-pointer hover:text-zinc-400">
                          🔧 {msg.tool_calls.length} tool call{msg.tool_calls.length > 1 ? 's' : ''} made
                        </summary>
                        <div className="mt-2 space-y-2">
                          {msg.tool_calls.map((tc, j) => (
                            <div key={j} className="text-xs bg-zinc-900 rounded-lg p-2">
                              <div className="text-blue-400 font-mono">{tc.name}</div>
                              <div className="text-zinc-500 mt-1">
                                <span className="text-zinc-600">Args:</span>{' '}
                                <code className="text-zinc-400">{JSON.stringify(tc.arguments)}</code>
                              </div>
                              <details className="mt-1">
                                <summary className="text-zinc-600 cursor-pointer hover:text-zinc-500">
                                  Result
                                </summary>
                                <pre className="mt-1 text-zinc-400 whitespace-pre-wrap overflow-x-auto max-h-40 overflow-y-auto">
                                  {tc.result}
                                </pre>
                              </details>
                            </div>
                          ))}
                        </div>
                      </details>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}
          
          {sending && (
            <div className="flex justify-start">
              <div className="bg-zinc-800 text-zinc-400 px-4 py-3 rounded-2xl">
                Thinking...
              </div>
            </div>
          )}
          
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input */}
      <div className="border-t border-zinc-800 px-6 py-4">
        <form onSubmit={sendMessage} className="max-w-3xl mx-auto flex gap-3">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Type a message..."
            className="flex-1 bg-zinc-800 text-white px-4 py-3 rounded-xl border border-zinc-700 focus:outline-none focus:border-zinc-500"
          />
          <button
            type="submit"
            disabled={sending || !input.trim()}
            className="bg-blue-600 text-white px-6 py-3 rounded-xl font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition"
          >
            Send
          </button>
        </form>
      </div>
    </div>
  )
}
