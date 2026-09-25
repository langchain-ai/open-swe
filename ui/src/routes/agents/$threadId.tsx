import { createFileRoute } from "@tanstack/react-router"

import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"

export const Route = createFileRoute("/agents/$threadId")({
  component: AgentThreadRoute,
})

function AgentThreadRoute() {
  const { threadId } = Route.useParams()
  return <AgentThreadPage threadId={threadId} />
}
