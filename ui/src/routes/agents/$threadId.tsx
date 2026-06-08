import { Navigate, createFileRoute } from "@tanstack/react-router"

import { AgentThreadView } from "@/components/agents/AgentThreadView"
import { AgentsShell } from "@/components/agents/AgentsSidebar"
import { Skeleton } from "@/components/ui/skeleton"
import { useAgentThread } from "@/lib/agents/queries"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/agents/$threadId")({
  component: AgentThreadPage,
})

function AgentThreadPage() {
  const { threadId } = Route.useParams()
  const session = useSession()
  const threadQuery = useAgentThread(threadId)

  if (session.isLoading) return null
  if (!session.data) return <Navigate to="/login" />

  if (threadQuery.isLoading) {
    return (
      <AgentsShell user={session.data} activeThreadId={threadId}>
        <main className="flex min-w-0 flex-1 items-center justify-center p-6">
          <Skeleton className="h-40 w-full max-w-md" />
        </main>
      </AgentsShell>
    )
  }

  if (threadQuery.isError || !threadQuery.data) {
    return <Navigate to="/agents" />
  }

  return <AgentThreadView user={session.data} thread={threadQuery.data} />
}
