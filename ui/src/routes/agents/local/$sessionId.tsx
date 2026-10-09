import { Navigate, createFileRoute } from "@tanstack/react-router"
import { useEffect } from "react"

import { LocalAgentThreadView } from "@/features/agents/components/LocalAgentThreadView"
import { useReadyLegacyLocalThread } from "@/features/agents/lib/legacyLocal"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import {
  ensureThreadLoad,
  threadDetailFailed,
  threadLocalResolved,
} from "@/lib/perf/threadLoad"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/local/$sessionId")({
  component: LocalAgentThreadPage,
  head: () => ({ meta: [{ title: pageTitle("Local agent") }] }),
})

function LocalAgentThreadPage() {
  const { sessionId } = Route.useParams()
  const threadQuery = useReadyLegacyLocalThread(sessionId)
  const resolved = threadQuery.data !== undefined
  const failed = threadQuery.isError || threadQuery.data === null
  useEffect(() => {
    ensureThreadLoad(sessionId)
  }, [sessionId])
  const title = threadQuery.data?.title ?? null
  useEffect(() => {
    if (!title) return
    const documentTitle = pageTitle(title)
    document.title = documentTitle
    return () => {
      if (document.title === documentTitle)
        document.title = pageTitle("Local agent")
    }
  }, [title])
  useEffect(() => {
    if (failed) threadDetailFailed(sessionId)
    else if (resolved) threadLocalResolved(sessionId)
  }, [failed, resolved, sessionId])
  if (typeof window === "undefined" || !window.openSweDesktop) {
    return <Navigate to="/agents" />
  }
  if (threadQuery.isPending) {
    return (
      <main className="flex min-w-0 flex-1 items-center justify-center p-space-5">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    )
  }
  if (threadQuery.isError || !threadQuery.data) {
    return <Navigate to="/agents" />
  }
  return <LocalAgentThreadView sessionId={sessionId} />
}
