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

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [conversationList, setConversationList] = useState<ConversationSummary[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  // Created lazily on first send (or when a saved conversation is selected) so the backend's /conversations store owns the threadId.
  const agentRef = useRef<HttpAgent | null>(null);
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
    }
  }

  // Creates the backend-tracked conversation (once) and binds the agent to its threadId.
  async function ensureAgent(): Promise<HttpAgent> {
    if (agentRef.current) return agentRef.current;

    const conversation = await createConversation();
    setActiveThreadId(conversation.threadId);
    agentRef.current = new HttpAgent({ url: `${BFF_URL}/ag-ui`, threadId: conversation.threadId });
    loadConversationList();
    return agentRef.current;
  }

  // Clears the current session so the next message starts a brand-new conversation.
  function startNewConversation() {
    if (isRunning) return;
    agentRef.current = null;
    setActiveThreadId(null);
    setMessages([]);
  }

  // Loads a previously saved conversation's history and resumes it via its existing threadId.
  async function selectConversation(threadId: string) {
    if (isRunning || threadId === activeThreadId) return;

    const conversation = await fetchConversation(threadId);
    if (!conversation) return;

    setMessages(conversation.messages);
    setActiveThreadId(threadId);
    agentRef.current = new HttpAgent({
      url: `${BFF_URL}/ag-ui`,
      threadId,
      initialMessages: conversation.messages,
    });
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
        {},
        {
          onRunStartedEvent: () => {
            setMessages((prev) => [...prev, { id: assistantId, role: "assistant", content: "" }]);
          },
          onTextMessageContentEvent: ({ event }) => {
            // Target the update by id (not array position) so late/out-of-order deltas can never land on the wrong bubble.
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, content: m.content + event.delta } : m))
            );
          },
          // Backend sends this instead of RUN_FINISHED when the upstream call fails, so surface it in the same bubble.
          onRunErrorEvent: ({ event }) => {
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
          onRunFailed: ({ error }) => {
            console.error("Agent run failed", error);
            setIsRunning(false);
          },
        }
      );
    } catch (error) {
      console.error("Failed to get data", error);
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
  };
}
