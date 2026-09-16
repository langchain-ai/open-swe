import { Navigate, createFileRoute } from "@tanstack/react-router"
import { useEffect } from "react"

import { LocalAgentThreadView } from "@/features/agents/components/LocalAgentThreadView"
import { useReadyDesktopLocalThread } from "@/features/agents/lib/desktopLocal"
import { Skeleton } from "@/components/ui/skeleton"
import {
  ensureThreadLoad,
  threadDetailFailed,
  threadLocalResolved,
} from "@/lib/perf/threadLoad"

export const Route = createFileRoute("/agents/local/$sessionId")({
  component: LocalAgentThreadPage,
})

function LocalAgentThreadPage() {
  const { sessionId } = Route.useParams()
  const threadQuery = useReadyDesktopLocalThread(sessionId)
  const resolved = threadQuery.data !== undefined
  const failed = threadQuery.isError || threadQuery.data === null
  useEffect(() => {
    ensureThreadLoad(sessionId)
  }, [sessionId])
  useEffect(() => {
    if (failed) threadDetailFailed(sessionId)
    else if (resolved) threadLocalResolved(sessionId)
  }, [failed, resolved, sessionId])
  if (typeof window === "undefined" || !window.openSweDesktop) {
    return <Navigate to="/agents" />
  }
  if (threadQuery.isPending) {
    return (
      <main className="flex min-w-0 flex-1 items-center justify-center p-6">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    )
  }
  if (threadQuery.isError || !threadQuery.data) {
    return <Navigate to="/agents" />
  }
  return <LocalAgentThreadView sessionId={sessionId} />
}
