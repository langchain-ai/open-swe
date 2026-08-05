import { useEffect } from "react"
import { Outlet, createFileRoute, useRouterState } from "@tanstack/react-router"

import { AgentsShell } from "@/features/agents/components/AgentsSidebar"
import { Skeleton } from "@/components/ui/skeleton"
import { AgentThreadStreamProvider } from "@/features/agents/lib/AgentThreadStreamProvider"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/agents")({
  component: AgentsLayout,
})

/**
 * The `.agents-ui` class themes the layout subtree, but popovers, tooltips and
 * menus portal to `<body>`. Marking the document root while these routes are
 * mounted is what keeps those in the same palette.
 */
function useAgentsTheme() {
  useEffect(() => {
    document.documentElement.dataset["agentsTheme"] = "true"
    return () => {
      delete document.documentElement.dataset["agentsTheme"]
    }
  }, [])
}

function AgentsLayout() {
  useAgentsTheme()
  const session = useSession()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const [, section, threadId] = pathname.split("/")
  const activeThreadId =
    section === "agents" &&
    threadId &&
    threadId !== "automations" &&
    threadId !== "skills" &&
    threadId !== "threads" &&
    threadId !== "reviews"
      ? threadId
      : undefined

  if (session.isLoading) {
    return (
      <main className="agents-ui flex h-svh items-center justify-center bg-background p-6">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    )
  }

  if (!session.data) return <RequireLogin />

  return (
    <AgentsShell user={session.data} activeThreadId={activeThreadId}>
      <AgentThreadStreamProvider threadId={activeThreadId ?? null}>
        <Outlet />
      </AgentThreadStreamProvider>
    </AgentsShell>
  )
}
