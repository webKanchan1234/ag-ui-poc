import type { RefObject } from "react";
import { Box, Button } from "grommet";
import { Copy, Dislike, Like, Robot, User } from "grommet-icons";
import type { ChatMessage } from "../types";
import { renderMessageContent } from "../lib/markdown";
import { DynamicToolUI, type UiSpec } from "../lib/toolUi";

interface ChatMessagesProps {
  messages: ChatMessage[];
  isRunning: boolean;
  messagesEndRef: RefObject<HTMLDivElement | null>;
  onToolCallRespond: (messageId: string, toolCallId: string, result: unknown) => void;
}

export function ChatMessages({ messages, isRunning, messagesEndRef, onToolCallRespond }: ChatMessagesProps) {
  if (!messages.length) return null;

  return (
    <Box className="chat-messages" round="small" pad="medium" margin={{ bottom: "medium" }} style={{ maxHeight: "40vh" }}>
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
            {m.role === "user" ? <User size="small" color="white" /> : <Robot size="small" color="white" />}
          </Box>
          <Box className="chat-message-body">
            <Box className={`chat-bubble ${m.role}`}>
              {m.content ? renderMessageContent(m.content) : isRunning && m.role === "assistant" ? "..." : ""}
            </Box>
            {m.pendingToolCall && !m.pendingToolCall.resolved && !!m.pendingToolCall.args?.ui && (
              <Box margin={{ top: "small", left: "xsmall" }}>
                <DynamicToolUI
                  ui={m.pendingToolCall.args.ui as UiSpec}
                  onRespond={(result) => onToolCallRespond(m.id, m.pendingToolCall!.id, result)}
                />
              </Box>
            )}
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
  );
}
