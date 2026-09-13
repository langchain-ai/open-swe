import { Outlet, createFileRoute, useRouterState } from "@tanstack/react-router"
import { useEffect } from "react"

import { AgentsShell } from "@/features/agents/components/AgentsSidebar"
import { ErrorState, LoadingState } from "@/features/incidents/shared"
import { RequireLogin } from "@/lib/auth-redirect"
import { rememberAppLocation } from "@/lib/appLocation"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/incidents")({
  component: IncidentsLayout,
})

function IncidentsLayout() {
  const session = useSession()
  const href = useRouterState({ select: (state) => state.location.href })
  useEffect(() => {
    rememberAppLocation(href)
  }, [href])
  useEffect(() => {
    document.documentElement.dataset["agentsTheme"] = "true"
    return () => {
      delete document.documentElement.dataset["agentsTheme"]
    }
  }, [])
  if (session.isPending) return <LoadingState />
  if (session.error)
    return (
      <div className="mx-auto max-w-xl p-8">
        <ErrorState
          error={session.error}
          retry={() => void session.refetch()}
        />
      </div>
    )
  if (!session.data) return <RequireLogin />
  return (
    <AgentsShell user={session.data}>
      <div className="min-w-0 flex-1 overflow-y-auto">
        <Outlet />
      </div>
    </AgentsShell>
  )
}
