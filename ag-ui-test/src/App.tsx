import { useState, useRef, type ReactNode, useEffect } from 'react'
import './App.css'
import { HttpAgent } from "@ag-ui/client";
import { Button, Grommet, Box, TextInput, Text } from 'grommet';
import { Add, Send, User, Robot, Like, Dislike, Copy } from 'grommet-icons';

interface ChatMessage{
  id : string,
  role: "user" | "assistant"
  content: string;
}

// Renders "**bold**" and "[text](url)" markdown-lite segments as JSX, preserving line breaks.
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let idx = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index));
    if (match[1] !== undefined) {
      nodes.push(
        <a key={`${keyPrefix}-a-${idx}`} href={match[2]} target="_blank" rel="noopener noreferrer">
          {match[1]}
        </a>
      );
    } else {
      nodes.push(<strong key={`${keyPrefix}-b-${idx}`}>{match[3]}</strong>);
    }
    idx++;
    lastIndex = pattern.lastIndex;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

function renderMessageContent(content: string): ReactNode {
  return content.split("\n").map((line, i, arr) => (
    <span key={i}>
      {renderInline(line, `l${i}`)}
      {i < arr.length - 1 && <br />}
    </span>
  ));
}

function App() {
 const [messages, setMessages] = useState<ChatMessage[]>([])
 const [input, setInput] = useState("")
 const [isRunning, setIsRunning] = useState(false)
 // Local backend (backend_ai/server.js) proxies to the real HPE agent, so the browser never sees the OAuth client secret.
 const agentRef = useRef(new HttpAgent({ url: "http://127.0.0.1:3000/ag-ui" }))
 const messagesEndRef = useRef<HTMLDivElement | null>(null)

 // Adds the user's message to chat state + the agent's history, then streams the assistant's reply back via AG-UI events.
 async function sendQuery(query: string)
 {
  const userMessage : ChatMessage = { id: crypto.randomUUID(), role: "user", content: query }
  const assistantId = crypto.randomUUID()
  setMessages((prev)=>[...prev, userMessage])

  const agent = agentRef.current;
  agent.addMessage({ id: userMessage.id, role: "user", content: query })
  setIsRunning(true)

  try{
    await agent.runAgent({}, {
      onRunStartedEvent: () => {
        setMessages((prev) => [...prev, { id: assistantId, role: "assistant", content: "" }]);
      },
      onTextMessageContentEvent: ({ event }) => {
        // Target the update by id (not array position) so late/out-of-order deltas can never land on the wrong bubble.
        setMessages((prev)=>
          prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + event.delta } : m))
        )
      },
      onRunFinishedEvent: () => setIsRunning(false),
      onRunFailed: ({ error }) => {
        console.error("Agent run failed", error);
        setIsRunning(false)
      },
    })
  }
  catch(error){
    console.error("Failed to get data", error);
    setIsRunning(false)
  }
 }

 useEffect(()=>{
  messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
 }, [messages])

 const sendMessage = ()=>{
  const trimmed = input.trim()
  if(!trimmed || isRunning) return;

  setInput("")
  sendQuery(trimmed)
 }

 const handleExtraButtonClicks =(e: React.MouseEvent<HTMLButtonElement | HTMLAnchorElement>)=>{
  const value = (e.currentTarget as HTMLButtonElement).value;
  if(isRunning) return;
  sendQuery(value)
 }
   return (
    <Grommet style={{height:"100vh" }}>
      <Box fill="vertical" background="background-back" align="center" pad={{ top: "large" }} style={{height:"100%"}}>
        <Box width={{ max: "700px" }} fill pad="medium">
          <Text weight="bold" size="large" margin={{ bottom: "xsmall" }}>
            Welcome to HPE Support Center AI Troubleshooting (Beta).
          </Text>
          <Text color="text-weak" margin={{ bottom: "medium" }}>
            Try our Beta Troubleshooting and, if needed, we can escalate you to a live agent.
          </Text>

          {messages.length > 0 && (
            <Box
              className="chat-messages"
              round="small"
              pad="medium"
              margin={{ bottom: "medium" }}
              style={{ maxHeight: "40vh" }}
            >
              {messages.map((m) => (
                <div key={m.id} className="chat-message-row">
                  <Box
                    round="full"
                    width="32px"
                    height="32px"
                    flex={false}
                    align="center"
                    justify="center"
                    background={m.role === "user" ? "#2563eb" : "#5b6472"}
                  >
                    {m.role === "user" ? (
                      <User size="small" color="white" />
                    ) : (
                      <Robot size="small" color="white" />
                    )}
                  </Box>
                  <Box className="chat-message-body">
                    <Box className={`chat-bubble ${m.role}`}>
                      {m.content
                        ? renderMessageContent(m.content)
                        : isRunning && m.role === "assistant"
                        ? "..."
                        : ""}
                    </Box>
                    {m.role === "assistant" && !!m.content && (
                      <Box direction="row" gap="xsmall" margin={{ top: "small", left: "xsmall" }}>
                        <Button plain hoverIndicator="background" pad="xsmall" icon={<Like size="small" color="text-weak" />} />
                        <Button plain hoverIndicator="background" pad="xsmall" icon={<Dislike size="small" color="text-weak" />} />
                        <Button
                          plain
                          hoverIndicator="background"
                          pad="xsmall"
                          icon={<Copy size="small" color="text-weak" />}
                          onClick={() => navigator.clipboard.writeText(m.content)}
                        />
                      </Box>
                    )}
                  </Box>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </Box>
          )}
          <Box style={messages.length > 0 ? {marginTop: "auto"} : {}}>
          <Box
            background="white"
            round="small"
            border={{ color: "border", size: "xsmall" }}
            style={{ position: "relative", minHeight: 220 }}
          >
            <Box direction="row" align="start" pad="xxsmall">
              <Box align="center" margin={{ vertical: "xxsmall", horizontal: "xxsmall" }}>
                <Add />
                </Box>
              <Box flex fill="horizontal" justify="center">
                <TextInput
                  plain
                  className="message-input"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="Ask a question"
                  onKeyDown={(e) => e.key === "Enter" && sendMessage()}
                  disabled={isRunning}
                />
              </Box>
            </Box>
            <Button
              icon={<Send size="small" color="white" />}
              onClick={sendMessage}
              disabled={isRunning}
              style={{
                position: "absolute",
                right: 12,
                bottom: 12,
                borderRadius: "50%",
                width: 36,
                height: 36,
                padding: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: "#1a1a1a",
              }}
            />
          </Box>
          <Text size="xsmall" color="text-weak" margin={{ top: "xsmall" }}>
            AI responses may include mistakes. Verify important information.
          </Text>
        </Box>
        {!messages.length && <Box direction="row" gap="small" style={{marginTop:"20px"}}>
          <Button value="demo" onClick={handleExtraButtonClicks} secondary>Demo Button</Button>
          <Button value="demo2" onClick={handleExtraButtonClicks} secondary>Demo Button</Button>
        </Box>}
        </Box>
      </Box>
    </Grommet>
  );
}

export default App

