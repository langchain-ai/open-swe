import { useEffect, useRef } from "react"
import { Navigate } from "@tanstack/react-router"

import { AgentThreadView } from "@/features/agents/components/AgentThreadView"
import { Skeleton } from "@/components/ui/skeleton"
import { AgentThreadStreamBoundary } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { useAgentThread } from "@/features/agents/lib/queries"
import {
  ensureThreadLoad,
  threadDetailFailed,
  threadDetailResolved,
} from "@/lib/perf/threadLoad"

export function AgentThreadPage({
  threadId,
  active = true,
  autoFocusComposer = false,
}: {
  threadId: string
  active?: boolean
  autoFocusComposer?: boolean
}) {
  const threadQuery = useAgentThread(threadId)
  const title = threadQuery.data?.title
  const hasDetail = threadQuery.data !== undefined
  // A detail seeded from the sidebar list is on hand before the fetch returns.
  const detailCachedOnMount = useRef(hasDetail)

  useEffect(() => {
    if (active) ensureThreadLoad(threadId)
  }, [active, threadId])

  useEffect(() => {
    if (!active) return
    if (hasDetail)
      threadDetailResolved(threadId, { cached: detailCachedOnMount.current })
    else if (threadQuery.isError) threadDetailFailed(threadId)
  }, [active, hasDetail, threadId, threadQuery.isError])

  useEffect(() => {
    if (!active || !title) return
    const documentTitle = `${title} - Open SWE`
    document.title = documentTitle
    return () => {
      if (document.title === documentTitle) document.title = "Open SWE"
    }
  }, [active, title])

  if (threadQuery.isLoading) {
    return (
      <main className="flex min-w-0 flex-1 items-center justify-center p-6">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    )
  }

  if (threadQuery.isError || !threadQuery.data) {
    return active ? <Navigate to="/agents" /> : null
  }

  return (
    <AgentThreadStreamBoundary active={active}>
      <AgentThreadView
        thread={threadQuery.data}
        autoFocusComposer={autoFocusComposer}
      />
    </AgentThreadStreamBoundary>
  )
}
