import { Navigate, createFileRoute } from "@tanstack/react-router"

import { LocalAgentThreadView } from "@/features/agents/components/LocalAgentThreadView"

export const Route = createFileRoute("/agents/local/$sessionId")({
  component: LocalAgentThreadPage,
})

function LocalAgentThreadPage() {
  const { sessionId } = Route.useParams()
  if (typeof window === "undefined" || !window.openSweDesktop) {
    return <Navigate to="/agents" />
  }
  return <LocalAgentThreadView sessionId={sessionId} />
}
