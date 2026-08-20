import { Navigate } from "@tanstack/react-router"

import { AgentThreadView } from "@/features/agents/components/AgentThreadView"
import { Skeleton } from "@/components/ui/skeleton"
import { AgentThreadStreamBoundary } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { useAgentThread } from "@/features/agents/lib/queries"

export function AgentThreadPage({
  threadId,
  active = true,
  autoFocusComposer = false,
}: {
  threadId: string
  active?: boolean
  autoFocusComposer?: boolean
}) {
  const threadQuery = useAgentThread(threadId)

  if (threadQuery.isLoading) {
    return (
      <main className="flex min-w-0 flex-1 items-center justify-center p-6">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    )
  }

  if (threadQuery.isError || !threadQuery.data) {
    return active ? <Navigate to="/agents" /> : null
  }

  return (
    <AgentThreadStreamBoundary active={active}>
      <AgentThreadView
        thread={threadQuery.data}
        autoFocusComposer={autoFocusComposer}
      />
    </AgentThreadStreamBoundary>
  )
}
