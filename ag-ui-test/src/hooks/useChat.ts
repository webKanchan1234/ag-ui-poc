import { useEffect, useRef, useState } from "react";
import { HttpAgent } from "@ag-ui/client";
import type { ChatMessage, ConversationSummary } from "../types";
import {
  BFF_URL,
  createConversation,
  deleteConversation as deleteConversationRequest,
  fetchConversation,
  fetchConversations,
} from "../lib/api";
import { conversationLabel } from "../lib/conversation";

// The backend only persists role/content, so a still-open handoff prompt has to be
// re-derived from the conversation's own status/interruptId on reload. The original
// LLM-authored button labels aren't persisted either, so this restore uses the same
// default wording the backend falls back to.
const DEFAULT_HANDOFF_UI = {
  type: "button-group",
  buttons: [
    { label: "Yes, connect me", value: { approved: true }, variant: "primary" },
    { label: "No, continue with AI", value: { approved: false } },
  ],
};

function restorePendingToolCall(conversation: ConversationSummary): ChatMessage[] {
  if (conversation.status !== "HANDOFF_PENDING" || !conversation.interruptId) {
    return conversation.messages;
  }

  const lastAssistantIndex = [...conversation.messages].reverse().findIndex((m) => m.role === "assistant");
  if (lastAssistantIndex === -1) return conversation.messages;

  const targetIndex = conversation.messages.length - 1 - lastAssistantIndex;
  return conversation.messages.map((m, i) =>
    i === targetIndex
      ? {
          ...m,
          pendingToolCall: {
            id: conversation.interruptId!,
            name: "request_handoff",
            args: { reason: conversation.message, ui: DEFAULT_HANDOFF_UI },
          },
        }
      : m
  );
}

// Declares the one AI-invokable frontend tool: the AI decides when to call it, the
// frontend renders the Yes/No buttons for it (see ChatMessages).
const HANDOFF_TOOL = {
  name: "request_handoff",
  description: "Escalate the conversation to a live human support agent.",
  parameters: {
    type: "object",
    properties: {
      reason: { type: "string", description: "Why the user wants a human." },
    },
    required: ["reason"],
  },
};

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [conversationList, setConversationList] = useState<ConversationSummary[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  // Created lazily on first send (or when a saved conversation is selected) so the backend's /conversations store owns the threadId.
  const agentRef = useRef<HttpAgent | null>(null);
  // A random per-session id forwarded to the backend (and on to the upstream agent) to correlate turns within this browser session.
  const contextIdRef = useRef<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  async function loadConversationList() {
    setConversationList(await fetchConversations());
  }

  useEffect(() => {
    loadConversationList();
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  async function handleDeleteConversation(threadId: string) {
    await deleteConversationRequest(threadId);
    await loadConversationList();

    if (activeThreadId === threadId) {
      setActiveThreadId(null);
      setMessages([]);
      agentRef.current = null;
      contextIdRef.current = null;
    }
  }

  // Creates the backend-tracked conversation (once) and binds the agent to its threadId.
  async function ensureAgent(): Promise<HttpAgent> {
    if (agentRef.current) return agentRef.current;

    const conversation = await createConversation();
    setActiveThreadId(conversation.threadId);
    contextIdRef.current = crypto.randomUUID();
    agentRef.current = new HttpAgent({ url: `${BFF_URL}/ag-ui`, threadId: conversation.threadId });
    loadConversationList();
    return agentRef.current;
  }

  // Clears the current session so the next message starts a brand-new conversation.
  function startNewConversation() {
    if (isRunning) return;
    agentRef.current = null;
    contextIdRef.current = null;
    setActiveThreadId(null);
    setMessages([]);
  }

  // Loads a previously saved conversation's history and resumes it via its existing threadId.
  async function selectConversation(threadId: string) {
    if (isRunning || threadId === activeThreadId) return;

    const conversation = await fetchConversation(threadId);
    if (!conversation) return;

    setMessages(restorePendingToolCall(conversation));
    setActiveThreadId(threadId);
    contextIdRef.current = crypto.randomUUID();
    agentRef.current = new HttpAgent({
      url: `${BFF_URL}/ag-ui`,
      threadId,
      initialMessages: conversation.messages,
    });
  }

  // Builds the run subscriber shared by a normal turn and a tool-result continuation turn.
  function buildSubscribers(assistantId: string) {
    return {
      onRunStartedEvent: () => {
        setMessages((prev) => [...prev, { id: assistantId, role: "assistant", content: "" }]);
      },
      onTextMessageContentEvent: ({ event }: { event: { delta: string } }) => {
        // Target the update by id (not array position) so late/out-of-order deltas can never land on the wrong bubble.
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + event.delta } : m))
        );
      },
      // The AI decided to invoke a tool and supplied a frontend-renderable ui widget; attach it to this bubble.
      onToolCallEndEvent: ({
        event,
        toolCallName,
        toolCallArgs,
      }: {
        event: { toolCallId: string };
        toolCallName: string;
        toolCallArgs: Record<string, unknown>;
      }) => {
        if (!toolCallArgs?.ui) return;
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, pendingToolCall: { id: event.toolCallId, name: toolCallName, args: toolCallArgs } }
              : m
          )
        );
      },
      // Backend sends this instead of RUN_FINISHED when the upstream call fails, so surface it in the same bubble.
      onRunErrorEvent: ({ event }: { event: { message: string } }) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, content: event.message } : m))
        );
        setIsRunning(false);
        loadConversationList();
      },
      onRunFinishedEvent: () => {
        setIsRunning(false);
        loadConversationList();
      },
      onRunFailed: ({ error }: { error: unknown }) => {
        console.error("Agent run failed", error);
        setIsRunning(false);
      },
    };
  }

  // Adds the user's message to chat state + the agent's history, then streams the assistant's reply back via AG-UI events.
  async function sendQuery(query: string) {
    const userMessage: ChatMessage = { id: crypto.randomUUID(), role: "user", content: query };
    const assistantId = crypto.randomUUID();
    setMessages((prev) => [...prev, userMessage]);
    setIsRunning(true);

    let agent: HttpAgent;
    try {
      agent = await ensureAgent();
    } catch (error) {
      console.error("Failed to start conversation", error);
      setIsRunning(false);
      return;
    }

    agent.addMessage({ id: userMessage.id, role: "user", content: query });

    try {
      await agent.runAgent(
        { tools: [HANDOFF_TOOL], forwardedProps: { context_id: contextIdRef.current } },
        buildSubscribers(assistantId)
      );
    } catch (error) {
      console.error("Failed to get data", error);
      setIsRunning(false);
    }
  }

  // Sends the user's response to a pending tool widget (whatever shape it produced) and resumes the run.
  async function respondToToolCall(messageId: string, toolCallId: string, result: unknown) {
    if (isRunning || !agentRef.current) return;

    setMessages((prev) =>
      prev.map((m) =>
        m.id === messageId && m.pendingToolCall
          ? { ...m, pendingToolCall: { ...m.pendingToolCall, resolved: true } }
          : m
      )
    );
    setIsRunning(true);

    const agent = agentRef.current;
    agent.addMessage({
      id: crypto.randomUUID(),
      role: "tool",
      toolCallId,
      content: JSON.stringify(result),
    });

    const assistantId = crypto.randomUUID();
    try {
      await agent.runAgent(
        { forwardedProps: { context_id: contextIdRef.current } },
        buildSubscribers(assistantId)
      );
    } catch (error) {
      console.error("Failed to resolve handoff", error);
      setIsRunning(false);
    }
  }

  function sendMessage() {
    const trimmed = input.trim();
    if (!trimmed || isRunning) return;

    setInput("");
    sendQuery(trimmed);
  }

  function sendDemoQuery(value: string) {
    if (isRunning) return;
    sendQuery(value);
  }

  // Recomputed every render so it always reflects the latest fetched list, not just when searchQuery changes.
  const filteredConversationList = conversationList.filter((c) =>
    conversationLabel(c).toLowerCase().includes(searchQuery.toLowerCase().trim())
  );

  return {
    messages,
    input,
    setInput,
    isRunning,
    activeThreadId,
    searchQuery,
    setSearchQuery,
    filteredConversationList,
    messagesEndRef,
    startNewConversation,
    selectConversation,
    deleteConversation: handleDeleteConversation,
    sendMessage,
    sendDemoQuery,
    respondToToolCall,
  };
}
