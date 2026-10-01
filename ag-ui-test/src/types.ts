export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

export interface ConversationSummary {
  threadId: string;
  status: "AI_ACTIVE" | "CLOSED";
  messages: ChatMessage[];
}
