import './App.css'
import { Box, Grommet, Text } from 'grommet'
import { useChat } from './hooks/useChat'
import { Sidebar } from './components/Sidebar'
import { ChatMessages } from './components/ChatMessages'
import { ChatInput } from './components/ChatInput'

function App() {
  const chat = useChat()

  return (
    <Grommet style={{ height: "100vh" }}>
      <Box direction="row" fill style={{ height: "100%" }}>
        <Sidebar
          conversations={chat.filteredConversationList}
          activeThreadId={chat.activeThreadId}
          searchQuery={chat.searchQuery}
          onSearchChange={chat.setSearchQuery}
          onNewConversation={chat.startNewConversation}
          onSelectConversation={chat.selectConversation}
          onDeleteConversation={chat.deleteConversation}
        />
        <Box fill="vertical" flex background="background-back" align="center" pad={{ top: "large" }} style={{ height: "100%", overflowY: "auto" }}>
          <Box width={{ max: "700px" }} fill pad="medium">
            <Text weight="bold" size="large" margin={{ bottom: "xsmall" }}>
              Welcome to HPE Support Center AI Troubleshooting (Beta).
            </Text>
            <Text color="text-weak" margin={{ bottom: "medium" }}>
              Try our Beta Troubleshooting and, if needed, we can escalate you to a live agent.
            </Text>

            <ChatMessages messages={chat.messages} isRunning={chat.isRunning} messagesEndRef={chat.messagesEndRef} />

            <ChatInput
              input={chat.input}
              onInputChange={chat.setInput}
              onSend={chat.sendMessage}
              isRunning={chat.isRunning}
              pushToBottom={chat.messages.length > 0}
              showDemoButtons={!chat.messages.length}
              onDemoQuery={chat.sendDemoQuery}
            />
          </Box>
        </Box>
      </Box>
    </Grommet>
  )
}

export default App

