import { useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Outlet,
  createFileRoute,
  useMatch,
  useRouterState,
} from "@tanstack/react-router"
import { ThreadCacheProvider } from "@langchain/react"

import { AgentsShell } from "@/features/agents/components/AgentsSidebar"
import { PluginTabs } from "@/features/plugins/components/PluginTabs"
import { parsePullRequestSelection } from "@/features/reviews/search"
import { reviewChatQuery } from "@/features/reviews/lib/reviewKeys"
import { AppLoading } from "@/components/AppLoading"
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
    from: "/agents/reviews/",
    shouldThrow: false,
  })
  const selectedReview = parsePullRequestSelection(reviewMatch?.search.pr)
  const activeThreadId = threadMatch?.params.threadId
  // A review page's sidebar row is the user's review chat thread. The page
  // loads it; the layout only reads it, so off a review page it asks for nothing.
  const reviewChat = useQuery({
    ...reviewChatQuery(selectedReview ?? { owner: "", repo: "", number: 0 }),
    enabled: false,
  })
  const activeReviewThreadId = selectedReview
    ? reviewChat.data?.thread_id
    : undefined
  const location = useRouterState({
    select: (state) => state.location,
  })
  useEffect(() => {
    rememberAppLocation(location.href)
  }, [location.href])

  if (session.isLoading) {
    return <AppLoading />
  }

  if (!session.data) return <RequireLogin />

  return (
    <AgentsShell
      user={session.data}
      activeThreadId={activeThreadId ?? activeReviewThreadId}
      hideThreadSidebar={Boolean(reviewMatch)}
    >
      {/* Keyed by user, so a new sign-in starts with nothing another user read. */}
      <ThreadCacheProvider key={session.data.user_id}>
        <Outlet />
      </ThreadCacheProvider>
      <PluginTabs />
    </AgentsShell>
  )
}
