import { Box, Button, DataSearch, Text } from "grommet";
import { Trash } from "grommet-icons";
import type { ConversationSummary } from "../types";
import { conversationLabel } from "../lib/conversation";

interface SidebarProps {
  conversations: ConversationSummary[];
  activeThreadId: string | null;
  searchQuery: string;
  onSearchChange: (value: string) => void;
  onNewConversation: () => void;
  onSelectConversation: (threadId: string) => void;
  onDeleteConversation: (threadId: string) => void;
}

export function Sidebar({
  conversations,
  activeThreadId,
  searchQuery,
  onSearchChange,
  onNewConversation,
  onSelectConversation,
  onDeleteConversation,
}: SidebarProps) {
  return (
    <Box
      width="300px"
      flex={false}
      background="background-back"
      pad="medium"
      style={{ borderRight: "1px solid #e0e0e0", overflowY: "auto" }}
    >
      <Button label="+ New conversation" onClick={onNewConversation} margin={{ bottom: "medium" }} primary />
      <Box
        style={{ display: "flex", flexDirection: "row", justifyContent: "space-between", gap: "24px", alignItems: "center" }}
      >
        <Text size="small" weight="bold" color="text-weak" margin={{ bottom: "small" }}>
          History
        </Text>
        <DataSearch size="xsmall" value={searchQuery} onChange={(e) => onSearchChange(e.target.value)} />
      </Box>
      <Box margin={{ top: "small" }}>
        {conversations.map((c) => (
          <Box
            key={c.threadId}
            pad="small"
            round="small"
            margin={{ top: "xsmall" }}
            background={c.threadId === activeThreadId ? "#e0e0e0" : "white"}
            hoverIndicator="background"
            onClick={() => onSelectConversation(c.threadId)}
            style={{ cursor: "pointer", display: "flex", flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}
          >
            <Text size="small" truncate weight={c.threadId === activeThreadId ? "bold" : "normal"}>
              {conversationLabel(c)}
            </Text>
            <Button>
              <Trash size="small" onClick={() => onDeleteConversation(c.threadId)} />
            </Button>
          </Box>
        ))}
      </Box>
    </Box>
  );
}
