import { useEffect } from "react"
import {
  Outlet,
  Navigate,
  createFileRoute,
  useMatch,
  useRouterState,
} from "@tanstack/react-router"

import { AgentsShell } from "@/features/agents/components/AgentsSidebar"
import { Skeleton } from "@/components/ui/skeleton"
import { AgentStreamProvider } from "@/features/agents/lib/stream/AgentStreamProvider"
import { useExperimentalAssistantUi, useProfile } from "@/lib/profile"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"
import { isDesktopLocalModeEnabled } from "@/lib/desktop-local-mode"
import { rememberAppLocation } from "@/lib/appLocation"
import { useDesktopThreadSource } from "@/features/agents/lib/desktopThreadSource"

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
  const profile = useProfile()
  const experimentalAssistantUi = useExperimentalAssistantUi()
  const navigate = Route.useNavigate()
  const threadMatch = useMatch({
    from: "/agents/$threadId",
    shouldThrow: false,
  })
  const localMatch = useMatch({
    from: "/agents/local/$sessionId",
    shouldThrow: false,
  })
  const activeThreadId = threadMatch?.params.threadId
  const activeLocalSessionId = localMatch?.params.sessionId
  const homeMatch = useMatch({ from: "/agents/", shouldThrow: false })
  const [desktopSource] = useDesktopThreadSource()
  const localHome =
    Boolean(homeMatch) &&
    (!session.data ||
      Boolean(homeMatch?.search.localProject) ||
      (typeof window !== "undefined" &&
        Boolean(window.openSweDesktop) &&
        desktopSource === "local" &&
        !homeMatch?.search.repo &&
        !homeMatch?.search.noProject))
  const runtimeThreadId = activeLocalSessionId ?? activeThreadId ?? null
  // Only a thread route has to wait for the profile: mounting the runtime the
  // profile does not select hydrates that thread's transcript a second time.
  const location = useRouterState({
    select: (state) => state.location,
  })
  const pathname = location.pathname
  const awaitingRuntimeChoice =
    Boolean(session.data) &&
    profile.isPending &&
    (runtimeThreadId !== null ||
      pathname === "/agents" ||
      pathname === "/agents/")
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
  if (!awaitingRuntimeChoice && experimentalAssistantUi && !localHome) {
    if (activeThreadId)
      return (
        <Navigate
          to="/assistant/$threadId"
          params={{ threadId: activeThreadId }}
          replace
        />
      )
    if (pathname === "/agents" || pathname === "/agents/")
      return (
        <Navigate
          to="/assistant"
          search={{
            repo: homeMatch?.search.repo,
            noProject: homeMatch?.search.noProject,
          }}
          replace
        />
      )
  }

  return (
    <AgentsShell
      user={session.data ?? null}
      localOnly={localOnly}
      activeThreadId={activeThreadId}
      activeLocalSessionId={activeLocalSessionId}
    >
      {awaitingRuntimeChoice ? (
        <main className="flex min-w-0 flex-1 items-center justify-center p-6">
          <Skeleton className="h-40 w-full max-w-md" />
        </main>
      ) : (
        <AgentStreamProvider
          threadId={runtimeThreadId}
          transport={activeLocalSessionId ? "local" : "cloud"}
          onThreadCreated={(id) => {
            if (!activeThreadId) {
              void navigate({
                to: "/agents/$threadId",
                params: { threadId: id },
              })
            }
          }}
        >
          <Outlet />
        </AgentStreamProvider>
      )}
    </AgentsShell>
  )
}
