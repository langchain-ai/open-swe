import { Outlet, createFileRoute } from "@tanstack/react-router"
import { useEffect } from "react"

import { InvestigateShell } from "@/features/investigate/InvestigateShell"
import { ErrorState, LoadingState } from "@/features/investigate/shared"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/investigate")({
  component: InvestigateLayout,
})

function InvestigateLayout() {
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
    <InvestigateShell user={session.data}>
      <Outlet />
    </InvestigateShell>
  )
}
