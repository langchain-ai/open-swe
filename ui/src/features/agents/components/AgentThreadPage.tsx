import { useEffect, useRef } from "react"
import { CatchBoundary } from "@tanstack/react-router"
import { LoadError, useLoadTimedOut } from "@/components/LoadError"

import { AgentThreadView } from "@/features/agents/components/AgentThreadView"
import { Skeleton } from "@/components/ui/skeleton"
import { AgentThreadStreamBoundary } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { ThreadSourceProvider } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import { useAgentThread } from "@/features/agents/lib/queries"
import {
  ensureThreadLoad,
  threadDetailFailed,
  threadDetailResolved,
} from "@/lib/perf/threadLoad"

export function AgentThreadPage(props: { threadId: string; active?: boolean }) {
  return (
    <CatchBoundary
      getResetKey={() => props.threadId}
      errorComponent={({ error, reset }) => (
        <LoadError
          title="Unable to display thread"
          context={`Thread: ${props.threadId}`}
          error={error}
          retry={reset}
        />
      )}
    >
      <AgentThreadContent key={props.threadId} {...props} />
    </CatchBoundary>
  )
}

function AgentThreadContent({
  threadId,
  active = true,
}: {
  threadId: string
  active?: boolean
}) {
  const threadQuery = useAgentThread(threadId)
  const transcript = threadQuery.data?.transcript === "v2"
  const timedOut = useLoadTimedOut(threadQuery.isPending)
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
      if (document.title === documentTitle) document.title = "Agents - Open SWE"
    }
  }, [active, title])

  if (threadQuery.isPending && !timedOut) {
    return (
      <main className="flex min-w-0 flex-1 items-center justify-center p-6">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    )
  }

  if (!threadQuery.data) {
    return (
      <LoadError
        title="Unable to load thread"
        context={`Thread: ${threadId}`}
        error={
          threadQuery.error ??
          "Loading took longer than 30 seconds. Check your connection and try again."
        }
      />
    )
  }

  return (
    <AgentThreadStreamBoundary active={active}>
      <ThreadSourceProvider threadId={threadId} transcript={transcript}>
        <AgentThreadView thread={threadQuery.data} />
      </ThreadSourceProvider>
    </AgentThreadStreamBoundary>
  )
}
