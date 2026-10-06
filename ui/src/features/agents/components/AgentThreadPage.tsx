import { useEffect, useRef } from "react"
import { CatchBoundary } from "@tanstack/react-router"
import { LoadError, useLoadTimedOut } from "@/components/LoadError"

import { AgentThreadView } from "@/features/agents/components/AgentThreadView"
import { SubagentThreadView } from "@/features/agents/components/subagents/SubagentThreadView"
import { Box, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Skeleton } from "@langchain/gtm-platform-design-system/ui/skeleton"
import { AgentThreadStreamBoundary } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { ThreadSourceProvider } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import { useAgentThread } from "@/features/agents/lib/queries"
import {
  ensureThreadLoad,
  threadDetailFailed,
  threadDetailResolved,
} from "@/lib/perf/threadLoad"
import { pageTitle } from "@/lib/pageTitle"

export function AgentThreadPage(props: {
  threadId: string
  active?: boolean
  /** Show this subagent's transcript instead of the thread's own. */
  subagentId?: string
}) {
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
  subagentId,
}: {
  threadId: string
  active?: boolean
  subagentId?: string
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
    const documentTitle = pageTitle(title)
    document.title = documentTitle
    return () => {
      if (document.title === documentTitle) document.title = pageTitle("Agents")
    }
  }, [active, title])

  if (threadQuery.isPending && !timedOut) return <ThreadLoadingSkeleton />

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
        {subagentId ? (
          <SubagentThreadView
            key={subagentId}
            thread={threadQuery.data}
            subagentId={subagentId}
          />
        ) : (
          <AgentThreadView thread={threadQuery.data} />
        )}
      </ThreadSourceProvider>
    </AgentThreadStreamBoundary>
  )
}

/**
 * A thread that is still loading: the band's place and a few transcript lines
 * on the thread measure, so the first paint already has the thread's shape.
 */
export function ThreadLoadingSkeleton() {
  return (
    <Stack
      grow
      aria-busy="true"
      aria-label="Loading thread"
      className="min-w-0"
    >
      <Box className="h-toolbar shrink-0 px-3 py-3">
        <Skeleton className="h-full w-48" />
      </Box>
      <Stack gap="lg" className="mx-auto w-full max-w-thread px-4 pt-6">
        <Skeleton className="ml-auto h-10 w-2/3 rounded-panel" />
        <Stack gap="sm">
          <Skeleton className="h-3 w-5/6" />
          <Skeleton className="h-3 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </Stack>
      </Stack>
    </Stack>
  )
}
