import { useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { CatchBoundary } from "@tanstack/react-router"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { LoadError, useLoadTimedOut } from "@/components/LoadError"

import { AgentThreadView } from "@/features/agents/components/AgentThreadView"
import { SubagentThreadView } from "@/features/agents/components/subagents/SubagentThreadView"
import { AgentThreadStreamBoundary } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { ThreadSourceProvider } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import { agentThreadKeys, useAgentThread } from "@/features/agents/lib/queries"
import type { ComposerDraft } from "@/features/agents/lib/types"
import {
  ensureThreadLoad,
  threadDetailFailed,
  threadDetailResolved,
} from "@/lib/perf/threadLoad"
import { pageTitle } from "@/lib/pageTitle"
import { useSession } from "@/lib/session"
import {
  pendingThreadStarts,
  usePendingThreadStarts,
} from "@/features/agents/lib/pendingThreadStarts"
import { AgentThreadHeader } from "@/features/agents/components/AgentThreadHeader"
import { AgentPromptBar } from "@/features/agents/components/AgentPromptBar"
import { AgentComposerDock } from "@/features/agents/components/composer/AgentComposerDock"
import { Messages } from "@/features/agents/components/messages"

export function AgentThreadPage(props: {
  threadId: string
  active?: boolean
  /** Show this subagent's transcript instead of the thread's own. */
  subagentId?: string
  composerDraft?: ComposerDraft
  /** False when the thread is embedded in a page that names the tab itself. */
  ownsTitle?: boolean
}) {
  const [attempt, setAttempt] = useState(0)
  const retry = () => setAttempt((current) => current + 1)
  const session = useSession()
  const pending = usePendingThreadStarts().entries.find(
    (entry) =>
      entry.thread.id === props.threadId &&
      entry.userId === session.data?.user_id &&
      entry.phase !== "accepted"
  )
  if (pending)
    return (
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <AgentThreadHeader
          startingThread={pending.thread}
          target={
            pending.thread.sandboxBridgeClient === "desktop"
              ? "This Mac"
              : "Cloud"
          }
          panelCollapsed
        />
        <Messages
          messages={pending.thread.messages}
          isStreaming
          contentWidthClass="max-w-3xl"
        />
        <AgentComposerDock>
          <AgentPromptBar
            compact
            disabled
            busy
            activeRun={{ threadId: props.threadId, running: true }}
            onStop={
              pending.phase === "stopping"
                ? undefined
                : () => pendingThreadStarts.stop(props.threadId)
            }
            stoppable={pending.phase !== "stopping"}
            placeholder={
              pending.phase === "stopping"
                ? "Stopping after startup…"
                : "Starting Robin…"
            }
          />
        </AgentComposerDock>
      </div>
    )
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
      <AgentThreadContent
        key={`${props.threadId}:${attempt}`}
        {...props}
        retry={retry}
      />
    </CatchBoundary>
  )
}

function AgentThreadContent({
  threadId,
  active = true,
  subagentId,
  composerDraft,
  ownsTitle = true,
  retry,
}: {
  threadId: string
  active?: boolean
  subagentId?: string
  composerDraft?: ComposerDraft
  ownsTitle?: boolean
  retry: () => void
}) {
  const queryClient = useQueryClient()
  const threadQuery = useAgentThread(threadId, active)
  const retryDetail = () => {
    void queryClient.resetQueries({
      queryKey: agentThreadKeys.detail(threadId),
      exact: true,
    })
    retry()
  }
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
    if (!active || !ownsTitle || !title) return
    const documentTitle = pageTitle(title)
    document.title = documentTitle
    return () => {
      if (document.title === documentTitle) document.title = pageTitle("Agents")
    }
  }, [active, ownsTitle, title])

  if (threadQuery.isPending && !timedOut) {
    return (
      <main className="flex min-w-0 flex-1 items-center justify-center p-space-5">
        <Skeleton className="h-40 w-full max-w-md" />
      </main>
    )
  }

  if (!threadQuery.data) {
    return (
      <LoadError
        title="Unable to load thread"
        retry={retryDetail}
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
          <AgentThreadView
            thread={threadQuery.data}
            composerDraft={composerDraft}
            retry={retry}
          />
        )}
      </ThreadSourceProvider>
    </AgentThreadStreamBoundary>
  )
}
