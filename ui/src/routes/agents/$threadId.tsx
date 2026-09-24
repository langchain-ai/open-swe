import { createFileRoute } from "@tanstack/react-router"

import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"

export interface AgentThreadSearch {
  /** The `task` tool-call id of a subagent to view instead of the thread itself. */
  subagent?: string
}

export const Route = createFileRoute("/agents/$threadId")({
  validateSearch: (search: Record<string, unknown>): AgentThreadSearch => {
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
