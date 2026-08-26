import { createFileRoute } from "@tanstack/react-router"

import { RecentAgentThreads } from "@/features/agents/components/RecentAgentThreads"

export const Route = createFileRoute("/agents/$threadId")({
  validateSearch: (
    search: Record<string, unknown>
  ): { feedback?: boolean } => ({
    feedback:
      search.feedback === true || search.feedback === "true" ? true : undefined,
  }),
  component: AgentThreadRoute,
})

function AgentThreadRoute() {
  const { threadId } = Route.useParams()
  const { feedback } = Route.useSearch()
  return (
    <RecentAgentThreads
      activeThreadId={threadId}
      autoFocusComposer={feedback}
    />
  )
}
