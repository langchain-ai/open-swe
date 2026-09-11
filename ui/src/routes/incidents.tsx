import { Outlet, createFileRoute } from "@tanstack/react-router"
import { useEffect } from "react"

import { IncidentsShell } from "@/features/incidents/IncidentsShell"
import { ErrorState, LoadingState } from "@/features/incidents/shared"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/incidents")({
  component: IncidentsLayout,
})

function IncidentsLayout() {
  const session = useSession()
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
    <IncidentsShell user={session.data}>
      <Outlet />
    </IncidentsShell>
  )
}
