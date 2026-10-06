import { Outlet, createFileRoute, useRouterState } from "@tanstack/react-router"
import { useEffect } from "react"

import { Box } from "@langchain/gtm-platform-design-system/ui/box"

import { AgentsShell } from "@/features/agents/components/AgentsSidebar"
import { ErrorState, LoadingState } from "@/features/incidents/shared"
import { RequireLogin } from "@/lib/auth-redirect"
import { rememberAppLocation } from "@/lib/appLocation"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/incidents")({
  component: IncidentsLayout,
})

/** Incidents is an Agents-rail destination, so it shares the Agents shell. */
function IncidentsLayout() {
  const session = useSession()
  const href = useRouterState({ select: (state) => state.location.href })
  useEffect(() => {
    rememberAppLocation(href)
  }, [href])
  if (session.isPending) return <LoadingState />
  if (session.error)
    return (
      <Box className="mx-auto w-full max-w-reading p-8">
        <ErrorState
          error={session.error}
          retry={() => void session.refetch()}
        />
      </Box>
    )
  if (!session.data) return <RequireLogin />
  return (
    <AgentsShell user={session.data}>
      <Outlet />
    </AgentsShell>
  )
}
