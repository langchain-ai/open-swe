import { Navigate, createFileRoute } from "@tanstack/react-router"

import { LocalAgentThreadView } from "@/features/agents/components/LocalAgentThreadView"
import { AgentThreadStreamProvider } from "@/features/agents/lib/AgentThreadStreamProvider"

export const Route = createFileRoute("/agents/local/$sessionId")({
  component: LocalAgentThreadPage,
})

function LocalAgentThreadPage() {
  const { sessionId } = Route.useParams()
  if (typeof window === "undefined" || !window.openSweDesktop) {
    return <Navigate to="/agents" />
  }
  return (
    <AgentThreadStreamProvider threadId={sessionId} transport="local">
      <LocalAgentThreadView sessionId={sessionId} />
    </AgentThreadStreamProvider>
  )
}
