import { useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Outlet,
  createFileRoute,
  useMatch,
  useRouterState,
} from "@tanstack/react-router"

import { AgentsShell } from "@/features/agents/components/AgentsSidebar"
import { planQueryOptions } from "@/lib/plan"
import { Skeleton } from "@/components/ui/skeleton"
import { AgentStreamProvider } from "@/features/agents/lib/stream/AgentStreamProvider"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"
import { isDesktopLocalModeEnabled } from "@/lib/desktop-local-mode"
import { rememberAppLocation } from "@/lib/appLocation"

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

/** Only shared plans render full screen, and the status arrives with the plan
 * fetch, so the sidebar stays hidden until it resolves: showing it first would
 * make it vanish again on every shared plan. */
function useHideSidebarForPlan(threadId: string | undefined): boolean {
  const plan = useQuery({
    ...planQueryOptions(threadId ?? ""),
    enabled: Boolean(threadId),
  })
  if (!threadId) return false
  return plan.isPending || plan.data?.status === "shared"
}

function AgentsLayout() {
  useAgentsTheme()
  const session = useSession()
  const navigate = Route.useNavigate()
  const threadMatch = useMatch({
    from: "/agents/$threadId",
    shouldThrow: false,
  })
  const localMatch = useMatch({
    from: "/agents/local/$sessionId",
    shouldThrow: false,
  })
  const planMatch = useMatch({
    from: "/agents/$threadId_/plan",
    shouldThrow: false,
  })
  const hideSidebar = useHideSidebarForPlan(
    session.data ? planMatch?.params.threadId : undefined
  )
  const activeThreadId = threadMatch?.params.threadId
  const activeLocalSessionId = localMatch?.params.sessionId
  const location = useRouterState({
    select: (state) => state.location,
  })
  const pathname = location.pathname
  const localOnly = !session.data && isDesktopLocalModeEnabled()
  const isLocalRoute =
    pathname === "/agents" ||
    pathname === "/agents/" ||
    Boolean(activeLocalSessionId)

  useEffect(() => {
    rememberAppLocation(location.href)
  }, [location.href])

  if (session.isLoading) {
    return (
      <main className="agents-ui flex h-svh items-center justify-center bg-background p-6">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    )
  }

  if (!session.data && (!localOnly || !isLocalRoute)) return <RequireLogin />

  return (
    <AgentsShell
      user={session.data ?? null}
      localOnly={localOnly}
      activeThreadId={activeThreadId}
      activeLocalSessionId={activeLocalSessionId}
      hideSidebar={hideSidebar}
    >
      <AgentStreamProvider
        threadId={activeLocalSessionId ?? activeThreadId ?? null}
        transport={activeLocalSessionId ? "local" : "cloud"}
        onThreadCreated={(id) => {
          if (!activeThreadId) {
            void navigate({ to: "/agents/$threadId", params: { threadId: id } })
          }
        }}
      >
        <Outlet />
      </AgentStreamProvider>
    </AgentsShell>
  )
}
