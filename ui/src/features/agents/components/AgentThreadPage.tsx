import { useContext, useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { CatchBoundary } from "@tanstack/react-router"
import { LoadError, useLoadTimedOut } from "@/components/LoadError"

import { AgentThreadView } from "@/features/agents/components/AgentThreadView"
import { ConversationLoading } from "@/features/agents/components/ConversationLoading"
import { SubagentThreadView } from "@/features/agents/components/subagents/SubagentThreadView"
import { EmbeddedThreadContext } from "@/features/agents/lib/embeddedThread"
import { readStoredPanelCollapsed } from "@/features/agents/lib/gitPanelPreferences"
import { AgentThreadStreamBoundary } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import {
  ThreadSourceProvider,
  useThreadSource,
} from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import { agentThreadKeys, useAgentThread } from "@/features/agents/lib/queries"
import { transcriptSnapshots } from "@/features/agents/lib/transcript/useThreadTranscript"
import type { ComposerDraft } from "@/features/agents/lib/types"
import {
  ensureThreadLoad,
  threadDetailFailed,
  threadDetailPending,
  threadDetailResolved,
  threadHydrated,
  useThreadTranscriptPaint,
} from "@/lib/perf/threadLoad"
import { pageTitle } from "@/lib/pageTitle"
import { useSession } from "@/lib/session"
import { readPreference, useStreamPreference } from "@/lib/streamPreference"
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
        <AgentThreadHeader startingThread={pending.thread} panelCollapsed />
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
  // The stream reads a thread by its id alone, so under the SDK setting it
  // starts with the summary instead of after it. Otherwise the summary picks
  // the source.
  const streamFirst = useStreamPreference()
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

  // Most threads turn out to be transcript threads, so their read leaves with
  // the summary instead of after it. A thread older than the transcript log
  // wastes a 404, beside the head script's own on a page load. The stored
  // setting, not `streamFirst`, which reads off while a page hydrates.
  const summaryPending = threadQuery.isPending
  useEffect(() => {
    if (active && summaryPending && !readPreference())
      transcriptSnapshots.preload(threadId)
  }, [active, summaryPending, threadId])

  useEffect(() => {
    if (!active) return
    if (hasDetail)
      threadDetailResolved(threadId, { cached: detailCachedOnMount.current })
    else if (threadQuery.isError) threadDetailFailed(threadId)
    else threadDetailPending(threadId)
  }, [active, hasDetail, threadId, threadQuery.isError])

  useEffect(() => {
    if (!active || !ownsTitle || !title) return
    const documentTitle = pageTitle(title)
    document.title = documentTitle
    return () => {
      if (document.title === documentTitle) document.title = pageTitle("Agents")
    }
  }, [active, ownsTitle, title])

  const awaitingSummary = threadQuery.isPending && !timedOut
  const skeleton = <AgentThreadLoading threadId={threadId} />
  if (awaitingSummary && !streamFirst) return skeleton

  if (!awaitingSummary && !threadQuery.data) {
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
        {!threadQuery.data ? (
          // A subagent's view finds its task through the summary.
          subagentId ? (
            skeleton
          ) : (
            <AgentThreadAwaitingSummary threadId={threadId} />
          )
        ) : subagentId ? (
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

/** The thread page's frame with placeholders where its title and transcript go. */
function AgentThreadLoading({ threadId }: { threadId: string }) {
  const embedded = useContext(EmbeddedThreadContext)
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {!embedded && (
        <AgentThreadHeader
          panelCollapsed={readStoredPanelCollapsed(threadId)}
          loading
        />
      )}
      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
        <ConversationLoading />
        <AgentComposerDock>
          <AgentPromptBar compact disabled placeholder="Loading…" />
        </AgentComposerDock>
      </div>
    </div>
  )
}

/**
 * The thread's transcript, read by the stream while the summary the rest of the
 * page needs is still on its way. It shows only a thread with messages: a
 * missing, forbidden, empty or failed read waits for the summary to say which.
 */
function AgentThreadAwaitingSummary({ threadId }: { threadId: string }) {
  const embedded = useContext(EmbeddedThreadContext)
  const source = useThreadSource()
  const shown =
    source.messages.length > 0 ? source.messages.slice(source.shownFrom) : null

  useEffect(() => {
    if (!source.isHydrating) threadHydrated(threadId)
  }, [source.isHydrating, threadId])
  useThreadTranscriptPaint(threadId, shown, source)

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {!embedded && (
        <AgentThreadHeader
          panelCollapsed={readStoredPanelCollapsed(threadId)}
          loading
        />
      )}
      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
        {shown ? (
          <Messages
            messages={shown}
            transcript={source.messages}
            threadId={threadId}
            scrollKey={threadId}
            isStreaming={source.isRunning}
            contentWidthClass="max-w-3xl"
            loadEarlier={
              source.hasOlder
                ? {
                    loading: source.isLoadingOlder,
                    onLoadEarlier: source.loadOlder,
                    onLoadAll: source.loadAllOlder,
                  }
                : null
            }
          />
        ) : (
          <ConversationLoading />
        )}
        <AgentComposerDock>
          <AgentPromptBar compact disabled placeholder="Loading…" />
        </AgentComposerDock>
      </div>
    </div>
  )
}
