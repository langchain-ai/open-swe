import { useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Outlet,
  createFileRoute,
  useMatch,
  useRouterState,
} from "@tanstack/react-router"

import { AgentsShell } from "@/features/agents/components/AgentsSidebar"
import { reviewChatQuery } from "@/features/agents/lib/queries"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { RequireLogin } from "@/lib/auth-redirect"
import { useSession } from "@/lib/session"
import { rememberAppLocation } from "@/lib/appLocation"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents")({
  component: AgentsLayout,
  head: () => ({ meta: [{ title: pageTitle("Agents") }] }),
})

function AgentsLayout() {
  const session = useSession()
  const threadMatch = useMatch({
    from: "/agents/$threadId",
    shouldThrow: false,
  })
  const reviewMatch = useMatch({
    from: "/agents/reviews/$owner/$repo/$number",
    shouldThrow: false,
  })
  const activeThreadId = threadMatch?.params.threadId
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
  const location = useRouterState({
    select: (state) => state.location,
  })
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

  return (
    <AgentsShell
      user={session.data}
      activeThreadId={activeThreadId ?? activeReviewThreadId}
    >
      <Outlet />
    </AgentsShell>
  )
}
