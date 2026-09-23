import { useMemo, useState } from 'react'
import './App.css'

const AG_UI_URL = import.meta.env.VITE_AGUI_URL || 'http://127.0.0.1:3000/ag-ui'

function App() {
  const [messages, setMessages] = useState([
    {
      id: 'welcome',
      role: 'assistant',
      content: 'Welcome to HPE Support Center AI Troubleshooting (Beta). Ask your question to begin.'
    }
  ])
  const [input, setInput] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [status, setStatus] = useState('Ready')
  const [statusType, setStatusType] = useState('ready')

  const canSend = useMemo(() => !isSending && input.trim().length > 0, [isSending, input])

  const appendMessage = (message) => {
    setMessages((prev) => [...prev, message])
  }

  const appendAssistantDelta = (messageId, delta) => {
    setMessages((prev) =>
      prev.map((msg) => (msg.id === messageId ? { ...msg, content: msg.content + delta } : msg))
    )
  }

  const appendMetaMessage = (id, content) => {
    setMessages((prev) => {
      if (prev.some((item) => item.id === id)) {
        return prev
      }
      return [...prev, { id, role: 'meta', content }]
    })
  }

  const handleSend = async (event) => {
    event.preventDefault()
    const prompt = input.trim()
    if (!prompt || isSending) {
      return
    }

    const userMessageId = `user-${Date.now()}`
    const assistantMessageId = `assistant-${Date.now() + 1}`
    const threadId = `thread-${Date.now()}`
    const runId = `run-${Date.now()}`

    appendMessage({ id: userMessageId, role: 'user', content: prompt })
    appendMessage({ id: assistantMessageId, role: 'assistant', content: '' })

    setInput('')
    setIsSending(true)
    setStatus('Analyzing with AI...')
    setStatusType('loading')

    let streamedAnyContent = false
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 30000)

    try {
      const response = await fetch(AG_UI_URL, {
        method: 'POST',
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          threadId,
          runId,
          messages: [{ role: 'user', content: prompt }]
        })
      })

      if (!response.ok || !response.body) {
        throw new Error(`Request failed (${response.status})`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          break
        }

        buffer += decoder.decode(value, { stream: true })
        const events = buffer.split(/\r?\n\r?\n/)
        buffer = events.pop() || ''

        for (const eventChunk of events) {
          const dataLine = eventChunk
            .split(/\r?\n/)
            .find((line) => line.trimStart().startsWith('data:'))

          if (!dataLine) {
            continue
          }

          const dataPayload = dataLine.replace(/^\s*data:\s?/, '')
          let payload
          try {
            payload = JSON.parse(dataPayload)
          } catch {
            continue
          }

          if (payload.type === 'RUN_STARTED') {
            setStatus('Analyzing with AI...')
            setStatusType('loading')
          }

          if (payload.type === 'CUSTOM' && payload.name === 'LOCAL_AGENT_REDIRECT') {
            setStatus('Connecting to live agent...')
            setStatusType('handoff')
            appendMetaMessage(
              `meta-${runId}`,
              'Connecting you to a live agent. Please wait while we transfer your conversation.'
            )
          }

          if (payload.type === 'CUSTOM' && payload.name === 'AI_ERROR') {
            const reason = payload?.value?.reason || 'Unknown OpenAI error'
            setStatus('AI error')
            setStatusType('error')
            appendAssistantDelta(assistantMessageId, `\n\n[AI Error] ${reason}`)
          }

          if (payload.type === 'TEXT_MESSAGE_CONTENT') {
            streamedAnyContent = true
            appendAssistantDelta(assistantMessageId, payload.delta || '')
            setStatus('Generating response...')
            setStatusType('loading')
          }

          if (payload.type === 'RUN_FINISHED') {
            const route = payload.route || 'unknown'
            if (route === 'local-agent') {
              setStatus('Connected to live agent')
              setStatusType('handoff')
            } else {
              setStatus('Completed (AI)')
              setStatusType('ready')
            }
          }
        }
      }

      if (!streamedAnyContent) {
        appendAssistantDelta(
          assistantMessageId,
          'No stream content received. Verify backend logs and OPENAI_API_KEY configuration.'
        )
        setStatus('Completed (no content)')
        setStatusType('error')
      }
    } catch (error) {
      if (error?.name === 'AbortError') {
        setStatus('Timed out')
      } else {
        setStatus('Failed')
      }
      setStatusType('error')
      const reason = error?.message ? ` (${error.message})` : ''
      appendAssistantDelta(
        assistantMessageId,
        `Sorry, request failed. Please check backend on ${AG_UI_URL}.${reason}`
      )
    } finally {
      clearTimeout(timeoutId)
      setIsSending(false)
    }
  }

  return (
    <main className="vision-page">
      <h1 className="vision-title">HPE Support Center: Augmented AI Experience Vision</h1>

      <section className="portal-shell">
        <header className="portal-bar">
          <div className="brand-row">
            <button className="icon-btn" aria-label="Menu">
              ☰
            </button>
            <div className="brand-text">HPE Support Center</div>
          </div>
          <div className="bar-actions">
            <span className="pill">Exit AI Troubleshooting</span>
            <span className="dot-icon" />
            <span className="dot-icon" />
            <span className="avatar">IO</span>
          </div>
        </header>

        <div className="intro-block">
          <p className="intro-title">Welcome to HPE Support Center AI Troubleshooting (Beta).</p>
          <p className="intro-sub">
            Try our Beta Troubleshooting and, if needed, we can escalate you to a live agent.
          </p>
          <div className="example-row">
            <span className="chip">Check my warranty</span>
            <span className="chip">How do I upgrade firmware</span>
            <span className="chip">List my products</span>
          </div>
        </div>

        <section className="chat-card">
          <header className="chat-header">
            <div>
              <h2>AG-UI Test Client</h2>
              <p className="sub">Backend: {AG_UI_URL}</p>
            </div>
            <span className={`status ${statusType}`}>{status}</span>
          </header>

          <div className="messages">
            {messages.map((message) => (
              <div key={message.id} className={`message-row ${message.role}`}>
                <div className="message">{message.content || '...'}</div>
              </div>
            ))}
          </div>

          <form className="composer" onSubmit={handleSend}>
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="Type your support issue"
              disabled={isSending}
            />
            <button type="submit" disabled={!canSend}>
              Send
            </button>
          </form>
        </section>
      </section>
    </main>
  )
}

export default App
