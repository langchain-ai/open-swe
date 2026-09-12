import { lazy, Suspense } from "react"
import type { AssistantConversationProps } from "./assistant-ui/AssistantConversation"

const Conversation = lazy(() => import("./assistant-ui/AssistantConversation"))

export function ExperimentalConversation(props: AssistantConversationProps) {
  return (
    <Suspense
      fallback={
        <div
          role="status"
          className="flex flex-1 items-center justify-center text-sm text-muted-foreground"
        >
          Loading conversation…
        </div>
      }
    >
      <Conversation key={props.threadId} {...props} />
    </Suspense>
  )
}
