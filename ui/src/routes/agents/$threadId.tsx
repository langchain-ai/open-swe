import { createFileRoute } from "@tanstack/react-router"

import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"

export const Route = createFileRoute("/agents/$threadId")({
  // `subagent` is the `task` call id of a subagent to view instead of the thread.
  validateSearch: (search: Record<string, unknown>): { subagent?: string } => {
    const subagent = search["subagent"]
    return typeof subagent === "string" && subagent ? { subagent } : {}
  },
  component: AgentThreadRoute,
})

function AgentThreadRoute() {
  const { threadId } = Route.useParams()
  const { subagent } = Route.useSearch()
  return <AgentThreadPage threadId={threadId} subagentId={subagent} />
}
