import { createFileRoute } from "@tanstack/react-router"

import { Skeleton } from "@/components/ui/skeleton"
import { RecentAgentThreads } from "@/features/agents/components/RecentAgentThreads"

export const Route = createFileRoute("/agents/$threadId")({
  pendingMs: 0,
  pendingComponent: AgentThreadPending,
  component: AgentThreadRoute,
})

function AgentThreadPending() {
  return (
    <main className="flex min-w-0 flex-1 items-center justify-center p-6">
      <Skeleton className="h-40 w-full max-w-md" />
    </main>
  )
}

function AgentThreadRoute() {
  const { threadId } = Route.useParams()
  return <RecentAgentThreads activeThreadId={threadId} />
}
