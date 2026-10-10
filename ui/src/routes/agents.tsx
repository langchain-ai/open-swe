import { useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Outlet,
  Navigate,
  createFileRoute,
  useMatch,
  useRouterState,
} from "@tanstack/react-router"

import { AgentsShell } from "@/features/agents/components/AgentsSidebar"
import { reviewChatQuery } from "@/features/agents/lib/queries"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { useExperimentalAssistantUi, useProfile } from "@/lib/profile"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"
import { rememberAppLocation } from "@/lib/appLocation"
import { useDesktopThreadSource } from "@/features/agents/lib/desktopThreadSource"
import { useLocalThreads } from "@/features/agents/lib/desktopLocal"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents")({
  component: AgentsLayout,
  head: () => ({ meta: [{ title: pageTitle("Agents") }] }),
})

function AgentsLayout() {
  const session = useSession()
  const profile = useProfile()
  const experimentalAssistantUi = useExperimentalAssistantUi()
  const threadMatch = useMatch({
    from: "/agents/$threadId",
    shouldThrow: false,
  })
  const localMatch = useMatch({
    from: "/agents/local/$sessionId",
    shouldThrow: false,
  })
  const reviewMatch = useMatch({
    from: "/agents/reviews/$owner/$repo/$number",
    shouldThrow: false,
  })
  const activeThreadId = threadMatch?.params.threadId
  const activeLocalSessionId = localMatch?.params.sessionId
  // A review page's sidebar row is the user's review chat thread. The page
  // loads it; the layout only reads it, so off a review page it asks for nothing.
  const reviewChat = useQuery({
    ...reviewChatQuery({
      owner: reviewMatch?.params.owner ?? "",
      repo: reviewMatch?.params.repo ?? "",
      number: Number(reviewMatch?.params.number),
    }),
    enabled: false,
  })
  const activeReviewThreadId = reviewMatch
    ? reviewChat.data?.thread_id
    : undefined
  const homeMatch = useMatch({ from: "/agents/", shouldThrow: false })
  const [desktopSource] = useDesktopThreadSource()
  const localHome =
    Boolean(homeMatch) &&
    (Boolean(homeMatch?.search.localRepo) ||
      (typeof window !== "undefined" &&
        Boolean(window.openSweDesktop) &&
        desktopSource === "local" &&
        !homeMatch?.search.repo &&
        !homeMatch?.search.noRepo))
  // A "This Mac" thread needs this app's bridge and git controls, which only
  // the agents view has.
  const localThreads = useLocalThreads()
  const thisMacThread = Boolean(
    activeThreadId &&
    localThreads.data?.some((thread) => thread.id === activeThreadId)
  )
  const runtimeThreadId = activeLocalSessionId ?? activeThreadId ?? null
  // Only a thread route has to wait for the profile: mounting the runtime the
  // profile does not select hydrates that thread's transcript a second time.
  const location = useRouterState({
    select: (state) => state.location,
  })
  const pathname = location.pathname
  const awaitingRuntimeChoice =
    Boolean(session.data) &&
    (profile.isPending || localThreads.isLoading) &&
    (runtimeThreadId !== null ||
      pathname === "/agents" ||
      pathname === "/agents/")
  useEffect(() => {
    rememberAppLocation(location.href)
  }, [location.href])

  if (session.isLoading) {
    return (
      <main className="agents-ui flex h-svh items-center justify-center bg-surface-level-1 p-space-5">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    )
  }

  if (!session.data) return <RequireLogin />
  if (!awaitingRuntimeChoice && experimentalAssistantUi && !localHome) {
    if (activeThreadId && !thisMacThread)
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
            noRepo: homeMatch?.search.noRepo,
          }}
          replace
        />
      )
  }

  return (
    <AgentsShell
      user={session.data}
      activeThreadId={activeThreadId ?? activeReviewThreadId}
      activeLocalSessionId={activeLocalSessionId}
    >
      {awaitingRuntimeChoice ? (
        <main className="flex min-w-0 flex-1 items-center justify-center p-space-5">
          <Skeleton className="h-40 w-full max-w-md" />
        </main>
      ) : (
        <Outlet />
      )}
    </AgentsShell>
  )
}
