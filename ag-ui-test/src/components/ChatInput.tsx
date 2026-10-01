import { Box, Button, Text, TextInput } from "grommet";
import { Add, Send } from "grommet-icons";

interface ChatInputProps {
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  isRunning: boolean;
  pushToBottom: boolean;
  showDemoButtons: boolean;
  onDemoQuery: (value: string) => void;
}

export function ChatInput({
  input,
  onInputChange,
  onSend,
  isRunning,
  pushToBottom,
  showDemoButtons,
  onDemoQuery,
}: ChatInputProps) {
  return (
    <>
      <Box style={pushToBottom ? { marginTop: "auto" } : {}}>
        <Box background="white" round="small" border={{ color: "border", size: "xsmall" }} style={{ position: "relative", minHeight: 220 }}>
          <Box direction="row" align="start" pad="xxsmall">
            <Box align="center" margin={{ vertical: "xxsmall", horizontal: "xxsmall" }}>
              <Add />
            </Box>
            <Box flex fill="horizontal" justify="center">
              <TextInput
                plain
                className="message-input"
                value={input}
                onChange={(e) => onInputChange(e.target.value)}
                placeholder="Ask a question"
                onKeyDown={(e) => e.key === "Enter" && onSend()}
                disabled={isRunning}
              />
            </Box>
          </Box>
          <Button
            icon={<Send size="small" color="white" />}
            onClick={onSend}
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
      {showDemoButtons && (
        <Box direction="row" gap="small" style={{ marginTop: "20px" }}>
          <Button onClick={() => onDemoQuery("demo")} secondary>
            Demo Button
          </Button>
          <Button onClick={() => onDemoQuery("demo2")} secondary>
            Demo Button
          </Button>
        </Box>
      )}
    </>
  );
}
