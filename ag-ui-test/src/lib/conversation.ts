import type { ConversationSummary } from "../types";

// Uses the first user message as a short label for the sidebar entry.
export function conversationLabel(conversation: ConversationSummary): string {
  const firstUserMessage = conversation.messages.find((m) => m.role === "user");
  return firstUserMessage?.content || "New conversation";
}
