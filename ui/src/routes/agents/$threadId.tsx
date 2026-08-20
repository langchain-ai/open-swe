import { createFileRoute } from "@tanstack/react-router"

import { AgentThreadPending } from "@/features/agents/components/AgentThreadPage"
import { RecentAgentThreads } from "@/features/agents/components/RecentAgentThreads"

export const Route = createFileRoute("/agents/$threadId")({
  pendingMs: 0,
  pendingComponent: AgentThreadPending,
  component: AgentThreadRoute,
})

function AgentThreadRoute() {
  const { threadId } = Route.useParams()
  return <RecentAgentThreads activeThreadId={threadId} />
}
