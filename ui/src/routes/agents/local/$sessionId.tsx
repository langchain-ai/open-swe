import { Navigate, createFileRoute } from "@tanstack/react-router"

import { LocalAgentThreadView } from "@/features/agents/components/LocalAgentThreadView"
import { AgentThreadStreamProvider } from "@/features/agents/lib/AgentThreadStreamProvider"
import { useReadyDesktopLocalThread } from "@/features/agents/lib/desktopLocal"
import { Skeleton } from "@/components/ui/skeleton"

export const Route = createFileRoute("/agents/local/$sessionId")({
  component: LocalAgentThreadPage,
})

function LocalAgentThreadPage() {
  const { sessionId } = Route.useParams()
  const threadQuery = useReadyDesktopLocalThread(sessionId)
  if (typeof window === "undefined" || !window.openSweDesktop) {
    return <Navigate to="/agents" />
  }
  if (threadQuery.isPending) {
    return (
      <main className="flex min-w-0 flex-1 items-center justify-center p-6">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    )
  }
  if (threadQuery.isError || !threadQuery.data) {
    return <Navigate to="/agents" />
  }
  return (
    <AgentThreadStreamProvider threadId={sessionId} transport="local">
      <LocalAgentThreadView sessionId={sessionId} />
    </AgentThreadStreamProvider>
  )
}
