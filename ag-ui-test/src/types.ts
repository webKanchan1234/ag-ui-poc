export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  // Present while the AI is waiting on a response to a frontend-rendered tool widget (see DynamicToolUI).
  pendingToolCall?: {
    id: string;
    name: string;
    args: Record<string, unknown>;
    resolved?: boolean;
  };
}

export interface ConversationSummary {
  threadId: string;
  status: "AI_ACTIVE" | "HANDOFF_PENDING" | "LIVE_CONNECTING" | "LIVE_SIMULATED" | "LIVE_UNCERTAIN" | "CLOSED";
  interruptId?: string | null;
  message?: string;
  messages: ChatMessage[];
}
