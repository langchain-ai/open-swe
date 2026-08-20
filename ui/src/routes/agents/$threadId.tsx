import { createFileRoute } from "@tanstack/react-router"

import { RecentAgentThreads } from "@/features/agents/components/RecentAgentThreads"

export const Route = createFileRoute("/agents/$threadId")({
  component: AgentThreadRoute,
})

function AgentThreadRoute() {
  const { threadId } = Route.useParams()
  return <RecentAgentThreads activeThreadId={threadId} />
}
