import type { ConversationSummary } from "../types";

export const BFF_URL = "http://127.0.0.1:3000";

export async function fetchConversations(): Promise<ConversationSummary[]> {
  const response = await fetch(`${BFF_URL}/conversations`);
  if (!response.ok) return [];
  return response.json();
}

export async function createConversation(): Promise<ConversationSummary> {
  const response = await fetch(`${BFF_URL}/conversations`, { method: "POST" });
  if (!response.ok) throw new Error("Failed to create conversation");
  return response.json();
}

export async function fetchConversation(threadId: string): Promise<ConversationSummary | null> {
  const response = await fetch(`${BFF_URL}/conversations/${threadId}`);
  if (!response.ok) return null;
  return response.json();
}

export async function deleteConversation(threadId: string): Promise<void> {
  await fetch(`${BFF_URL}/conversations/${threadId}/delete`, { method: "POST" });
}
