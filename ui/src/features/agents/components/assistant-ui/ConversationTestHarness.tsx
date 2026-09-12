import { AssistantRuntimeProvider } from "@assistant-ui/react"
import AssistantConversation from "./AssistantConversation"
import type { AssistantConversationProps } from "./AssistantConversation"
import { useConversationRuntime } from "@/features/agents/lib/assistant-ui/conversationRuntime"

export function ConversationTestHarness(props: AssistantConversationProps) {
  const runtime = useConversationRuntime(props)
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <AssistantConversation {...props} />
    </AssistantRuntimeProvider>
  )
}
